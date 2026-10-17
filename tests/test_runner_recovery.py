"""Regressions for session builds and wrapper/child process termination."""

import asyncio
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.simulation.runner as runner_module
from app.infrastructure.docker_process_manager import ManagedDockerProcess
from app.services.xapp_service import XAppService
from app.simulation.models import Simulation, SimulationConfiguration
from app.simulation.runner import SimulationRunner


@pytest.mark.parametrize("build_code", [0, 2])
def test_packaged_xapp_is_rebuilt_before_a_binary_is_selected(monkeypatch, tmp_path, build_code):
    calls = []
    synced = []
    selected = []
    container = object()
    processes = SimpleNamespace(session=SimpleNamespace(container=container))

    def exec_text(command):
        calls.append(command)
        return (build_code, "compiler output") if command.startswith("cmake --build") else (0, "")

    def sync(_processes, local, remote):
        synced.append((local, remote))
        return SimpleNamespace(digest="a" * 64, state="updated")

    processes.exec_text = exec_text
    monkeypatch.setattr(runner_module, "sync_packaged_scenario", sync)
    monkeypatch.setattr(XAppService, "_find_source_dir", lambda *_: "/flexric/examples/xApp/c/orange")
    monkeypatch.setattr(XAppService, "_find_built_executable", lambda *_: selected.append(True) or "/flexric/build/new-xapp")
    item = Simulation("xApp rebuild", SimulationConfiguration(scenario="optimized", parameters={}, xapp="optimized"))
    args = (
        processes, {"xapps": {"optimized": {"executable": "./xapp_my_energy_optimized"}}},
        {"components": {"flexric_root": {"path": "/flexric"}}}, tmp_path, item,
    )
    if build_code:
        with pytest.raises(RuntimeError, match="Build dostarczonego xAppa"):
            SimulationRunner._prepare_packaged_xapp(*args)
        assert not selected  # A stale executable must never become a fallback.
        assert item.runtime["xapp_source"]["build_state"] == "failed"
    else:
        assert SimulationRunner._prepare_packaged_xapp(*args) == (
            "/flexric/build/new-xapp", ["./xapp_my_energy_optimized"]
        )
        assert item.runtime["xapp_source"]["build_state"] == "verified"
        assert item.runtime["xapp_source"]["sha256"] == "a" * 64
    assert len(synced) == 1
    assert synced[0][0].name == "xapp_my_energy_optimized.c"
    assert synced[0][1] == "/flexric/examples/xApp/c/orange/xapp_my_energy_optimized.c"
    assert "compiler output" in (tmp_path / "logs" / "xapp-build.log").read_text(encoding="utf-8")


def test_managed_process_launches_in_a_separate_process_group(tmp_path):
    created = []

    class Container:
        id = "test-container"
        name = "test-container"

        def exec_run(self, *_args, **_kwargs):
            return 0, b""

    class Api:
        def exec_create(self, _container, command, **_kwargs):
            created.append(command)
            return {"Id": "test-exec"}

        def exec_start(self, *_args, **_kwargs):
            return iter([])

        def exec_inspect(self, _exec_id):
            return {"Running": False, "ExitCode": 0}

    process = ManagedDockerProcess("ns3", lambda: (SimpleNamespace(api=Api()), Container()))

    async def run():
        await process.start(["./ns3", "run", "scratch/example"], "/ns3", tmp_path / "ns3.log")
        assert await process.wait() == 0

    asyncio.run(run())
    assert created[0][:4] == ["setsid", "--wait", "/bin/bash", "-lc"]


def test_closed_log_stream_keeps_process_alive_until_docker_confirms_exit(tmp_path):
    inspected = threading.Event()
    finished = threading.Event()

    class Api:
        def exec_start(self, *_args, **_kwargs):
            yield b"READY\n", None

        def exec_inspect(self, _exec_id):
            inspected.set()
            # Docker can report ExitCode=0 while Running is still true.
            return {"Running": not finished.is_set(), "ExitCode": 7 if finished.is_set() else 0}

    async def run():
        from app.infrastructure.docker_process_manager import ProcessStatus

        process = ManagedDockerProcess("ric", lambda: None)
        process._exec_id = "exec"
        process.state.status = ProcessStatus.RUNNING
        pump = asyncio.create_task(process._pump(SimpleNamespace(api=Api()), "exec", tmp_path / "ric.log"))
        waiter = asyncio.create_task(process.wait())
        try:
            assert await runner_module.run_in_thread(inspected.wait, 2)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), 0.1)
            assert process.alive
            assert process.state.exit_code is None
            finished.set()
            assert await asyncio.wait_for(waiter, 2) == 7
            assert not process.alive
        finally:
            finished.set()
            await asyncio.wait_for(pump, 2)
            await waiter

    asyncio.run(run())


