import asyncio
import copy
import threading
import time
from pathlib import Path

import pytest

from app.data.storage import SimulationStorage
from app.monitoring.live_metrics import LiveMetricsCollector
from app.simulation.models import SimulationResult, SimulationStatus
from app.simulation.queue_timing import QueueTimeBudget, QueueTimePlan
from app.simulation.runner import SimulationRunner
from app.simulation.session_controller import SessionController


CONFIG = {
    "runtime": {"wall_clock_timeout_s": 3600},
    "scenarios": {
        "optimized": {
            "allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"],
            "minimum_sim_time_s": 0.2,
        }
    },
}


class CalibrationRunner:
    """Lekki backend kolejki: pierwszy run zwraca faktyczny punkt ns-3."""

    def __init__(self, measured_time=321.25, measurement_source="ee_timeseries.csv"):
        self.measured_time = measured_time
        self.measurement_source = measurement_source
        self.calls = []
        self.stop_resets = 0

    def preflight(self):
        return {"ok": True, "checks": ["Docker dostępny"], "errors": []}

    def reset_stop(self):
        self.stop_resets += 1

    def request_stop(self):
        return None

    def run(self, item, callback):
        self.calls.append(
            {
                "id": item.id,
                "parameters": copy.deepcopy(item.configuration.parameters),
                "queue_timing": copy.deepcopy(item.runtime.get("queue_timing")),
            }
        )
        timing = dict(item.runtime["queue_timing"])
        timing["active_elapsed_real_s"] = 600.0
        if len(self.calls) == 1 and self.measured_time is not None:
            timing.update(
                {
                    "state": "measured",
                    "observed_simulated_time_s": self.measured_time,
                    "measurement_source": self.measurement_source,
                }
            )
        item.runtime["queue_timing"] = timing
        item.status = SimulationStatus.COMPLETED
        item.result = SimulationResult(metrics={"total_energy_j": float(len(self.calls))})
        return item


class FailedFirstCalibrationRunner(CalibrationRunner):
    """Pierwszy run zawodzi, lecz kolejka nadal ma wykonać resztę."""

    def run(self, item, callback):
        result = super().run(item, callback)
        if len(self.calls) == 1:
            result.status = SimulationStatus.FAILED
            result.error = "celowy błąd pierwszego runu"
        return result


class CleanupWarningRunner(CalibrationRunner):
    """Pierwszy run ma niepotwierdzony cleanup, ale kolejka ma iść dalej."""

    def run(self, item, callback):
        result = super().run(item, callback)
        if len(self.calls) == 1:
            result.runtime["cleanup_failed"] = "tymczasowy błąd Docker API"
        return result


def wait_until_idle(controller: SessionController) -> None:
    deadline = time.time() + 2.0
    while controller.running and time.time() < deadline:
        time.sleep(0.01)
    assert not controller.running


