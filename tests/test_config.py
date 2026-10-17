import json
from pathlib import Path

import app.config as config_module
from app.config import ConfigStore


def test_restore_bundled_default_keeps_a_timestamped_backup(tmp_path: Path, monkeypatch) -> None:
    bundled_dir = tmp_path / "bundle"
    bundled_dir.mkdir()
    bundled = bundled_dir / "config.json"
    bundled.write_text(
        json.dumps({"lab": {"simulator_image": "new-image"}, "readiness": {"ric_port": 36421}}),
        encoding="utf-8",
    )
    target = tmp_path / "user-data" / "config.json"
    target.parent.mkdir()
    old_data = {"lab": {"simulator_image": "old-image"}, "readiness": {"ric_port": 36421}}
    target.write_text(json.dumps(old_data), encoding="utf-8")
    monkeypatch.setattr(config_module, "RESOURCE_DIR", bundled_dir)
    store = ConfigStore(target)

    backup = store.restore_bundled_default()

    assert backup is not None
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8")) == old_data
    assert json.loads(target.read_text(encoding="utf-8"))["lab"]["simulator_image"] == "new-image"
    assert store.as_dict()["lab"]["simulator_image"] == "new-image"


def test_restore_bundled_default_is_non_destructive_for_the_bundled_file(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "config.json"
    bundled.write_text(
        json.dumps({"lab": {"simulator_image": "image"}, "readiness": {"ric_port": 36421}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "RESOURCE_DIR", tmp_path)
    store = ConfigStore(bundled)

    assert store.restore_bundled_default() is None
    assert list(tmp_path.glob("*.before-update-*.json")) == []
