import json

import pytest

from app.services.scenario_service import ScenarioService
from app.simulation.models import SimulationConfiguration


def app_config():
    return {
        "scenarios": {
            "optimized": {
                "allowed_params": ["simTime", "N_Ues", "N_MmWaveEnbNodes", "RngRun", "Bandwidth"],
                "minimum_sim_time_s": 0.2,
            }
        },
        "xapps": {
            "none": {},
            "optimized": {"parameters": {"EE_DECISION_PERIOD_SEC": {"default": 5}}},
        },
    }


def configuration():
    return SimulationConfiguration(
        scenario="optimized",
        parameters={
            "simTime": 20,
            "N_Ues": 3,
            "N_MmWaveEnbNodes": 2,
            "RngRun": 1,
            "Bandwidth": 20_000_000,
            "not_supported": 4,
        },
        xapp="optimized",
        xapp_environment={"EE_DECISION_PERIOD_SEC": 3, "NOT_REAL": 5},
    )


def test_service_saves_only_backend_supported_configuration(tmp_path):
    service = ScenarioService(tmp_path, app_config())
    item = service.create("Niski ruch", configuration(), "opis")

    restored = service.get(item.id)

    assert restored.name == "Niski ruch"
    assert "not_supported" not in restored.configuration.parameters
    assert restored.configuration.xapp_environment == {"EE_DECISION_PERIOD_SEC": 3}
    assert service.list()[0].id == item.id


def test_service_duplicate_update_export_import_and_delete(tmp_path):
    service = ScenarioService(tmp_path / "saved", app_config())
    original = service.create("Bazowy", configuration())
    duplicate = service.duplicate(original.id)
    changed = service.update(duplicate.id, "Zmieniony", configuration(), "nowy")
    destination = tmp_path / "export.json"

    service.export(changed.id, destination)
    imported = service.import_file(destination, "Zaimportowany")
    service.delete(original.id)

    assert changed.name == "Zmieniony"
    assert imported.name == "Zaimportowany"
    assert [item.name for item in service.list()] == ["Zaimportowany", "Zmieniony"]
    with pytest.raises(FileNotFoundError):
        service.get(original.id)


def test_import_rejects_bad_json(tmp_path):
    service = ScenarioService(tmp_path / "saved", app_config())
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "Bad", "configuration": {"scenario": "missing"}}), encoding="utf-8")

    with pytest.raises(ValueError):
        service.import_file(bad)
