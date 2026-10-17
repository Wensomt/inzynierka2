import hashlib
import tarfile
from io import BytesIO
from pathlib import Path

from app.simulation.scenario_sync import (
    source_declares_ee_timeseries,
    source_digest,
    sync_packaged_scenario,
)
from app.simulation.models import Simulation, SimulationConfiguration
from app.simulation.runner import SimulationRunner


class FakeContainer:
    def __init__(self, files):
        self.files = dict(files)
        self.put_calls = []

    def put_archive(self, directory, payload):
        self.put_calls.append((directory, payload))
        with tarfile.open(fileobj=BytesIO(payload), mode="r:*") as archive:
            member = archive.next()
            assert member is not None
            content = archive.extractfile(member)
            assert content is not None
            self.files[str(Path(directory) / member.name).replace("\\", "/")] = content.read()
        return True


class FakeProcesses:
    def __init__(self, files):
        self.session = type("Session", (), {"container": FakeContainer(files)})()
        self.touched = []

    def exec_text(self, command):
        path = command.rsplit(" ", 1)[-1].strip("'")
        if command.startswith("touch "):
            self.touched.append(path)
            return (0, "")
        value = self.session.container.files.get(path)
        if value is None:
            return (3, "")
        return (0, hashlib.sha256(value).hexdigest() + "\n")


def test_sync_skips_identical_source(tmp_path):
    source = tmp_path / "scenario.cc"
    source.write_text("int main() {}\n", encoding="utf-8")
    remote = "/workspace/ns3/scratch/scenario.cc"
    processes = FakeProcesses({remote: source.read_bytes()})

    result = sync_packaged_scenario(processes, source, remote)

    assert result.state == "unchanged"
    assert result.digest == source_digest(source)
    assert not processes.session.container.put_calls
    assert processes.touched == [remote]


def test_sync_copies_changed_source_and_verifies_digest(tmp_path):
    source = tmp_path / "scenario.cc"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    remote = "/workspace/ns3/scratch/scenario.cc"
    processes = FakeProcesses({remote: b"old"})

    result = sync_packaged_scenario(processes, source, remote)

    assert result.state == "updated"
    assert processes.session.container.files[remote] == source.read_bytes()
    assert processes.touched == [remote]


def test_source_declaration_detects_the_real_live_timeseries_markers(tmp_path):
    source = tmp_path / "scenario.cc"
    source.write_text(
        "void WriteEeTimeseries() { /* ee_timeseries.csv */ }\n"
        "// DeliveredBits,CumulativeEE_bit_per_J\n",
        encoding="utf-8",
    )

    assert source_declares_ee_timeseries(source) is True
    assert source_declares_ee_timeseries(tmp_path / "missing.cc") is False


def test_fresh_image_can_receive_packaged_scenario_before_discovery_lists_it():
    simulation = Simulation(
        name="Nowy obraz",
        configuration=SimulationConfiguration(scenario="optimized", parameters={"simTime": 2}),
    )
    config = {"scenarios": {"optimized": {"path": "scratch/my_energy_scenario_optimized.cc"}}}

    destination = SimulationRunner._container_scenario_source_path(
        config,
        {"scenarios": []},
        simulation,
        ns3_root="/workspace/ns3-mmwave-oran",
    )

    assert destination == "/workspace/ns3-mmwave-oran/scratch/my_energy_scenario_optimized.cc"


def test_runner_forces_the_synced_scratch_target_to_build_before_running() -> None:
    class BuildProcesses:
        def __init__(self) -> None:
            self.commands = []

        def exec_text(self, command):
            self.commands.append(command)
            return 0, "built"

    processes = BuildProcesses()

    target = SimulationRunner._rebuild_synced_scenario(
        processes,
        "/workspace/ns3-mmwave-oran",
        "/workspace/ns3-mmwave-oran/scratch/my_energy_scenario_optimized.cc",
    )

    assert target == "scratch/my_energy_scenario_optimized"
    assert processes.commands == [
        "cd /workspace/ns3-mmwave-oran && env USER=oranlab ./ns3 build scratch/my_energy_scenario_optimized"
    ]


def test_runner_rejects_a_failed_or_outside_synced_scenario_build() -> None:
    class FailingProcesses:
        def exec_text(self, _command):
            return 1, "compiler error"

    try:
        SimulationRunner._rebuild_synced_scenario(
            FailingProcesses(),
            "/workspace/ns3-mmwave-oran",
            "/workspace/ns3-mmwave-oran/scratch/my_energy_scenario_optimized.cc",
        )
    except RuntimeError as exc:
        assert "compiler error" in str(exc)
    else:
        raise AssertionError("Błąd builda nie może zostać zignorowany")

    try:
        SimulationRunner._rebuild_synced_scenario(
            FailingProcesses(),
            "/workspace/ns3-mmwave-oran",
            "/tmp/other.cc",
        )
    except RuntimeError as exc:
        assert "nie należy" in str(exc)
    else:
        raise AssertionError("Ścieżka poza ns-3 nie może zostać zbudowana")
