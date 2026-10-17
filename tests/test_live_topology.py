from app.monitoring.live_topology import LiveTopologyCollector


CELL_HEADER = "timestamp,id,x,y,simid,ESstate,currEC,maxEC,totalcurrEC\n"
UE_HEADER = "timestamp,id,x,y,type,cell,simid\n"


def test_live_topology_uses_actual_positions_and_reported_associations() -> None:
    collector = LiveTopologyCollector()
    assert collector.ingest_cells_csv(CELL_HEADER + (
        "100,0,1000,1000,99,0,0,0,0\n"
        "100,2,500,500,99,0,7.5,10,7.5\n"
        "100,3,800,600,99,1,0,10,7.5\n"
    )) == 2
    assert collector.ingest_ues_csv(UE_HEADER + (
        "100,11,520,510,mc,2,99\n"
        "100,12,770,620,mc,3,99\n"
    )) == 2

    snapshot = collector.snapshot()

    assert snapshot["cells"] == [
        {"id": "2", "x": 500.0, "y": 500.0, "role": "gNB", "tx_sleeping": False, "current_energy_j": 7.5, "sample_time_s": 0.001},
        {"id": "3", "x": 800.0, "y": 600.0, "role": "gNB", "tx_sleeping": True, "current_energy_j": 0.0, "sample_time_s": 0.001},
    ]
    assert snapshot["ues"] == [
        {"id": "11", "x": 520.0, "y": 510.0, "role": "UE", "serving_cell": "2", "type": "mc", "sample_time_s": 0.001},
        {"id": "12", "x": 770.0, "y": 620.0, "role": "UE", "serving_cell": "3", "type": "mc", "sample_time_s": 0.001},
    ]


def test_live_topology_keeps_latest_position_per_entity_and_reports_limit() -> None:
    collector = LiveTopologyCollector(max_entities=1)
    collector.ingest_cells_csv(CELL_HEADER + (
        "200,2,600,700,99,false,8,10,8\n"
        "100,2,500,500,99,false,7,10,7\n"
        "200,3,900,900,99,false,1,10,9\n"
    ))
    collector.ingest_ues_csv(UE_HEADER + (
        "100,11,1,2,mc,2,99\n"
        "200,11,3,4,mc,2,99\n"
        "200,12,5,6,mc,2,99\n"
    ))

    snapshot = collector.snapshot()

    assert snapshot["cells"] == [
        {"id": "2", "x": 600.0, "y": 700.0, "role": "gNB", "tx_sleeping": False, "current_energy_j": 8.0, "sample_time_s": 0.101}
    ]
    assert snapshot["ues"] == [
        {
            "id": "11",
            "x": 3.0,
            "y": 4.0,
            "role": "UE",
            "serving_cell": "2",
            "type": "mc",
            "sample_time_s": 0.101,
            "trail": [
                {"x": 1.0, "y": 2.0, "sample_time_s": 0.001},
                {"x": 3.0, "y": 4.0, "sample_time_s": 0.101},
            ],
        }
    ]
    assert snapshot["data_status"]["truncated_cells"] == 1
    assert snapshot["data_status"]["truncated_ues"] == 1


def test_live_topology_includes_reported_lte_anchor_and_bounded_ue_trail() -> None:
    collector = LiveTopologyCollector(max_trail_points=2)
    assert collector.ingest_enbs_csv(
        CELL_HEADER + "100,1,100,200,99,0,0,0,0\n"
    ) == 1
    assert collector.ingest_ues_csv(
        UE_HEADER
        + "100,7,110,210,mc,1,99\n"
        + "200,7,120,215,mc,1,99\n"
        + "300,7,130,225,mc,1,99\n"
    ) == 3

    snapshot = collector.snapshot()

    assert snapshot["source"] == "enbs.txt + ue_position.txt"
    assert snapshot["cells"] == [
        {"id": "1", "x": 100.0, "y": 200.0, "role": "eNB", "tx_sleeping": False, "current_energy_j": 0.0, "sample_time_s": 0.001}
    ]
    assert snapshot["ues"][0]["serving_cell"] == "1"
    assert snapshot["ues"][0]["trail"] == [
        {"x": 120.0, "y": 215.0, "sample_time_s": 0.101},
        {"x": 130.0, "y": 225.0, "sample_time_s": 0.201},
    ]
