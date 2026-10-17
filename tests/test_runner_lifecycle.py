import asyncio
import threading
from pathlib import Path

import pytest

import app.simulation.runner as runner_module
from app.monitoring.live_metrics import LiveMetricsCollector
from app.monitoring.live_topology import LiveTopologyCollector
from app.monitoring.xapp_decisions import XappDecisionCollector
from app.simulation.kpm_live import KpmLiveCollector
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus
from app.simulation.runner import SimulationRunner


class FakeProcess:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.alive = True
        self.started = []

    async def wait(self):
        delay, code = self.outcomes.pop(0)
        await asyncio.sleep(delay)
        if code is not None:
            self.alive = False
        return code

    async def start(self, command, cwd, log_path, environment):
        self.started.append({"command": command, "cwd": cwd, "log_path": log_path, "environment": environment})
        self.alive = True

    def logs_after(self, after, _limit):
        return {"lines": [], "last_seq": after}


class FakeProcesses:
    def __init__(self, **processes):
        self.processes = processes

    def get(self, name):
        return self.processes[name]

    def snapshot(self):
        return {
            name: {"name": name, "status": "ready" if name != "xapp" else "running"}
            for name in self.processes
        }


def runner_for_wait() -> SimulationRunner:
    runner = object.__new__(SimulationRunner)
    runner._stop_requested = threading.Event()
    runner._live_sequences = {}
    return runner


def simulation() -> Simulation:
    return Simulation(
        name="Kontrola xApp",
        configuration=SimulationConfiguration(scenario="optimized", parameters={"simTime": 10}),
    )


def test_runner_does_not_clear_stop_requested_before_single_run() -> None:
    runner = runner_for_wait()
    runner._stop_requested.set()
    seen = []

    async def fake_run(item, _progress):
        seen.append(runner._stop_requested.is_set())
        return item

    runner._run = fake_run  # type: ignore[method-assign]
    runner.run(simulation())

    assert seen == [True]


def test_runner_retries_one_failed_run_after_confirmed_session_cleanup(tmp_path: Path) -> None:
    """Druga próba używa nowej sesji i nie dziedziczy częściowych wyników."""

    class RetryStorage:
        def __init__(self) -> None:
            self.saved = []
            self.prepared = []

        def save(self, item) -> None:
            self.saved.append((item.status, item.error))

        def prepare_retry(self, item, attempt):
            self.prepared.append((item.id, attempt, item.result))
            item.result = None
            item.error = None
            return tmp_path / "attempts" / "attempt-{}".format(attempt)

    class RetrySessions:
        def __init__(self) -> None:
            self.cleaned = []

        def ensure_session_cleanup(self, session_id):
            self.cleaned.append(session_id)
            return ["oran-energy-lab-old"]

    runner = runner_for_wait()
    runner.storage = RetryStorage()
    runner.sessions = RetrySessions()
    runner._current_session = None
    calls = []

    async def fake_run(item, _progress):
        calls.append(item.id)
        retry = item.runtime.get("run_retry")
        item.runtime = {"session_id": "session-{}".format(len(calls)), "run_retry": retry}
        if len(calls) == 1:
            item.status = SimulationStatus.FAILED
            item.error = "chwilowy błąd Docker"
            item.result = SimulationResult(metrics={"total_energy_j": 1.0})
        else:
            item.status = SimulationStatus.COMPLETED
            item.error = None
            item.result = SimulationResult(metrics={"total_energy_j": 2.0})
        return item

    runner._run = fake_run  # type: ignore[method-assign]
    item = simulation()

    result = runner.run(item)

    assert calls == [item.id, item.id]
    assert runner.sessions.cleaned == ["session-1"]
    assert runner.storage.prepared == [(item.id, 1, SimulationResult(metrics={"total_energy_j": 1.0}))]
    assert result.status is SimulationStatus.COMPLETED
    assert result.result is not None and result.result.metrics["total_energy_j"] == 2.0
    retry = result.runtime["run_retry"]
    assert retry["state"] == "finished"
    assert [entry["status"] for entry in retry["attempts"]] == ["FAILED", "COMPLETED"]
    assert retry["previous_artifacts"].endswith("attempt-1")


