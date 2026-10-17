from app.monitoring.live_metrics import LiveMetricsCollector, estimate_remaining_s


def test_optimized_timeseries_parses_real_flow_monitor_loss_and_delay_columns() -> None:
    collector = LiveMetricsCollector()
    accepted = collector.ingest_csv_text(
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J,PacketLoss_pct,MeanDelay_ms\n"
        "1,1000000,100,100,1000000,10000,0.25,18.5\n"
    )

    assert accepted == 1
    snapshot = collector.snapshot()
    assert snapshot["current_metrics"]["packet_loss_pct"] == 0.25
    assert snapshot["current_metrics"]["delay_ms"] == 18.5
    assert snapshot["series"]["packet_loss_pct"] == [{"x": 1.0, "y": 0.25}]
    assert snapshot["series"]["delay_ms"] == [{"x": 1.0, "y": 18.5}]


HEADER = (
    "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
    "IntervalThroughput_bps,CumulativeEE_bit_per_J\n"
)

ENERGY_HEADER = "Time,NetEnergy,DiffEnergy\n"
METRICS_HEADER = (
    "time_s,total_energy_j,total_power_w,delivered_bits,throughput_bps,"
    "packet_loss_pct,ee_bit_per_j,j_per_mbit,mean_prb_utilization_pct,p05_sinr_db\n"
)


def test_live_snapshot_uses_real_cumulative_values_and_eta() -> None:
    collector = LiveMetricsCollector()
    accepted = collector.ingest_csv_text(
        HEADER
        + "1,1000000,100,100,1000000,10000\n"
        + "4,8000000,400,100,2000000,20000\n"
    )

    snapshot = collector.snapshot(sim_duration_s=10, elapsed_real_s=8)

    assert accepted == 2
    assert snapshot["sim_time_s"] == 4.0
    assert snapshot["progress_pct"] == 40.0
    assert snapshot["eta_s"] == 12.0
    assert snapshot["current_metrics"] == {
        "energy_efficiency_bit_per_j": 20000.0,
        "energy_per_mbit_j": 50.0,
        "total_energy_j": 400.0,
        "average_power_w": 100.0,
        "throughput_mbit_per_s": 2.0,
        "delivered_mbit": 8.0,
    }
    assert snapshot["series"]["energy_j"][-1] == {"x": 4.0, "y": 400.0}
    assert snapshot["series"]["energy_per_mbit_j"][-1] == {"x": 4.0, "y": 50.0}
    assert snapshot["series"]["throughput_mbit_per_s"][-1] == {"x": 4.0, "y": 2.0}


def test_incremental_text_skips_stale_rows_and_waits_for_complete_tail() -> None:
    collector = LiveMetricsCollector()
    first = HEADER + "1,1000000,100,100,1000000,10000\n2,2000000,200"
    assert collector.ingest_csv_text(first) == 1
    assert collector.last_time_s == 1.0

    assert collector.ingest_csv_text(
        ",100,1000000,10000\n1,1000000,100,100,1000000,10000\n"
    ) == 1
    snapshot = collector.snapshot()

    assert snapshot["sim_time_s"] == 2.0
    assert snapshot["data_status"]["stale_rows"] == 1
    assert snapshot["data_status"]["invalid_rows"] == 0
    assert snapshot["progress_pct"] is None
    assert snapshot["eta_s"] is None


def test_metrics_csv_is_a_full_real_live_source_with_quality_kpis() -> None:
    collector = LiveMetricsCollector()
    accepted = collector.ingest_csv_text(
        METRICS_HEADER
        + "1,533.75,533.75,12000000,12000000,0.0,22482.0,44.48,3.66,17.13\n"
        + "2,1067.5,533.75,24000000,12000000,0.1,22482.0,44.48,3.70,17.20\n",
        source="metrics.csv",
    )

    snapshot = collector.snapshot(sim_duration_s=10, elapsed_real_s=4)

    assert accepted == 2
    assert collector.primary_source == "metrics.csv"
    assert collector.has_full_metric_schema is True
    assert snapshot["data_status"]["data_source"] == "metrics.csv"
    assert snapshot["current_metrics"]["energy_efficiency_bit_per_j"] == 22482.0
    assert snapshot["current_metrics"]["energy_per_mbit_j"] == 44.48
    assert snapshot["current_metrics"]["throughput_mbit_per_s"] == 12.0
    assert snapshot["current_metrics"]["delivered_mbit"] == 24.0
    assert snapshot["current_metrics"]["packet_loss_pct"] == 0.1
    assert snapshot["current_metrics"]["mean_prb_pct"] == 3.70
    assert snapshot["current_metrics"]["p05_sinr_db"] == 17.20
    assert snapshot["series"]["packet_loss_pct"][-1] == {"x": 2.0, "y": 0.1}


def test_full_header_with_blank_core_values_is_not_a_complete_live_sample() -> None:
    collector = LiveMetricsCollector()
    accepted = collector.ingest_csv_text(
        METRICS_HEADER + "1,100,,,,0.0,,,3.5,12\n",
        source="metrics.csv",
    )

    assert accepted == 1
    assert collector.has_full_metric_schema is True
    assert collector.has_complete_primary_sample is False
    snapshot = collector.snapshot()
    assert snapshot["current_metrics"]["total_energy_j"] == 100.0
    assert snapshot["current_metrics"]["energy_efficiency_bit_per_j"] is None
    assert snapshot["current_metrics"]["throughput_mbit_per_s"] is None


