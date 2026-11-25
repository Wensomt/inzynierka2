import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_shipped_linux_image_build_stages_current_scenario_and_xapp() -> None:
    manifest = json.loads((ROOT / "resources/simulator/sources.json").read_text(encoding="utf-8"))
    raw = (ROOT / "resources/simulator/Dockerfile").read_bytes()
    dockerfile = raw.decode("utf-8")
    assert manifest["schema_version"] == 1
    assert manifest["platform"] == "linux/amd64"
    assert hashlib.sha256(raw).hexdigest() == manifest["dockerfile_sha256"]
    assert re.fullmatch(r"docker.io/library/ubuntu:22\.04@sha256:[0-9a-f]{64}", manifest["base_image"])
    assert dockerfile.splitlines()[0] == "FROM " + manifest["base_image"] + " AS buildenv"
    for repository in manifest["repositories"]:
        for source in [repository, *repository["submodules"]]:
            assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])
            assert source["commit"] in dockerfile and source["url"] in dockerfile
    for patch in manifest["patches"]:
        assert hashlib.sha256((ROOT / patch["path"]).read_bytes()).hexdigest() == patch["sha256"]
    assert "COPY oran-lab-resources /workspace/oran-lab-resources" in dockerfile
    assert "my_energy_scenario_optimized.cc" in dockerfile
    assert "xapp_my_energy_optimized.c" in dockerfile
    assert "add_executable(xapp_my_energy_optimized" in dockerfile
    assert "./ns3 build" in dockerfile


def test_linux_launcher_runs_the_gui_from_the_package_root() -> None:
    launcher = (ROOT / "scripts" / "linux" / "launcher.sh").read_text(encoding="utf-8")

    assert '(cd "$ORAN_LAB_ROOT" && "$venv_dir/bin/python" -m app.main)' in launcher
