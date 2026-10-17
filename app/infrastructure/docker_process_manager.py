from __future__ import annotations

import asyncio
import posixpath
import re
import shlex
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple, Union

from app.async_compat import run_in_thread

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


ContainerProvider = Callable[[], Tuple[Any, Any]]


class ProcessStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    EXITED = "exited"
    FAILED = "failed"


@dataclass
class ProcessState:
    name: str
    status: ProcessStatus = ProcessStatus.STOPPED
    pid: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    command: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    log_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "state": self.status.value,
            "pid": self.pid,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "returncode": self.exit_code,
            "command": list(self.command),
            "cwd": self.cwd,
            "log_path": self.log_path,
            "error": self.error,
        }


@dataclass
class ManagedDockerProcess:
    name: str
    container_provider: ContainerProvider
    state: ProcessState = field(init=False)
    _sequence: int = 0
    _log_generation: int = 0
    _lines: Deque[Tuple[int, str]] = field(default_factory=lambda: deque(maxlen=10000))
    _line_event: asyncio.Event = field(default_factory=asyncio.Event)
    _wait_event: asyncio.Event = field(default_factory=asyncio.Event)
    _pump_task: Optional[asyncio.Task] = None
    _exec_id: Optional[str] = None
    _last_exec_inspection: Dict[str, Any] = field(default_factory=dict)
    _pid_file: str = field(init=False)
    _loop: Optional[asyncio.AbstractEventLoop] = None

    def __post_init__(self) -> None:
        self.state = ProcessState(name=self.name)
        self._pid_file = "/tmp/oran-lab-{}.pid".format(self.name)

    @property
    def alive(self) -> bool:
        return (
            self._exec_id is not None
            and self.state.exit_code is None
            and self.state.status in {
                ProcessStatus.STARTING,
                ProcessStatus.RUNNING,
                ProcessStatus.READY,
                ProcessStatus.STOPPING,
            }
        )

    @property
    def current_sequence(self) -> int:
        return self._sequence

    @property
    def log_generation(self) -> int:
        """Numer uruchomienia, potrzebny odbiorcom przy resetowaniu seq."""

        return self._log_generation

    async def start(
        self,
        command: List[str],
        cwd: Union[Path, str],
        log_path: Path,
        environment: Optional[Dict[str, str]] = None,
    ) -> None:
        if self.alive:
            raise RuntimeError("Proces {} juz dziala".format(self.name))
        cwd_text = str(cwd)
        client, container = self.container_provider()
        await run_in_thread(self._validate_container_command, container, command, cwd_text)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_generation += 1
        self._sequence = 0
        self._lines.clear()
        self._line_event.clear()
        self._wait_event.clear()
        self._loop = asyncio.get_running_loop()
        self._last_exec_inspection = {}
        self.state = ProcessState(
            name=self.name,
            status=ProcessStatus.STARTING,
            started_at=utc_now(),
            command=list(command),
            cwd=cwd_text,
            log_path=str(log_path),
        )
        launch_lines = [
            "[O-RAN LAB] backend: docker exec",
            "[O-RAN LAB] container: {}".format(getattr(container, "name", "")),
            "[O-RAN LAB] cwd: {}".format(cwd_text),
            "[O-RAN LAB] command: {}".format(shlex.join(command)),
            "[O-RAN LAB] supervisor: setsid --wait; lifecycle: Docker Running + ExitCode",
        ]
        if environment:
            launch_lines.append(
                "[O-RAN LAB] environment: {}".format(
                    " ".join(
                        "{}={}".format(key, shlex.quote(str(value)))
                        for key, value in sorted(environment.items())
                    )
                )
            )
        log_path.write_text("\n".join(launch_lines) + "\n", encoding="utf-8")
        for line in launch_lines:
            self._append_line(line)

        script = "\n".join(
            [
                "set -e",
                "rm -f {}".format(shlex.quote(self._pid_file)),
                "cd {}".format(shlex.quote(cwd_text)),
                "echo $$ > {}".format(shlex.quote(self._pid_file)),
                "if command -v stdbuf >/dev/null 2>&1; then",
                "  exec stdbuf -oL -eL {}".format(shlex.join(command)),
                "else",
                "  exec {}".format(shlex.join(command)),
                "fi",
            ]
        )
        api = client.api
        exec_info = api.exec_create(
            container.id,
            # ns3 is a Python wrapper which launches the simulator as a child.
            # A private process group lets stop() terminate both, including
            # descendants that keep the Docker stdout stream open. If setsid
            # itself is a group leader, it forks. --wait keeps Docker attached
            # to the supervisor until its child exits and forwards that code.
            # Without it Docker can report EXIT_SUCCESS while ns-3/RIC lives.
            ["setsid", "--wait", "/bin/bash", "-lc", script],
            stdout=True,
            stderr=True,
            stdin=False,
            tty=False,
            environment={key: str(value) for key, value in (environment or {}).items()},
        )
        self._exec_id = exec_info["Id"]
        self.state.status = ProcessStatus.RUNNING
        self._pump_task = asyncio.create_task(self._pump(client, self._exec_id, log_path))

    def mark_ready(self) -> None:
        if self.alive:
            self.state.status = ProcessStatus.READY

    async def wait(self) -> int:
        if self._exec_id is None or self.state.exit_code is not None:
            return self.state.exit_code if self.state.exit_code is not None else 0
        await self._wait_event.wait()
        return self.state.exit_code if self.state.exit_code is not None else -1

    async def stop(self, timeout_s: float = 5.0) -> None:
        if not self.alive:
            if self.state.status not in {ProcessStatus.EXITED, ProcessStatus.FAILED}:
                self.state.status = ProcessStatus.STOPPED
            return
        self.state.status = ProcessStatus.STOPPING
        _, container = self.container_provider()
        kill_term = self._group_signal_script("TERM")
        await run_in_thread(container.exec_run, ["/bin/bash", "-lc", kill_term], stdout=True, stderr=True)
        try:
            await asyncio.wait_for(self.wait(), timeout=max(0.1, timeout_s))
        except asyncio.TimeoutError:
            kill_kill = self._group_signal_script("KILL")
            await run_in_thread(container.exec_run, ["/bin/bash", "-lc", kill_kill], stdout=True, stderr=True)
            try:
                await asyncio.wait_for(self.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.state.exit_code = -9
                self.state.error = "Proces nie zakonczyl sie po SIGKILL"
                self.state.status = ProcessStatus.FAILED
                self.state.ended_at = utc_now()
                self._wait_event.set()
        if self.state.status == ProcessStatus.STOPPING:
            self.state.status = ProcessStatus.STOPPED

    def _group_signal_script(self, signal: str) -> str:
        if signal not in {"TERM", "KILL"}:
            raise ValueError("Unsupported process signal")
        # The pid file is written inside setsid, so its PID is also its PGID.
        # Validate it before using a negative group ID; never signal group 0/1.
        return "\n".join([
            "if [ -f {} ]; then".format(shlex.quote(self._pid_file)),
            "  process_pid=$(cat {})".format(shlex.quote(self._pid_file)),
            "  case \"$process_pid\" in ''|*[!0-9]*) exit 1;; esac",
            "  if [ \"$process_pid\" -gt 1 ]; then",
            "    kill -{} -- -\"$process_pid\" 2>/dev/null || true".format(signal),
            "  fi",
            "fi",
        ])

    async def wait_for_log(
        self,
        patterns: Iterable[str],
        timeout_s: float,
        after: int = 0,
    ) -> str:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        deadline = asyncio.get_running_loop().time() + timeout_s
        checked_sequence = max(0, after)
        while True:
            lines = list(self._lines)
            for sequence, line in lines:
                if sequence <= checked_sequence:
                    continue
                if any(pattern.search(line) for pattern in compiled):
                    return line
                checked_sequence = sequence
            if not self.alive:
                raise RuntimeError("{} zakonczyl sie przed gotowoscia".format(self.name))
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timeout gotowosci procesu {}".format(self.name))
            self._line_event.clear()
            try:
                await asyncio.wait_for(self._line_event.wait(), min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass

    def logs_after(self, after: int = 0, limit: int = 500) -> Dict[str, Any]:
        if after > self._sequence:
            after = 0
        selected = [
            (seq, line)
            for seq, line in self._lines
            if seq > after
        ][-max(1, min(limit, 2000)):]
        return {
            "lines": [
                {"seq": seq, "generation": self._log_generation, "text": line}
                for seq, line in selected
            ],
            "last_seq": selected[-1][0] if selected else after,
            "generation": self._log_generation,
        }

    def full_log(self, max_lines: int = 100000) -> str:
        path = Path(self.state.log_path) if self.state.log_path else None
        if path and path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-max(1, min(max_lines, 200000)):])
        return "\n".join(line for _, line in self._lines)

    def snapshot(self) -> Dict[str, Any]:
        data = self.state.to_dict()
        data["command_display"] = shlex.join(self.state.command) if self.state.command else None
        data["exec_id"] = self._exec_id
        data["log_generation"] = self._log_generation
        data["exec_inspection"] = dict(self._last_exec_inspection)
        return data

    def reset(self) -> None:
        if self.alive:
            raise RuntimeError("Nie mozna zresetowac aktywnego procesu {}".format(self.name))
        self._exec_id = None
        self._last_exec_inspection = {}
        self._sequence = 0
        self._lines.clear()
        self._line_event.clear()
        self._wait_event.clear()
        self.state = ProcessState(name=self.name)

    async def _pump(self, client: Any, exec_id: str, log_path: Path) -> None:
        code: Optional[int] = None
        error: Optional[str] = None
        try:
            code = await run_in_thread(self._stream_exec, client, exec_id, log_path)
        except Exception as exc:
            error = str(exc)
        finally:
            self.state.exit_code = code
            self.state.ended_at = utc_now()
            self.state.pid = None
            if self.state.status == ProcessStatus.STOPPING:
                self.state.status = ProcessStatus.STOPPED
            elif code == 0:
                self.state.status = ProcessStatus.EXITED
            else:
                self.state.status = ProcessStatus.FAILED
                self.state.error = error or "Proces zakonczyl sie kodem {}".format(code)
            message = "[O-RAN LAB] {} exec={} {}".format(
                utc_now(), exec_id,
                "monitoring error: {}".format(error) if error else "finished: exit_code={}".format(code),
            )
            self._append_line(message)
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(message + "\n")
            except OSError:
                pass
            self._wait_event.set()
            self._line_event.set()

    def _stream_exec(self, client: Any, exec_id: str, log_path: Path) -> int:
        buffer = ""
        with log_path.open("a", encoding="utf-8", errors="replace") as handle:
            stream = client.api.exec_start(exec_id, stream=True, demux=True)
            for chunk in stream:
                if isinstance(chunk, tuple):
                    payloads = [item for item in chunk if item]
                else:
                    payloads = [chunk] if chunk else []
                for payload in payloads:
                    if isinstance(payload, bytes):
                        text = payload.decode("utf-8", errors="replace")
                    else:
                        text = str(payload)
                    buffer += text
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.rstrip("\r")
                        handle.write(line + "\n")
                        handle.flush()
                        self._append_line_threadsafe(line)
            if buffer:
                line = buffer.rstrip("\r")
                handle.write(line + "\n")
                handle.flush()
                self._append_line_threadsafe(line)
        # EOF is only the end of stdout/stderr, not proof of process exit.
        # In particular ExitCode=0 may be present while Running is true.
        reported_open_exec = False
        while True:
            info = client.api.exec_inspect(exec_id)
            self._last_exec_inspection = {
                key: info.get(key) for key in ("ID", "Running", "ExitCode", "Pid")
            }
            if info.get("Running") is False:
                code = info.get("ExitCode")
                if code is None:
                    raise RuntimeError("Docker potwierdził koniec exec bez kodu wyjścia: {}".format(exec_id))
                return int(code)
            if info.get("Running") is not True:
                raise RuntimeError("Docker nie zwrócił stanu Running dla exec: {}".format(exec_id))
            if not reported_open_exec:
                message = "[O-RAN LAB] {} log stream closed; Docker exec {} still Running; waiting for exit".format(utc_now(), exec_id)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(message + "\n")
                self._append_line_threadsafe(message)
                reported_open_exec = True
            time.sleep(0.25)

    def _append_line_threadsafe(self, line: str) -> None:
        if self._loop is None:
            self._append_line(line)
            return
        self._loop.call_soon_threadsafe(self._append_line, line)

    def _append_line(self, line: str) -> None:
        self._sequence += 1
        self._lines.append((self._sequence, line))
        self._line_event.set()

    @staticmethod
    def _validate_container_command(container: Any, command: List[str], cwd: str) -> None:
        if not command or not command[0]:
            raise ValueError("Pusta komenda procesu")
        executable = command[0]
        if executable.startswith("./"):
            candidate = posixpath.join(cwd, executable[2:])
            check = "test -d {0} && test -f {1} && test -x {1}".format(
                shlex.quote(cwd),
                shlex.quote(candidate),
            )
        elif "/" in executable:
            check = "test -d {0} && test -f {1} && test -x {1}".format(
                shlex.quote(cwd),
                shlex.quote(executable),
            )
        else:
            check = "test -d {0} && command -v {1} >/dev/null 2>&1".format(
                shlex.quote(cwd),
                shlex.quote(executable),
            )
        raw = container.exec_run(["/bin/bash", "-lc", check], stdout=True, stderr=True)
        exit_code = int(getattr(raw, "exit_code", raw[0] if isinstance(raw, tuple) else 1) or 0)
        if exit_code != 0:
            raise FileNotFoundError(
                "Program nie istnieje albo nie jest wykonywalny w kontenerze: cwd={} command={}".format(
                    cwd,
                    shlex.join(command),
                )
            )
