"""Sprawdzenie prawdziwego backendu: python -m scripts.check_simulator.

Wyniki i konfiguracja testu trafiają do data/experiments/backend-check.
Nie zmienia konfiguracji użytkownika ani obrazu Docker.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import shutil
from pathlib import Path
from datetime import datetime

from app.comparison.comparator import compare_simulations
from app.config import BASE_DIR, RESOURCE_DIR, ConfigStore
from app.services.my_energy_sources import source_path
from app.data.results_parser import ResultsParser
from app.data.storage import SimulationStorage
from app.simulation.models import Simulation
from app.simulation.runner import SimulationRunner
from app.simulation.session_controller import default_configuration


def snapshot_source(source: Path, directory: Path) -> str:
    """Hash the bytes written to the snapshot, not a later source reread."""
    payload = source.read_bytes()
    with (directory / source.name).open("xb") as handle:
        handle.write(payload)
    return hashlib.sha256(payload).hexdigest()


def experiment_provenance(results, reused, hashes, config_path: Path) -> dict:
    """Separate new experiment observations from historical run provenance."""
    return {
        "schema_version": 1,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "configuration_snapshot_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "source_snapshot_sha256": dict(hashes),
        "runs": {item.id: {
            "baseline_reuse": {"reused": item.id in reused,
                               "original_simulation_id": item.id if item.id in reused else None,
                               "meaning": "Reuse is not a fresh independent measurement" if item.id in reused else "New attempt"},
            "provenance_manifest": item.runtime.get("provenance_manifest"),
            "unknown_reason": None if item.runtime.get("provenance_manifest") else "No manifest observed; legacy history not upgraded",
        } for item in results},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim-time", type=float, default=10)
    parser.add_argument("--ues", type=int, default=12)
    parser.add_argument("--cells", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--xapps", nargs="+", choices=("none", "optimized"), default=["none", "optimized"])
    parser.add_argument("--interval-us", type=int, default=2000)
    parser.add_argument("--max-ues-shutdown", type=int, default=4)
    parser.add_argument("--policy", choices=("conservative", "energy"), default="conservative")
    parser.add_argument("--baseline", type=Path, help="Katalog ukończonego przebiegu bez xApp; ponowne użycie przy identycznych parametrach i źródle scenariusza")
    args = parser.parse_args()
    directory = BASE_DIR / "data" / "experiments" / "backend-check" / datetime.now().strftime("%Y%m%d-%H%M%S")
    directory.mkdir(parents=True, exist_ok=True)
    print("Katalog kontroli:", directory, flush=True)
    config_path = directory / "config.json"
    config_path.write_text((BASE_DIR / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    config = ConfigStore(config_path)
    snapshot = directory / "sources"
    snapshot.mkdir()
    hashes = {}
    for kind in ("scenario", "xapp"):
        source = source_path(config.as_dict(), kind)
        hashes[source.name] = snapshot_source(source, snapshot)
    patch = RESOURCE_DIR / "resources" / "patch_kpm_clock.py"
    hashes[patch.name] = snapshot_source(patch, snapshot)
    frozen = config.as_dict()
    frozen.setdefault("lab", {})["my_energy_source_dir"] = str(snapshot)
    frozen.setdefault("runtime", {})["wall_clock_timeout_s"] = 14400
    config.save(frozen)
    storage = SimulationStorage(directory / "simulations")
    runner = SimulationRunner(config, storage, ResultsParser())
    results = []
    observations = {}
    reused = set()

    def save_report(failures):
        report = {
            "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
            "reused_baseline_ids": sorted(reused),
            "provenance_manifest": experiment_provenance(results, reused, hashes, config_path),
            "runs": [item.to_dict() for item in results],
            "source_sha256": hashes,
            "live_observations": observations,
            "comparison": vars(compare_simulations(*results)) if len(results) == 2 and all(item.result for item in results) else None,
            "failures": failures,
        }
        target = directory / "report.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=vars), encoding="utf-8")
        temporary.replace(target)

    for xapp in args.xapps:
        configuration = default_configuration(config.as_dict())
        configuration.parameters["simTime"] = args.sim_time
        configuration.parameters.update(N_Ues=args.ues, N_MmWaveEnbNodes=args.cells, RngRun=args.seed, UdpIntervalUs=args.interval_us)
        configuration.xapp = xapp
        if xapp == "optimized":
            configuration.xapp_environment["EE_MAX_UES_FOR_SHUTDOWN"] = args.max_ues_shutdown
            if args.policy == "energy":
                configuration.xapp_environment.update(
                    EE_MIN_TARGET_SINR_DB=5, EE_MIN_HANDOVER_GAIN_DB=-100,
                    EE_SHUTDOWN_CONFIRMATION_SEC=1,
                )
        if xapp == "none" and args.baseline:
            previous = Simulation.from_dict(json.loads((args.baseline / "simulation.json").read_text(encoding="utf-8")))
            if previous.status.value != "COMPLETED" or previous.result is None or previous.configuration.xapp != "none":
                raise ValueError("Punkt odniesienia musi być ukończonym przebiegiem bez xApp")
            if previous.configuration.parameters != configuration.parameters or previous.configuration.scenario != configuration.scenario:
                raise ValueError("Punkt odniesienia ma inne parametry scenariusza")
            if previous.runtime.get("scenario_source", {}).get("sha256") != hashes["my_energy_scenario_optimized.cc"]:
                raise ValueError("Punkt odniesienia ma inną wersję źródła scenariusza")
            if previous.runtime.get("kpm_clock_patch_sha256") != hashes["patch_kpm_clock.py"]:
                raise ValueError("Punkt odniesienia ma inną wersję zegara KPM")
            if not previous.result.metadata.get("measurement_consistent") or previous.runtime.get("experiment_valid") is False:
                raise ValueError("Punkt odniesienia ma nieważny pomiar")
            shutil.copytree(args.baseline, storage.directory(previous.id))
            results.append(previous)
            observations[previous.id] = []
            reused.add(previous.id)
            save_report(["Eksperyment w toku; porównanie nie jest jeszcze ukończone"])
            print("REUSED_BASELINE", previous.id, str(args.baseline), flush=True)
            continue
        simulation = Simulation(name="Kontrola backendu — " + xapp, configuration=configuration)
        storage.create(simulation)
        observations[simulation.id] = []
        results.append(simulation)
        save_report(["Eksperyment w toku; porównanie nie jest jeszcze ukończone"])
        last_time = None

        def progress(_simulation, phase, message, payload=None):
            nonlocal last_time
            if phase == "LOG":
                return
            payload = payload or {}
            if phase == "LIVE_UPDATE":
                time_s = payload.get("progress", {}).get("simulated_time_s")
                if time_s == last_time:
                    return
                last_time = time_s
                observations[simulation.id].append({"sim_time_s": time_s, "accepted_rows": payload.get("data_status", {}).get("accepted_rows", 0), "metrics": payload.get("current_metrics")})
                print(phase, time_s, payload.get("current_metrics"), flush=True)
            else:
                print(phase, message, flush=True)

        try:
            results[-1] = runner.run(simulation, progress)
        finally:
            save_report(["Eksperyment nie został jeszcze zakończony"])
        print("RESULT", simulation.status.value, simulation.error, flush=True)
    failures = []
    for item in results:
        if item.status.value != "COMPLETED" or item.result is None:
            failures.append(item.name + ": przebieg nieukończony")
            continue
        metrics = item.result.metrics
        if item.id not in reused and max((sample["accepted_rows"] for sample in observations[item.id]), default=0) < 2:
            failures.append(item.name + ": brak kolejnych próbek live")
        if not item.result.metadata.get("measurement_consistent"):
            failures.append(item.name + ": niespójny pomiar")
        if any(metrics.get(key) is None for key in ("total_energy_j", "delivered_mbit", "delay_ms", "packet_loss_pct")):
            failures.append(item.name + ": niepełny pomiar energii i QoS")
    save_report(failures)
    print("KONTROLA:", failures or "OK", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
