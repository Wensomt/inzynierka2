import time
import csv
from datetime import datetime
from pathlib import Path

import pytest

from app.data.storage import SimulationStorage
from app.gui.configuration_library import suggested_configuration_name
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus
from app.simulation.session_controller import SessionController
from app.simulation.validation import validate_configuration


def sample(name: str = "Symulacja 1") -> Simulation:
    return Simulation(
        name=name,
        configuration=SimulationConfiguration(
            scenario="optimized",
            parameters={"simTime": 30, "N_Ues": 4, "N_MmWaveEnbNodes": 2, "RngRun": 1},
        ),
    )


def test_storage_saves_configuration_and_result(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    item = sample("Baseline")
    storage.create(item)
    item.status = SimulationStatus.COMPLETED
    item.result = SimulationResult(metrics={"total_energy_j": 12.0})
    storage.save(item)

    restored = storage.load(item.id)
    assert restored.name == "Baseline"
    assert restored.status == SimulationStatus.COMPLETED
    assert restored.result is not None
    assert restored.result.metrics["total_energy_j"] == 12.0

    storage.save_configuration("Niski ruch", [item])
    assert storage.list_configurations() == ["Niski ruch"]
    loaded = storage.load_configuration("Niski ruch")
    assert loaded[0].name == "Baseline"
    assert loaded[0].configuration.parameters["N_Ues"] == 4

    export_path = storage.export_result_csv(item)
    with export_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert ["symulacja", "id", item.id, "", ""] in rows
    assert ["konfiguracja", "N_Ues", "4", "", ""] in rows


def test_retry_preparation_preserves_failed_artifacts_and_clears_active_run(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    item = sample("Retry")
    storage.create(item)
    directory = storage.directory(item.id)
    (directory / "logs" / "runner.log").write_text("pierwsza próba\n", encoding="utf-8")
    (directory / "timeseries" / "metrics.csv").write_text("stare dane\n", encoding="utf-8")
    item.status = SimulationStatus.FAILED
    item.error = "błąd pierwszej próby"
    item.result = SimulationResult(metrics={"total_energy_j": 1.0})
    storage.save(item)

    archived = storage.prepare_retry(item, 1)

    assert archived == directory / "attempts" / "attempt-1"
    assert (archived / "logs" / "runner.log").read_text(encoding="utf-8") == "pierwsza próba\n"
    assert (archived / "timeseries" / "metrics.csv").read_text(encoding="utf-8") == "stare dane\n"
    assert list((directory / "logs").iterdir()) == []
    assert list((directory / "timeseries").iterdir()) == []
    assert not (directory / "result.json").exists()
    assert item.result is None
    assert item.error is None


def test_suggested_configuration_name_does_not_require_typed_name() -> None:
    point_in_time = datetime(2026, 9, 14, 15, 30)
    assert suggested_configuration_name([], point_in_time) == "Zapis 2026-09-14 15-30"
    assert suggested_configuration_name(
        ["Zapis 2026-09-14 15-30"], point_in_time
    ) == "Zapis 2026-09-14 15-30 2"


def test_configuration_import_export_library_keeps_names_and_avoids_overwrite(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    item = sample("Baseline")
    storage.create(item)
    first_path = storage.save_configuration("Niski ruch", [item])

    records = storage.list_configuration_records()
    assert [(record.name, record.simulation_count) for record in records] == [("Niski ruch", 1)]
    assert records[0].saved_at
    with pytest.raises(FileExistsError):
        storage.save_configuration("Niski ruch", [item])

    exported = tmp_path / "outside" / "niski-ruch.json"
    storage.export_configuration("Niski ruch", exported)
    storage.delete_configuration("Niski ruch")
    assert not first_path.exists()
    assert storage.list_configurations() == []

    imported = storage.import_configuration(exported)
    duplicate = storage.import_configuration(exported)
    assert imported.name == "Niski ruch"
    assert duplicate.name == "Niski ruch 2"
    assert storage.load_configuration(imported.name)[0].configuration.parameters["N_Ues"] == 4
    with pytest.raises(ValueError):
        storage.delete_configuration("../Niski ruch")


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def preflight(self):
        return {"ok": True, "checks": ["Docker dostępny"], "errors": []}

    def run(self, item, callback):
        self.calls.append(item.id)
        callback(item, "RUNNING", "Działanie")
        if len(self.calls) == 1:
            item.status = SimulationStatus.FAILED
            item.error = "Celowy błąd"
        else:
            item.status = SimulationStatus.COMPLETED
            item.result = SimulationResult(metrics={"total_energy_j": 2.0})
        return item

    def request_stop(self):
        return None


class SequentialRunner:
    def __init__(self, preflight_ok=True) -> None:
        self.preflight_ok = preflight_ok
        self.calls = []
        self.stop_resets = 0

    def preflight(self):
        return {
            "ok": self.preflight_ok,
            "checks": ["Docker dostępny"] if self.preflight_ok else [],
            "errors": [] if self.preflight_ok else ["Brak Docker"],
        }

    def reset_stop(self):
        self.stop_resets += 1

    def run(self, item, callback):
        self.calls.append(item.id)
        item.status = SimulationStatus.COMPLETED
        item.result = SimulationResult(metrics={"total_energy_j": float(len(self.calls))})
        callback(item, "COMPLETED", "Zakończono")
        return item

    def request_stop(self):
        return None


def test_queue_continues_after_a_failed_simulation(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    runner = FakeRunner()
    config = {"scenarios": {"optimized": {"allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"]}}}
    controller = SessionController(config, storage, runner)  # type: ignore[arg-type]
    controller.new_session(2)

    controller.start_all()
    while controller.running:
        time.sleep(0.01)

    assert len(runner.calls) == 2
    assert controller.simulations[0].status == SimulationStatus.FAILED
    assert controller.simulations[1].status == SimulationStatus.COMPLETED


def test_three_simulations_are_run_in_tab_order(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    runner = SequentialRunner()
    config = {"scenarios": {"optimized": {"allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"]}}}
    controller = SessionController(config, storage, runner)  # type: ignore[arg-type]
    created = controller.new_session(3)

    controller.start_all()
    while controller.running:
        time.sleep(0.01)

    assert runner.stop_resets == 1
    assert runner.calls == [item.id for item in created]
    assert all(item.status == SimulationStatus.COMPLETED for item in controller.simulations)


def test_failed_preflight_still_returns_queue_to_idle(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    runner = SequentialRunner(preflight_ok=False)
    config = {"scenarios": {"optimized": {"allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"]}}}
    controller = SessionController(config, storage, runner)  # type: ignore[arg-type]
    controller.new_session(1)

    controller.start_all()
    while controller.running:
        time.sleep(0.01)
    phases = []
    while not controller.events.empty():
        phases.append(controller.events.get_nowait().phase)

    assert "VALIDATION_FAILED" in phases
    assert phases[-1] == "IDLE"
    assert controller.simulations[0].status is SimulationStatus.FAILED
    assert controller.simulations[0].runtime["failure_kind"] == "preflight"
    log = storage.directory(controller.simulations[0].id) / "logs" / "runner.log"
    assert "RETRY_SKIPPED" in log.read_text(encoding="utf-8")


def test_unhandled_runner_error_does_not_abandon_the_second_run(tmp_path: Path) -> None:
    class RaisingRunner(SequentialRunner):
        def run(self, item, callback):
            if not self.calls:
                self.calls.append(item.id)
                raise RuntimeError("unexpected transport failure")
            return super().run(item, callback)

    storage = SimulationStorage(tmp_path / "simulations")
    runner = RaisingRunner()
    controller = SessionController({}, storage, runner)
    controller.new_session(2)
    controller._run_queue()
    assert len(runner.calls) == 2
    assert controller.simulations[0].status is SimulationStatus.FAILED
    assert controller.simulations[0].runtime["experiment_valid"] is False
    assert controller.simulations[1].status is SimulationStatus.COMPLETED
    assert controller.drain_events(100)[-1].phase == "IDLE"


def test_raising_preflight_returns_queue_to_idle(tmp_path: Path) -> None:
    class RaisingRunner(SequentialRunner):
        def preflight(self):
            raise RuntimeError("Docker connection broken")

    controller = SessionController({}, SimulationStorage(tmp_path / "simulations"), RaisingRunner())
    controller.new_session(2)
    controller._run_queue()
    assert [event.phase for event in controller.drain_events(100)][-2:] == ["QUEUE_FAILED", "IDLE"]


def test_rerun_creates_new_identifier_without_replacing_previous_result(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    runner = FakeRunner()
    config = {"scenarios": {"optimized": {"allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"]}}}
    controller = SessionController(config, storage, runner)  # type: ignore[arg-type]
    original = controller.new_session(1)[0]
    original.status = SimulationStatus.COMPLETED
    original.result = SimulationResult(metrics={"total_energy_j": 3.0})
    storage.save(original)

    replacement = controller.start_all()[0]
    while controller.running:
        time.sleep(0.01)

    assert replacement.id != original.id
    assert storage.load(original.id).result is not None
    assert storage.load(original.id).result.metrics["total_energy_j"] == 3.0
    assert controller.simulations[0].status in {SimulationStatus.FAILED, SimulationStatus.COMPLETED}


def test_validation_rejects_fractional_ns3_integer_parameters() -> None:
    configuration = SimulationConfiguration(
        scenario="optimized",
        parameters={"simTime": 10, "N_Ues": 2.5, "N_MmWaveEnbNodes": 2, "RngRun": 1},
    )
    errors = validate_configuration(
        configuration,
        {"scenarios": {"optimized": {"allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"]}}},
    )

    assert any("Liczba UE musi być liczbą całkowitą" in error for error in errors)


def test_validation_uses_scenario_minimum_simulation_time() -> None:
    configuration = SimulationConfiguration(
        scenario="optimized",
        parameters={"simTime": 0.2, "N_Ues": 2, "N_MmWaveEnbNodes": 2, "RngRun": 1},
    )
    errors = validate_configuration(
        configuration,
        {
            "scenarios": {
                "optimized": {
                    "minimum_sim_time_s": 0.2,
                    "allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"],
                }
            }
        },
    )

    assert not any("Czas symulacji" in error for error in errors)


def test_validation_rejects_non_finite_numbers() -> None:
    configuration = SimulationConfiguration(
        scenario="optimized",
        parameters={"simTime": "nan", "N_Ues": 2, "N_MmWaveEnbNodes": 2, "RngRun": 1, "Bandwidth": "inf"},
    )
    errors = validate_configuration(
        configuration,
        {
            "scenarios": {
                "optimized": {
                    "allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun", "Bandwidth"]
                }
            }
        },
    )

    assert any("Czas symulacji" in error for error in errors)
    assert any("Szerokość pasma" in error for error in errors)


def test_validation_rejects_xapp_environment_not_declared_by_selected_xapp() -> None:
    configuration = SimulationConfiguration(
        scenario="optimized",
        parameters={"simTime": 10, "N_Ues": 2, "N_MmWaveEnbNodes": 2, "RngRun": 1},
        xapp="optimized",
        xapp_environment={"EE_DECISION_PERIOD_SEC": "2.5", "NOT_REAL": 1},
    )
    errors = validate_configuration(
        configuration,
        {
            "scenarios": {"optimized": {"allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun"]}},
            "xapps": {"optimized": {"parameters": {"EE_DECISION_PERIOD_SEC": {"type": "integer"}}}},
        },
    )

    assert any("NOT_REAL" in error for error in errors)
    assert any("EE_DECISION_PERIOD_SEC" in error for error in errors)