@pytest.mark.parametrize("inspection", [
    {"Running": False, "ExitCode": None},
    {"ExitCode": 0},
])
def test_incomplete_exec_inspection_is_a_monitoring_error(tmp_path, inspection):
    api = SimpleNamespace(exec_start=lambda *a, **kw: iter([]), exec_inspect=lambda _: inspection)
    process = ManagedDockerProcess("ric", lambda: None)
    with pytest.raises(RuntimeError, match="Docker"):
        process._stream_exec(SimpleNamespace(api=api), "exec", tmp_path / "ric.log")


@pytest.mark.skipif(os.name != "posix" or not shutil.which("setsid"), reason="Real POSIX process groups require Linux/setsid")
@pytest.mark.parametrize("exit_code", [0, 23])
def test_group_leader_forwards_the_child_exit_code(tmp_path, exit_code):
    class Api:
        def exec_create(self, _container, command, **_kwargs):
            self.command = command
            return {"Id": "local-exec"}

        def exec_start(self, _exec_id, **_kwargs):
            # This forces setsid to fork; an ordinary subprocess test does not.
            child = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            try:
                for line in child.stdout:
                    yield line, None
                self.code = child.wait(timeout=3)
            finally:
                child.stdout.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)

        def exec_inspect(self, _exec_id):
            return {"Running": False, "ExitCode": self.code}

    class Container:
        id = name = "local-test"

        def exec_run(self, command, **_kwargs):
            result = subprocess.run(command, capture_output=True, timeout=3)
            return result.returncode, result.stdout + result.stderr

    process = ManagedDockerProcess("ns3", lambda: (SimpleNamespace(api=api), Container()))
    process._pid_file = str(tmp_path / "process.pid")
    api = Api()

    async def run():
        await process.start([sys.executable, "-c", "import sys; print('READY'); sys.exit({})".format(exit_code)],
                            tmp_path, tmp_path / "ns3.log")
        assert await asyncio.wait_for(process.wait(), 5) == exit_code

    asyncio.run(run())


@pytest.mark.skipif(os.name != "posix" or not shutil.which("setsid"), reason="Real POSIX process groups require Linux/setsid")
@pytest.mark.parametrize("group_leader", [False, True])
def test_two_runs_stop_the_wrapper_and_its_child_and_release_streams(tmp_path: Path, group_leader):
    """Use real OS processes and pipes behind the small Docker API adapter."""

    class LocalApi:
        def __init__(self):
            self.command = None
            self.process = None
            self.environment = None

        def exec_create(self, _container, command, **kwargs):
            self.command = command
            self.environment = dict(os.environ, **kwargs.get("environment", {}))
            return {"Id": "local-exec"}

        def exec_start(self, _exec_id, **_kwargs):
            self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            env=self.environment, start_new_session=group_leader)
            for line in self.process.stdout:
                yield line, None
            self.process.wait(timeout=2)

        def exec_inspect(self, _exec_id):
            return {"Running": self.process.poll() is None, "ExitCode": self.process.returncode}

    class LocalContainer:
        id = "local-test"
        name = "local-test"

        def exec_run(self, command, **_kwargs):
            result = subprocess.run(command, capture_output=True, timeout=3)
            return result.returncode, result.stdout + result.stderr

    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import subprocess,sys\n"
        "child=subprocess.Popen([sys.executable,'-u','-c',"
        "'import time; print(\"CHILD_READY\", flush=True); time.sleep(30)'])\n"
        "child.wait()\n", encoding="utf-8",
    )

    for attempt in range(2):
        api = LocalApi()
        process = ManagedDockerProcess("ns3", lambda: (SimpleNamespace(api=api), LocalContainer()))
        process._pid_file = str(tmp_path / "run-{}.pid".format(attempt))

        async def run():
            await process.start([sys.executable, str(wrapper)], tmp_path, tmp_path / "run-{}.log".format(attempt))
            await process.wait_for_log(["CHILD_READY"], 3)
            # Force setsid's fork path, as can happen under Docker/runc.
            # Without --wait its parent returns 0 while the child is alive.
            with pytest.raises(subprocess.TimeoutExpired):
                await runner_module.run_in_thread(api.process.wait, 0.1)
            await process.stop(0.2)
            await asyncio.wait_for(asyncio.shield(process._pump_task), 3)
            assert not process.alive

        try:
            asyncio.run(run())
            assert api.process.poll() is not None
        finally:
            # Limit damage even if this regression fails under a Linux CI run.
            if api.process and api.process.poll() is None:
                import signal
                os.killpg(api.process.pid, signal.SIGKILL)