def test_total_budget_gives_every_run_the_same_real_limit_without_copying_sim_time(tmp_path: Path) -> None:
    runner = CalibrationRunner()
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        runner,  # type: ignore[arg-type]
    )
    simulations = controller.new_session(2)
    simulations[0].configuration.parameters["RngRun"] = 17
    simulations[1].configuration.parameters["RngRun"] = 42
    controller.set_total_real_time_budget(1200)

    controller.start_all()
    wait_until_idle(controller)

    assert runner.stop_resets == 1
    assert runner.calls[0]["parameters"]["RngRun"] == 17
    assert runner.calls[1]["parameters"]["RngRun"] == 17
    # A 10-minute budget split over two items means 5 real minutes for each.
    # It must never turn an observed ns-3 time from run 1 into run 2's input.
    assert runner.calls[0]["parameters"]["simTime"] == 1000
    assert runner.calls[1]["parameters"]["simTime"] == 1000
    assert [item.configuration.parameters["simTime"] for item in controller.simulations] == [
        1000,
        1000,
    ]
    assert [item.configuration.parameters["RngRun"] for item in controller.simulations] == [17, 17]
    assert [call["queue_timing"]["per_run_target_active_real_s"] for call in runner.calls] == [
        600.0,
        600.0,
    ]
    assert all(
        call["queue_timing"]["cutoff_policy"] == "hard_real_time_each_run"
        for call in runner.calls
    )

    timing = controller.queue_time_budget()
    assert timing["calibration_state"] == "per_run_hard_real_time"
    assert timing["sim_time_policy"] == "observational_only"
    assert timing["completed_active_real_s"] == {
        controller.simulations[0].id: 600.0,
        controller.simulations[1].id: 600.0,
    }
    assert timing["shared_rng_run"] == 17
    assert timing["rng_was_consistent"] is False
    assert timing["estimated_queue_remaining_s"] == 0.0

    phases = [event.phase for event in controller.drain_events(100)]
    assert "TIME_BUDGET_CONFIGURED" in phases
    assert "TIME_BUDGET_RNG_NOTICE" in phases
    assert "TIME_BUDGET_CALIBRATED" not in phases


def test_total_budget_starts_next_run_without_any_ns3_time_observation(tmp_path: Path) -> None:
    runner = CalibrationRunner(measured_time=None)
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        runner,  # type: ignore[arg-type]
    )
    controller.new_session(2)
    controller.set_total_real_time_budget(1200)

    controller.start_all()
    wait_until_idle(controller)

    assert len(runner.calls) == 2
    assert runner.calls[0]["parameters"]["simTime"] == 1000
    assert runner.calls[1]["parameters"]["simTime"] == 1000
    assert runner.calls[0]["parameters"]["RngRun"] == runner.calls[1]["parameters"]["RngRun"]
    timing = controller.queue_time_budget()
    assert timing["calibration_state"] == "per_run_hard_real_time"
    assert timing["observed_simulated_times_s"] == {}
    assert timing["estimated_completion_epoch"] is not None
    assert timing["estimated_completion_basis"] == "completed_queue"
    phases = [event.phase for event in controller.drain_events(100)]
    assert "TIME_BUDGET_CALIBRATION_FALLBACK" not in phases


def test_total_budget_records_metrics_csv_as_observation_only(tmp_path: Path) -> None:
    runner = CalibrationRunner(measured_time=321.25, measurement_source="metrics.csv")
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        runner,  # type: ignore[arg-type]
    )
    controller.new_session(2)
    controller.set_total_real_time_budget(1200)

    controller.start_all()
    wait_until_idle(controller)

    assert len(runner.calls) == 2
    assert runner.calls[1]["parameters"]["simTime"] == 1000
    timing = controller.queue_time_budget()
    assert timing["calibration_state"] == "per_run_hard_real_time"
    assert timing["observed_simulated_times_s"] == {
        controller.simulations[0].id: 321.25,
    }
    assert timing["estimated_completion_basis"] == "completed_queue"


def test_total_budget_runs_remaining_items_when_first_run_fails(tmp_path: Path) -> None:
    runner = FailedFirstCalibrationRunner(measured_time=None)
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        runner,  # type: ignore[arg-type]
    )
    controller.new_session(2)
    controller.set_total_real_time_budget(1200)

    controller.start_all()
    wait_until_idle(controller)

    assert len(runner.calls) == 2
    assert runner.calls[0]["parameters"]["simTime"] == runner.calls[1]["parameters"]["simTime"]
    assert controller.simulations[0].status == SimulationStatus.FAILED
    assert controller.simulations[1].status == SimulationStatus.COMPLETED
    timing = controller.queue_time_budget()
    assert timing["calibration_state"] == "per_run_hard_real_time"
    assert timing["completed_active_real_s"] == {
        controller.simulations[0].id: 600.0,
        controller.simulations[1].id: 600.0,
    }


