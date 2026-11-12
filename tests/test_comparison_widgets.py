"""Regresje prawdziwych widgetów: błędy Tcl nie wychodzą w testach atrap."""

import tkinter as tk

import pytest

from app.gui.comparison_view import ComparisonView
from app.gui.live_dashboard import LiveDashboard, metric_rows
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult
from app.simulation.runner import SimulationRunner


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("Brak ekranu Tk")
    window.withdraw()
    yield window
    window.update_idletasks()
    window.destroy()


def test_comparison_populates_tables_and_keeps_selected_chart_after_resize(root):
    runs = [Simulation(name=name, configuration=SimulationConfiguration("optimized", {}), result=SimulationResult(
        metrics={"total_energy_j": energy, "energy_efficiency_bit_per_j": 10000, "delay_ms": 5},
        series={"energy_j": [{"x": 1, "y": energy}], "delay_ms": [{"x": 1, "y": 5}]},
    )) for name, energy in (("Bazowy", 100), ("My Energy", 80))]
    view = ComparisonView(root, lambda: runs)
    view.refresh_candidates()
    assert view.energy_tree.item("total_energy_j", "values")[1:3] == ("100 J", "80 J")
    view.energy_tree.selection_set("total_energy_j")
    view._metric_selected(type("Event", (), {"widget": view.energy_tree})())
    view.quality_tree.selection_set("delay_ms")
    view._metric_selected(type("Event", (), {"widget": view.quality_tree})())
    view._draw_selected_chart()
    assert "opóźnienie" in view.chart_title.get()
    view.right_var.set(view.left_var.get())
    view.refresh_comparison()
    assert not view.energy_tree.get_children()
    assert not view.chart.find_all()


def test_dashboard_keeps_chart_units_and_undefined_final_metrics(root):
    dashboard = LiveDashboard(root)
    assert dashboard._display_series("energy_j", [(2, 5000)]) == ([(2, 5000)], "J")
    assert dashboard._display_series("delivered_mbit", [(2, 5000)]) == ([(2, 5000)], "Mbit")
    values = dict(metric_rows({"current_metrics": {"energy_per_mbit_j": 10}, "result": {"metrics": {"energy_per_mbit_j": None}}}))
    assert "energy_per_mbit_j" not in values


def test_kpm_does_not_fill_undefined_ip_energy_cost_with_pdcp_estimate():
    metrics = {"delivered_mbit": 0, "total_energy_j": 100, "energy_efficiency_bit_per_j": 0, "energy_per_mbit_j": None}
    series = {"energy_j": [{"x": 1, "y": 100}]}
    SimulationRunner._merge_kpm_live_data(metrics, series, {"energy_j": "ee_timeseries.csv"}, {
        "sample_time_s": 1, "integrated_delivered_mbit": 2,
        "series": {"delivered_mbit": [{"x": 1, "y": 2}]},
    })
    assert metrics["energy_per_mbit_j"] is None
    assert metrics["delivered_mbit"] == 0
    assert "energy_per_mbit_j" not in series
