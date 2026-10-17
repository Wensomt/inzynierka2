import hashlib
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.data.storage import SimulationStorage
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus


def _saved_run(storage: SimulationStorage, name: str = "Run do eksportu") -> Simulation:
    simulation = Simulation(
        name=name,
        configuration=SimulationConfiguration(
            scenario="optimized",
            parameters={"simTime": 60, "N_Ues": 4},
        ),
    )
    directory = storage.create(simulation)
    simulation.status = SimulationStatus.COMPLETED
    simulation.result = SimulationResult(
        metrics={"total_energy_j": 120.0, "delivered_mbit": 5.0},
        series={"energy_j": [{"x": 1.0, "y": 2.0}]},
    )
    storage.save(simulation)
    (directory / "timeseries" / "metrics.csv").write_text(
        "time_s,total_energy_j\n1,2\n",
        encoding="utf-8",
    )
    (directory / "logs" / "runner.log").write_text("rzeczywisty wpis logu\n", encoding="utf-8")
    return simulation


def test_full_result_bundle_round_trip_keeps_actual_artifacts_and_avoids_id_collision(
    tmp_path: Path,
) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    original = _saved_run(storage)
    bundle = storage.export_result_bundle(original, tmp_path / "wynik.zip")

    with zipfile.ZipFile(bundle) as archive:
        assert SimulationStorage.BUNDLE_MANIFEST in archive.namelist()
        assert "{}/timeseries/metrics.csv".format(original.id) in archive.namelist()
        assert "{}/logs/runner.log".format(original.id) in archive.namelist()

    imported = storage.import_result_bundle(bundle)

    assert imported.id != original.id
    assert storage.load(imported.id).result is not None
    preview = storage.read_run_artifact(imported.id, "timeseries/metrics.csv")
    assert "time_s,total_energy_j" in preview.text
    assert "rzeczywisty wpis" in storage.read_run_artifact(imported.id, "logs/runner.log").text
    assert Path(imported.log_path or "").parent == storage.directory(imported.id) / "logs"


def test_delete_moves_run_to_restore_bin_and_restore_preserves_results(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    simulation = _saved_run(storage, "Do odzyskania")
    original_directory = storage.directory(simulation.id)

    deleted = storage.delete(simulation.id)

    assert not original_directory.exists()
    assert [item.trash_id for item in storage.deleted_runs()] == [deleted.trash_id]

    restored = storage.restore_deleted_run(deleted.trash_id)

    assert restored.id == simulation.id
    assert storage.load(restored.id).name == "Do odzyskania"
    assert "rzeczywisty wpis" in storage.read_run_artifact(restored.id, "logs/runner.log").text
    assert storage.deleted_runs() == []


def test_trash_fallback_identifier_with_uuid_is_still_confined_to_the_trash_root(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    simulation = _saved_run(storage)

    fallback = storage._deleted_run_directory("{}--{}".format(simulation.id, uuid4().hex))

    assert fallback.parent == storage.deleted_runs_root.resolve()


def test_artifact_preview_is_bounded_and_cannot_escape_run_directory(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    simulation = _saved_run(storage)
    log = storage.directory(simulation.id) / "logs" / "runner.log"
    log.write_text("abcdefghij", encoding="utf-8")

    preview = storage.read_run_artifact(simulation.id, "logs/runner.log", maximum_bytes=5)

    assert preview.truncated is True
    assert preview.text.startswith("abcde")
    with pytest.raises(ValueError):
        storage.read_run_artifact(simulation.id, "../outside.txt")


def test_import_refuses_path_traversal_before_writing_files(tmp_path: Path) -> None:
    storage = SimulationStorage(tmp_path / "simulations")
    simulation = _saved_run(storage)
    payload = b"nie zapisuj tego"
    manifest = {
        "format": SimulationStorage.BUNDLE_FORMAT,
        "version": SimulationStorage.BUNDLE_VERSION,
        "simulation_id": simulation.id,
        "files": [
            {
                "path": "../outside.txt",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    bundle = tmp_path / "niebezpieczne.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(SimulationStorage.BUNDLE_MANIFEST, json.dumps(manifest))
        archive.writestr("{}/../outside.txt".format(simulation.id), payload)

    with pytest.raises(ValueError):
        storage.import_result_bundle(bundle)

    assert not (tmp_path / "outside.txt").exists()
