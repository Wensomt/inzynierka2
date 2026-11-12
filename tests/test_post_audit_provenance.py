"""Provenance write-path tests; no Docker daemon or scientific measurements."""
from types import SimpleNamespace

from app.simulation.docker_session import DockerSessionManager
from app.simulation.runner import SimulationRunner
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus
from app.data.storage import SimulationStorage
from app.config import ConfigStore
import asyncio
import pytest
import app.simulation.runner as runner_module
from scripts import check_simulator
import hashlib
import copy


@pytest.mark.parametrize('with_manifest', [False, True])
def test_storage_bundle_roundtrip_preserves_manifest_and_legacy(tmp_path, with_manifest):
    storage = SimulationStorage(tmp_path / 'simulations')
    item = Simulation(name='roundtrip', configuration=SimulationConfiguration('optimized', {'simTime': 12}))
    item.status = SimulationStatus.COMPLETED
    item.result = SimulationResult(metrics={'total_energy_j': 10})
    if with_manifest:
        SimulationRunner._record_provenance(item, {})
    original = copy.deepcopy(item.runtime)
    storage.create(item)
    bundle = storage.export_result_bundle(item, tmp_path / 'result.zip')
    imported = storage.import_result_bundle(bundle)
    assert storage.load(imported.id).runtime == original
    assert ('provenance_manifest' in imported.runtime) is with_manifest


def test_validation_failure_persists_unknown_environment_manifest(tmp_path):
    config = ConfigStore(tmp_path / 'config.json')
    storage = SimulationStorage(tmp_path / 'simulations')
    item = Simulation(name='invalid', configuration=SimulationConfiguration('optimized', {'simTime': -1}))
    storage.create(item)
    runner = SimulationRunner(config, storage)
    asyncio.run(runner._run(item, None))
    restored = storage.load(item.id)
    assert restored.status == SimulationStatus.FAILED
    manifest = restored.runtime['provenance_manifest']
    assert manifest['termination']['reason'] == 'validation'
    assert manifest['image']['image_id'] is None
    assert manifest['timestamps']['ended_at'] == restored.ended_at
    assert manifest['timestamps']['started_at'] is None
    assert manifest['timestamps']['unknown_reasons']['started_at']


def test_local_image_without_repo_digest_is_identified_not_error():
    container = SimpleNamespace(attrs={'Image': 'sha256:' + 'f' * 64},
                                image=SimpleNamespace(attrs={'RepoDigests': []}))
    actual = DockerSessionManager.image_provenance(container)
    assert actual['image_id'] == 'sha256:' + 'f' * 64
    assert actual['repo_digests'] is None
    assert actual['repo_digests_unknown_reason']


def test_unobserved_image_never_uses_container_id():
    actual = DockerSessionManager.image_provenance(SimpleNamespace(id='container-id', attrs={}))
    assert actual['image_id'] is None
    assert actual['image_id_unknown_reason']



def test_experiment_manifest_marks_reuse_without_upgrading_legacy(tmp_path):
    legacy = Simulation(name='legacy', configuration=SimulationConfiguration('optimized', {'simTime': 12}))
    original = copy.deepcopy(legacy.to_dict())
    config_path = tmp_path / 'config.json'
    config_path.write_bytes(b'{"configured": true}')
    report = check_simulator.experiment_provenance([legacy], {legacy.id}, {'source.cc': 'a' * 64}, config_path)
    assert report['schema_version'] == 1
    assert report['configuration_snapshot_sha256'] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert report['source_snapshot_sha256']['source.cc'] == 'a' * 64
    run = report['runs'][legacy.id]
    assert run['baseline_reuse']['reused'] is True
    assert run['baseline_reuse']['original_simulation_id'] == legacy.id
    assert run['provenance_manifest'] is None
    assert run['unknown_reason']
    assert legacy.to_dict() == original



def test_snapshot_hash_identifies_copied_bytes(tmp_path):
    source = tmp_path / 'input.cc'
    source.write_bytes(b'actual source bytes')
    target = tmp_path / 'sources'
    target.mkdir()
    digest = check_simulator.snapshot_source(source, target)
    source.write_bytes(b'later different bytes')
    assert (target / source.name).read_bytes() == b'actual source bytes'
    assert digest == hashlib.sha256((target / source.name).read_bytes()).hexdigest()



