import json
from pathlib import Path

import pytest

from app.simulation.models import SimulationConfiguration
from app.simulation.validation import validate_configuration


CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))


def configured(**parameters):
    return SimulationConfiguration("optimized", {"simTime": 10, "N_MmWaveEnbNodes": 4, "N_Ues": 3, "RngRun": 1, **parameters})


@pytest.mark.parametrize("key,value", [("UdpPacketSizeBytes", 11), ("UdpPacketSizeBytes", 65508),
    ("numberOfRaPreambles", 3), ("numberOfRaPreambles", 65), ("numberOfRaPreambles", 4.5),
    ("bufferSize", 4096), ("N_Ues", 513), ("indicationPeriodicity", 0), ("DrainTime_s", -1),
    ("TrafficStart_s", float("inf"))])
def test_rejects_values_the_backend_cannot_handle(key, value):
    assert validate_configuration(configured(**{key: value}), CONFIG)


def test_drain_and_traffic_start_must_leave_positive_generation_time():
    assert validate_configuration(configured(TrafficStart_s=8, DrainTime_s=2), CONFIG)
    assert not validate_configuration(configured(TrafficStart_s=0, DrainTime_s=0), CONFIG)


def test_backend_minimum_simulation_time_is_inclusive():
    assert not validate_configuration(configured(simTime=0.2), CONFIG)


def test_xapp_requires_live_e2_and_addressable_cell_ids():
    item = configured(enableE2FileLogging=True)
    item.xapp = "optimized"
    assert any("enableE2FileLogging" in error for error in validate_configuration(item, CONFIG))
    item = configured(N_MmWaveEnbNodes=9)
    assert not validate_configuration(item, CONFIG)
    item.xapp = "optimized"
    assert any("8 gNB" in error for error in validate_configuration(item, CONFIG))


def test_age_zero_is_not_a_valid_freshness_policy():
    item = configured()
    item.xapp = "optimized"
    item.xapp_environment["EE_KPM_MAX_AGE_SEC"] = 0
    assert any("EE_KPM_MAX_AGE_SEC" in error for error in validate_configuration(item, CONFIG))
