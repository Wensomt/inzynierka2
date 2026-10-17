from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from app.simulation.models import Simulation
from app.data.results_parser import ResultsParser


@dataclass(frozen=True)
class SavedConfiguration:
    """Lekki opis konfiguracji widoczny w bibliotece zapisów."""

    name: str
    simulation_count: int
    saved_at: str = ""


@dataclass(frozen=True)
class RunArtifact:
    """Jeden rzeczywisty plik należący do zapisanego runu."""

    relative_path: str
    size_bytes: int
    modified_at: str = ""


@dataclass(frozen=True)
class ArtifactPreview:
    """Ograniczony podgląd pliku, bez ładowania całych logów do GUI."""

    relative_path: str
    text: str
    truncated: bool = False
    binary: bool = False


@dataclass(frozen=True)
class DeletedRun:
    """Rekord runu przeniesionego do lokalnego kosza aplikacji."""

    trash_id: str
    simulation_id: str
    name: str
    status: str
    deleted_at: str


class SimulationStorage:
    """Trwały, prosty zapis niezależnych symulacji i konfiguracji."""

    _RUN_ID_PATTERN = re.compile(r"[0-9a-fA-F-]{8,64}\Z")
    _TRASH_ID_PATTERN = re.compile(r"[0-9a-fA-F-]{8,64}--[0-9A-Za-z-]{8,80}\Z")
    BUNDLE_MANIFEST = "oran-energy-lab-run.json"
    BUNDLE_FORMAT = "oran-energy-lab-run-bundle"
    BUNDLE_VERSION = 1
    MAX_BUNDLE_FILES = 10000
    MAX_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024
    ARTIFACT_PREVIEW_BYTES = 512 * 1024
    _RUN_ARTIFACT_DIRECTORIES = ("logs", "timeseries", "topology", "decisions", "raw")
    _RUN_RESULT_FILES = ("result.json", "summary.json", "summary.csv", "analysis.json")

    def __init__(self, root: Path) -> None:
        self.root = root
        self.configurations_root = root.parent / "configurations"
        self.exports_root = root.parent / "exports"
        self.deleted_runs_root = root.parent / "trash" / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.configurations_root.mkdir(parents=True, exist_ok=True)
        self.exports_root.mkdir(parents=True, exist_ok=True)
        self.deleted_runs_root.mkdir(parents=True, exist_ok=True)

    def create(self, simulation: Simulation) -> Path:
        directory = self.directory(simulation.id)
        directory.mkdir(parents=False, exist_ok=False)
        for name in ("logs", "timeseries", "topology", "decisions", "raw"):
            (directory / name).mkdir()
        simulation.log_path = str(directory / "logs" / "runner.log")
        self.save(simulation)
        return directory

    def save(self, simulation: Simulation) -> None:
        directory = self.directory(simulation.id)
        if not directory.is_dir():
            raise FileNotFoundError("Brak katalogu symulacji: {}".format(simulation.id))
        self._write_json(directory / "simulation.json", simulation.to_dict())
        self._write_json(directory / "configuration.json", simulation.configuration.to_dict())
        if simulation.result is not None:
            self._write_json(directory / "result.json", simulation.result.to_dict())

    def prepare_retry(self, simulation: Simulation, completed_attempt: int) -> Path:
        """Zachowuje artefakty nieudanej próby i przygotowuje czysty run.

        Automatyczny retry pozostaje tym samym logicalznym runem, ale nie
        może mieszać częściowych CSV ani logów z nową próbą.  Zamiast usuwać
        diagnostykę przenosimy dokładnie kontrolowane katalogi artefaktów do
        ``attempts/attempt-N`` i odtwarzamy ich puste odpowiedniki.
        """

        try:
            attempt = int(completed_attempt)
        except (TypeError, ValueError) as exc:
            raise ValueError("Numer ukończonej próby retry musi być dodatni") from exc
        if attempt < 1:
            raise ValueError("Numer ukończonej próby retry musi być dodatni")

        directory = self._existing_run_directory(simulation.id)
        attempts_root = directory / "attempts"
        if attempts_root.is_symlink():
            raise ValueError("Katalog prób symulacji nie może być linkiem symbolicznym")
        target = attempts_root / "attempt-{}".format(attempt)
        if target.exists() or target.is_symlink():
            raise FileExistsError("Archiwum próby {} już istnieje".format(attempt))

        for name in self._RUN_ARTIFACT_DIRECTORIES:
            source = directory / name
            if source.is_symlink():
                raise ValueError("Katalog artefaktów {} nie może być linkiem symbolicznym".format(name))
            if source.exists():
                if not source.is_dir():
                    raise ValueError("Katalog artefaktów {} ma niepoprawny typ".format(name))

        for name in self._RUN_RESULT_FILES:
            stale = directory / name
            if stale.is_symlink():
                raise ValueError("Plik wyniku {} nie może być linkiem symbolicznym".format(name))

        target.mkdir(parents=True, exist_ok=False)
        for name in self._RUN_ARTIFACT_DIRECTORIES:
            source = directory / name
            if source.exists():
                source.replace(target / name)
            source.mkdir(parents=False, exist_ok=False)

        for name in self._RUN_RESULT_FILES:
            stale = directory / name
            if stale.is_file():
                stale.unlink()

        simulation.result = None
        simulation.error = None
        simulation.log_path = str(directory / "logs" / "runner.log")
        self.save(simulation)
        return target

    def load(self, simulation_id: str) -> Simulation:
        path = self.directory(simulation_id) / "simulation.json"
        value = self._read_json(path)
        if not value:
            raise FileNotFoundError("Nie znaleziono zapisu symulacji: {}".format(simulation_id))
        simulation = Simulation.from_dict(value)
        return self._reparse_legacy_result(simulation)

    def history(self, limit: int = 500, finished_only: bool = False) -> List[Simulation]:
        entries: List[Simulation] = []
        for directory in self.root.iterdir() if self.root.is_dir() else []:
            value = self._read_json(directory / "simulation.json")
            if value:
                item = Simulation.from_dict(value)
                item = self._reparse_legacy_result(item)
                if finished_only and item.status.value not in {"COMPLETED", "FAILED", "STOPPED"}:
                    continue
                entries.append(item)
        return sorted(entries, key=lambda item: item.created_at, reverse=True)[:max(0, limit)]

    def _reparse_legacy_result(self, simulation: Simulation) -> Simulation:
        """Fill old result records from their preserved native artifacts."""

        if simulation.status.value not in {"COMPLETED", "FAILED", "STOPPED"}:
            return simulation
        directory = self.directory(simulation.id)
        if not directory.is_dir():
            return simulation
        old = simulation.result
        if old is None:
            has_artifacts = any(directory.glob("energyfilecell*.csv")) or any(
                (directory / "timeseries").glob("energyfilecell*.csv")
            )
            if not has_artifacts:
                return simulation
        else:
            try:
                parser_version = int(old.metadata.get("result_parser_version") or 0)
            except (TypeError, ValueError):
                parser_version = 0
            if parser_version >= ResultsParser.VERSION:
                return simulation
        parsed = ResultsParser().parse(directory)
        if old is not None:
            old_score = self._result_data_score(old)
            new_score = self._result_data_score(parsed)
            if new_score <= old_score:
                # Keep the richer historic metric payload, but carry forward
                # newly computed audit metadata and warnings (for example a
                # shortened common KPM/energy window) when the parser version
                # changes without adding another numeric series.
                old.metadata.update(parsed.metadata)
                old.warnings = list(dict.fromkeys(old.warnings + parsed.warnings))
                old.sources.update(
                    {
                        key: value
                        for key, value in parsed.sources.items()
                        if value and not old.sources.get(key)
                    }
                )
                old.metadata["result_parser_version"] = ResultsParser.VERSION
                self.save(simulation)
                return simulation
        simulation.result = parsed
        self.save(simulation)
        return simulation

    @staticmethod
    def _result_data_score(result: Any) -> int:
        metrics = getattr(result, "metrics", {}) or {}
        quality = getattr(result, "quality", {}) or {}
        series = getattr(result, "series", {}) or {}
        values = sum(value is not None for value in metrics.values())
        values += sum(value is not None for value in quality.values())
        return values * 1000 + sum(len(points or []) for points in series.values())

    def save_configuration(
        self,
        name: str,
        simulations: Iterable[Simulation],
        overwrite: bool = False,
    ) -> Path:
        """Zapisuje zestaw kart symulacji bez przypadkowego nadpisania innego zapisu."""

        clean_name = self._clean_configuration_name(name)
        path = self._configuration_path(clean_name)
        if path.exists() and not overwrite:
            raise FileExistsError(
                "Konfiguracja „{}” już istnieje. Wybierz inną nazwę albo użyj aktualizacji.".format(
                    clean_name
                )
            )
        payload = {
            "schema_version": 1,
            "name": clean_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "simulations": [
                {
                    "name": item.name,
                    "configuration": item.configuration.to_dict(),
                }
                for item in simulations
            ],
        }
        self._write_json(path, payload)
        return path

    def list_configurations(self) -> List[str]:
        return [item.name for item in self.list_configuration_records()]

    def list_configuration_records(self) -> List[SavedConfiguration]:
        """Zwraca metadane zapisów do listy w GUI, nie wymagając wpisywania nazwy."""

        records: List[SavedConfiguration] = []
        for path in self.configurations_root.glob("*.json"):
            if path.is_symlink():
                continue
            value = self._read_json(path)
            simulations = value.get("simulations") if isinstance(value, dict) else None
            if not isinstance(simulations, list):
                continue
            try:
                # Nazwa pliku jest identyfikatorem zapisu. Dzięki temu także
                # starszy plik z inną wartością pola "name" nadal da się otworzyć.
                name = self._clean_configuration_name(path.stem)
            except ValueError:
                continue
            records.append(
                SavedConfiguration(
                    name=name,
                    simulation_count=len(simulations),
                    saved_at=str(value.get("saved_at") or ""),
                )
            )
        return sorted(records, key=lambda item: item.name.casefold())

    def load_configuration(self, name: str) -> List[Simulation]:
        value = self._read_json(self._configuration_path(name))
        records = value.get("simulations") if isinstance(value, dict) else None
        if not isinstance(records, list):
            raise ValueError("Zapis konfiguracji jest niepoprawny")
        result = []
        for index, item in enumerate(records, start=1):
            if not isinstance(item, dict):
                continue
            result.append(Simulation.from_dict({
                "name": item.get("name") or "Symulacja {}".format(index),
                "configuration": item.get("configuration") or {},
            }))
        return result

    def delete_configuration(self, name: str) -> None:
        """Usuwa wyłącznie pojedynczy plik konfiguracji z kontrolowanego katalogu."""

        path = self._configuration_path(name)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("Nie znaleziono konfiguracji: {}".format(name))
        path.unlink()

    def export_configuration(self, name: str, destination: Path) -> Path:
        value = self._read_json(self._configuration_path(name))
        if not value:
            raise FileNotFoundError("Nie znaleziono konfiguracji: {}".format(name))
        path = Path(destination)
        self._write_json(path, value)
        return path

    def import_configuration(self, source: Path, name: Optional[str] = None) -> SavedConfiguration:
        """Importuje pojedynczy zapis i nadaje mu bezkolizyjną lokalną nazwę."""

        path = Path(source)
        value = self._read_json(path)
        records = value.get("simulations") if isinstance(value, dict) else None
        if not isinstance(records, list):
            raise ValueError("Plik konfiguracji nie zawiera listy symulacji.")
        simulations = self._simulations_from_configuration_records(records)
        if not simulations:
            raise ValueError("Plik konfiguracji nie zawiera poprawnych symulacji.")
        requested_name = name or str(value.get("name") or path.stem)
        unique_name = self._next_configuration_name(requested_name)
        self.save_configuration(unique_name, simulations)
        return next(
            item
            for item in self.list_configuration_records()
            if item.name == unique_name
        )

    def export_result_json(self, simulation: Simulation, destination: Optional[Path] = None) -> Path:
        path = destination or (self.exports_root / (self._export_stem(simulation) + ".json"))
        self._write_json(path, simulation.to_dict())
        return path

    def export_result_csv(self, simulation: Simulation, destination: Optional[Path] = None) -> Path:
        path = destination or (self.exports_root / (self._export_stem(simulation) + ".csv"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sekcja", "klucz", "wartość", "jednostka", "źródło"])
            for key, value in (
                ("id", simulation.id),
                ("nazwa", simulation.name),
                ("stan", simulation.status.value),
                ("utworzono", simulation.created_at),
                ("rozpoczęto", simulation.started_at or ""),
                ("zakończono", simulation.ended_at or ""),
                ("scenariusz", simulation.configuration.scenario),
                ("xapp", simulation.configuration.xapp),
            ):
                writer.writerow(["symulacja", key, value, "", ""])
            runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
            termination = runtime.get("termination")
            termination = termination if isinstance(termination, dict) else {}
            live = runtime.get("live")
            live = live if isinstance(live, dict) else {}
            live_metrics = live.get("current_metrics")
            live_metrics = live_metrics if isinstance(live_metrics, dict) else {}
            timing = runtime.get("queue_timing")
            timing = timing if isinstance(timing, dict) else {}
            partial = runtime.get("experiment_valid") is False or bool(termination.get("partial"))
            writer.writerow(["symulacja", "eksperyment_wazny", "nie" if partial else "tak", "", "runtime"])
            if termination.get("reason"):
                writer.writerow(["symulacja", "powod_zakonczenia", termination["reason"], "", "runtime"])
            if live.get("sim_time_s") is not None:
                writer.writerow(["telemetria_live", "ostatni_czas_symulowany_s", live["sim_time_s"], "s", "ostatnia próbka"])
            if timing.get("original_sim_time_s") is not None:
                writer.writerow(["symulacja", "docelowy_czas_symulacji_s", timing["original_sim_time_s"], "s", "konfiguracja kolejki"])
            if timing.get("per_run_target_active_real_s") is not None:
                writer.writerow(["symulacja", "limit_czasu_rzeczywistego_s", timing["per_run_target_active_real_s"], "s", "konfiguracja kolejki"])
            for key, value in simulation.configuration.parameters.items():
                writer.writerow(["konfiguracja", key, value, "", ""])
            writer.writerow(["symulacja", "błąd", simulation.error or "", "", ""])
            if simulation.result:
                units = {
                    "energy_efficiency_bit_per_j": "bit/J",
                    "energy_per_mbit_j": "J/Mbit",
                    "total_energy_j": "J",
                    "average_power_w": "W",
                    "throughput_mbit_per_s": "Mbit/s",
                    "delivered_mbit": "Mbit",
                    "delay_ms": "ms",
                    "packet_loss_pct": "%",
                    "mean_sinr_db": "dB",
                    "p05_sinr_db": "dB",
                    "mean_prb_pct": "%",
                    "active_ues": "UE",
                }
                for key, value in simulation.result.metrics.items():
                    writer.writerow([
                        "metryka",
                        key,
                        "" if value is None else value,
                        units.get(key, ""),
                        simulation.result.sources.get(key, ""),
                    ])
                for key, value in simulation.result.quality.items():
                    writer.writerow([
                        "jakość",
                        key,
                        "" if value is None else value,
                        units.get(key, ""),
                        simulation.result.sources.get(key, ""),
                    ])
                # Niektóre starsze przebiegi przerwano po stronie limitu
                # czasu, zanim ich scenariusz zapisał pełne KPI. Zachowujemy
                # wtedy ostatnią rzeczywistą telemetrię jako osobną sekcję,
                # jasno odróżnioną od końcowego wyniku eksperymentu.
                for key, value in live_metrics.items():
                    if key in simulation.result.metrics and simulation.result.metrics.get(key) is not None:
                        continue
                    if key in simulation.result.quality and simulation.result.quality.get(key) is not None:
                        continue
                    if value is None:
                        continue
                    source = "ostatnia próbka live przed zakończeniem"
                    kpm = live.get("kpm_live")
                    kpm = kpm if isinstance(kpm, dict) else {}
                    kpm_sources = kpm.get("metric_sources")
                    if isinstance(kpm_sources, dict) and kpm_sources.get(key):
                        source = "KPM: {} (ostatnia próbka przed zakończeniem)".format(kpm_sources[key])
                    writer.writerow([
                        "telemetria_live",
                        key,
                        value,
                        units.get(key, ""),
                        source,
                    ])
                for key, value in simulation.result.metadata.items():
                    writer.writerow([
                        "metadane", key,
                        json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value,
                        "s" if key.endswith("_s") else "", "",
                    ])
                for warning in simulation.result.warnings:
                    writer.writerow(["ostrzeżenie", "", warning, "", ""])
                has_final_data = any(value is not None for value in simulation.result.metrics.values()) or any(
                    value is not None for value in simulation.result.quality.values()
                ) or any(simulation.result.series.values())
                if not has_final_data:
                    writer.writerow([
                        "wynik",
                        "dane",
                        "Brak końcowych metryk i szeregów czasowych; sprawdź telemetrię live, artefakty i przyczynę zakończenia.",
                        "",
                        "",
                    ])
            else:
                writer.writerow([
                    "wynik",
                    "dane",
                    "Brak wyniku liczbowego. Sprawdź stan, błąd i log runu.",
                    "",
                    "",
                ])
        return path

    def export_result_timeseries_csv(
        self,
        simulation: Simulation,
        destination: Optional[Path] = None,
    ) -> Optional[Path]:
        """Eksportuje zapisane serie do prostego formatu z czasem w osobnej kolumnie."""

        result = simulation.result
        if result is None or not any(
            isinstance(points, list) and points for points in result.series.values()
        ):
            return None
        summary_path = Path(destination) if destination else self.exports_root / (
            self._export_stem(simulation) + ".csv"
        )
        path = summary_path.with_name(summary_path.stem + "-serie.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
        termination = runtime.get("termination")
        termination = termination if isinstance(termination, dict) else {}
        exported_status = simulation.status.value
        if runtime.get("experiment_valid") is False or termination.get("partial"):
            exported_status += " (częściowa)"
        units = {
            "energy_j": "J",
            "power_w": "W",
            "energy_efficiency_bit_per_j": "bit/J",
            "energy_per_mbit_j": "J/Mbit",
            "throughput_mbit_per_s": "Mbit/s",
            "delivered_mbit": "Mbit",
            "packet_loss_pct": "%",
            "delay_ms": "ms",
            "mean_prb_pct": "%",
            "p05_sinr_db": "dB",
            "mean_sinr_db": "dB",
            "active_ues": "UE",
        }
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "id_symulacji", "nazwa", "stan", "metryka", "czas_s", "wartość", "jednostka", "źródło",
            ])
            for key, points in result.series.items():
                if not isinstance(points, list):
                    continue
                for point in points:
                    if not isinstance(point, dict) or point.get("x") is None or point.get("y") is None:
                        continue
                    writer.writerow([
                        simulation.id,
                        simulation.name,
                        exported_status,
                        key,
                        point["x"],
                        point["y"],
                        units.get(key, ""),
                        result.sources.get(key, "artefakt runu"),
                    ])
        return path

    def export_comparison_csv(
        self,
        simulations: Iterable[Simulation],
        destination: Optional[Path] = None,
    ) -> Path:
        """Eksportuje porównywalne metryki i serie z wybranych runów."""

        items = list(simulations)
        if len(items) < 2:
            raise ValueError("Wybierz co najmniej dwie symulacje z zapisanym wynikiem.")
        if any(item.result is None for item in items):
            raise ValueError("Każda wybrana symulacja musi mieć zapisany wynik.")
        stamp = "-vs-".join(item.id[:8] for item in items[:3])
        path = Path(destination) if destination else self.exports_root / (stamp + "-porownanie.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        metric_units = {
            "energy_j": "J",
            "power_w": "W",
            "energy_efficiency_bit_per_j": "bit/J",
            "energy_per_mbit_j": "J/Mbit",
            "total_energy_j": "J",
            "average_power_w": "W",
            "throughput_mbit_per_s": "Mbit/s",
            "delivered_mbit": "Mbit",
            "delay_ms": "ms",
            "packet_loss_pct": "%",
            "mean_sinr_db": "dB",
            "p05_sinr_db": "dB",
            "mean_prb_pct": "%",
            "active_ues": "UE",
        }
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "typ", "id_symulacji", "nazwa", "stan", "scenariusz", "xapp",
                "metryka", "czas_s", "wartość", "jednostka", "źródło", "metadane",
            ])
            for item in items:
                result = item.result
                assert result is not None
                termination = item.runtime.get("termination") if isinstance(item.runtime, dict) else None
                termination = termination if isinstance(termination, dict) else {}
                partial = item.runtime.get("experiment_valid") is False or bool(termination.get("partial"))
                status = item.status.value + (" (częściowa)" if partial else "")
                common = [item.id, item.name, status, item.configuration.scenario, item.configuration.xapp]
                writer.writerow(["symulacja"] + common + ["", "", "", "", "", ""])
                for key, value in item.configuration.parameters.items():
                    writer.writerow(["konfiguracja"] + common + [key, "", value, "", "", ""])
                for section, values in (("metryka", result.metrics), ("jakość", result.quality)):
                    for key, value in values.items():
                        writer.writerow([
                            section, *common, key, "", "" if value is None else value,
                            metric_units.get(key, ""), result.sources.get(key, ""), "",
                        ])
                for key, points in result.series.items():
                    if not isinstance(points, list):
                        continue
                    for point in points:
                        if not isinstance(point, dict) or point.get("x") is None or point.get("y") is None:
                            continue
                        writer.writerow([
                            "seria", *common, key, point["x"], point["y"],
                            metric_units.get(key, ""), result.sources.get(key, "artefakt runu"), "",
                        ])
                for key, value in result.metadata.items():
                    writer.writerow([
                        "metadane", *common, key, "",
                        json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value,
                        "", "", "",
                    ])
                for warning in result.warnings:
                    writer.writerow(["ostrzeżenie", *common, "", "", warning, "", "", ""])
                live = item.runtime.get("live") if isinstance(item.runtime, dict) else None
                live = live if isinstance(live, dict) else {}
                if partial:
                    reason = str(termination.get("reason") or "nieznana przyczyna")
                    writer.writerow([
                        "ostrzeżenie", *common, "", "",
                        "Przebieg częściowy ({}); telemetria live nie jest końcowym wynikiem eksperymentu.".format(reason),
                        "", "", "",
                    ])
                if live.get("sim_time_s") is not None:
                    writer.writerow([
                        "metadane", *common, "ostatni_czas_symulowany_s", "",
                        live["sim_time_s"], "s", "runtime.live", "",
                    ])
                current_metrics = live.get("current_metrics")
                current_metrics = current_metrics if isinstance(current_metrics, dict) else {}
                for key, value in current_metrics.items():
                    if value is None or result.metrics.get(key) is not None or result.quality.get(key) is not None:
                        continue
                    kpm = live.get("kpm_live")
                    kpm = kpm if isinstance(kpm, dict) else {}
                    metric_sources = kpm.get("metric_sources")
                    source = "ostatnia próbka live przed zakończeniem"
                    if isinstance(metric_sources, dict) and metric_sources.get(key):
                        source = "KPM: {} (ostatnia próbka przed zakończeniem)".format(metric_sources[key])
                    writer.writerow([
                        "telemetria_live", *common, key, live.get("sim_time_s", ""), value,
                        metric_units.get(key, ""), source, "",
                    ])
                has_numeric_data = any(value is not None for value in result.metrics.values()) or any(
                    value is not None for value in result.quality.values()
                ) or any(result.series.values()) or any(value is not None for value in current_metrics.values())
                if not has_numeric_data:
                    writer.writerow([
                        "brak_danych_porownawczych", *common, "", "", "Brak metryk liczbowych", "", "", "",
                    ])
        return path

    def list_run_artifacts(self, simulation_id: str) -> List[RunArtifact]:
        """Zwraca pliki realnie zapisane dla jednego runu, bez podążania za linkami."""

        directory = self._existing_run_directory(simulation_id)
        entries: List[RunArtifact] = []
        for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.relative_to(directory)
                stat_result = path.stat()
            except (OSError, ValueError):
                continue
            entries.append(
                RunArtifact(
                    relative_path=relative.as_posix(),
                    size_bytes=int(stat_result.st_size),
                    modified_at=datetime.fromtimestamp(
                        stat_result.st_mtime, timezone.utc
                    ).isoformat(),
                )
            )
        return entries

    def read_run_artifact(
        self,
        simulation_id: str,
        relative_path: str,
        maximum_bytes: Optional[int] = None,
    ) -> ArtifactPreview:
        """Czyta ograniczony podgląd tekstowego artefaktu runu.

        Widok nie otwiera całych logów ani plików binarnych.  Ścieżka jest
        zawsze weryfikowana względem katalogu dokładnie jednego runu.
        """

        directory = self._existing_run_directory(simulation_id)
        relative = self._safe_relative_artifact_path(relative_path)
        path = (directory / Path(*relative.parts)).resolve()
        if not self._is_within(path, directory) or path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Nie znaleziono pliku runu: {}".format(relative_path))
        limit = maximum_bytes if maximum_bytes is not None else self.ARTIFACT_PREVIEW_BYTES
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = self.ARTIFACT_PREVIEW_BYTES
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        truncated = len(data) > limit
        if truncated:
            data = data[:limit]
        if b"\x00" in data:
            return ArtifactPreview(
                relative_path=relative.as_posix(),
                text="Podgląd binarny nie jest dostępny w aplikacji.",
                truncated=truncated,
                binary=True,
            )
        text = data.decode("utf-8", errors="replace")
        if truncated:
            text += "\n\n[Podgląd ograniczony do {} KiB]".format(max(1, limit // 1024))
        return ArtifactPreview(
            relative_path=relative.as_posix(),
            text=text,
            truncated=truncated,
        )

    def export_result_bundle(
        self,
        simulation: Simulation,
        destination: Optional[Path] = None,
    ) -> Path:
        """Eksportuje pełny, przenośny ZIP z wynikami, seriami i logami runu."""

        directory = self._existing_run_directory(simulation.id)
        # Eksport opisuje ten sam stan, który użytkownik widzi w liście
        # wyników; nie zostawiamy w ZIP-ie starszego simulation.json.
        self.save(simulation)
        path = Path(destination) if destination else self.exports_root / (
            self._export_stem(simulation) + ".zip"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        artifacts = self.list_run_artifacts(simulation.id)
        manifest = {
            "format": self.BUNDLE_FORMAT,
            "version": self.BUNDLE_VERSION,
            "simulation_id": simulation.id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "path": item.relative_path,
                    "size_bytes": item.size_bytes,
                    "sha256": self._sha256_file(directory / Path(*PurePosixPath(item.relative_path).parts)),
                }
                for item in artifacts
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        os.close(descriptor)
        try:
            with zipfile.ZipFile(
                temporary_name,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(
                    self.BUNDLE_MANIFEST,
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
                for item in artifacts:
                    source = directory / Path(*PurePosixPath(item.relative_path).parts)
                    archive.write(source, arcname="{}/{}".format(simulation.id, item.relative_path))
            Path(temporary_name).replace(path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return path

    def import_result_bundle(self, source: Path) -> Simulation:
        """Bezpiecznie importuje archiwum wyeksportowane przez to laboratorium.

        Import nie wypakowuje niczego poza katalogiem kontrolowanym przez
        aplikację. Przy kolizji identyfikatora zachowuje dotychczasowy run i
        nadaje importowanemu nowy identyfikator.
        """

        bundle = Path(source)
        if not bundle.is_file():
            raise FileNotFoundError("Nie znaleziono archiwum: {}".format(bundle))
        try:
            with zipfile.ZipFile(bundle, "r") as archive:
                simulation, source_id, members = self._validate_bundle(archive)
                target_id = source_id if not self.directory(source_id).exists() else self._new_run_id()
                temporary_directory = Path(
                    tempfile.mkdtemp(prefix="oran-run-import-", dir=str(self.root.parent))
                )
                extracted = temporary_directory / source_id
                try:
                    extracted.mkdir(parents=True, exist_ok=False)
                    for info, relative, expected_hash in members:
                        target = extracted / Path(*relative.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        digest = hashlib.sha256()
                        with archive.open(info, "r") as input_handle, target.open("wb") as output_handle:
                            while True:
                                chunk = input_handle.read(1024 * 1024)
                                if not chunk:
                                    break
                                digest.update(chunk)
                                output_handle.write(chunk)
                        if digest.hexdigest() != expected_hash:
                            raise ValueError("Archiwum ma niezgodną sumę kontrolną pliku {}.".format(
                                relative.as_posix()
                            ))
                    simulation.id = target_id
                    target_directory = self.directory(target_id)
                    simulation.log_path = str(target_directory / "logs" / "runner.log")
                    self._write_json(extracted / "simulation.json", simulation.to_dict())
                    self._write_json(extracted / "configuration.json", simulation.configuration.to_dict())
                    if simulation.result is not None:
                        self._write_json(extracted / "result.json", simulation.result.to_dict())
                    shutil.move(str(extracted), str(target_directory))
                    return simulation
                finally:
                    shutil.rmtree(str(temporary_directory), ignore_errors=True)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ValueError("Nie można odczytać archiwum wyników: {}".format(exc)) from exc

    def delete(self, simulation_id: str) -> DeletedRun:
        """Przenosi pojedynczy run do kosza aplikacji zamiast usuwać go trwale."""

        directory = self._existing_run_directory(simulation_id)
        simulation = self.load(simulation_id)
        deleted_at = datetime.now(timezone.utc).isoformat()
        trash_id = "{}--{}".format(
            simulation_id,
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        )
        target = self._deleted_run_directory(trash_id)
        if target.exists():
            target = self._deleted_run_directory("{}--{}".format(simulation_id, uuid4().hex))
        shutil.move(str(directory), str(target))
        self._write_json(
            target / ".deleted-run.json",
            {
                "trash_id": target.name,
                "simulation_id": simulation.id,
                "name": simulation.name,
                "status": simulation.status.value,
                "deleted_at": deleted_at,
            },
        )
        return DeletedRun(
            trash_id=target.name,
            simulation_id=simulation.id,
            name=simulation.name,
            status=simulation.status.value,
            deleted_at=deleted_at,
        )

    def deleted_runs(self) -> List[DeletedRun]:
        """Lista runów dostępnych do przywrócenia z kosza aplikacji."""

        result: List[DeletedRun] = []
        for directory in self.deleted_runs_root.iterdir() if self.deleted_runs_root.is_dir() else []:
            if not directory.is_dir() or directory.is_symlink():
                continue
            record = self._read_json(directory / ".deleted-run.json")
            simulation_id = str(record.get("simulation_id") or "")
            trash_id = str(record.get("trash_id") or directory.name)
            if (
                not self._RUN_ID_PATTERN.fullmatch(simulation_id)
                or trash_id != directory.name
                or not self._TRASH_ID_PATTERN.fullmatch(trash_id)
            ):
                continue
            result.append(
                DeletedRun(
                    trash_id=trash_id,
                    simulation_id=simulation_id,
                    name=str(record.get("name") or "Symulacja"),
                    status=str(record.get("status") or ""),
                    deleted_at=str(record.get("deleted_at") or ""),
                )
            )
        return sorted(result, key=lambda item: item.deleted_at, reverse=True)

    def restore_deleted_run(self, trash_id: str) -> Simulation:
        """Przywraca run z kosza; istniejącego runu nigdy nie nadpisuje."""

        directory = self._deleted_run_directory(trash_id)
        if not directory.is_dir() or directory.is_symlink():
            raise FileNotFoundError("Nie znaleziono usuniętego runu")
        record = self._read_json(directory / ".deleted-run.json")
        simulation_id = str(record.get("simulation_id") or "")
        if not self._RUN_ID_PATTERN.fullmatch(simulation_id):
            raise ValueError("Zapis kosza nie ma poprawnego identyfikatora runu.")
        simulation = Simulation.from_dict(self._read_json(directory / "simulation.json"))
        if simulation.id != simulation_id:
            raise ValueError("Zapis kosza nie odpowiada katalogowi runu.")
        target_id = simulation_id if not self.directory(simulation_id).exists() else self._new_run_id()
        target = self.directory(target_id)
        shutil.move(str(directory), str(target))
        try:
            simulation.id = target_id
            simulation.log_path = str(target / "logs" / "runner.log")
            self.save(simulation)
            (target / ".deleted-run.json").unlink(missing_ok=True)
        except Exception:
            # Katalog został już przeniesiony do bezpiecznego miejsca. Nie
            # wykonujemy drugiego ruchu, aby nie zgubić danych runu.
            raise
        return simulation

    def directory(self, simulation_id: str) -> Path:
        safe_id = str(simulation_id or "")
        if not self._RUN_ID_PATTERN.fullmatch(safe_id):
            raise ValueError("Niepoprawny identyfikator symulacji")
        root = self.root.resolve()
        candidate = (root / safe_id).resolve()
        if candidate.parent != root:
            raise ValueError("Niepoprawna ścieżka zapisu symulacji")
        return candidate

    def _existing_run_directory(self, simulation_id: str) -> Path:
        directory = self.directory(simulation_id)
        if not directory.is_dir() or directory.is_symlink():
            raise FileNotFoundError("Nie znaleziono zapisu symulacji: {}".format(simulation_id))
        metadata = self._read_json(directory / "simulation.json")
        if str(metadata.get("id") or "") != str(simulation_id):
            raise ValueError("Katalog nie należy do wskazanej symulacji.")
        return directory

    def _deleted_run_directory(self, trash_id: str) -> Path:
        safe_id = str(trash_id or "")
        if not self._TRASH_ID_PATTERN.fullmatch(safe_id):
            raise ValueError("Niepoprawny identyfikator kosza")
        root = self.deleted_runs_root.resolve()
        candidate = (root / safe_id).resolve()
        if candidate.parent != root:
            raise ValueError("Niepoprawna ścieżka kosza")
        return candidate

    def _new_run_id(self) -> str:
        while True:
            candidate = str(uuid4())
            if not self.directory(candidate).exists():
                return candidate

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return candidate.resolve() != root.resolve()

    @staticmethod
    def _safe_relative_artifact_path(value: str) -> PurePosixPath:
        raw = str(value or "")
        if not raw or raw.startswith("/") or "\\" in raw:
            raise ValueError("Niepoprawna ścieżka pliku wyników")
        path = PurePosixPath(raw)
        if path.is_absolute() or not path.parts:
            raise ValueError("Niepoprawna ścieżka pliku wyników")
        if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
            raise ValueError("Niepoprawna ścieżka pliku wyników")
        return path

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_bundle(self, archive: zipfile.ZipFile) -> Any:
        infos = archive.infolist()
        if len(infos) > self.MAX_BUNDLE_FILES + 1:
            raise ValueError("Archiwum zawiera za dużo plików.")
        names = set()
        total_size = 0
        manifest_info = None
        regular_infos = []
        for info in infos:
            if info.filename in names:
                raise ValueError("Archiwum zawiera powieloną nazwę pliku.")
            names.add(info.filename)
            if info.flag_bits & 0x1:
                raise ValueError("Szyfrowane archiwum nie jest obsługiwane.")
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise ValueError("Archiwum nie może zawierać linków symbolicznych.")
            if info.filename == self.BUNDLE_MANIFEST:
                if info.is_dir():
                    raise ValueError("Manifest archiwum jest niepoprawny.")
                manifest_info = info
                continue
            if info.is_dir():
                raise ValueError("Archiwum nie może zawierać pustych katalogów.")
            total_size += int(info.file_size)
            if total_size > self.MAX_BUNDLE_BYTES:
                raise ValueError("Archiwum przekracza limit importu.")
            regular_infos.append(info)
        if manifest_info is None:
            raise ValueError("Archiwum nie ma manifestu O-RAN Energy Lab.")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Manifest archiwum jest niepoprawny.") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Manifest archiwum jest niepoprawny.")
        if manifest.get("format") != self.BUNDLE_FORMAT or manifest.get("version") != self.BUNDLE_VERSION:
            raise ValueError("To nie jest obsługiwane archiwum wyników.")
        source_id = str(manifest.get("simulation_id") or "")
        if not self._RUN_ID_PATTERN.fullmatch(source_id):
            raise ValueError("Manifest nie ma poprawnego identyfikatora runu.")
        raw_files = manifest.get("files")
        if not isinstance(raw_files, list) or len(raw_files) != len(regular_infos):
            raise ValueError("Lista plików archiwum jest niepoprawna.")
        expected = {}
        for item in raw_files:
            if not isinstance(item, dict):
                raise ValueError("Lista plików archiwum jest niepoprawna.")
            relative = self._safe_relative_artifact_path(str(item.get("path") or ""))
            name = relative.as_posix()
            digest = str(item.get("sha256") or "").lower()
            if name in expected or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Lista plików archiwum jest niepoprawna.")
            try:
                size = int(item.get("size_bytes"))
            except (TypeError, ValueError):
                raise ValueError("Lista plików archiwum jest niepoprawna.")
            if size < 0:
                raise ValueError("Lista plików archiwum jest niepoprawna.")
            expected[name] = (size, digest)
        members = []
        for info in regular_infos:
            relative = self._bundle_relative_path(info.filename, source_id)
            name = relative.as_posix()
            expected_item = expected.get(name)
            if expected_item is None or int(info.file_size) != expected_item[0]:
                raise ValueError("Lista plików archiwum nie zgadza się z zawartością.")
            members.append((info, relative, expected_item[1]))
        if set(expected) != {relative.as_posix() for _, relative, _ in members}:
            raise ValueError("Lista plików archiwum nie zgadza się z zawartością.")
        simulation_member = next(
            (info for info, relative, _ in members if relative.as_posix() == "simulation.json"),
            None,
        )
        if simulation_member is None:
            raise ValueError("Archiwum nie zawiera opisu symulacji.")
        try:
            simulation_value = json.loads(archive.read(simulation_member).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Opis symulacji w archiwum jest niepoprawny.") from exc
        if not isinstance(simulation_value, dict):
            raise ValueError("Opis symulacji w archiwum jest niepoprawny.")
        simulation = Simulation.from_dict(simulation_value)
        if simulation.id != source_id:
            raise ValueError("Opis symulacji nie zgadza się z manifestem.")
        return simulation, source_id, members

    def _bundle_relative_path(self, name: str, source_id: str) -> PurePosixPath:
        if not name or name.startswith("/") or "\\" in name:
            raise ValueError("Archiwum zawiera niepoprawną ścieżkę.")
        path = PurePosixPath(name)
        if path.is_absolute() or len(path.parts) < 2 or path.parts[0] != source_id:
            raise ValueError("Archiwum zawiera niepoprawną ścieżkę.")
        return self._safe_relative_artifact_path("/".join(path.parts[1:]))

    @staticmethod
    def _export_stem(simulation: Simulation) -> str:
        name = "".join(char for char in simulation.name if char.isalnum() or char in " -_").strip()
        return "{}-{}-wynik".format(name or "symulacja", simulation.id[:8])

    @staticmethod
    def _clean_configuration_name(name: str) -> str:
        raw_name = " ".join(str(name or "").strip().split())
        clean_name = "".join(
            char for char in raw_name if char.isalnum() or char in " -_"
        ).strip()
        if not clean_name:
            raise ValueError("Podaj nazwę konfiguracji")
        if clean_name != raw_name:
            raise ValueError("Nazwa konfiguracji może zawierać tylko litery, cyfry, spacje, - i _")
        if len(clean_name) > 120:
            raise ValueError("Nazwa konfiguracji może mieć najwyżej 120 znaków")
        return clean_name

    def _configuration_path(self, name: str) -> Path:
        clean_name = self._clean_configuration_name(name)
        root = self.configurations_root.resolve()
        candidate = (root / (clean_name + ".json")).resolve()
        if candidate.parent != root:
            raise ValueError("Niepoprawna ścieżka konfiguracji")
        return candidate

    def _next_configuration_name(self, name: str) -> str:
        base = self._clean_configuration_name(name)
        candidate = base
        index = 2
        while self._configuration_path(candidate).exists():
            suffix = " {}".format(index)
            candidate = "{}{}".format(base[: 120 - len(suffix)].rstrip(), suffix)
            index += 1
        return candidate

    @staticmethod
    def _simulations_from_configuration_records(records: Iterable[Any]) -> List[Simulation]:
        result: List[Simulation] = []
        for index, item in enumerate(records, start=1):
            if not isinstance(item, dict):
                continue
            configuration = item.get("configuration") or {}
            if not isinstance(configuration, dict):
                continue
            result.append(
                Simulation.from_dict(
                    {
                        "name": item.get("name") or "Symulacja {}".format(index),
                        "configuration": configuration,
                    }
                )
            )
        return result

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_json(path: Path, value: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        handle, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            Path(temporary_name).replace(path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
