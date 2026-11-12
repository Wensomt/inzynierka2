"""Regression fixtures for audit F01/F02; not scientific measurements."""
import json
import pytest
import csv


@pytest.mark.parametrize('tx,rx', [(100, 101), (-1, -1), (100.5, 100.5), (float('inf'), 1), (100, float('nan'))])
def test_invalid_packet_counters_cannot_certify_measurement(tmp_path, tx, rx):
    payload = dict(total_energy_j=100, delivered_bits=1e7,
        measurement_start_s=0, measurement_end_s=10, measurement_duration_s=10,
        tx_packets=tx, rx_packets=rx, packet_loss_pct=0, delay_ms=5)
    (tmp_path / 'summary.json').write_text(json.dumps(payload))
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is False
    assert result.metadata['invalid_packet_counters']
    assert result.metadata['raw_packet_counters']['tx_packets'] == tx



@pytest.mark.parametrize('overrides', [
    {'lost_packets': 3}, {'packet_loss_pct': 50},
    {'tx_packets': None, 'TxPackets': 100, 'rx_packets': None, 'RxPackets': 101},
    {'rx_packets': 0},
])
def test_counter_contradictions_block_qualification(tmp_path, overrides):
    payload = dict(total_energy_j=100, delivered_bits=1e7, tx_packets=100, rx_packets=100,
        measurement_start_s=0, measurement_end_s=10, measurement_duration_s=10)
    payload.update(overrides)
    payload = {key: value for key, value in payload.items() if value is not None}
    (tmp_path / 'summary.json').write_text(json.dumps(payload))
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is False
    assert result.warnings


@pytest.mark.parametrize('energy,rx,bits,expected_ee', [(100, 0, 0, 0), (0, 100, 1e7, None)])
def test_zero_delivery_or_zero_energy_remains_readable(tmp_path, energy, rx, bits, expected_ee):
    payload = dict(total_energy_j=energy, delivered_bits=bits, tx_packets=100, rx_packets=rx,
        lost_packets=100-rx, delay_sum_s=0 if rx == 0 else 0.5,
        measurement_start_s=0, measurement_end_s=10, measurement_duration_s=10,
        data_layer='udp_application_payload')
    (tmp_path / 'summary.json').write_text(json.dumps(payload))
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is True
    assert result.metrics['energy_efficiency_bit_per_j'] == expected_ee
    if rx == 0:
        assert result.metrics['delay_ms'] is None
        assert result.metrics['packet_loss_pct'] == 100


