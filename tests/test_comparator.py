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
        item.runtime = {"scenario_source": {"sha256": "scenario-fixture"},
                        "kpm_clock_patch_sha256": "clock-fixture"}
        item.result.metadata = {"measurement_duration_s": 10.0, "measurement_start_s": 0.0,
                                "measurement_end_s": 10.0, "measurement_consistent": True,
                                "accounting_source": "summary.json", "qos_source": "udp_application_seq_ts",
                                "packet_loss_definition": "tx-rx / tx",
                                "energy_scope": "nr_radio_model", "data_layer": "udp_application_payload",
                                "simulator_source": "mmwave_LENA_oran_custom"}
        # Synthetic complete write-path manifest, not a real ns-3 run.
        from app.simulation.runner import SimulationRunner
        item.started_at = "2026-01-01T00:00:00+00:00"
        item.ended_at = "2026-01-01T00:01:00+00:00"
        item.runtime["termination"] = {"reason": "natural", "partial": False}
        SimulationRunner._record_provenance(item, {}, {"image_id": "sha256:" + "a" * 64})
    right.result.metrics["total_energy_j"] = 80.0
    return left, right


@pytest.mark.parametrize('side', [0, 1, 'both'])
def test_two_source_hashes_without_manifest_do_not_certify_pair(side):
    items = controlled_pair()
    for index in ([0, 1] if side == 'both' else [side]):
        items[index].runtime.pop('provenance_manifest')
    result = compare_simulations(*items)
    assert result.assessment_kind == 'inconclusive'
    assert any('manifest' in warning.lower() for warning in result.warnings)


@pytest.mark.parametrize('problem', ['image', 'backend', 'configuration', 'source', 'clock',
    'timestamps', 'termination', 'termination_partial_missing', 'window', 'rng', 'reuse', 'image_mismatch'])
def test_incomplete_manifest_cannot_certify_pair(problem):
    left, right = controlled_pair()
    manifest = right.runtime['provenance_manifest']
    if problem == 'image_mismatch':
        manifest['image']['image_id'] = 'sha256:' + 'b' * 64
    elif problem == 'termination_partial_missing':
        right.runtime['termination'].pop('partial')
    elif problem in ('source', 'clock'):
        manifest['sources']['scenario' if problem == 'source' else 'clock_patch']['sha256'] = None
    elif problem == 'window':
        manifest['windows']['actual']['end_s'] = 9
    elif problem == 'reuse':
        manifest.pop('baseline_reuse')
    else:
        manifest.pop(problem)
    assert compare_simulations(left, right).assessment_kind == 'inconclusive'


@pytest.mark.parametrize('tx,rx', [(100, 101), (-1, -1), (100.5, 100.5), (float('inf'), 1), (100, float('nan'))])
def test_imported_counter_marker_does_not_override_impossible_counts(tx, rx):
    left, right = controlled_pair()
    right.result.metadata.update(tx_packets=tx, rx_packets=rx, measurement_consistent=True)
    assert compare_simulations(left, right).assessment_kind == 'inconclusive'


def test_nonhex_image_id_cannot_certify_pair():
    left, right = controlled_pair()
    for item in (left, right):
        item.runtime['provenance_manifest']['image']['image_id'] = 'sha256:' + 'z' * 64
    comparison = compare_simulations(left, right)
    assert comparison.assessment_kind == 'inconclusive'
    assert any('image_id' in warning for warning in comparison.warnings)


@pytest.mark.parametrize('counters,loss', [
    ({'tx_packets': 100, 'rx_packets': 90, 'lost_packets': 0}, 10.0),
    ({'tx_packets': 100, 'rx_packets': 90, 'lost_packets': 10}, 0.0),
    ({'tx_packets': 100, 'rx_packets': 90}, 0.0),
])
def test_contradictory_imported_packet_loss_cannot_certify_pair(counters, loss):
    left, right = controlled_pair()
    right.result.metadata.update(counters)
    right.result.metrics['packet_loss_pct'] = loss
    result = compare_simulations(left, right)
    assert result.assessment_kind == 'inconclusive'
    assert any('pakiet' in warning for warning in result.warnings)


@pytest.mark.parametrize('counters,loss', [
    ({'tx_packets': 100, 'rx_packets': 90, 'lost_packets': 10}, 10.0),
    ({'tx_packets': 100, 'rx_packets': 90}, 10.0),
    ({}, 1.2),
])
def test_consistent_or_absent_counters_are_not_contradictions(counters, loss):
    left, right = controlled_pair()
    right.result.metadata.update(counters)
    right.result.metrics['packet_loss_pct'] = loss
    result = compare_simulations(left, right)
    assert not any('sprzeczne liczniki pakietów' in warning for warning in result.warnings)
    assert result.assessment_kind in ('improved', 'tradeoff')


def test_zero_rx_with_correct_loss_is_not_counter_contradiction():
    left, right = controlled_pair()
    right.result.metadata.update(tx_packets=100, rx_packets=0, lost_packets=100)
    right.result.metrics.update(packet_loss_pct=100.0, delivered_mbit=0)
    result = compare_simulations(left, right)
    assert result.assessment_kind == 'inconclusive'
    assert not any('sprzeczne liczniki pakietów' in warning for warning in result.warnings)


def test_valid_hex_image_without_repo_digest_can_certify_pair():
    left, right = controlled_pair()
    for item in (left, right):
        image = item.runtime['provenance_manifest']['image']
        image['image_id'] = 'sha256:' + 'f' * 64
        image['repo_digests'] = None
    assert compare_simulations(left, right).assessment_kind == 'improved'


def test_energy_saving_requires_qos_without_degradation():
    left, right = controlled_pair()
    assert compare_simulations(left, right).assessment_kind == "improved"
    right.result.metrics["packet_loss_pct"] = 4.0
    comparison = compare_simulations(left, right)
    assert comparison.assessment_kind == "tradeoff"
    assert "packet loss" in comparison.assessment


@pytest.mark.parametrize("kind", ["scenario", "clock"])
def test_edited_model_cannot_be_presented_as_isolated_xapp_improvement(kind):
    left, right = controlled_pair()
    if kind == "scenario":
        left.runtime["scenario_source"] = {"sha256": "before"}
        right.runtime["scenario_source"] = {"sha256": "after"}
    else:
        left.runtime["kpm_clock_patch_sha256"] = "before"
        right.runtime["kpm_clock_patch_sha256"] = "after"
    assert compare_simulations(left, right).assessment_kind == "inconclusive"


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
