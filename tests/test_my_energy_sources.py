import json
import tkinter as tk

import pytest

from app.config import ConfigStore
from app.services import my_energy_sources as sources
from app.gui.my_energy_view import MyEnergyView
from resources.patch_kpm_clock import patch


def test_local_edits_survive_template_update_and_have_backup(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "resources").mkdir(parents=True)
    template = bundle / "resources" / sources.FILES["xapp"]
    template.write_text("original", encoding="utf-8")
    monkeypatch.setattr(sources, "RESOURCE_DIR", bundle)
    monkeypatch.setattr(sources, "APP_DATA_DIR", tmp_path / "user")
    path = sources.source_path({}, "xapp")
    sources.save_source({}, "xapp", "my changes")
    template.write_text("new template", encoding="utf-8")
    assert sources.source_path({}, "xapp").read_text() == "my changes"
    assert next((path.parent / "backups").iterdir()).read_text() == "original"


def test_unedited_copy_updates_but_experiment_snapshot_does_not(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "resources").mkdir(parents=True)
    template = bundle / "resources" / sources.FILES["scenario"]
    template.write_text("v1", encoding="utf-8")
    monkeypatch.setattr(sources, "RESOURCE_DIR", bundle)
    monkeypatch.setattr(sources, "APP_DATA_DIR", tmp_path / "user")
    path = sources.source_path({}, "scenario")
    frozen = {"lab": {"my_energy_source_dir": str(tmp_path / "snapshot")}}
    snapshot = sources.source_path(frozen, "scenario")
    template.write_text("v2", encoding="utf-8")
    assert sources.source_path({}, "scenario").read_text() == "v2"
    assert sources.source_path(frozen, "scenario").read_text() == "v1"


def test_kpm_clock_patch_is_idempotent_and_refuses_unknown_backend():
    original = "#include <ns3/log.h>\ncolstartTime = get_time_now_us ();\n"
    updated = patch(original)
    assert "Simulator::Now ().GetMicroSeconds ()" in updated
    assert patch(updated) == updated
    with pytest.raises(ValueError):
        patch("unsupported source")


def test_old_exe_configuration_gains_layout_without_losing_user_defaults(tmp_path):
    path = tmp_path / "config.json"
    original = {"scenarios": {"optimized": {"path": "scratch/my_energy_scenario_optimized.cc", "allowed_params": ["simTime"]}},
                "xapps": {"optimized": {"parameters": {"EE_DECISION_PERIOD_SEC": {"unit": "s", "default": 7}}}}}
    path.write_text(json.dumps(original), encoding="utf-8")
    config = ConfigStore(path).as_dict()
    assert config["scenarios"]["optimized"]["allowed_params"] == ["simTime", "CellLayout"]
    assert config["xapps"]["optimized"]["parameters"]["EE_DECISION_PERIOD_SEC"] == {"unit": "s ns-3", "default": 7}
    assert json.loads(path.with_name("config.before-my-energy-editor.json").read_text()) == original


def test_editor_saves_both_sources_and_creates_equal_comparison_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "APP_DATA_DIR", tmp_path / "user")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No Tk display")
    root.withdraw()
    try:
        config = ConfigStore(tmp_path / "config.json")
        pairs = []
        view = MyEnergyView(root, config, lambda cfg: pairs.append(cfg.to_dict()), lambda: True)
        view._preset(6, 24)
        view._pair()
        assert pairs[0]["parameters"]["N_MmWaveEnbNodes"] == 6
        assert pairs[0]["parameters"]["N_Ues"] == 24
        view._find("xapp", "void forEachCell(")
        assert view.editors["xapp"].tag_ranges("found")
        for kind in ("xapp", "scenario"):
            editor = view.editors[kind]
            editor.insert("end", "\n// editor test\n")
            view._save(kind)
            assert sources.source_path(config.as_dict(), kind).read_text(encoding="utf-8").endswith("// editor test\n")
    finally:
        root.destroy()
