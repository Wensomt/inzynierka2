from app.comparison.comparator import compare_simulations
import pytest

from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus


def finished(name: str, ue_count: int, xapp: str, ee: float) -> Simulation:
    return Simulation(
        name=name,
        configuration=SimulationConfiguration(
            scenario="optimized",
            parameters={"simTime": 300, "N_Ues": ue_count, "N_MmWaveEnbNodes": 4, "RngRun": 1},
            xapp=xapp,
        ),
        result=SimulationResult(metrics={
            "energy_efficiency_bit_per_j": ee,
            "energy_per_mbit_j": 0.1,
            "total_energy_j": 100.0,
            "average_power_w": 10.0,
            "throughput_mbit_per_s": 4.0,
            "delay_ms": None,
            "packet_loss_pct": 1.2,
            "delivered_mbit": 40.0,
        }),
    )


def test_comparison_allows_different_configurations_and_shows_diff() -> None:
    left = finished("Baseline", 10, "none", 10.0)
    right = finished("Większy ruch", 20, "optimized", 12.0)

    comparison = compare_simulations(left, right)

    labels = {item.label for item in comparison.configuration_differences}
    assert "Liczba UE" in labels
    assert "xApp" in labels
    ee = next(item for item in comparison.energy if item.key == "energy_efficiency_bit_per_j")
    assert ee.left == 10.0
    assert ee.right == 12.0
    assert ee.difference == 20.0
    delay = next(item for item in comparison.quality if item.key == "delay_ms")
    assert delay.left is None and delay.right is None and delay.difference is None


def test_packet_loss_uses_percentage_points() -> None:
    left = finished("A", 10, "none", 10.0)
    right = finished("B", 10, "none", 10.0)
    right.result.metrics["packet_loss_pct"] = 1.7  # type: ignore[union-attr]

    row = next(item for item in compare_simulations(left, right).quality if item.key == "packet_loss_pct")

    assert row.difference_kind == "percentage_points"
    assert round(row.difference or 0, 4) == 0.5


def test_comparison_includes_available_quality_kpis() -> None:
    left = finished("A", 10, "none", 10.0)
    right = finished("B", 10, "none", 10.0)
    left.result.quality["p05_sinr_db"] = 12.0  # type: ignore[union-attr]
    right.result.quality["p05_sinr_db"] = 14.0  # type: ignore[union-attr]

    comparison = compare_simulations(left, right)

    sinr = next(item for item in comparison.quality if item.key == "p05_sinr_db")
    assert sinr.left == 12.0
    assert sinr.right == 14.0
    assert sinr.difference == 2.0
    assert sinr.difference_kind == "absolute"


def controlled_pair():
    left, right = finished("Bazowy", 10, "none", 400000), finished("xApp", 10, "optimized", 500000)
    for item in (left, right):
        item.status = SimulationStatus.COMPLETED
        item.result.metrics["delay_ms"] = 5.0
        item.result.metadata = {"measurement_duration_s": 10.0, "measurement_start_s": 0.0,
                                "energy_scope": "nr_radio_model", "data_layer": "udp_application_payload",
                                "simulator_source": "mmwave_LENA_oran_custom"}
    right.result.metrics["total_energy_j"] = 80.0
    return left, right


def test_energy_saving_requires_qos_without_degradation():
    left, right = controlled_pair()
    assert compare_simulations(left, right).assessment_kind == "improved"
    right.result.metrics["packet_loss_pct"] = 4.0
    comparison = compare_simulations(left, right)
    assert comparison.assessment_kind == "tradeoff"
    assert "packet loss" in comparison.assessment


def test_lower_delivered_data_is_not_automatically_an_energy_success():
    left, right = controlled_pair()
    right.result.metrics.update(delivered_mbit=20, throughput_mbit_per_s=2)
    assert compare_simulations(left, right).assessment_kind == "tradeoff"


def test_explicit_qos_limit_can_allow_measured_baseline_degradation():
    left, right = controlled_pair()
    right.result.metrics["delay_ms"] = 6.0
    assert compare_simulations(left, right).assessment_kind == "tradeoff"
    assert compare_simulations(left, right, {"max_delay_ms": 7}).assessment_kind == "improved"
    assert compare_simulations(left, right, {"max_delay_ms": 5}).assessment_kind == "tradeoff"


@pytest.mark.parametrize("limits", [{"max_delay_ms": float("nan")}, {"max_packet_loss_pct": 101}, {"max_delay_ms": -1}, {"invented": 3}])
def test_qos_limits_reject_invalid_values(limits):
    with pytest.raises(ValueError):
        compare_simulations(*controlled_pair(), limits)


@pytest.mark.parametrize("problem", ["partial", "crash", "window", "source", "missing", "config", "metadata"])
def test_invalid_experiment_cannot_receive_a_positive_assessment(problem):
    left, right = controlled_pair()
    if problem == "partial":
        right.runtime["termination"] = {"partial": True}
    elif problem == "crash":
        right.runtime["experiment_valid"] = False
    elif problem == "window":
        right.result.metadata["measurement_duration_s"] = 9.0
    elif problem == "source":
        right.result.metadata["simulator_source"] = "SOURCE_LOCAL_DISCRETE_SIMULATOR"
    elif problem == "missing":
        right.result.metrics["delay_ms"] = None
    elif problem == "config":
        right.configuration.parameters["N_Ues"] = 20
    else:
        right.result.metadata.clear()
    comparison = compare_simulations(left, right)
    assert comparison.assessment_kind == "inconclusive"
    assert comparison.warnings
