from app.gui.docker_view import docker_records, filter_log_lines
from app.gui.live_dashboard import metric_rows, series_points


def test_log_filter_keeps_only_requested_level_and_search_text():
    lines = ["normal line", "WARN: slow", "ERROR: failed", "warning: retry"]

    assert filter_log_lines(lines, level="ERROR") == ["ERROR: failed"]
    assert filter_log_lines(lines, query="retry", level="WARNING") == ["warning: retry"]


def test_docker_records_keeps_container_and_real_processes():
    records = docker_records(
        {
            "container": {"name": "session", "state": "running"},
            "processes": {"ric": {"name": "ric", "status": "ready"}},
        }
    )

    assert [(kind, name) for kind, name, _ in records] == [("container", "session"), ("process", "ric")]


def test_dashboard_helpers_skip_missing_metric_and_bad_chart_points():
    rows = metric_rows({"current_metrics": {"total_energy_j": 12, "delay_ms": None}})

    assert rows == [("total_energy_j", 12)]
    assert series_points([{"x": 1, "y": 2}, {"x": "bad", "y": 3}]) == [(1.0, 2.0)]