def test_blank_value_does_not_reuse_an_older_metric_at_a_new_time() -> None:
    collector = LiveMetricsCollector()
    collector.ingest_csv_text(
        HEADER
        + "1,1000000,100,100,1000000,10000\n"
        + "2,,200,100,,\n"
    )

    snapshot = collector.snapshot()
    assert snapshot["sim_time_s"] == 2.0
    assert snapshot["current_metrics"]["total_energy_j"] == 200.0
    assert snapshot["current_metrics"]["delivered_mbit"] is None
    assert snapshot["current_metrics"]["throughput_mbit_per_s"] is None
    assert snapshot["current_metrics"]["energy_efficiency_bit_per_j"] is None


def test_header_changes_bad_rows_and_retention_are_handled() -> None:
    collector = LiveMetricsCollector(max_points=2)
    assert collector.ingest_csv_text(
        HEADER
        + "1,1000000,100,100,1000000,10000\n"
        + "2,2000000,200,100,1000000,10000\n"
    ) == 2

    changed_header = (
        "Time,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J,Unused\n"
    )
    assert collector.ingest_csv_text(
        changed_header
        + "bad,3000000,300,100,1000000,10000,x\n"
        + "3,3000000,300,100,1000000,10000,x\n"
        + "4,4000000,400,100,1000000,10000,x\n"
    ) == 2

    snapshot = collector.snapshot()
    assert snapshot["data_status"]["header_changes"] == 1
    assert snapshot["data_status"]["invalid_rows"] == 1
    assert snapshot["series"]["energy_j"] == [
        {"x": 3.0, "y": 300.0},
        {"x": 4.0, "y": 400.0},
    ]


def test_eta_helper_refuses_an_unfounded_estimate() -> None:
    assert estimate_remaining_s(10, 0, 5) is None
    assert estimate_remaining_s(0, 4, 5) is None
    assert estimate_remaining_s(10, 4, -1) is None
    assert estimate_remaining_s(10, 12, 8) == 0.0


def test_energy_file_fallback_uses_only_observed_energy_and_power() -> None:
    collector = LiveMetricsCollector()

    accepted = collector.ingest_energy_files(
        {
            "energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n",
            "energyfilecell3.csv": ENERGY_HEADER + "1,200,200\n",
        }
    )
    assert accepted == 2
    assert collector.ingest_energy_files(
        {
            "energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n2,150,50\n",
            "energyfilecell3.csv": ENERGY_HEADER + "1,200,200\n2,280,80\n",
        }
    ) == 2

    snapshot = collector.snapshot(sim_duration_s=10, elapsed_real_s=4)

    assert snapshot["sim_time_s"] == 2.0
    assert snapshot["progress_pct"] == 20.0
    assert snapshot["eta_s"] == 16.0
    assert snapshot["current_metrics"] == {
        "energy_efficiency_bit_per_j": None,
        "energy_per_mbit_j": None,
        "total_energy_j": 430.0,
        "average_power_w": 215.0,
        "throughput_mbit_per_s": None,
        "delivered_mbit": None,
    }
    assert snapshot["series"]["energy_j"] == [
        {"x": 1.0, "y": 300.0},
        {"x": 2.0, "y": 430.0},
    ]
    assert snapshot["series"]["power_w"] == [{"x": 2.0, "y": 130.0}]
    assert snapshot["series"]["energy_efficiency_bit_per_j"] == []
    assert snapshot["series"]["delivered_mbit"] == []
    assert snapshot["data_status"]["data_source"] == "energyfilecell*.csv"
    assert snapshot["data_status"]["has_live_data"] is True
    assert snapshot["data_status"]["energy_file_count"] == 2
    assert "energy_efficiency_bit_per_j" in snapshot["data_status"]["unavailable_series"]
    assert snapshot["series_sources"] == {
        "energy_j": "energyfilecell*.csv (NetEnergy)",
        "power_w": "energyfilecell*.csv (różnica sumy NetEnergy / czas)",
    }


def test_energy_file_fallback_does_not_publish_partial_cell_sum() -> None:
    collector = LiveMetricsCollector()
    collector.ingest_energy_files(
        {
            "energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n",
            "energyfilecell3.csv": ENERGY_HEADER,
        }
    )

    snapshot = collector.snapshot()

    assert snapshot["current_metrics"]["total_energy_j"] is None
    assert snapshot["series"]["energy_j"] == []
    assert snapshot["data_status"]["has_live_data"] is False


def test_energy_file_fallback_waits_for_a_complete_last_row() -> None:
    collector = LiveMetricsCollector()
    collector.ingest_energy_files(
        {"energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n"}
    )

    assert collector.ingest_energy_files(
        {"energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n2,150"}
    ) == 0
    assert collector.snapshot()["current_metrics"]["total_energy_j"] == 100.0

    assert collector.ingest_energy_files(
        {"energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n2,150,50\n"}
    ) == 1
    assert collector.snapshot()["current_metrics"]["total_energy_j"] == 150.0


def test_primary_timeseries_takes_precedence_over_energy_file_fallback() -> None:
    collector = LiveMetricsCollector()
    collector.ingest_energy_files(
        {"energyfilecell2.csv": ENERGY_HEADER + "1,100,100\n"}
    )
    collector.ingest_csv_text(HEADER + "2,8000000,400,100,2000000,20000\n")

    snapshot = collector.snapshot()

    assert snapshot["data_status"]["data_source"] == "ee_timeseries.csv"
    assert snapshot["current_metrics"]["total_energy_j"] == 400.0
    assert snapshot["series"]["energy_j"] == [{"x": 2.0, "y": 400.0}]
    assert snapshot["series"]["delivered_mbit"] == [{"x": 2.0, "y": 8.0}]
