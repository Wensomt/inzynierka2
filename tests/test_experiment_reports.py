import json
import shutil
import sys

import pytest

from scripts import check_simulator, summarize_experiment
from app.services.my_energy_sources import FILES


def test_interrupted_experiment_retains_run_index(tmp_path, monkeypatch):
    shutil.copy2(check_simulator.BASE_DIR / "config.json", tmp_path / "config.json")
    monkeypatch.setattr(check_simulator, "BASE_DIR", tmp_path)
    monkeypatch.setattr(check_simulator, "source_path", lambda config, kind: check_simulator.RESOURCE_DIR / "resources" / FILES[kind])
    monkeypatch.setattr(sys, "argv", ["check_simulator", "--xapps", "none", "optimized"])

    class InterruptedRunner:
        def __init__(self, *args):
            pass

        def run(self, simulation, progress):
            if simulation.configuration.xapp == "optimized":
                raise RuntimeError("test interruption")
            return simulation

    monkeypatch.setattr(check_simulator, "SimulationRunner", InterruptedRunner)
    with pytest.raises(RuntimeError, match="test interruption"):
        check_simulator.main()
    report = json.loads(next(tmp_path.glob("data/experiments/backend-check/*/report.json")).read_text(encoding="utf-8"))
    assert [run["configuration"]["xapp"] for run in report["runs"]] == ["none", "optimized"]
    assert report["comparison"] is None
    assert report["failures"]


@pytest.mark.parametrize("scenario_hash, status, expected", [("same", "COMPLETED", True), ("changed", "COMPLETED", False), ("same", "RUNNING", False)])
def test_savings_require_complete_pair_with_same_model(tmp_path, scenario_hash, status, expected):
    runs = []
    for mode, energy, digest, state in (("none", 100, "same", "COMPLETED"), ("optimized", 80, scenario_hash, status)):
        runs.append({
            "id": mode, "status": state,
            "configuration": {"xapp": mode, "scenario": "optimized", "parameters": {"RngRun": 1}},
            "runtime": {"scenario_source": {"sha256": digest}, "kpm_clock_patch_sha256": "clock"},
            "result": {"metrics": {"total_energy_j": energy}, "metadata": {"measurement_consistent": True}},
        })
    (tmp_path / "report.json").write_text(json.dumps({"runs": runs}), encoding="utf-8")
    result = summarize_experiment.summarize(tmp_path)
    assert result["valid_pair"] is expected
    assert result["energy_saving_pct"] == (20 if expected else None)
