from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_linux_image_build_stages_current_scenario_and_xapp() -> None:
    """Pierwszy build obrazu nie może zależeć od ręcznego deployu źródeł."""
    setup = (ROOT / "scripts" / "linux" / "setup.sh").read_text(encoding="utf-8")
    dockerfile = (
        ROOT
        / ".simulator-src"
        / "mmwave-LENA-oran"
        / "ns3_docker"
        / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "prepare_local_lab_resources" in setup
    assert 'prepare_local_lab_resources "$source_context"' in setup
    assert "COPY oran-lab-resources /workspace/oran-lab-resources" in dockerfile
    assert "my_energy_scenario_optimized.cc" in dockerfile
    assert "xapp_my_energy_optimized.c" in dockerfile
    assert "add_executable(xapp_my_energy_optimized" in dockerfile
    assert "./ns3 build" in dockerfile


def test_linux_launcher_runs_the_gui_from_the_package_root() -> None:
    launcher = (ROOT / "scripts" / "linux" / "launcher.sh").read_text(encoding="utf-8")

    assert '(cd "$ORAN_LAB_ROOT" && "$venv_dir/bin/python" -m app.main)' in launcher
