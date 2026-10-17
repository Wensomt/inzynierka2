import json
from pathlib import Path

import pytest

from app.data.results_parser import ResultsParser


def test_parser_reads_saved_metrics_and_keeps_missing_delay_as_na(tmp_path: Path) -> None:
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,IntervalThroughput_bps\n"
        "10,12000000,600,60,1200000\n",
        encoding="utf-8",
    )
    (timeseries / "ee_summary.csv").write_text(
        "simTime_s,nMmWaveCells,nUes,deliveredData_bits,totalEnergy_J,EE_bit_per_J\n"
        "10,4,6,12000000,600,20000\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["total_energy_j"] == 600.0
    assert result.metrics["delivered_mbit"] == 12.0
    assert result.metrics["energy_efficiency_bit_per_j"] == 20000.0
    assert result.metrics["energy_per_mbit_j"] == 50.0
    assert result.metrics["throughput_mbit_per_s"] == 1.2
    assert result.metrics["delay_ms"] is None
    assert result.metrics["packet_loss_pct"] is None
    assert result.series["delivered_mbit"] == [{"x": 10.0, "y": 12.0}]
    assert result.series["energy_per_mbit_j"] == [{"x": 10.0, "y": 50.0}]
    assert any("Opóźnienie" in warning for warning in result.warnings)


def test_parser_prefers_summary_definition_when_available(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(
        '{"total_energy_j": 400, "delivered_mbit": 10, "ee_bit_per_j": 25000, '
        '"energy_cost_j_per_mbit": 40, "average_power_w": 50, '
        '"average_throughput_bps": 1250000, "packet_loss_pct": 0.5}',
        encoding="utf-8",
    )
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J\n10,12000000,600\n", encoding="utf-8"
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["total_energy_j"] == 400.0
    assert result.metrics["delivered_mbit"] == 10.0
    assert result.metrics["energy_efficiency_bit_per_j"] == 25000.0
    assert result.metrics["energy_per_mbit_j"] == 40.0


def test_parser_prefers_scenario_summary_over_raw_timeseries(tmp_path: Path) -> None:
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,IntervalThroughput_bps\n"
        "12,12000000,600,50,1000000\n",
        encoding="utf-8",
    )
    (timeseries / "ee_summary.csv").write_text(
        "simTime_s,nMmWaveCells,nUes,deliveredData_bits,totalEnergy_J,EE_bit_per_J\n"
        "10,4,6,10000000,400,25000\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["total_energy_j"] == 400.0
    assert result.metrics["delivered_mbit"] == 10.0
    assert result.metrics["average_power_w"] == 40.0
    assert result.metrics["energy_efficiency_bit_per_j"] == 25000.0
    assert result.metrics["energy_per_mbit_j"] == 40.0
    assert result.sources["total_energy_j"] == "ee_summary.csv"


def test_parser_reads_quality_metrics_only_when_saved(tmp_path: Path) -> None:
    (tmp_path / "metrics.csv").write_text(
        "packet_loss_pct,p05_sinr_db,mean_prb_utilization_pct,active_ues\n"
        "0.2,11.5,23.0,4\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["packet_loss_pct"] == 0.2
    assert result.quality["p05_sinr_db"] == 11.5
    assert result.quality["mean_prb_pct"] == 23.0
    assert result.quality["active_ues"] == 4.0


def test_parser_reads_real_flowmonitor_quality_from_full_ee_timeseries(tmp_path: Path) -> None:
    """Pełny scenariusz zapisuje loss/delay bez osobnego metrics.csv."""
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J,PacketLoss_pct,MeanDelay_ms\n"
        "10,12000000,600,60,1200000,20000,0.25,14.5\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["energy_efficiency_bit_per_j"] == 20000.0
    assert result.metrics["energy_per_mbit_j"] == 50.0
    assert result.metrics["packet_loss_pct"] == 0.25
    assert result.metrics["delay_ms"] == 14.5
    assert result.sources["packet_loss_pct"] == "ee_timeseries.csv"
    assert result.sources["delay_ms"] == "ee_timeseries.csv"


def test_parser_uses_complete_metrics_csv_when_ee_timeseries_is_missing(tmp_path: Path) -> None:
    """Starszy backend zapisał KPI, ale nie utworzył osobnego pliku EE."""
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "metrics.csv").write_text(
        "time_s,total_energy_j,total_power_w,delivered_bits,throughput_bps,"
        "packet_loss_pct,ee_bit_per_j,j_per_mbit,mean_prb_utilization_pct,"
        "p05_sinr_db,mean_sinr_db,mean_active_ues\n"
        "10,600,65,12000000,1200000,0.2,20000,50,23,11.5,14.0,4\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["total_energy_j"] == 600.0
    assert result.metrics["delivered_mbit"] == 12.0
    assert result.metrics["average_power_w"] == 60.0
    assert result.metrics["throughput_mbit_per_s"] == 1.2
    assert result.metrics["energy_efficiency_bit_per_j"] == 20000.0
    assert result.metrics["energy_per_mbit_j"] == 50.0
    assert result.metrics["packet_loss_pct"] == 0.2
    assert result.quality["mean_sinr_db"] == 14.0
    assert result.quality["p05_sinr_db"] == 11.5
    assert result.quality["mean_prb_pct"] == 23.0
    assert result.quality["active_ues"] == 4.0
    assert result.sources["total_energy_j"] == "metrics.csv"
    assert result.sources["average_power_w"] == "metrics.csv (total_energy_j / time_s)"
    assert result.series["energy_j"] == [{"x": 10.0, "y": 600.0}]
    assert result.series["power_w"] == [{"x": 10.0, "y": 65.0}]
    assert result.series["throughput_mbit_per_s"] == [{"x": 10.0, "y": 1.2}]
    assert result.series["energy_efficiency_bit_per_j"] == [{"x": 10.0, "y": 20000.0}]
    assert result.series["energy_per_mbit_j"] == [{"x": 10.0, "y": 50.0}]
    assert result.series["delivered_mbit"] == [{"x": 10.0, "y": 12.0}]


def test_parser_uses_metrics_csv_only_for_missing_ee_series(tmp_path: Path) -> None:
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "ee_timeseries.csv").write_text(
        "Time_s,TotalEnergy_J,IntervalPower_W\n10,400,40\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.csv").write_text(
        "time_s,total_energy_j,delivered_bits,throughput_bps,ee_bit_per_j,j_per_mbit\n"
        "10,600,12000000,1200000,20000,50\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    # Energy and delivered bits used for the ratio must have a common source.
    # The old expectation (400 J, 12 Mbit, 20000 bit/J) was contradictory.
    assert result.metrics["total_energy_j"] == 600.0
    assert result.metrics["delivered_mbit"] == 12.0
    assert result.metrics["energy_efficiency_bit_per_j"] == 20000.0
    assert result.metrics["energy_per_mbit_j"] == 50.0
    assert result.series["energy_j"] == [{"x": 10.0, "y": 400.0}]
    assert result.series["delivered_mbit"] == [{"x": 10.0, "y": 12.0}]
    assert result.series["throughput_mbit_per_s"] == [{"x": 10.0, "y": 1.2}]


def test_parser_derives_average_throughput_from_summary_not_last_interval(tmp_path: Path) -> None:
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    (timeseries / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalThroughput_bps\n"
        "10,10000000,400,3000000\n",
        encoding="utf-8",
    )
    (timeseries / "ee_summary.csv").write_text(
        "simTime_s,deliveredData_bits,totalEnergy_J,EE_bit_per_J\n"
        "10,10000000,400,25000\n",
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["throughput_mbit_per_s"] == 1.0
    assert result.sources["throughput_mbit_per_s"].startswith("ee_summary.csv")


def test_parser_reads_legacy_summary_and_analysis_files(tmp_path: Path) -> None:
    (tmp_path / "summary.csv").write_text(
        "metric,value,unit\n"
        "delivered_mbit,25,Mbit\n"
        "total_energy_j,500,J\n"
        "average_power_w,50,W\n"
        "average_throughput_bps,2500000,bit/s\n"
        "ee_bit_per_j,50000,bit/J\n"
        "energy_per_mbit_j,20,J/Mbit\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis.json").write_text(
        '{"quality": {"mean_sinr_db": 8.5, "p05_sinr_db": 2.0, "mean_prb_pct": 40, "mean_active_ues": 3}}',
        encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics["total_energy_j"] == 500.0
    assert result.metrics["energy_efficiency_bit_per_j"] == 50000.0
    assert result.metrics["throughput_mbit_per_s"] == 2.5
    assert result.quality["mean_sinr_db"] == 8.5
    assert result.quality["active_ues"] == 3.0


def test_final_ns3_summary_restores_all_kpis_and_packet_weighted_delay(tmp_path: Path) -> None:
    (tmp_path / "ee_summary.csv").write_text(
        "simTime_s,deliveredData_bits,totalEnergy_J,EE_bit_per_J,measurement_start_s,"
        "measurement_duration_s,tx_packets,rx_packets,delay_sum_s,packet_loss_pct,"
        "mean_delay_ms,energy_scope,data_layer,packet_loss_definition,simulator_source\n"
        "10,12000000,600,20000,0,10,1000,900,18,0,5,nr_radio_model,"
        "udp_application_payload,not_received_by_measurement_end,mmwave_LENA_oran_custom\n",
        encoding="utf-8",
    )
    (tmp_path / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J,PacketLoss_pct,MeanDelay_ms\n"
        "9,9000000,500,1,10\n", encoding="utf-8",
    )

    result = ResultsParser().parse(tmp_path)

    assert result.metrics == {
        "energy_efficiency_bit_per_j": 20000.0,
        "energy_per_mbit_j": 50.0,
        "total_energy_j": 600.0,
        "average_power_w": 60.0,
        "throughput_mbit_per_s": 1.2,
        "delivered_mbit": 12.0,
        "delay_ms": 20.0,
        "packet_loss_pct": 10.0,
    }
    assert result.metadata["measurement_duration_s"] == 10.0
    assert result.metadata["rx_packets"] == 900.0
    assert result.metadata["data_layer"] == "udp_application_payload"
    assert result.sources["delay_ms"].startswith("ee_summary.csv")


def test_average_throughput_uses_cumulative_delivery_not_last_interval(tmp_path: Path) -> None:
    (tmp_path / "metrics.csv").write_text(
        "time_s,total_energy_j,delivered_bits,throughput_bps\n"
        "1,100,9000000,9000000\n10,1000,10000000,111111\n", encoding="utf-8",
    )
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["throughput_mbit_per_s"] == 1.0
    assert result.series["throughput_mbit_per_s"][-1]["y"] == 0.111111


def test_explicit_measurement_window_prevents_mixing_warmup_and_active_time(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(json.dumps({
        "total_energy_j": 400, "delivered_bits": 8e6,
        "measurement_start_s": 2, "measurement_end_s": 10,
        "average_throughput_bps": 99999999,
    }), encoding="utf-8")
    (tmp_path / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J\n10,10000000,800\n", encoding="utf-8",
    )
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["average_power_w"] == 50
    assert result.metrics["throughput_mbit_per_s"] == 1
    assert result.metrics["energy_per_mbit_j"] == 50
    assert result.metadata["measurement_duration_s"] == 8


@pytest.mark.parametrize("tx,expected_loss", [(0, None), (100, 100.0)])
def test_zero_delivered_data_does_not_claim_zero_cost_or_delay(tmp_path: Path, tx, expected_loss) -> None:
    (tmp_path / "summary.json").write_text(json.dumps({
        "total_energy_j": 400, "delivered_bits": 0, "measurement_duration_s": 10,
        "tx_packets": tx, "rx_packets": 0, "delay_sum_s": 0,
        "delay_ms": 0, "packet_loss_pct": 0, "energy_cost_j_per_mbit": 0,
    }), encoding="utf-8")
    # A stale live row must not override a final undefined denominator.
    (tmp_path / "metrics.csv").write_text("time_s,delay_ms,packet_loss_pct\n9,4,0\n", encoding="utf-8")
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["energy_efficiency_bit_per_j"] == 0
    assert result.metrics["energy_per_mbit_j"] is None
    assert result.metrics["delay_ms"] is None
    assert result.metrics["packet_loss_pct"] == expected_loss
    assert result.metrics["throughput_mbit_per_s"] == 0


def test_bom_and_truncated_last_csv_record_do_not_erase_valid_measurement(tmp_path: Path) -> None:
    (tmp_path / "ee_timeseries.csv").write_text(
        "Time_s,DeliveredBits,TotalEnergy_J\n1,1000000,50\n2,2000000", encoding="utf-8-sig",
    )
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["total_energy_j"] == 50
    assert result.metrics["delivered_mbit"] == 1


def test_energy_copies_are_not_double_counted_and_cell_times_must_match(tmp_path: Path) -> None:
    timeseries = tmp_path / "timeseries"
    timeseries.mkdir()
    for directory in (tmp_path, timeseries):
        (directory / "energyfilecell1.csv").write_text("Time,NetEnergy\n10,100\n", encoding="utf-8")
    (timeseries / "energyfilecell2.csv").write_text("Time,NetEnergy\n10,200\n", encoding="utf-8")
    assert ResultsParser().parse(tmp_path).metrics["total_energy_j"] == 300
    (timeseries / "energyfilecell2.csv").write_text("Time,NetEnergy\n9,200\n", encoding="utf-8")
    assert ResultsParser().parse(tmp_path).metrics["total_energy_j"] is None


def test_invalid_metrics_and_inconsistent_efficiency_are_not_presented_as_valid(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(json.dumps({
        "total_energy_j": 100, "delivered_mbit": 2,
        "ee_bit_per_j": 9000, "energy_cost_j_per_mbit": 9,
        "packet_loss_pct": 120, "delay_ms": -2,
    }), encoding="utf-8")
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["energy_efficiency_bit_per_j"] == 20000
    assert result.metrics["energy_per_mbit_j"] == 50
    assert result.metrics["delay_ms"] is None
    assert result.metrics["packet_loss_pct"] is None
    assert any("Skorygowano" in warning for warning in result.warnings)
    assert "derived" in result.sources["energy_per_mbit_j"]


def test_historical_simplified_simulator_is_labeled(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(
        '{"simulator_source":"SOURCE_LOCAL_DISCRETE_SIMULATOR"}', encoding="utf-8",
    )
    assert any("nie jest pomiarem symulatora ns-3" in item for item in ResultsParser().parse(tmp_path).warnings)


def test_accounting_metadata_follows_the_selected_complete_source(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(json.dumps({
        "total_energy_j": 400, "measurement_duration_s": 8, "delay_ms": 5,
    }), encoding="utf-8")
    (tmp_path / "ee_summary.csv").write_text(
        "simTime_s,deliveredData_bits,totalEnergy_J\n10,12000000,600\n", encoding="utf-8",
    )
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["total_energy_j"] == 600
    assert result.metadata["measurement_duration_s"] == 10
    assert result.metadata["accounting_source"] == "ee_summary.csv"
    assert result.metadata["measurement_consistent"] is False


def test_qos_from_a_different_window_cannot_certify_energy_improvement(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(json.dumps({
        "total_energy_j": 400, "delivered_bits": 8e6, "measurement_duration_s": 8,
    }), encoding="utf-8")
    (tmp_path / "metrics.csv").write_text(
        "time_s,delay_ms,packet_loss_pct\n10,5,0.1\n", encoding="utf-8",
    )
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["delay_ms"] == 5
    assert result.metadata["measurement_consistent"] is False
    assert any("innego" in warning for warning in result.warnings)
