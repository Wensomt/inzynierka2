from app.monitoring.xapp_decisions import XappDecisionCollector


def test_energy_xapp_decision_history_is_real_bounded_and_deduplicated() -> None:
    collector = XappDecisionCollector(max_events=2)
    text = (
        "time,event,cell_id,ue_id,ue_count,serving_sinr_db,target_cell,target_sinr_db,reason\n"
        "10,candidate,2,7,3,11.2,0,0,stable_low_load\n"
        "11,handover_sent,2,7,3,11.2,4,14.6,relocation_before_shutdown\n"
        "12,switch_off,2,7,0,0,0,0,policy_accepted\n"
    )

    assert collector.ingest_files({"xapp_ee_decisions-1.csv": text}) == 3
    # Re-reading a Docker tail cannot duplicate visible history.
    assert collector.ingest_files({"xapp_ee_decisions-1.csv": text}) == 0

    snapshot = collector.snapshot()
    assert [item["event"] for item in snapshot["events"]] == [
        "handover_sent",
        "switch_off",
    ]
    assert snapshot["events"][-1]["reason"] == "policy_accepted"
    assert snapshot["data_status"]["accepted_rows"] == 3
    assert snapshot["data_status"]["duplicate_rows"] == 3


def test_ml_xapp_time_in_microseconds_is_converted_without_inventing_fields() -> None:
    collector = XappDecisionCollector()
    text = (
        "time_us,latency_us,load_dl,tb_dl,serving_sinr,neighbor_metric,drb_estab,drb_rel,p_high_load,decision,updates\n"
        "2500000,17,12,4,9.1,8.5,3,1,0.82,KEEP_ACTIVE_HIGH_LOAD,5\n"
    )

    assert collector.ingest_files({"ml_xapp_orange_kpm_decisions.csv": text}) == 1
    event = collector.snapshot()["events"][0]
    assert event["time_s"] == 2.5
    assert event["event"] == "KEEP_ACTIVE_HIGH_LOAD"
    assert "cell_id" not in event
