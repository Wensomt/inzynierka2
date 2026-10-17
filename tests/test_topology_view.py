from pathlib import Path

from app.gui.topology_view import (
    normalize_viewport,
    pan_viewport,
    parse_topology_artifacts,
    planned_topology,
    topology_from_payload,
    zoom_viewport,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_parse_topology_artifacts_uses_latest_real_positions_and_serving_cell(tmp_path):
    topology = tmp_path / "topology"
    _write(
        topology / "gnbs.txt",
        "timestamp,id,x,y,simid,ESstate,currEC,maxEC,totalcurrEC\n"
        "100000,0,4000,4000,100000,0,0,0,0\n"
        "100100,2,2000,2000,100000,0,1,1,2\n"
        "100100,3,2600,2000,100000,0,1,1,2\n"
        "100200,2,2000,2000,100000,1,1,1,2\n"
        "100200,3,2600,2000,100000,0,1,1,2\n",
    )
    _write(
        topology / "ue_position.txt",
        "timestamp,id,x,y,type,cell,simid\n"
        "100100,1,2010,1990,mc,2,100000\n"
        "100200,1,2020,1980,mc,3,100000\n"
        "100200,2,2510,2005,mc,3,100000\n",
    )

    snapshot = parse_topology_artifacts(tmp_path)

    assert snapshot.mode == "reported"
    assert snapshot.source == "gnbs.txt + ue_position.txt"
    assert snapshot.sample_time_s == 0.2
    assert snapshot.bounds == (0.0, 4000.0, 0.0, 4000.0)
    assert [(cell.id, cell.tx_sleeping) for cell in snapshot.cells] == [("2", True), ("3", False)]
    assert [(ue.id, ue.x, ue.serving_cell) for ue in snapshot.ues] == [
        ("1", 2020.0, "3"),
        ("2", 2510.0, "3"),
    ]


def test_planned_topology_only_draws_known_gnb_geometry_without_fake_ues():
    snapshot = planned_topology(
        {
            "scenario": "optimized",
            "parameters": {
                "N_MmWaveEnbNodes": 3,
                "IntersideDistanceCells": 600,
                "N_Ues": 10,
            },
        }
    )

    assert snapshot.mode == "planned"
    assert snapshot.ues == []
    assert not snapshot.has_reported_associations
    assert [(cell.id, round(cell.x), round(cell.y)) for cell in snapshot.cells] == [
        ("gNB #1", 2000, 2000),
        ("gNB #2", 2600, 2000),
        ("gNB #3", 1400, 2000),
    ]


def test_payload_keeps_missing_serving_cell_missing_instead_of_selecting_nearest():
    snapshot = topology_from_payload(
        {
            "cells": [{"id": 2, "x": 0, "y": 0}],
            "ues": [{"id": 7, "x": 2, "y": 3}],
            "sample_time_s": 12,
        }
    )

    assert snapshot.ues[0].serving_cell is None
    assert not snapshot.has_reported_associations


def test_payload_keeps_only_reported_bounded_ue_trail() -> None:
    snapshot = topology_from_payload(
        {
            "cells": [{"id": 1, "x": 0, "y": 0, "role": "eNB"}],
            "ues": [
                {
                    "id": 7,
                    "x": 4,
                    "y": 5,
                    "serving_cell": 1,
                    "trail": [
                        {"x": 1, "y": 2},
                        {"x": "bad", "y": 3},
                        {"x": 4, "y": 5},
                    ],
                }
            ],
        }
    )

    assert snapshot.cells[0].role == "eNB"
    assert snapshot.ues[0].serving_cell == "1"
    assert snapshot.ues[0].trail == ((1.0, 2.0), (4.0, 5.0))


def test_zoom_viewport_keeps_point_under_cursor_and_enforces_smallest_span():
    zoomed = zoom_viewport((0.0, 100.0, 0.0, 100.0), 25.0, 75.0, 2.0)

    assert zoomed == (12.5, 62.5, 37.5, 87.5)
    # Ten sam punkt ma identyczną względną pozycję w nowym widoku, dlatego
    # zoom kółkiem nie powoduje „uciekania” obiektu spod kursora.
    assert (25.0 - zoomed[0]) / (zoomed[1] - zoomed[0]) == 0.25
    assert (75.0 - zoomed[2]) / (zoomed[3] - zoomed[2]) == 0.75

    minimum = zoom_viewport((0.0, 2.0, 0.0, 2.0), 1.0, 1.0, 10.0, minimum_span_m=1.0)
    assert minimum == (0.5, 1.5, 0.5, 1.5)


def test_pan_viewport_preserves_scale_and_normalize_handles_point_bounds():
    moved = pan_viewport((10.0, 110.0, 20.0, 70.0), -4.0, 7.0)

    assert moved == (6.0, 106.0, 27.0, 77.0)
    assert moved[1] - moved[0] == 100.0
    assert moved[3] - moved[2] == 50.0
    assert normalize_viewport((5.0, 5.0, 10.0, 10.0), minimum_span_m=2.0) == (4.0, 6.0, 9.0, 11.0)
