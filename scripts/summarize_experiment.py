"""python -m scripts.summarize_experiment data/experiments/backend-check/<folder>."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def summarize(directory: Path, others=()) -> dict:
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    runs = [(directory, run) for run in report["runs"]]
    for other in others:
        data = json.loads((other / "report.json").read_text(encoding="utf-8"))
        runs.extend((other, run) for run in data["runs"])
    rows = []
    for origin, run in runs:
        root = origin / "simulations" / run["id"]
        events = []
        for path in sorted((root / "decisions").glob("*.csv")):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                events.extend(csv.DictReader(handle))
        counts = Counter(item.get("event") for item in events)
        off_cells = set()
        latest_cells = {}
        first_off = {}
        topology = root / "topology" / "gnbs.txt"
        if topology.is_file():
            with topology.open(encoding="utf-8-sig", newline="") as handle:
                for point in csv.DictReader(handle):
                    if point.get("id") == "0":
                        continue
                    latest_cells[point["id"]] = point
                    if point.get("ESstate") == "1":
                        off_cells.add(point["id"])
                        first_off.setdefault(point["id"], (float(point["timestamp"]) - float(point["simid"])) / 1000)
        latest_ues = {}
        positions = root / "topology" / "ue_position.txt"
        if positions.is_file():
            with positions.open(encoding="utf-8-sig", newline="") as handle:
                for point in csv.DictReader(handle):
                    latest_ues[point["id"]] = point
        final_off = {key for key, point in latest_cells.items() if point.get("ESstate") == "1"}
        metrics = (run.get("result") or {}).get("metrics", {})
        row = {
            "id": run["id"], "xapp": run["configuration"]["xapp"], "status": run["status"],
            "rng_run": run["configuration"]["parameters"]["RngRun"],
            **{key: metrics.get(key) for key in ("total_energy_j", "energy_per_mbit_j", "delivered_mbit", "delay_ms", "packet_loss_pct")},
            "candidates": counts["candidate"], "denials": counts["deny"],
            "handovers_sent": counts["handover_sent"], "handovers_confirmed": counts["handover_confirmed"],
            "switch_off_commands": counts["switch_off"], "off_cells_in_topology": sorted(off_cells),
            "first_off_time_s": first_off,
            "final_ue_total": len(latest_ues),
            "final_ue_cell_counts": dict(Counter(point["cell"] for point in latest_ues.values())),
            "ues_on_final_off_cells": sum(point["cell"] in final_off for point in latest_ues.values()),
            "final_off_cell_interval_energy_j": {key: float(latest_cells[key]["currEC"]) for key in final_off},
            "denial_reasons": dict(Counter(item.get("reason") for item in events if item.get("event") == "deny")),
        }
        rows.append(row)
    by_mode = {row["xapp"]: row for row in rows}
    saving = None
    configs = {run["configuration"]["xapp"]: run["configuration"] for _, run in runs}
    matched = set(configs) >= {"none", "optimized"} and configs["none"]["parameters"] == configs["optimized"]["parameters"] and configs["none"]["scenario"] == configs["optimized"]["scenario"]
    scenario_hashes = [run.get("runtime", {}).get("scenario_source", {}).get("sha256") for _, run in runs]
    clock_hashes = [run.get("runtime", {}).get("kpm_clock_patch_sha256") for _, run in runs]
    matched_sources = bool(scenario_hashes) and all(scenario_hashes) and len(set(scenario_hashes)) == 1 and all(clock_hashes) and len(set(clock_hashes)) == 1
    valid_pair = matched and matched_sources and len(rows) == 2 and all(
        run["status"] == "COMPLETED" and run.get("runtime", {}).get("experiment_valid") is not False
        and (run.get("result") or {}).get("metadata", {}).get("measurement_consistent") is True
        for _, run in runs
    )
    if valid_pair:
        baseline = by_mode["none"].get("total_energy_j")
        controlled = by_mode["optimized"].get("total_energy_j")
        if baseline and controlled is not None:
            saving = 100 * (baseline - controlled) / baseline
    summary = {"runs": rows, "matched_scenario_parameters": matched, "matched_scenario_sources": matched_sources, "valid_pair": valid_pair, "energy_saving_pct": saving, "source_sha256": report.get("source_sha256", {})}
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (directory / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return summary


def plot(directory: Path, others=()):
    # Optional research tool; matplotlib is not needed by the desktop app.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(10, 6.5), constrained_layout=True)
    definitions = (("energy_j", "Energia radia NR [J]"), ("power_w", "Moc w przedziale [W]"),
                   ("delivered_mbit", "Odebrane dane UDP [Mbit]"), ("packet_loss_pct", "Jeszcze nieodebrane pakiety [%]"))
    for origin in (directory, *others):
        data = json.loads((origin / "report.json").read_text(encoding="utf-8"))
        for run in data["runs"]:
            if run.get("result") is None:
                continue
            label = "Bez xApp" if run["configuration"]["xapp"] == "none" else "My Energy"
            for ax, (key, title) in zip(axes.flat, definitions):
                points = run["result"]["series"].get(key, [])
                ax.plot([p["x"] for p in points], [p["y"] for p in points], label=label, linewidth=1.5)
                ax.set(xlabel="Czas ns-3 [s]", ylabel=title)
                ax.grid(alpha=0.25)
            topology = origin / "simulations" / run["id"] / "topology" / "gnbs.txt"
            if topology.is_file():
                with topology.open(encoding="utf-8-sig", newline="") as handle:
                    for point in csv.DictReader(handle):
                        if point.get("id") != "0" and point.get("ESstate") == "1":
                            off_time = (float(point["timestamp"]) - float(point["simid"])) / 1000
                            for ax in axes[0]:
                                ax.axvline(off_time, color="#444444", linestyle="--", linewidth=0.9, label="Pierwsze wyłączenie gNB")
                            break
    for ax in axes.flat:
        ax.legend()
    fig.suptitle("Porównanie pojedynczej pary przebiegów — wynik modelu symulacyjnego")
    fig.savefig(directory / "comparison.png", dpi=180)
    fig.savefig(directory / "comparison.pdf")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--other", type=Path, nargs="*", default=[])
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory, args.other), ensure_ascii=False, indent=2))
    if args.plots:
        plot(args.directory, args.other)
