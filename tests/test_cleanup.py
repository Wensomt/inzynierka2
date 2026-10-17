import os

import pytest

from app.cleanup.guardian import MANAGED_LABEL, SESSION_LABEL, cleanup_session_containers, process_is_alive
from app.simulation.docker_session import DockerSession, DockerSessionError, DockerSessionManager


class FakeContainer:
    def __init__(self, name, labels, status="running"):
        self.name = name
        self.labels = labels
        self.status = status
        self.stopped = False
        self.removed = False

    def reload(self):
        return None

    def stop(self, timeout=5):
        self.stopped = True
        self.status = "exited"

    def remove(self, force=True):
        self.removed = True


class StopFailsContainer(FakeContainer):
    def stop(self, timeout=5):
        raise RuntimeError("timeout")


class FakeContainers:
    def __init__(self, values):
        self.values = values

    def list(self, all=True, filters=None):
        return self.values


class FakeClient:
    def __init__(self, values):
        self.containers = FakeContainers(values)


class DynamicContainers(FakeContainers):
    def list(self, all=True, filters=None):
        return [item for item in self.values if not item.removed]


class DynamicClient(FakeClient):
    def __init__(self, values):
        self.containers = DynamicContainers(values)


class FakeGuardian:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True


class PersistentContainer(FakeContainer):
    def remove(self, force=True):
        return None


def test_cleanup_removes_only_containers_owned_by_the_current_session() -> None:
    target = FakeContainer("target", {MANAGED_LABEL: "true", SESSION_LABEL: "abc"})
    other_session = FakeContainer("other", {MANAGED_LABEL: "true", SESSION_LABEL: "def"})
    foreign = FakeContainer("foreign", {"owner": "other"})

    removed = cleanup_session_containers("abc", FakeClient([target, other_session, foreign]))

    assert removed == ["target"]
    assert target.stopped and target.removed
    assert not other_session.stopped and not other_session.removed
    assert not foreign.stopped and not foreign.removed


def test_cleanup_uses_force_remove_when_graceful_stop_fails() -> None:
    target = StopFailsContainer("target", {MANAGED_LABEL: "true", SESSION_LABEL: "abc"})

    removed = cleanup_session_containers("abc", FakeClient([target]))

    assert removed == ["target"]
    assert target.removed


def test_confirmed_cleanup_terminates_its_guardian() -> None:
    target = FakeContainer("target", {MANAGED_LABEL: "true", SESSION_LABEL: "abc"})
    guardian = FakeGuardian()
    session = DockerSession("abc", DynamicClient([target]), target, guardian)  # type: ignore[arg-type]

    removed = DockerSessionManager({}).stop(session)

    assert removed == ["target"]
    assert guardian.terminated and guardian.waited


def test_retry_cleanup_is_confined_to_one_session_and_confirms_empty_result() -> None:
    target = FakeContainer("target", {MANAGED_LABEL: "true", SESSION_LABEL: "abc"})
    other = FakeContainer("other", {MANAGED_LABEL: "true", SESSION_LABEL: "def"})

    removed = DockerSessionManager({}).ensure_session_cleanup(
        "abc", DynamicClient([target, other])
    )

    assert removed == ["target"]
    assert target.removed
    assert not other.removed


def test_stop_terminates_guardian_even_when_container_cleanup_cannot_be_confirmed() -> None:
    target = PersistentContainer("target", {MANAGED_LABEL: "true", SESSION_LABEL: "abc"})
    guardian = FakeGuardian()
    session = DockerSession("abc", DynamicClient([target]), target, guardian)  # type: ignore[arg-type]

    with pytest.raises(DockerSessionError):
        DockerSessionManager({}).stop(session)

    assert guardian.terminated and guardian.waited


def test_process_is_alive_recognizes_current_process() -> None:
    assert process_is_alive(os.getpid()) is True
