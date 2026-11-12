from app.simulation.kpm_live import KpmLiveCollector


CU_CP_HEADER = "timestamp,UE (imsi),L3 serving SINR\n"
DU_HEADER = "timestamp,dlPrbUsage,DRB.MeanActiveUeDl,TB.ErrTotalNbrDl.1.UEID\n"
CU_UP_HEADER = (
    "timestamp,UE (imsi),DRB.PdcpSduBitRateDl.UEID (pdcpThroughput),"
    "DRB.PdcpSduDelayDl.UEID (PdcpLatency)\n"
)


def test_kpm_collector_aggregates_only_reported_kpm_values() -> None:
    collector = KpmLiveCollector()

    accepted = collector.ingest_files(
        {
            "cu-cp-cell-1.txt": CU_CP_HEADER
            + "1000,1,10\n1000,2,20\n2000,1,15\n2000,2,25\n",
            "du-cell-1.txt": DU_HEADER
            + "1000,4.5,2,0\n2000,5.5,3,0\n",
            "cu-up-cell-1.txt": CU_UP_HEADER
            + "1000,1,12,20\n1000,2,24,40\n"
            + "2000,1,12,30\n2000,2,24,50\n",
        }
    )

    snapshot = collector.snapshot()

    assert accepted == 10
    assert snapshot["sample_time_s"] == 2.0
    assert snapshot["current_metrics"] == {
        "throughput_mbit_per_s": 0.036000000000000004,
        "mean_delay_ms": 4.0,
        "mean_sinr_db": 20.0,
        "p05_sinr_db": 15.0,
        "mean_prb_utilization_pct": 5.5,
        "active_ues": 3.0,
    }
    assert snapshot["per_cell"] == {
        "1": {
            "mean_sinr_db": 20.0,
            "p05_sinr_db": 15.0,
            "prb_utilization_pct": 5.5,
            "active_ues": 3.0,
            "throughput_mbit_per_s": 0.036000000000000004,
            "mean_delay_ms": 4.0,
            "sample_time_s": 2.0,
        }
    }
    assert snapshot["metric_sources"] == {
        "throughput_mbit_per_s": ["cu-up-cell-1.txt"],
        "mean_delay_ms": ["cu-up-cell-1.txt"],
        "mean_sinr_db": ["cu-cp-cell-1.txt"],
        "p05_sinr_db": ["cu-cp-cell-1.txt"],
        "mean_prb_utilization_pct": ["du-cell-1.txt"],
        "active_ues": ["du-cell-1.txt"],
        "delivered_mbit": ["cu-up-cell-1.txt"],
    }
    assert snapshot["series"]["throughput_mbit_per_s"] == [
        {"x": 1.0, "y": 0.036000000000000004},
        {"x": 2.0, "y": 0.036000000000000004}
    ]


def test_kpm_collector_deduplicates_repeated_tails_and_waits_for_complete_row() -> None:
    collector = KpmLiveCollector()
    first = CU_CP_HEADER + "1000,1,10\n2000,1,"

    assert collector.ingest_files({"cu-cp-cell-1.txt": first}) == 1
    assert collector.snapshot()["current_metrics"]["mean_sinr_db"] == 10.0

    # Kontynuacja niedopisanego rekordu nie potrzebuje ponownego nagłówka.
    assert collector.ingest_files({"cu-cp-cell-1.txt": "20\n"}) == 1
    # Ten sam pełny ogon nie tworzy drugiego pomiaru.
    assert collector.ingest_files(
        {"cu-cp-cell-1.txt": CU_CP_HEADER + "1000,1,10\n2000,1,20\n"}
    ) == 0

    snapshot = collector.snapshot()
    assert snapshot["current_metrics"]["mean_sinr_db"] == 20.0
    assert snapshot["data_status"]["partial_rows"] == 1
    assert snapshot["data_status"]["stale_rows"] == 2


def test_kpm_collector_uses_explicit_pdcp_bitrate_not_report_window_volume() -> None:
    collector = KpmLiveCollector()
    header = (
        "timestamp,UE (imsi),M.PdcpBytesDl (cellDlTxVolume),"
        "DRB.PdcpSduBitRateDl.UEID (pdcpThroughput)\n"
    )

    assert collector.ingest_files(
        {
            "nr-cu-up-cell-2.txt": header
            + "1000,1,1000,8\n"
            + "2000,1,1,8\n"
        }
    ) == 2

    snapshot = collector.snapshot()
    # Wartość objętości raportu zmienia się z 1000 na 1, a mimo to nie
    # tworzy błędnej wartości ujemnej: bitrate jest jawnie podany w Kbit/s.
    assert snapshot["current_metrics"]["throughput_mbit_per_s"] == 0.008
    assert snapshot["per_cell"]["2"]["throughput_mbit_per_s"] == 0.008


def test_kpm_collector_uses_report_interval_volume_only_when_bitrate_is_absent() -> None:
    collector = KpmLiveCollector()
    header = (
        "timestamp,ueImsiComplete,"
        "QosFlow.PdcpPduVolumeDL_Filter.UEID (txPdcpPduBytesNrRlc)\n"
    )

    assert collector.ingest_files(
        {
            "cu-up-cell-2.txt": header
            + "1000,00001,10\n"
            + "2000,00001,20\n"
        }
    ) == 2

    snapshot = collector.snapshot()
    # Pole ma wartość Kbit w okresie raportowym, zatem 20 Kbit / 1 s.
    assert snapshot["current_metrics"]["throughput_mbit_per_s"] == 0.02
    assert snapshot["metric_sources"]["throughput_mbit_per_s"] == ["cu-up-cell-2.txt"]


def test_kpm_collector_keeps_missing_or_unsupported_files_as_missing_data() -> None:
    collector = KpmLiveCollector()

    assert collector.ingest_files({}) == 0
    assert collector.ingest_files({"metrics.csv": "time_s,throughput_bps\n1,10\n"}) == 0

    snapshot = collector.snapshot()
    assert snapshot["sample_time_s"] is None
    assert all(value is None for value in snapshot["current_metrics"].values())
    assert snapshot["data_status"]["has_live_data"] is False
    assert snapshot["data_status"]["unsupported_files"] == 1
    assert snapshot["data_status"]["source_files"] == []