def test_scenario_csv_explicit_end_cannot_contradict_simtime(tmp_path):
    payload = dict(simTime_s=12, measurement_start_s=0, measurement_end_s=10,
                   measurement_duration_s=10, totalEnergy_J=100, deliveredData_bits=1e7,
                   simulator_source='mmwave_LENA_oran_custom')
    with (tmp_path / 'ee_summary.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=payload)
        writer.writeheader()
        writer.writerow(payload)
    assert ResultsParser().parse(tmp_path).metadata['measurement_consistent'] is False



def test_literal_scenario_csv_normalizes_simtime_as_end(tmp_path):
    header = 'simTime_s,nMmWaveCells,nUes,deliveredData_bits,totalEnergy_J,EE_bit_per_J,measurement_start_s,measurement_duration_s,traffic_start_s,traffic_stop_s,drain_duration_s,tx_packets,rx_packets,lost_packets,delay_sum_s,packet_loss_pct,mean_delay_ms,avg_throughput_mbps,energy_scope,data_layer,packet_loss_definition,simulator_source,qos_source'
    values = '10,3,12,10000000,100,100000,0,10,2,8,2,100,100,0,0.5,0,5,1,nr_radio_model,udp_application_payload,not_received_by_measurement_end,mmwave_LENA_oran_custom,udp_application_seq_ts'
    (tmp_path / 'ee_summary.csv').write_text(header + '\n' + values + '\n')
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is True
    assert result.metadata['measurement_end_s'] == 10
    assert result.metrics['average_power_w'] == 10


from app.comparison.comparator import compare_simulations
from app.data.results_parser import ResultsParser
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus


def pair():
    result = []
    for mode, energy in [('none', 100), ('optimized', 80)]:
        result.append(Simulation(
            name=mode,
            configuration=SimulationConfiguration('optimized', {'simTime': 10, 'RngRun': 1}, xapp=mode),
            status=SimulationStatus.COMPLETED,
            result=SimulationResult(
                metrics={'energy_efficiency_bit_per_j': 1e7 / energy, 'total_energy_j': energy,
                         'delivered_mbit': 10, 'throughput_mbit_per_s': 1, 'packet_loss_pct': 0, 'delay_ms': 5},
                metadata={'measurement_duration_s': 10, 'measurement_start_s': 0,
                          'energy_scope': 'nr_radio_model', 'data_layer': 'udp_application_payload',
                          'simulator_source': 'mmwave_LENA_oran_custom'})))
    return result


def test_no_provenance_cannot_certify_pair():
    left, right = pair()
    assert compare_simulations(left, right).assessment_kind == 'inconclusive'


def test_declared_window_must_be_possible(tmp_path):
    (tmp_path / 'summary.json').write_text(json.dumps({
        'total_energy_j': 100, 'delivered_bits': 1e7,
        'measurement_start_s': 20, 'measurement_end_s': 10, 'measurement_duration_s': 10,
        'tx_packets': 100, 'rx_packets': 100, 'delay_sum_s': 0.5}), encoding='utf-8')
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is False


def complete_pair():
    items = pair()
    for item in items:
        item.runtime = {'scenario_source': {'sha256': 'scenario-fixture'}, 'kpm_clock_patch_sha256': 'clock-fixture'}
        item.result.metadata.update(measurement_consistent=True, measurement_end_s=10,
                                    accounting_source='summary.json', packet_loss_definition='tx-rx / tx',
                                    qos_source='udp_application_seq_ts')
        from app.simulation.runner import SimulationRunner
        item.started_at = '2026-01-01T00:00:00+00:00'
        item.ended_at = '2026-01-01T00:01:00+00:00'
        item.runtime['termination'] = {'reason': 'natural', 'partial': False}
        SimulationRunner._record_provenance(item, {}, {'image_id': 'sha256:' + 'a' * 64})
    return items


@pytest.mark.parametrize('value', [None, False])
@pytest.mark.parametrize('side', [0, 1, 'both'])
def test_consistency_requires_positive_evidence(value, side):
    items = complete_pair()
    for index in ([0, 1] if side == 'both' else [side]):
        if value is None:
            items[index].result.metadata.pop('measurement_consistent')
        else:
            items[index].result.metadata['measurement_consistent'] = value
    assert compare_simulations(*items).assessment_kind == 'inconclusive'


@pytest.mark.parametrize('energy,expected', [(80, 'improved'), (100, 'no_improvement'), (120, 'no_improvement')])
def test_complete_pair_preserves_material_verdict(energy, expected):
    left, right = complete_pair()
    right.result.metrics.update(total_energy_j=energy, energy_efficiency_bit_per_j=1e7 / energy)
    assert compare_simulations(left, right).assessment_kind == expected


@pytest.mark.parametrize('key', ['simulator_source', 'data_layer', 'packet_loss_definition',
                                 'qos_source', 'accounting_source', 'measurement_start_s',
                                 'measurement_end_s', 'energy_scope'])
def test_missing_shared_definition_is_unknown(key):
    items = complete_pair()
    for item in items:
        item.result.metadata.pop(key)
    assert compare_simulations(*items).assessment_kind == 'inconclusive'


@pytest.mark.parametrize('key', ['measurement_start_s', 'measurement_end_s', 'measurement_duration_s'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_comparison_does_not_trust_invalid_window_marker(key, value):
    items = complete_pair()
    for item in items:
        item.result.metadata[key] = value
    assert compare_simulations(*items).assessment_kind == 'inconclusive'


def test_qos_other_source_requires_possible_window(tmp_path):
    common = dict(measurement_start_s=0, measurement_end_s=10, measurement_duration_s=10)
    (tmp_path / 'summary.json').write_text(json.dumps(dict(common, total_energy_j=100, delivered_bits=1e7)))
    (tmp_path / 'analysis.json').write_text(json.dumps({'summary': dict(common, measurement_end_s=5, delay_ms=2, packet_loss_pct=0)}))
    assert ResultsParser().parse(tmp_path).metadata['measurement_consistent'] is False


@pytest.mark.parametrize('key', ['measurement_start_s', 'measurement_end_s', 'measurement_duration_s'])
@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), -float('inf')])
def test_parser_rejects_unknown_or_nonfinite_window(tmp_path, key, value):
    payload = dict(total_energy_j=100, delivered_bits=1e7,
                   measurement_start_s=0, measurement_end_s=10, measurement_duration_s=10)
    if value is None:
        payload.pop(key)
    else:
        payload[key] = value
    (tmp_path / 'summary.json').write_text(json.dumps(payload))
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is False
    assert result.warnings


@pytest.mark.parametrize('start,end,duration', [(20, 10, 10), (0, 10, 8), (0, 0, 0)])
def test_parser_rejects_contradictory_window(tmp_path, start, end, duration):
    (tmp_path / 'summary.json').write_text(json.dumps(dict(total_energy_j=100, delivered_bits=1e7,
        measurement_start_s=start, measurement_end_s=end, measurement_duration_s=duration)))
    assert ResultsParser().parse(tmp_path).metadata['measurement_consistent'] is False


@pytest.mark.parametrize('start,end', [(0, 12), (2, 10)])
def test_rates_use_actual_measurement_length_not_simtime(tmp_path, start, end):
    (tmp_path / 'summary.json').write_text(json.dumps(dict(total_energy_j=120, delivered_bits=24e6,
        measurement_start_s=start, measurement_end_s=end, measurement_duration_s=end-start, simTime=100)))
    result = ResultsParser().parse(tmp_path)
    assert result.metadata['measurement_consistent'] is True
    assert result.metrics['average_power_w'] == pytest.approx(120 / (end-start))
    assert result.metrics['throughput_mbit_per_s'] == pytest.approx(24 / (end-start))


@pytest.mark.parametrize('part', ['runtime', 'scenario_source', 'clock'])
@pytest.mark.parametrize('side', [0, 1, 'both'])
def test_missing_provenance_component_is_unknown(part, side):
    items = complete_pair()
    for index in ([0, 1] if side == 'both' else [side]):
        if part == 'runtime':
            items[index].runtime = {}
        elif part == 'scenario_source':
            items[index].runtime.pop('scenario_source')
        else:
            items[index].runtime.pop('kpm_clock_patch_sha256')
    result = compare_simulations(*items)
    assert result.assessment_kind == 'inconclusive'
    assert result.warnings


@pytest.mark.parametrize('key,value', [('accounting_source', 'other.csv'), ('measurement_start_s', 2),
    ('measurement_end_s', 12), ('measurement_duration_s', 8)])
def test_different_measurement_definitions_do_not_certify_pair(key, value):
    items = complete_pair()
    items[1].result.metadata[key] = value
    assert compare_simulations(*items).assessment_kind == 'inconclusive'