def test_queue_attempts_next_run_when_previous_cleanup_cannot_be_confirmed(tmp_path: Path) -> None:
    runner = CleanupWarningRunner()
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        runner,  # type: ignore[arg-type]
    )
    controller.new_session(2)
    controller.set_total_real_time_budget(1200)

    controller.start_all()
    wait_until_idle(controller)

    assert len(runner.calls) == 2
    events = controller.drain_events(100)
    assert any(event.phase == "CLEANUP_FAILED" for event in events)
    assert any(
        event.phase == "IDLE" and "ostrzeżeniem cleanupu" in event.message
        for event in events
    )
    assert controller.simulations[1].status == SimulationStatus.COMPLETED


def test_queue_time_budget_config_is_opt_in() -> None:
    assert QueueTimeBudget.from_mapping({}) is None
    assert QueueTimeBudget.from_mapping({"enabled": False, "total_real_time_s": 1200}) is None
    budget = QueueTimeBudget.from_mapping({"enabled": True, "total_real_time_s": 1200})
    assert budget is not None
    assert budget.per_run_real_time_s(2) == 600.0


def test_setting_budget_immediately_exposes_per_run_plan_and_finish_date(tmp_path: Path) -> None:
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        CalibrationRunner(),  # type: ignore[arg-type]
    )
    controller.new_session(2)

    snapshot = controller.set_total_real_time_budget(600.0)

    assert snapshot["per_run_target_active_real_s"] == 300.0
    assert snapshot["planned_remaining_active_real_s"] == 600.0
    assert snapshot["planned_completion_epoch"] is not None
    assert snapshot["estimated_queue_remaining_s"] is None


def test_queue_finish_estimate_adds_only_observed_real_setup_overhead(tmp_path: Path) -> None:
    """Data końca po pierwszym runie uwzględnia Docker/RIC, nigdy simTime."""
    controller = SessionController(
        CONFIG,
        SimulationStorage(tmp_path / "simulations"),
        CalibrationRunner(),  # type: ignore[arg-type]
    )
    simulations = controller.new_session(3)
    plan = QueueTimePlan(
        budget=QueueTimeBudget(1800.0),
        simulation_ids=[item.id for item in simulations],
        original_rng_runs={item.id: 1 for item in simulations},
        completed_active_real_s={simulations[0].id: 600.0},
        # 45 s to rzeczywisty, zmierzony czas rozruchu i archiwizacji pierwszego
        # runu. Nie ma tu żadnego parametru/odczytu czasu modelowanego.
        completed_setup_overhead_real_s={simulations[0].id: 45.0},
        finished_simulation_ids=[simulations[0].id],
    )
    controller._queue_time_plan = plan

    snapshot = controller.queue_time_budget()

    assert snapshot["planned_remaining_active_real_s"] == 1200.0
    assert snapshot["estimated_queue_remaining_s"] == 1290.0
    assert snapshot["estimated_completion_epoch"] is not None
    assert snapshot["estimated_completion_basis"] == (
        "configured_per_run_hard_real_time_plus_observed_setup_overhead"
    )
    assert snapshot["estimated_completion_scope"] == "active_ns3_plus_observed_setup_overhead"


class WaitingProcess:
    def __init__(self, exit_code=-15):
        self.alive = True
        self._done = asyncio.Event()
        self.stop_calls = 0
        self.exit_code = exit_code

    async def wait(self):
        await self._done.wait()
        return self.exit_code

    async def stop(self, _timeout_s):
        self.stop_calls += 1
        self.alive = False
        self._done.set()

    def logs_after(self, after, _limit):
        return {"lines": [], "last_seq": after}


class WaitingProcesses:
    def __init__(self):
        self.items = {name: WaitingProcess() for name in ("ns3", "ric", "xapp")}

    def get(self, name):
        return self.items[name]

    def snapshot(self):
        return {name: {"name": name, "status": "running"} for name in self.items}


