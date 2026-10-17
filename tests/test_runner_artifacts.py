import io
import json
import tarfile
from pathlib import Path

from app.data.results_parser import ResultsParser
from app.simulation.runner import SimulationRunner


def test_runner_collects_summary_and_quality_artifacts() -> None:
    assert "summary.json" in SimulationRunner.OUTPUT_GROUPS["summary"]
    assert "metrics.csv" in SimulationRunner.OUTPUT_GROUPS["timeseries"]
    assert "cell_metrics.csv" in SimulationRunner.OUTPUT_GROUPS["timeseries"]
    assert "ue_metrics.csv" in SimulationRunner.OUTPUT_GROUPS["timeseries"]


class ArchiveProcesses:
    def __init__(self) -> None:
        self.files = {
            "/workspace/ns3/summary.json": b'{"total_energy_j": 400, "delivered_mbit": 10}',
            "/workspace/ns3/metrics.csv": b"packet_loss_pct,p05_sinr_db\n0.3,9.5\n",
        }

    def exec_text(self, _script: str):
        records = [
            {
                "group": "summary" if Path(path).name == "summary.json" else "timeseries",
                "path": path,
            }
            for path in self.files
        ]
        return 0, json.dumps(records)

    def get_archive(self, path: str):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            content = self.files[path]
            info = tarfile.TarInfo(name=Path(path).name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return [payload.getvalue()], {}


def test_archive_places_summary_files_where_parser_reads_them(tmp_path: Path) -> None:
    runner = object.__new__(SimulationRunner)
    runner._archive_outputs(ArchiveProcesses(), "/workspace/ns3", tmp_path)

    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "timeseries" / "metrics.csv").is_file()
    result = ResultsParser().parse(tmp_path)
    assert result.metrics["total_energy_j"] == 400.0
    assert result.metrics["packet_loss_pct"] == 0.3
    assert result.quality["p05_sinr_db"] == 9.5