def test_run_launches_ns3_with_user_environment_without_docker_user_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The source-image wrapper needs USER, while Docker must keep its normal UID."""

    class FakeConfig:
        def as_dict(self):
            return {
                "scenarios": {"optimized": {"path": "scratch/live.cc"}},
                "xapps": {
                    "optimized": {
                        "executable": "./xapp_my_energy_optimized",
                    }
                },
                "runtime": {},
            }

    class FakeStorage:
        def directory(self, _simulation_id: str) -> Path:
            return tmp_path

        def save(self, _simulation: Simulation) -> None:
            pass

    class FakeParser:
        def parse(self, _directory: Path) -> SimulationResult:
            return SimulationResult()

    class FakeSessions:
        def __init__(self) -> None:
            self.session = type(
                "Session",
                (),
                {
                    "id": "session-1",
                    "container": type("Container", (), {"name": "session-1", "status": "running"})(),
                    "client": object(),
                },
            )()

        def start(self):
            return self.session

        def stop(self, _session):
            return []

    class CapturingProcess:
        def __init__(self, name: str) -> None:
            self.name = name
            self.starts = []

        async def start(self, command, cwd, log_path, environment=None, **keywords) -> None:
            CapturingProcesses.start_order.append(self.name)
            self.starts.append(
                {
                    "command": command,
                    "cwd": cwd,
                    "log_path": log_path,
                    "environment": environment,
                    "keywords": keywords,
                }
            )

        def mark_ready(self) -> None:
            pass

    class CapturingProcesses:
        instance = None
        start_order = []

        def __init__(self, _session) -> None:
            type(self).start_order = []
            self.items = {
                name: CapturingProcess(name) for name in ("ric", "ns3", "xapp")
            }
            type(self).instance = self

        def get(self, name: str) -> CapturingProcess:
            return self.items[name]

        def snapshot(self):
            return {}

    class FakeDiscovery:
        def __init__(self, _config) -> None:
            pass

        def discover(self, **_keywords):
            return {
                "docker": {"available": True},
                "simulator": {"image_found": True, "container_found": True},
                "components": {
                    "near_rt_ric": {"detected": True, "path": "/workspace/ric/nearRT-RIC"},
                    "ns3_root": {"detected": True, "path": "/workspace/ns3"},
                },
                "xapps": [
                    {"executable_path": "/workspace/xapp/xapp_my_energy_optimized"}
                ],
            }

    async def no_wait(*_args, **_keywords) -> None:
        pass

    monkeypatch.setattr(runner_module, "_SessionProcesses", CapturingProcesses)
    monkeypatch.setattr(runner_module, "DockerLabDiscovery", FakeDiscovery)
    monkeypatch.setattr(runner_module, "validate_configuration", lambda *_args: [])
    monkeypatch.setattr(runner_module, "packaged_scenario_source", lambda *_args: None)
    monkeypatch.setattr(runner_module, "packaged_scenario_path", lambda *_args: None)

    runner = object.__new__(SimulationRunner)
    runner.config = FakeConfig()
    runner.storage = FakeStorage()
    runner.parser = FakeParser()
    runner.sessions = FakeSessions()
    runner._stop_requested = threading.Event()
    runner._current_session = None
    runner._live_sequences = {}
    runner._container_scenario_source_path = lambda *_args, **_keywords: "/workspace/ns3/scratch/live.cc"
    runner._scenario_expression = lambda *_args, **_keywords: "scratch/live.cc --simTime=5"
    runner._prepare_packaged_xapp = lambda *_args: ("/workspace/xapp", ["./xapp_my_energy_optimized"])
    runner._clean_outputs = lambda *_args, **_keywords: None
    runner._wait_for_sctp = no_wait
    runner._wait_for_logs = no_wait
    runner._wait_for_end = no_wait
    runner._stop_processes = no_wait
    runner._archive_outputs = lambda *_args, **_keywords: None
    runner._verify_required_live_timeseries = lambda *_args, **_keywords: None
    runner._calibration_target_active_real_s = lambda *_args, **_keywords: None

    item = simulation()
    item.configuration.xapp = "optimized"
    completed = asyncio.run(runner._run(item, None))

    assert completed.status is SimulationStatus.COMPLETED
    ns3_launches = CapturingProcesses.instance.get("ns3").starts
    assert len(ns3_launches) == 1
    assert ns3_launches[0]["command"] == ["./ns3", "run", "scratch/live.cc --simTime=5"]
    assert ns3_launches[0]["environment"] == {"USER": "oranlab"}
    # A Docker ``user``/UID override would appear as another keyword here;
    # the compatibility workaround supplies only the process environment.
    assert ns3_launches[0]["keywords"] == {}
    assert CapturingProcesses.start_order == ["ric", "ns3", "xapp"]


def test_xapp_exhaustion_does_not_stop_ns3(tmp_path: Path) -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([(0.02, 0)]),
        ric=FakeProcess([(1.0, 0)]),
        xapp=FakeProcess([(0.0, 7)]),
    )
    item = simulation()

    asyncio.run(
        runner_for_wait()._wait_for_end(
            processes,
            {"command": ["./xapp"], "cwd": "/xapp", "environment": {}, "decision_log_root": "/ns3"},
            tmp_path,
            {"runtime": {"xapp_max_restarts": 0, "wall_clock_timeout_s": 1}},
            item,
            None,
        )
    )

    assert not processes.get("xapp").started
    assert any("trwa dalej bez xAppa" in value for value in item.runtime["warnings"])
    assert item.runtime["xapp_events"][0]["exit_code"] == 7
    assert item.runtime["experiment_valid"] is False


@pytest.mark.parametrize("ns3_code", [0, 134])
def test_ns3_exit_during_xapp_restart_keeps_its_actual_exit_code(tmp_path: Path, ns3_code: int) -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([(0.01, ns3_code)]),
        ric=FakeProcess([(1.0, 0)]),
        xapp=FakeProcess([(0.0, 7)]),
    )
    item = simulation()
    operation = runner_for_wait()._wait_for_end(
        processes,
        {"command": ["./xapp"], "cwd": "/xapp", "environment": {}, "decision_log_root": "/ns3"},
        tmp_path,
        {"runtime": {"xapp_max_restarts": 1, "xapp_restart_delay_s": 0.03}},
        item, None,
    )
    if ns3_code:
        with pytest.raises(RuntimeError, match="kodem 134"):
            asyncio.run(operation)
    else:
        asyncio.run(operation)
    assert not processes.get("xapp").started
    assert item.runtime["termination"]["exit_code"] == ns3_code
    assert item.runtime["experiment_valid"] is False


def test_failed_docker_start_preserves_cleanup_identity_and_discards_previous_live_state(monkeypatch, tmp_path):
    from app.simulation.docker_session import DockerSessionError
    from app.data.storage import SimulationStorage

    class Config:
        def as_dict(self):
            return {}

    class Sessions:
        def start(self):
            raise DockerSessionError("partial Docker start", "partially-created-session")

    item = simulation()
    item.runtime = {"session_id": "old-session", "live": {"energy": 99}}
    item.result = SimulationResult(metrics={"total_energy_j": 99})
    storage = SimulationStorage(tmp_path / "runs")
    storage.create(item)
    monkeypatch.setattr(runner_module, "validate_configuration", lambda *_: [])
    runner = SimulationRunner(Config(), storage, sessions=Sessions())

    result = asyncio.run(runner._run(item, None))

    assert result.status is SimulationStatus.FAILED
    assert result.runtime["session_id"] == "partially-created-session"
    assert "live" not in result.runtime
    assert result.result is None
    assert result.runtime["experiment_valid"] is False


def test_zero_delivery_is_a_valid_measured_outcome(tmp_path):
    item = simulation()
    item.runtime["live_metrics_contract"] = {"required": True, "file": "ee_timeseries.csv"}
    (tmp_path / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
        "2,0,100,50,0,0\n", encoding="utf-8",
    )
    SimulationRunner._verify_required_live_timeseries(tmp_path, item)
    assert item.runtime["live_metrics_contract"]["state"] == "artifact_verified"


def test_xapp_restart_uses_a_new_decision_log_file(tmp_path: Path) -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([(0.03, 0)]),
        ric=FakeProcess([(1.0, 0)]),
        xapp=FakeProcess([(0.0, 9), (1.0, 0)]),
    )
    item = simulation()

    asyncio.run(
        runner_for_wait()._wait_for_end(
            processes,
            {
                "command": ["./xapp"],
                "cwd": "/xapp",
                "environment": {"EE_DECISION_LOG": "/ns3/xapp_ee_decisions-1.csv"},
                "decision_log_root": "/ns3",
            },
            tmp_path,
            {"runtime": {"xapp_max_restarts": 1, "xapp_restart_delay_s": 0, "wall_clock_timeout_s": 1}},
            item,
            None,
        )
    )

    assert len(processes.get("xapp").started) == 1
    assert processes.get("xapp").started[0]["environment"]["EE_DECISION_LOG"].endswith("xapp_ee_decisions-2.csv")
    assert item.runtime["xapp_events"][1]["event"] == "restart"


def test_readiness_wait_publishes_live_data_before_e2_is_ready() -> None:
    process = FakeProcess([])
    process.logs_after = lambda after, _limit: {  # type: ignore[method-assign]
        "lines": [{"text": "Sent E2-SETUP-REQUEST"}],
        "last_seq": after + 1,
    }
    processes = FakeProcesses(ns3=process)
    published = []

    async def publish_live() -> None:
        published.append("snapshot")

    asyncio.run(
        runner_for_wait()._wait_for_logs(
            processes,
            ("ns3",),
            ("E2[- ]SETUP[- ]REQUEST",),
            1.0,
            live_poll=publish_live,
        )
    )

    assert published == ["snapshot"]


def test_normal_ns3_exit_publishes_a_final_live_snapshot(tmp_path: Path) -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([(0.02, 0)]),
        ric=FakeProcess([(1.0, 0)]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    published = []

    async def publish(*_args, **_kwargs):
        published.append("snapshot")

    async def container_snapshot(_processes):
        return {"name": "session", "state": "running"}

    runner._publish_live_snapshot = publish  # type: ignore[method-assign]
    runner._container_snapshot = container_snapshot  # type: ignore[method-assign]
    asyncio.run(
        runner._wait_for_end(
            processes,
            None,
            tmp_path,
            {"runtime": {"live_poll_interval_s": 60, "wall_clock_timeout_s": 1}},
            item,
            None,
            root="/ns3",
            live_metrics=LiveMetricsCollector(),
        )
    )

    # Pierwszy snapshot pochodzi z początku pętli, drugi z obsługi końca
    # ns-3. Przy długim interwale nie mógł powstać z normalnego pollingu.
    assert published == ["snapshot", "snapshot"]


def test_live_snapshot_uses_actual_timeseries_and_structured_event() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    header = "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
    text = header + "2,4000000,200,100,2000000,20000\n"
    runner._read_live_timeseries_tail = lambda *_args: (text, None)  # type: ignore[method-assign]
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
        )

    asyncio.run(run())

    assert received[-1][1] == "LIVE_UPDATE"
    payload = received[-1][3]
    assert payload["progress"]["percent"] == 20.0
    assert payload["current_metrics"]["energy_per_mbit_j"] == 50.0
    assert payload["processes"]["ric"]["role"] == "nearRT-RIC"
    assert item.runtime["live"]["data_collection"] == "OK"


def test_live_snapshot_prefers_full_metrics_csv_over_direct_ee_series() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    metrics = (
        "time_s,total_energy_j,total_power_w,delivered_bits,throughput_bps,"
        "packet_loss_pct,ee_bit_per_j,j_per_mbit,mean_prb_utilization_pct,p05_sinr_db\n"
        "2,200,100,4000000,2000000,0.1,20000,50,3.5,12\n"
    )
    direct_series = (
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
        "2,4000000,250,125,2000000,16000\n"
    )
    runner._read_live_metrics_tail = lambda *_args: (metrics, None)  # type: ignore[method-assign]
    runner._read_live_timeseries_tail = lambda *_args: (direct_series, None)  # type: ignore[method-assign]
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
        )

    asyncio.run(run())

    payload = received[-1][3]
    assert payload["data_collection"] == "OK"
    assert payload["data_status"]["data_source"] == "metrics.csv"
    assert payload["current_metrics"]["total_energy_j"] == 200.0
    assert payload["current_metrics"]["energy_per_mbit_j"] == 50.0
    assert payload["current_metrics"]["packet_loss_pct"] == 0.1


def test_live_snapshot_falls_back_when_metrics_csv_has_blank_core_values() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    incomplete_metrics = (
        "time_s,total_energy_j,total_power_w,delivered_bits,throughput_bps,"
        "packet_loss_pct,ee_bit_per_j,j_per_mbit,mean_prb_utilization_pct,p05_sinr_db\n"
        "2,200,100,,,,,,3.5,12\n"
    )
    direct_series = (
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
        "2,4000000,250,125,2000000,16000\n"
    )
    runner._read_live_metrics_tail = lambda *_args: (incomplete_metrics, None)  # type: ignore[method-assign]
    runner._read_live_timeseries_tail = lambda *_args: (direct_series, None)  # type: ignore[method-assign]
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
        )

    asyncio.run(run())

    payload = received[-1][3]
    assert payload["data_collection"] == "OK"
    assert payload["data_status"]["data_source"] == "ee_timeseries.csv"
    assert payload["current_metrics"]["energy_efficiency_bit_per_j"] == 16000.0
    assert payload["current_metrics"]["energy_per_mbit_j"] == 62.5
    assert payload["current_metrics"]["throughput_mbit_per_s"] == 2.0
    assert payload["current_metrics"]["delivered_mbit"] == 4.0


def test_live_snapshot_adds_only_reported_kpm_quality_data() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    series = (
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
        "2,4000000,200,100,2000000,20000\n"
    )
    runner._read_live_metrics_tail = lambda *_args: (None, "brak metrics.csv")  # type: ignore[method-assign]
    runner._read_live_timeseries_tail = lambda *_args: (series, None)  # type: ignore[method-assign]
    runner._read_live_kpm_file_tails = lambda *_args: {  # type: ignore[method-assign]
        "cu-cp-cell-1.txt": (
            "timestamp,UE (imsi),L3 serving SINR\n"
            "1000,1,10\n2000,1,12\n"
        ),
        "du-cell-1.txt": (
            "timestamp,dlPrbUsage,DRB.MeanActiveUeDl\n"
            "1000,4,2\n2000,6,3\n"
        ),
        "cu-up-cell-1.txt": (
            "timestamp,UE (imsi),DRB.PdcpSduVolumeDl_Filter.UEID (txBytes),"
            "DRB.PdcpSduDelayDl.UEID (PdcpLatency)\n"
            "1000,1,1000,20\n2000,1,3000,30\n"
        ),
    }
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
            live_kpm=KpmLiveCollector(),
        )

    asyncio.run(run())

    payload = received[-1][3]
    assert payload["current_metrics"]["p05_sinr_db"] == 12.0
    assert payload["current_metrics"]["mean_prb_pct"] == 6.0
    assert payload["current_metrics"]["delay_ms"] == 3.0
    assert payload["current_metrics"]["active_ues"] == 3.0
    assert payload["series"]["p05_sinr_db"] == [{"x": 2.0, "y": 12.0}]
    assert payload["series_sources"]["p05_sinr_db"] == "KPM: cu-cp-cell-1.txt"
    assert payload["kpm_live"]["data_status"]["source_file_count"] == 3


def test_live_snapshot_includes_only_real_xapp_decision_csv_records() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.configuration.xapp = "optimized"
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    runner._read_live_metrics_tail = lambda *_args: (None, "brak metrics.csv")  # type: ignore[method-assign]
    runner._read_live_timeseries_tail = lambda *_args: (  # type: ignore[method-assign]
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
        "1,1000000,100,100,1000000,10000\n",
        None,
    )
    runner._read_live_energy_file_tails = lambda *_args: {}  # type: ignore[method-assign]
    runner._read_live_xapp_decision_tails = lambda *_args: {  # type: ignore[method-assign]
        "xapp_ee_decisions-1.csv": (
            "time,event,cell_id,ue_id,ue_count,serving_sinr_db,target_cell,target_sinr_db,reason\n"
            "1,handover_sent,2,7,3,10,4,14,relocation_before_shutdown\n"
        )
    }
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            1.0,
            {"name": "session", "state": "running"},
            live_xapp_decisions=XappDecisionCollector(),
        )

    asyncio.run(run())

    payload = received[-1][3]
    assert payload["xapp_decisions"]["events"] == [
        {
            "time_s": 1.0,
            "event": "handover_sent",
            "source": "xapp_ee_decisions-1.csv",
            "cell_id": 2.0,
            "ue_id": 7.0,
            "ue_count": 3.0,
            "serving_sinr_db": 10.0,
            "target_cell": 4.0,
            "target_sinr_db": 14.0,
            "reason": "relocation_before_shutdown",
        }
    ]
    assert payload["data_status"]["xapp_decisions"]["accepted_rows"] == 1


def test_live_snapshot_marks_a_required_primary_series_while_waiting() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    item.runtime["live_metrics_contract"] = {
        "required": True,
        "file": "ee_timeseries.csv",
        "state": "source_verified",
    }
    runner = runner_for_wait()
    runner._read_live_timeseries_tail = lambda *_args: (  # type: ignore[method-assign]
        None, "Plik ee_timeseries.csv nie został jeszcze utworzony."
    )
    runner._read_live_energy_file_tails = lambda *_args: {}  # type: ignore[method-assign]
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
        )

    asyncio.run(run())

    payload = received[-1][3]
    assert payload["data_collection"] == "CZEKA NA PEŁNE METRYKI"
    assert payload["data_status"]["primary_required"] is True
    assert payload["live_metrics_contract"]["state"] == "awaiting_samples"


def test_required_live_artifact_is_verified_before_completed_status(tmp_path: Path) -> None:
    item = simulation()
    item.runtime["live_metrics_contract"] = {
        "required": True,
        "file": "ee_timeseries.csv",
    }
    series_directory = tmp_path / "timeseries"
    series_directory.mkdir()
    (series_directory / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
        "1,1000000,100,100,1000000,10000\n",
        encoding="utf-8",
    )

    SimulationRunner._verify_required_live_timeseries(tmp_path, item)

    assert item.runtime["live_metrics_contract"]["state"] == "artifact_verified"
    assert item.runtime["live_metrics_contract"]["accepted_rows"] == 1


def test_required_live_artifact_accepts_complete_metrics_csv(tmp_path: Path) -> None:
    item = simulation()
    item.runtime["live_metrics_contract"] = {
        "required": True,
        "accepted_files": ["metrics.csv", "ee_timeseries.csv"],
    }
    series_directory = tmp_path / "timeseries"
    series_directory.mkdir()
    (series_directory / "metrics.csv").write_text(
        "time_s,total_energy_j,total_power_w,delivered_bits,throughput_bps,"
        "ee_bit_per_j,j_per_mbit\n"
        "1,100,100,1000000,1000000,10000,100\n",
        encoding="utf-8",
    )

    SimulationRunner._verify_required_live_timeseries(tmp_path, item)

    contract = item.runtime["live_metrics_contract"]
    assert contract["state"] == "artifact_verified"
    assert contract["artifact_file"] == "metrics.csv"


def test_required_live_artifact_cannot_be_silently_replaced_by_energy_only(tmp_path: Path) -> None:
    item = simulation()
    item.runtime["live_metrics_contract"] = {
        "required": True,
        "file": "ee_timeseries.csv",
    }
    series_directory = tmp_path / "timeseries"
    series_directory.mkdir()
    (series_directory / "energyfilecell2.csv").write_text(
        "Time,NetEnergy,DiffEnergy\n1,100,100\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="ee_timeseries.csv"):
        SimulationRunner._verify_required_live_timeseries(tmp_path, item)

    assert item.runtime["live_metrics_contract"]["state"] == "artifact_missing"


def test_live_snapshot_uses_real_energy_file_fallback_without_inventing_traffic() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    runner._read_live_timeseries_tail = lambda *_args: (  # type: ignore[method-assign]
        None, "Plik ee_timeseries.csv nie został jeszcze utworzony."
    )
    runner._read_live_energy_file_tails = lambda *_args: {  # type: ignore[method-assign]
        "energyfilecell2.csv": "Time,NetEnergy,DiffEnergy\n1,100,100\n2,150,50\n",
        "energyfilecell3.csv": "Time,NetEnergy,DiffEnergy\n1,200,200\n2,280,80\n",
    }
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
        )

    asyncio.run(run())

    payload = received[-1][3]
    assert payload["data_collection"] == "PARTIAL"
    assert payload["current_metrics"]["total_energy_j"] == 430.0
    assert payload["current_metrics"]["energy_efficiency_bit_per_j"] is None
    assert payload["series"]["energy_j"][-1] == {"x": 2.0, "y": 430.0}
    assert payload["series"]["throughput_mbit_per_s"] == []


def test_live_snapshot_includes_only_real_reported_topology() -> None:
    processes = FakeProcesses(
        ns3=FakeProcess([]),
        ric=FakeProcess([]),
        xapp=FakeProcess([]),
    )
    item = simulation()
    item.status = item.status.RUNNING
    runner = runner_for_wait()
    header = "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
    runner._read_live_timeseries_tail = lambda *_args: (  # type: ignore[method-assign]
        header + "2,4000000,200,100,2000000,20000\n", None
    )
    cells = (
        "timestamp,id,x,y,simid,ESstate,currEC,maxEC,totalcurrEC\n"
        "100,0,4000,4000,99,0,0,0,0\n"
        "100,2,2000,2000,99,0,3,4,3\n"
    )
    ues = "timestamp,id,x,y,type,cell,simid\n100,7,2010,1990,mc,2,99\n"
    runner._read_live_topology_tail = (  # type: ignore[method-assign]
        lambda _processes, _root, filename: (
            cells if filename == "gnbs.txt" else ues if filename == "ue_position.txt" else None
        )
    )
    received = []

    async def run():
        await runner._publish_live_snapshot(
            processes,
            "/ns3",
            LiveMetricsCollector(),
            item,
            lambda *args: received.append(args),
            4.0,
            {"name": "session", "state": "running"},
            live_topology=LiveTopologyCollector(),
        )

    asyncio.run(run())

    topology = received[-1][3]["topology"]
    assert topology["bounds"] == (0.0, 4000.0, 0.0, 4000.0)
    assert topology["cells"][0]["id"] == "2"
    assert topology["cells"][0]["tx_sleeping"] is False
    assert topology["ues"][0]["serving_cell"] == "2"
    assert topology["sample_time_s"] == 0.001
