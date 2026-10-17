from pathlib import Path
import json

import pytest

from app.config import ConfigStore
from app.services.xapp_service import XAppService


def write_config(path: Path) -> ConfigStore:
    path.write_text(
        '''{
  "lab": {"simulator_image": "wcnc_tap"},
  "readiness": {"ric_port": 36421},
  "xapps": {
    "none": {"label": "Wyłączony"},
    "optimized": {"label": "My Energy", "executable": "./xapp_my_energy_optimized", "parameters": {"EE_DECISION_PERIOD_SEC": {"default": 5}}},
    "orange": {"label": "Orange", "executable": "./xapp_es_with_cell_util"}
  }
}''',
        encoding="utf-8",
    )
    return ConfigStore(path)


def test_custom_xapp_is_copied_from_actual_my_energy_source(tmp_path):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))

    item = service.create_from_my_energy_template("My custom energy")

    assert item.custom is True
    assert item.buildable is True
    assert item.source_path is not None and item.source_path.is_file()
    assert item.target is not None and item.target.startswith("xapp_my_custom_energy_")
    assert service.get(item.key).name == "My custom energy"
    manifest = json.loads((service.projects_root / item.key / "xapp.json").read_text(encoding="utf-8"))
    assert manifest["source_path"] == item.source_path.name


def test_two_custom_xapps_with_same_name_have_different_cmake_targets(tmp_path):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))

    first = service.create_from_my_energy_template("My custom energy")
    second = service.create_from_my_energy_template("My custom energy")

    assert first.key != second.key
    assert first.target != second.target


def test_orange_is_not_marked_buildable_without_a_local_source(tmp_path):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))
    orange = service.get("orange")

    assert orange.buildable is False
    assert service.validate(orange) == []


def test_cmake_script_uses_the_existing_flexric_target_shape(tmp_path):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))
    script = service._cmake_target_script("/workspace/flexric/examples/xApp/c/myxapp/CMakeLists.txt", "xapp_demo")

    assert "xapp_demo.c" in script
    assert "e42_xapp" in script


def test_built_custom_xapp_keeps_its_source_and_build_status(tmp_path):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))
    item = service.create_from_my_energy_template("My custom energy")
    log_path = tmp_path / "build.log"

    service._activate_built_xapp(item, "oran-energy-lab-xapp:custom", "/opt/flexric/build", log_path)
    service._finish_build(item, True, "OK", log_path, "oran-energy-lab-xapp:custom", "/opt/flexric/build")

    restored = service.get(item.key)
    assert restored.custom is True
    assert restored.buildable is True
    assert restored.source_path == item.source_path
    assert restored.last_build_status == "SUCCESS"
    assert restored.image == "oran-energy-lab-xapp:custom"


def test_existing_target_directory_is_preferred_over_the_fallback(tmp_path, monkeypatch):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))

    def fake_exec(_container, command):
        if command.startswith("test -f"):
            return 0, ""
        if "myxapp" in command:
            return 1, ""
        if "orange" in command:
            return 0, ""
        raise AssertionError(command)

    monkeypatch.setattr(XAppService, "_exec", staticmethod(fake_exec))
    selected = service._find_source_dir(object(), "/opt/flexric", "xapp_my_energy_optimized")

    assert selected == "/opt/flexric/examples/xApp/c/orange"


def test_duplicate_existing_target_is_rejected_before_build(tmp_path, monkeypatch):
    service = XAppService(tmp_path / "xapps", write_config(tmp_path / "config.json"))
    monkeypatch.setattr(XAppService, "_exec", staticmethod(lambda _container, _command: (0, "")))

    with pytest.raises(RuntimeError, match="kilku katalogach CMake"):
        service._find_source_dir(object(), "/opt/flexric", "xapp_my_energy_optimized")


def test_generated_cmake_patch_adds_real_newlines_and_is_idempotent(tmp_path):
    import shlex
    import subprocess
    import sys

    path = tmp_path / "CMakeLists.txt"
    path.write_text("# existing project\n", encoding="utf-8")
    command = shlex.split(XAppService._cmake_target_script(str(path), "xapp_demo"))
    subprocess.run([sys.executable, *command[1:]], check=True)
    first = path.read_text(encoding="utf-8")
    assert "\n\nadd_executable(xapp_demo\n" in first
    assert "\\n" not in first
    subprocess.run([sys.executable, *command[1:]], check=True)
    assert path.read_text(encoding="utf-8") == first
