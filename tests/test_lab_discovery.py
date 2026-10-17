import json
from pathlib import Path

from app.config import ConfigStore
from app.infrastructure.lab_discovery import DockerLabDiscovery, DockerUnavailable


class FakeImage:
    id = "sha256:image"
    tags = ["wcnc_tap:latest"]
    attrs = {"RepoTags": tags, "RepoDigests": []}


class FakeContainer:
    id = "abc123"
    short_id = "abc123"
    name = "oran-energy-lab-session"
    status = "running"
    image = FakeImage()
    labels = {"com.oranenergylab.managed": "true"}
    attrs = {
        "Id": id,
        "Name": "/oran-energy-lab-session",
        "State": {"Status": "running"},
        "Config": {"Image": "wcnc_tap:latest", "Labels": labels},
    }

    def __init__(self, inventory):
        self.inventory = inventory

    def exec_run(self, command, stdout=True, stderr=True):
        assert command[:2] == ["/bin/sh", "-lc"]
        return 0, json.dumps(self.inventory).encode("utf-8")


class FakeClient:
    def __init__(self, inventory):
        self.container = FakeContainer(inventory)

    def ping(self):
        return True

    def version(self):
        return {"Version": "27"}


def test_discovery_reports_missing_docker_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"lab": {"simulator_image": "wcnc_tap"}, "xapps": {}}', encoding="utf-8")
    discovery = DockerLabDiscovery(
        ConfigStore(path),
        docker_client_factory=lambda: (_ for _ in ()).throw(DockerUnavailable("brak Docker")),
    )

    snapshot = discovery.status()

    assert snapshot["docker"]["available"] is False
    assert "brak Docker" in snapshot["warnings"][0]


def test_discovery_reads_the_explicit_session_container(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"lab": {"simulator_image": "wcnc_tap"}, "xapps": {}}', encoding="utf-8")
    inventory = {
        "near_rt_ric": "/workspace/flexric/build/examples/ric/nearRT-RIC",
        "ns3_wrapper": "/workspace/ns3/ns3",
        "ns3_root": "/workspace/ns3",
        "flexric_root": "/workspace/flexric",
        "e2sim_root": None,
        "scenarios": [], "xapps": [], "libraries": [], "flexric_configs": [],
        "versions": {}, "commits": {},
    }
    client = FakeClient(inventory)

    snapshot = DockerLabDiscovery(ConfigStore(path)).discover(
        save=False, client=client, container=client.container
    )

    assert snapshot["docker"]["available"] is True
    assert snapshot["simulator"]["container_found"] is True
    assert snapshot["components"]["ns3_root"]["path"] == "/workspace/ns3"