class NaturalNs3Process(WaitingProcess):
    """Kończy ns-3 po budżecie bez udostępnienia próbki live."""

    async def wait(self):
        await asyncio.sleep(0.35)
        self.alive = False
        return 0


class NaturalProcesses(WaitingProcesses):
    def __init__(self):
        super().__init__()
        self.items["ns3"] = NaturalNs3Process()


@pytest.mark.parametrize("exit_code", [-15, 143, 15, -9, 137, 9])
def test_runner_real_time_cutoff_is_not_reported_as_user_stop(tmp_path: Path, exit_code) -> None:
    runner = object.__new__(SimulationRunner)
    runner._stop_requested = threading.Event()
    runner._live_sequences = {}
    processes = WaitingProcesses()
    processes.items["ns3"] = WaitingProcess(exit_code)
    collector = LiveMetricsCollector()

    async def publish(_processes, _root, live_collector, _item, _callback, _elapsed, _container):
        live_collector.ingest_csv_text(
            "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
            "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
            "2,2000000,100,50,1000000,20000\n"
        )

    runner._publish_live_snapshot = publish  # type: ignore[method-assign]

    async def container_snapshot(_processes):
        return {"name": "test", "state": "running"}

    runner._container_snapshot = container_snapshot  # type: ignore[method-assign]
    from app.simulation.models import Simulation, SimulationConfiguration

    item = Simulation(
        name="Budżet rzeczywisty",
        configuration=SimulationConfiguration(scenario="optimized", parameters={"simTime": 1000}),
        runtime={"queue_timing": {"cutoff_policy": "hard_real_time_each_run"}},
    )

    asyncio.run(
        runner._wait_for_end(
            processes,
            None,
            tmp_path,
            {"runtime": {"wall_clock_timeout_s": 2, "live_poll_interval_s": 0.01}},
            item,
            None,
            root="/ns3",
            live_metrics=collector,
            real_time_budget_target_active_s=0.01,
        )
    )

    assert processes.get("ns3").stop_calls == 1
    timing = item.runtime["queue_timing"]
    assert timing["state"] == "observed"
    assert timing["measurement_source"] == "ee_timeseries.csv"
    assert timing["observed_simulated_time_s"] == 2.0
    assert timing["cutoff_requested"] is True


def test_runner_hard_stops_every_budgeted_run_when_no_live_measurement_is_available(tmp_path: Path) -> None:
    runner = object.__new__(SimulationRunner)
    runner._stop_requested = threading.Event()
    runner._live_sequences = {}
    processes = NaturalProcesses()
    collector = LiveMetricsCollector()
    phases = []

    async def publish(*_args, **_kwargs):
        # Celowo nie przekazujemy ee_timeseries.csv.
        return None

    runner._publish_live_snapshot = publish  # type: ignore[method-assign]

    async def container_snapshot(_processes):
        return {"name": "test", "state": "running"}

    runner._container_snapshot = container_snapshot  # type: ignore[method-assign]
    from app.simulation.models import Simulation, SimulationConfiguration

    item = Simulation(
        name="Budżet bez pliku live",
        configuration=SimulationConfiguration(scenario="optimized", parameters={"simTime": 1000}),
        runtime={"queue_timing": {"cutoff_policy": "hard_real_time_each_run"}},
    )

    asyncio.run(
        runner._wait_for_end(
            processes,
            None,
            tmp_path,
            {"runtime": {"wall_clock_timeout_s": 2, "live_poll_interval_s": 0.01}},
            item,
            lambda _item, phase, *_args: phases.append(phase),
            root="/ns3",
            live_metrics=collector,
            real_time_budget_target_active_s=0.01,
        )
    )

    assert processes.get("ns3").stop_calls == 1
    assert "TIME_BUDGET_CUTOFF" in phases
    timing = item.runtime["queue_timing"]
    assert timing["state"] == "observation_unavailable"
    assert timing["cutoff_requested"] is True
