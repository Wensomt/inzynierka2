from app.gui.live_dashboard import chart_bounds, series_points, starts_new_simulation


def test_chart_bounds_expands_single_real_sample_without_creating_points() -> None:
    points = [(2.0, 5.0)]

    assert chart_bounds(points) == (1.5, 2.5, 4.5, 5.5)
    assert points == [(2.0, 5.0)]


def test_chart_bounds_accepts_only_valid_manual_viewport() -> None:
    points = [(1.0, 2.0), (4.0, 8.0)]

    assert chart_bounds(points, (1.5, 3.5, 3.0, 7.0)) == (1.5, 3.5, 3.0, 7.0)
    assert chart_bounds(points, (3.0, 1.0, 2.0, 8.0)) == (1.0, 4.0, 2.0, 8.0)


def test_series_points_keeps_only_reported_finite_samples() -> None:
    assert series_points([{"x": 1, "y": 2}, {"x": "bad", "y": 4}, {"x": 3, "y": "nan"}]) == [
        (1.0, 2.0)
    ]


def test_new_simulation_identifier_resets_the_merged_dashboard_state() -> None:
    assert starts_new_simulation(
        {"simulation": {"id": "run-1"}, "queue_terminal": True},
        {"simulation": {"id": "run-2"}},
    ) is True
    assert starts_new_simulation(
        {"simulation": {"id": "run-1"}},
        {"simulation": {"id": "run-1", "status": "RUNNING"}},
    ) is False