def test_failed_session_keeps_actual_image_identity(tmp_path, monkeypatch):
    config = ConfigStore(tmp_path / 'config.json')
    storage = SimulationStorage(tmp_path / 'simulations')
    item = Simulation(name='failed', configuration=SimulationConfiguration(
        'optimized', {'simTime': 12, 'N_Ues': 4, 'N_MmWaveEnbNodes': 2, 'RngRun': 1}))
    storage.create(item)
    container = SimpleNamespace(name='session', attrs={'Image': 'sha256:' + 'e' * 64},
                                image=SimpleNamespace(attrs={'RepoDigests': []}))
    session = SimpleNamespace(id='test-session', container=container, client=None)
    sessions = SimpleNamespace(start=lambda: session, stop=lambda _: [])
    def fail_discovery(*args, **kwargs):
        raise RuntimeError('synthetic discovery failure')
    monkeypatch.setattr(runner_module.DockerLabDiscovery, 'discover', fail_discovery)
    runner = SimulationRunner(config, storage, sessions=sessions)
    asyncio.run(runner._run(item, None))
    restored = storage.load(item.id)
    assert restored.status == SimulationStatus.FAILED
    manifest = restored.runtime['provenance_manifest']
    assert manifest['image']['image_id'] == 'sha256:' + 'e' * 64
    assert manifest['image']['repo_digests'] is None
    assert manifest['image']['repo_digests_unknown_reason']
    assert manifest['termination']['reason'] == 'error'



def test_stopped_runner_persists_attempt_manifest_without_invented_environment(tmp_path):
    config = ConfigStore(tmp_path / 'config.json')
    storage = SimulationStorage(tmp_path / 'simulations')
    item = Simulation(name='stopped', configuration=SimulationConfiguration(
        'optimized', {'simTime': 12, 'N_Ues': 4, 'N_MmWaveEnbNodes': 2, 'RngRun': 1}))
    storage.create(item)
    runner = SimulationRunner(config, storage)
    runner.request_stop()
    asyncio.run(runner._run(item, None))
    restored = storage.load(item.id)
    assert restored.status == SimulationStatus.STOPPED
    manifest = restored.runtime['provenance_manifest']
    assert manifest['image']['image_id'] is None
    assert manifest['image']['image_id_unknown_reason']
    assert manifest['termination']['reason'] == 'user_stop'
    assert manifest['timestamps']['ended_at'] == restored.ended_at
    assert manifest['windows']['actual']['duration_s'] is None



def test_manifest_records_observed_inputs_and_separates_actual_window():
    simulation = Simulation(name='manifest', configuration=SimulationConfiguration(
        'optimized', {'simTime': 12, 'RngRun': 3}, xapp='none'))
    simulation.started_at = '2026-01-01T00:00:00+00:00'
    simulation.ended_at = '2026-01-01T00:00:20+00:00'
    simulation.status = SimulationStatus.COMPLETED
    simulation.runtime = {
        'scenario_source': {'sha256': 'c' * 64, 'container_path': '/scenario.cc'},
        'kpm_clock_patch_sha256': 'd' * 64,
        'termination': {'reason': 'ns3_exit', 'partial': False},
    }
    simulation.result = SimulationResult(metadata={
        'measurement_start_s': 2, 'measurement_end_s': 10,
        'measurement_duration_s': 8, 'measurement_consistent': True})
    SimulationRunner._record_provenance(simulation, {'lab': {'simulator_image': 'tag'}},
                                      {'image_id': 'sha256:' + 'a' * 64})
    manifest = simulation.runtime['provenance_manifest']
    assert manifest['schema_version'] == 1
    assert manifest['image']['image_id'] == 'sha256:' + 'a' * 64
    assert manifest['sources']['scenario']['sha256'] == 'c' * 64
    assert manifest['sources']['clock_patch']['sha256'] == 'd' * 64
    assert manifest['configuration']['sha256']
    assert manifest['rng']['run'] == 3
    assert manifest['rng']['seed'] is None
    assert manifest['rng']['stream_mapping'] is None
    assert manifest['rng']['unknown_reasons']['stream_mapping']
    assert manifest['windows']['planned']['end_s'] == 12
    assert manifest['windows']['actual']['start_s'] == 2
    assert manifest['windows']['actual']['duration_s'] == 8
    assert manifest['timestamps']['ended_at'] == simulation.ended_at
    assert manifest['termination'] == simulation.runtime['termination']
    assert manifest['baseline_reuse']['reused'] is False



def test_image_identity_is_not_container_id_or_mutable_tag():
    container = SimpleNamespace(
        id='container-id', attrs={'Image': 'sha256:' + 'a' * 64, 'Config': {'Image': 'mutable:latest'}},
        image=SimpleNamespace(id='sha256:' + 'a' * 64, attrs={'RepoDigests': ['repo@sha256:' + 'b' * 64]}),
    )
    actual = DockerSessionManager.image_provenance(container)
    assert actual['image_id'] == 'sha256:' + 'a' * 64
    assert actual['repo_digests'] == ['repo@sha256:' + 'b' * 64]
    assert actual['requested_reference'] == 'mutable:latest'
