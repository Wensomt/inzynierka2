from collections import deque
from types import SimpleNamespace

from app.data.storage import SimulationStorage
from app.gui.docker_view import DockerView
from app.gui.live_dashboard import format_estimated_completion
from app.gui.main_window import MainWindow
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus
from app.simulation.session_controller import QueueEvent, SessionController


def _simulation() -> Simulation:
    return Simulation(
        name="Live",
        configuration=SimulationConfiguration(
            scenario="optimized",
            parameters={"simTime": 30},
        ),
    )


def test_controller_prioritises_live_event_and_batches_burst_logs(tmp_path):
    controller = SessionController(
        {"scenarios": {"optimized": {"allowed_params": ["simTime"]}}},
        SimulationStorage(tmp_path / "simulations"),
        object(),  # type: ignore[arg-type]
    )
    simulation = _simulation()
    controller.simulations = [simulation]

    for sequence in range(1, controller.MAX_PENDING_LOG_RECORDS + 9):
        controller._on_runner_event(
            simulation,
            "LOG",
            "line {}".format(sequence),
            1,
            1,
            {
                "log_source": "xapp",
                "log": {"seq": sequence, "generation": 1, "text": "line {}".format(sequence)},
            },
        )
    controller._on_runner_event(
        simulation,
        "LIVE_UPDATE",
        "live",
        1,
        1,
        {"progress_pct": 10.0},
    )

    first = controller.drain_events(1)
    assert [event.phase for event in first] == ["LIVE_UPDATE"]

    log_event = controller.drain_events(1)[0]
    assert log_event.phase == "LOG"
    assert len(log_event.payload["logs"]) <= controller.LOG_BATCH_SIZE + 1
    assert "Pominięto" in log_event.payload["logs"][0]["text"]


def test_log_generation_accepts_restart_sequence_from_one_without_tk():
    sink = SimpleNamespace(
        _last_sequences={},
        _last_generations={},
        _logs={},
        max_visible_log_lines=10,
    )

    assert DockerView.append_log(sink, "xapp", {"text": "old"}, 4, 1, refresh=False)
    assert DockerView.append_log(sink, "xapp", {"text": "new"}, 1, 2, refresh=False)

    assert list(sink._logs["xapp"]) == ["old", "new"]
    assert sink._last_sequences["xapp"] == 1
    assert sink._last_generations["xapp"] == 2


def test_terminal_snapshot_uses_the_same_final_metrics_and_series():
    simulation = _simulation()
    simulation.status = SimulationStatus.COMPLETED
    simulation.result = SimulationResult(
        metrics={"total_energy_j": 12.0},
        series={"energy_j": [{"x": 1.0, "y": 12.0}]},
    )

    snapshot = SessionController._simulation_snapshot(simulation)

    assert snapshot["status"] == "COMPLETED"
    assert snapshot["result"]["metrics"]["total_energy_j"] == 12.0
    assert snapshot["result"]["series"]["energy_j"] == [{"x": 1.0, "y": 12.0}]


def test_saved_chart_window_uses_only_archived_result_series():
    simulation = _simulation()
    simulation.status = SimulationStatus.COMPLETED
    simulation.result = SimulationResult(
        metrics={"total_energy_j": 12.0},
        series={"energy_j": [{"x": 1.0, "y": 12.0}]},
        sources={"total_energy_j": "ee_timeseries.csv"},
    )

    payload = MainWindow._saved_result_dashboard_payload(simulation)

    assert payload["stage"] == "ZARCHIWIZOWANY WYNIK"
    assert payload["result"]["series"] == {"energy_j": [{"x": 1.0, "y": 12.0}]}
    assert payload["result"]["series_sources"]["energy_j"] == "ee_timeseries.csv"
    assert payload["data_status"]["data_source"] == "artefakty runu"


def test_component_crash_event_immediately_marks_xapp_as_failed():
    payload = MainWindow._normalise_event_payload(
        QueueEvent("run-1", "XAPP_CRASHED", "xApp zakończył się kodem 1.", payload={"component": "xapp", "exit_code": 1}),
        {"component": "xapp", "exit_code": 1},
    )

    assert payload["processes"]["xapp"]["status"] == "failed"
    assert payload["processes"]["xapp"]["exit_code"] == 1
    assert payload["xapp_status"] == "failed"


def test_terminal_event_overrides_an_old_running_snapshot():
    payload = MainWindow._normalise_event_payload(
        QueueEvent("run-1", "FAILED", "Błąd"),
        {"simulation": {"id": "run-1", "status": "RUNNING"}},
    )

    assert payload["status"] == "FAILED"
    assert payload["stage"] == "FAILED"
    assert payload["simulation"]["status"] == "FAILED"


def test_dashboard_event_payload_includes_current_queue_position():
    payload = MainWindow._normalise_event_payload(
        QueueEvent("run-2", "LIVE_UPDATE", "live", current=2, total=4),
        {"overall_progress_pct": 37.5},
    )

    assert payload["current"] == 2
    assert payload["total"] == 4
    assert payload["queue_summary"] == {
        "current": 2,
        "total": 4,
        "overall_progress_pct": 37.5,
    }


def test_completion_time_uses_only_backend_supplied_absolute_time():
    assert format_estimated_completion({}) is None
    assert format_estimated_completion({"estimated_completion_at": ""}) is None
    assert format_estimated_completion({"estimated_completion_at": "2026-09-13T21:45:00"}) == "13.09.2026 21:45"
    assert format_estimated_completion({"estimated_completion_at": "backendowy zapis terminu"}) == "backendowy zapis terminu"


def test_completion_time_accepts_epoch_from_queue_payload():
    rendered = format_estimated_completion({"estimated_completion_epoch": 1_790_000_000})

    assert rendered is not None
    assert len(rendered) == len("13.09.2026 21:45")


def test_completion_time_marks_budget_plan_until_real_estimate_exists():
    planned = format_estimated_completion({"planned_completion_epoch": 1_790_000_000})
    estimated = format_estimated_completion(
        {"planned_completion_epoch": 1_790_000_000},
        {"estimated_completion_epoch": 1_790_003_600},
    )

    assert planned is not None
    assert planned.startswith("plan: ")
    assert estimated is not None
    assert not estimated.startswith("plan: ")


def test_idle_event_is_marked_as_a_queue_terminal_state():
    payload = MainWindow._normalise_event_payload(
        QueueEvent(None, "IDLE", "Kolejka zakończona.", current=2, total=2),
        {},
    )

    assert payload["phase"] == "IDLE"
    assert payload["queue_terminal"] is True
    assert payload["queue_terminal_status"] == "ZAKOŃCZONA"


def test_terminal_queue_marker_is_not_added_to_a_failed_run():
    payload = MainWindow._normalise_event_payload(
        QueueEvent("run-1", "FAILED", "Błąd runu", current=1, total=2),
        {},
    )

    assert "queue_terminal" not in payload
