from __future__ import annotations

import csv
import json
import math
import re
from bisect import bisect_right
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.simulation.models import SimulationResult
from app.simulation.kpm_live import KpmLiveCollector


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
            return [
                {
                    str(key).strip(): "" if value is None else str(value).strip()
                    for key, value in row.items()
                    if key is not None
                }
                for row in csv.DictReader(handle)
                # An interrupted writer can leave a truncated final record.
                # Never replace a complete measurement with such a record.
                if None not in row and all(value is not None for value in row.values())
            ]
    except (OSError, csv.Error):
        return []


class ResultsParser:
    """Czyta zapisane artefakty; wyprowadza tylko wartości obliczalne z nich."""

    VERSION = 6

    def parse(self, directory: Path) -> SimulationResult:
        summary = self._read_json(directory / "summary.json")
        analysis = self._read_json(directory / "analysis.json")
        series_rows = _read_csv(self._path(directory, "ee_timeseries.csv", "timeseries"))
        summary_rows = _read_csv(self._path(directory, "ee_summary.csv", "timeseries"))
        # This scenario's CSV names the measured endpoint simTime_s. Do not
        # infer endpoints from a global configured horizon or overwrite an
        # explicit (possibly invalid) endpoint. Raw files remain untouched.
        for row in summary_rows:
            if (row.get("simulator_source") == "mmwave_LENA_oran_custom"
                    and "measurement_end_s" not in row and "simTime_s" in row):
                row["measurement_end_s"] = row["simTime_s"]
        # ``metrics.csv`` bywa zapisywany zarówno w katalogu głównym starszego
        # wyniku, jak i w ``timeseries`` nowszego archiwum.  Jest to prawdziwy
        # strumień KPI scenariusza, nie dane zastępcze tworzone przez GUI.
        local_rows = _read_csv(self._path(directory, "metrics.csv", "timeseries"))
        legacy_summary_rows = _read_csv(directory / "summary.csv")
        native = self._native_artifacts(directory)

        metrics: Dict[str, Optional[float]] = {
            "energy_efficiency_bit_per_j": None,
            "energy_per_mbit_j": None,
            "total_energy_j": None,
            "average_power_w": None,
            "throughput_mbit_per_s": None,
            "delivered_mbit": None,
            "delay_ms": None,
            "packet_loss_pct": None,
        }
        quality: Dict[str, Optional[float]] = {
            "mean_sinr_db": None,
            "p05_sinr_db": None,
            "mean_prb_pct": None,
            "active_ues": None,
        }
        sources: Dict[str, str] = {}

        # Podsumowanie scenariusza jest jego końcową definicją wyniku.  Szereg
        # czasowy służy jako źródło zastępcze i do wykresów; może obejmować
        # inny okres niż dane podsumowujące.
        accounting = (
            "total_energy_j", "delivered_mbit", "average_power_w",
            "throughput_mbit_per_s", "energy_efficiency_bit_per_j", "energy_per_mbit_j",
        )
        candidates = (
            lambda m, q, s: self._from_summary(summary, m, q, s),
            lambda m, q, s: self._from_analysis(analysis, m, q, s),
            lambda m, q, s: self._from_legacy_summary_rows(legacy_summary_rows, m, q, s),
            lambda m, q, s: self._from_ee_summary_rows(summary_rows, m, s),
            lambda m, q, s: self._from_ee_rows(series_rows, m, s),
            lambda m, q, s: self._from_local_rows(local_rows, m, q, s),
            lambda m, q, s: self._from_energy_files(native, m, q, s),
        )
        candidate_payloads = (
            summary,
            analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {},
            {str(row.get("metric") or ""): row.get("value") for row in legacy_summary_rows},
            summary_rows[-1] if summary_rows else {},
            series_rows[-1] if series_rows else {},
            local_rows[-1] if local_rows else {},
            native.get("metadata", {}),
        )
        selected_accounting = None
        metric_origins: Dict[str, int] = {}
        quality_origins: Dict[str, int] = {}
        coherent_accounting = False
        audit_warnings: List[str] = []
        for index, populate in enumerate(candidates):
            candidate = dict.fromkeys(metrics)
            candidate_quality = dict.fromkeys(quality)
            candidate_sources: Dict[str, str] = {}
            populate(candidate, candidate_quality, candidate_sources)
            self._validate(candidate, audit_warnings)
            self._complete_derived(candidate, candidate_sources, audit_warnings)
            complete = all(candidate.get(key) is not None for key in ("total_energy_j", "delivered_mbit"))
            if complete and not coherent_accounting:
                # A ratio is meaningful only for energy and useful data from
                # the same observation window. Prefer a complete source as a
                # unit over mixing individual fields from different files.
                for key in accounting:
                    metrics[key] = candidate[key]
                    metric_origins[key] = index
                    sources.pop(key, None)
                    if key in candidate_sources:
                        sources[key] = candidate_sources[key]
                coherent_accounting = True
                selected_accounting = index
            for key, value in candidate.items():
                if key in accounting and coherent_accounting:
                    continue
                if metrics[key] is None and key not in sources and (value is not None or key in candidate_sources):
                    metrics[key] = value
                    metric_origins[key] = index
                    sources[key] = candidate_sources.get(key, "")
            for key, value in candidate_quality.items():
                if quality[key] is None and value is not None:
                    quality[key] = value
                    quality_origins[key] = index
                    sources[key] = candidate_sources.get(key, "")

        series = self._series(series_rows)
        # Część starszych przebiegów nie ma ``ee_timeseries.csv``, choć ich
        # ``metrics.csv`` zawiera kompletną serię energii i ruchu.  Gdy plik
        # energii istnieje tylko częściowo, uzupełniamy wyłącznie brakujące
        # serie, aby nie nadpisywać bardziej szczegółowych próbek.
        series = self._fill_missing_series(series, self._series(local_rows))
        for key, points in dict(native.get("series") or {}).items():
            if not series.get(key) and points:
                series[key] = list(points)
        metadata = (self._metadata(candidate_payloads[selected_accounting], {}, [])
                    if selected_accounting is not None else self._metadata(summary, analysis, summary_rows))
        metadata["measurement_consistent"] = coherent_accounting
        if selected_accounting is not None:
            metadata["accounting_source"] = sources.get("total_energy_j")
            primary_payload = candidate_payloads[selected_accounting]
            for key in ("delay_ms", "packet_loss_pct"):
                origin = metric_origins.get(key)
                if origin is None or origin == selected_accounting:
                    continue
                other = candidate_payloads[origin]
                duration, other_duration = self._duration(primary_payload), self._duration(other)
                start = self._first(primary_payload, ("measurement_start_s", "MeasurementStart_s"))
                other_start = self._first(other, ("measurement_start_s", "MeasurementStart_s"))
                if (not self._valid_window(other) or duration is None or other_duration is None or duration != other_duration or start != other_start
                    or (key == "packet_loss_pct" and primary_payload.get("packet_loss_definition") != other.get("packet_loss_definition"))):
                    metadata["measurement_consistent"] = False
                    audit_warnings.append("{} pochodzi z innego lub nieokreślonego okna/definicji niż energia i dane; nie kwalifikuje się do wspólnej oceny EE/QoS.".format(key))
            for key in ("mean_sinr_db", "p05_sinr_db", "mean_prb_pct", "active_ues"):
                origin = quality_origins.get(key)
                if origin is None or origin == selected_accounting:
                    continue
                other = candidate_payloads[origin]
                duration, other_duration = self._duration(primary_payload), self._duration(other)
                start = self._first(primary_payload, ("measurement_start_s", "MeasurementStart_s"))
                other_start = self._first(other, ("measurement_start_s", "MeasurementStart_s"))
                if duration is None or other_duration is None or duration != other_duration or start != other_start:
                    metadata["quality_measurement_consistent"] = False
                    audit_warnings.append("{} pochodzi z innego lub nieokreślonego okna pomiarowego niż energia i dane.".format(key))
        if native.get("metadata") and selected_accounting in (None, len(candidates) - 1):
            metadata.update(native["metadata"])
            metadata["measurement_consistent"] = coherent_accounting
            if coherent_accounting:
                metadata["accounting_source"] = sources.get("total_energy_j")
        if selected_accounting is not None and not self._valid_window(candidate_payloads[selected_accounting]):
            metadata["measurement_consistent"] = False
            audit_warnings.append("Niepełne, nieskończone lub sprzeczne okno pomiarowe energii i danych.")
        # Validate the counters from the sources actually used, not a spare
        # file whose fields happened to be readable. Keep raw evidence even
        # when numeric conversion rejects NaN/inf or fractional counts.
        used_origins = {selected_accounting, metric_origins.get("delay_ms"), metric_origins.get("packet_loss_pct")}
        invalid_counters = []
        for origin in sorted(index for index in used_origins if index is not None):
            payload = candidate_payloads[origin]
            raw = {}
            for key, aliases in (("tx_packets", ("tx_packets", "TxPackets", "txPackets")),
                                 ("rx_packets", ("rx_packets", "RxPackets", "rxPackets")),
                                 ("lost_packets", ("lost_packets",))):
                for alias in aliases:
                    if alias in payload:
                        raw[key] = payload[alias]
                        break
            if not raw:
                continue
            if origin == selected_accounting:
                metadata["raw_packet_counters"] = raw
            counts = {key: _number(value) for key, value in raw.items()}
            issues = [key for key, value in counts.items()
                      if value is None or value < 0 or not value.is_integer() or isinstance(raw[key], bool)]
            tx, rx = counts.get("tx_packets"), counts.get("rx_packets")
            if tx is not None and rx is not None and rx > tx:
                issues.append("rx_packets > tx_packets")
            if not issues and tx is not None and rx is not None:
                lost = counts.get("lost_packets")
                if lost is not None and lost != tx - rx:
                    issues.append("lost_packets != tx_packets - rx_packets")
                # C++ CSV defaults to six significant digits; this is only
                # serialization roundoff, never a QoS comparison tolerance.
                declared_loss = self._first(payload, ("packet_loss_pct", "loss_pct", "packet_loss_percent", "PacketLoss_pct"))
                if declared_loss is not None and tx > 0 and not math.isclose(declared_loss, 100 * (tx-rx) / tx, rel_tol=1e-5, abs_tol=1e-6):
                    issues.append("packet_loss_pct contradicts counters")
                if rx == 0 and (metrics.get("delivered_mbit") or 0) > 0:
                    issues.append("positive delivered data with zero received packets")
            if issues:
                invalid_counters.extend(issues)
        if invalid_counters:
            metadata["invalid_packet_counters"] = invalid_counters
            metadata["measurement_consistent"] = False
            audit_warnings.append("Niepoprawne liczniki pakietów: " + ", ".join(invalid_counters))
        metadata["result_parser_version"] = self.VERSION
        if metadata.get("rx_packets") == 0 and (metrics.get("delivered_mbit") or 0) > 0 and metadata.get("data_layer") == "udp_application_payload":
            metrics["packet_loss_pct"] = None
            metrics["delay_ms"] = None
            metadata["measurement_consistent"] = False
            audit_warnings.append("Odbiornik UDP otrzymał dane, ale licznik QoS podaje rx=0. Historyczny pomiar strat i opóźnienia jest niewiarygodny; uruchom ponownie aktualny scenariusz.")
        warnings = self._warnings(metrics) + list(dict.fromkeys(audit_warnings))
        if metadata.get("simulator_source") == "SOURCE_LOCAL_DISCRETE_SIMULATOR":
            warnings.append("Historyczny wynik uproszczonego modelu lokalnego; nie jest pomiarem symulatora ns-3.")
        if metadata.get("measurement_duration_s") is None:
            warnings.append("Brak jawnego opisu okna pomiarowego w podsumowaniu; szeregi skumulowane przyjmują początek t=0.")
        energy_skew = _number(metadata.get("energy_cell_max_timestamp_skew_s"))
        if energy_skew is not None and energy_skew > 1e-6:
            warnings.append(
                "Pliki energii komórek kończą się w różnych chwilach (różnica {:.4g} s). Nie wyznaczono z nich końcowej energii ani EE.".format(
                    energy_skew
                )
            )
        if str(metadata.get("data_layer") or "").startswith("KPM PDCP"):
            warnings.append("Dostarczone dane są estymatą z całki bitrate'u PDCP; Orange nie zapisuje w tym przebiegu licznika odebranych bajtów IP.")
            configuration = self._read_json(directory / "configuration.json")
            parameters = configuration.get("parameters")
            parameters = parameters if isinstance(parameters, dict) else {}
            configured_duration = _number(parameters.get("simTime"))
            measured_duration = _number(metadata.get("measurement_duration_s"))
            if (
                configured_duration is not None
                and configured_duration > 0
                and measured_duration is not None
                and measured_duration + 1e-9 < configured_duration
            ):
                coverage_pct = 100.0 * measured_duration / configured_duration
                omitted_duration = configured_duration - measured_duration
                metadata["configured_simulation_duration_s"] = configured_duration
                metadata["measurement_coverage_pct"] = coverage_pct
                warnings.append(
                    "Wspólne okno EE/KPM obejmuje {:.4g} s z ustawionych {:.4g} s ({:.1f}%); końcowe {:.4g} s bez próbki KPM nie weszło do obliczeń."
                    .format(
                        measured_duration,
                        configured_duration,
                        coverage_pct,
                        omitted_duration,
                    )
                )
        return SimulationResult(
            metrics=metrics,
            quality=quality,
            series=series,
            sources=sources,
            warnings=warnings,
            metadata=metadata,
        )

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _path(directory: Path, name: str, group: str) -> Path:
        candidates = (directory / group / name, directory / name)
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])

    def _from_summary(
        self,
        summary: Dict[str, Any],
        metrics: Dict[str, Optional[float]],
        quality: Dict[str, Optional[float]],
        sources: Dict[str, str],
        source_name: str = "summary.json",
    ) -> None:
        if not summary:
            return
        self._set(metrics, "total_energy_j", summary, ("total_energy_j", "totalEnergy_J", "TotalEnergy_J"), sources, source_name)
        self._set(metrics, "average_power_w", summary, ("average_power_w",), sources, source_name)
        self._set(metrics, "delivered_mbit", summary, ("delivered_mbit",), sources, source_name)
        delivered = self._first(summary, ("delivered_bits", "deliveredData_bits", "DeliveredBits"))
        if metrics["delivered_mbit"] is None and delivered is not None:
            metrics["delivered_mbit"] = delivered / 1e6
            sources["delivered_mbit"] = source_name
        self._set(metrics, "energy_efficiency_bit_per_j", summary, ("ee_bit_per_j", "cumulative_ee_bit_per_j", "EE_bit_per_J"), sources, source_name)
        self._set(metrics, "energy_per_mbit_j", summary, ("energy_cost_j_per_mbit", "j_per_mbit", "energy_per_mbit_j"), sources, source_name)
        throughput = self._first(summary, ("average_throughput_bps",))
        if metrics["throughput_mbit_per_s"] is None and throughput is not None:
            metrics["throughput_mbit_per_s"] = throughput / 1e6
            sources["throughput_mbit_per_s"] = source_name
        self._set(metrics, "throughput_mbit_per_s", summary, ("avg_throughput_mbps", "throughput_mbit_per_s"), sources, source_name)
        duration = self._duration(summary)
        if duration is not None and duration > 0:
            for key, numerator in (("average_power_w", metrics["total_energy_j"]), ("throughput_mbit_per_s", metrics["delivered_mbit"])):
                if numerator is not None:
                    metrics[key] = numerator / duration
                    sources[key] = source_name + " (cumulative total / measurement duration)"
        self._set(metrics, "delay_ms", summary, ("delay_ms", "mean_delay_ms", "average_delay_ms", "avg_delay_ms", "MeanDelay_ms"), sources, source_name)
        self._set(metrics, "packet_loss_pct", summary, ("packet_loss_pct", "loss_pct", "packet_loss_percent", "PacketLoss_pct"), sources, source_name)
        self._from_packet_counters(summary, metrics, sources, source_name, final=True)
        self._set(quality, "mean_sinr_db", summary, ("mean_sinr_db",), sources, source_name)
        self._set(quality, "p05_sinr_db", summary, ("p05_sinr_db",), sources, source_name)
        self._set(quality, "mean_prb_pct", summary, ("mean_prb_pct", "mean_prb_utilization_pct"), sources, source_name)
        self._set(quality, "active_ues", summary, ("mean_active_ues", "active_ues"), sources, source_name)
        qos = summary.get("qos")
        if isinstance(qos, dict):
            qos_source = source_name + ":qos"
            self._set(metrics, "delay_ms", qos, ("delay_ms", "mean_delay_ms"), sources, qos_source)
            self._set(metrics, "packet_loss_pct", qos, ("packet_loss_pct", "loss_pct"), sources, qos_source)
            self._set(quality, "p05_sinr_db", qos, ("p05_sinr_db",), sources, qos_source)

    def _valid_window(self, source: Dict[str, Any]) -> bool:
        start = self._first(source, ("measurement_start_s", "MeasurementStart_s"))
        end = self._first(source, ("measurement_end_s",))
        duration = self._first(source, ("measurement_duration_s", "MeasurementDuration_s", "analysis_duration_s"))
        if source.get("simulator_source") == "mmwave_LENA_oran_custom" and "simTime_s" in source:
            scenario_end = _number(source["simTime_s"])
            if scenario_end is None or end is None or not math.isclose(scenario_end, end, rel_tol=1e-9, abs_tol=1e-9):
                return False
        # One nanosecond absolute / 1e-9 relative: numerical serialization
        # roundoff only, not a scientific or QoS window tolerance.
        return (start is not None and end is not None and duration is not None
                and start >= 0 and end > start and duration > 0
                and math.isclose(duration, end - start, rel_tol=1e-9, abs_tol=1e-9))

    def _duration(self, source: Dict[str, Any]) -> Optional[float]:
        duration = self._first(source, ("measurement_duration_s", "MeasurementDuration_s", "analysis_duration_s"))
        if duration is not None:
            return duration
        end = self._first(source, ("measurement_end_s", "simTime_s", "Time_s", "time_s", "Time"))
        start = self._first(source, ("measurement_start_s", "MeasurementStart_s"))
        return end - (start or 0.0) if end is not None else None

    def _from_packet_counters(
        self, source: Dict[str, Any], metrics: Dict[str, Optional[float]],
        sources: Dict[str, str], source_name: str, *, final: bool = False,
    ) -> None:
        rx = self._first(source, ("rx_packets", "RxPackets", "rxPackets"))
        tx = self._first(source, ("tx_packets", "TxPackets", "txPackets"))
        delay_sum = self._first(source, ("delay_sum_s", "DelaySum_s"))
        if delay_sum is not None:
            delay_sum *= 1000.0
        else:
            delay_sum = self._first(source, ("delay_sum_ms", "DelaySum_ms"))
        if rx is not None and rx >= 0 and delay_sum is not None and delay_sum >= 0:
            metrics["delay_ms"] = delay_sum / rx if rx > 0 else None
            sources["delay_ms"] = source_name + " (sum delay / received packets)"
        if final and tx is not None and rx is not None and 0 <= rx <= tx:
            metrics["packet_loss_pct"] = 100.0 * (tx - rx) / tx if tx > 0 else None
            sources["packet_loss_pct"] = source_name + " ((tx-rx) / tx; measurement end)"

    def _metadata(self, summary: Dict[str, Any], analysis: Dict[str, Any], rows: Sequence[Dict[str, str]]) -> Dict[str, Any]:
        raw = summary or (analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {}) or (rows[-1] if rows else {})
        keys = (
            "measurement_start_s", "measurement_end_s", "measurement_duration_s",
            "traffic_start_s", "traffic_stop_s", "drain_duration_s", "warmup_s",
            "energy_scope", "energy_profile", "data_layer", "simulator_source", "packet_loss_definition", "qos_source",
            "tx_packets", "rx_packets", "lost_packets", "monitor_lost_packets", "delay_sum_s",
        )
        metadata = {key: raw[key] for key in keys if key in raw}
        for key in metadata:
            if key.endswith("_s") or key.endswith("_packets"):
                metadata[key] = _number(metadata[key])
        duration = self._duration(raw)
        if duration is not None:
            metadata["measurement_duration_s"] = duration
        metadata["definitions"] = {
            "energy_efficiency_bit_per_j": "delivered useful bits / modeled energy over the same window",
            "energy_per_mbit_j": "modeled joules / delivered Mbit over the same window; undefined with zero delivery",
            "throughput_mbit_per_s": "delivered Mbit / measurement duration (includes idle and drain time)",
            "delivered_mbit": "received UDP application bytes including SeqTsHeader" if metadata.get("data_layer") == "udp_application_payload" else "estimated integral of reported PDCP bitrate; not an IP receive counter",
            "delay_ms": "sum of end-to-end delays / received packets; undefined without received packets",
            "packet_loss_pct": "final: packets not received after drain / transmitted packets; live: not received yet, including packets in transit" if metadata.get("qos_source") == "udp_application_seq_ts" else "final: packets not received by the end / transmitted packets; historical live: FlowMonitor declared losses",
            "mean_sinr_db": "arithmetic mean of KPM per-report mean SINR values over the measurement window",
            "p05_sinr_db": "arithmetic mean of KPM per-report p05 SINR values over the measurement window",
            "mean_prb_pct": "arithmetic mean of reported per-cell DL PRB utilization over the measurement window",
            "active_ues": "arithmetic mean of per-cell DRB.MeanActiveUeDl reports; not unique IMSIs",
        }
        return metadata

    def _from_analysis(
        self,
        analysis: Dict[str, Any],
        metrics: Dict[str, Optional[float]],
        quality: Dict[str, Optional[float]],
        sources: Dict[str, str],
    ) -> None:
        if not analysis:
            return
        summary = analysis.get("summary")
        if isinstance(summary, dict):
            self._from_summary(summary, metrics, quality, sources, "analysis.json:summary")
        values = analysis.get("quality")
        if isinstance(values, dict):
            self._set(quality, "mean_sinr_db", values, ("mean_sinr_db",), sources, "analysis.json:quality")
            self._set(quality, "p05_sinr_db", values, ("p05_sinr_db",), sources, "analysis.json:quality")
            self._set(quality, "mean_prb_pct", values, ("mean_prb_pct", "mean_prb_utilization_pct"), sources, "analysis.json:quality")
            self._set(quality, "active_ues", values, ("mean_active_ues", "active_ues"), sources, "analysis.json:quality")

    def _from_legacy_summary_rows(
        self,
        rows: Sequence[Dict[str, str]],
        metrics: Dict[str, Optional[float]],
        quality: Dict[str, Optional[float]],
        sources: Dict[str, str],
    ) -> None:
        values = {
            str(row.get("metric") or "").strip(): row.get("value")
            for row in rows
            if str(row.get("metric") or "").strip()
        }
        if values:
            self._from_summary(values, metrics, quality, sources, "summary.csv")

    def _from_ee_rows(
        self,
        rows: Sequence[Dict[str, str]],
        metrics: Dict[str, Optional[float]],
        sources: Dict[str, str],
    ) -> None:
        if not rows:
            return
        row = rows[-1]
        energy = self._first(row, ("TotalEnergy_J", "total_energy_j", "totalEnergy_J"))
        delivered = self._first(row, ("DeliveredBits", "delivered_bits", "deliveredData_bits"))
        sim_time = self._duration(row)
        power = self._first(row, ("IntervalPower_W", "interval_power_w", "total_power_w"))
        if metrics["total_energy_j"] is None and energy is not None:
            metrics["total_energy_j"] = energy
            sources["total_energy_j"] = "ee_timeseries.csv"
        if metrics["delivered_mbit"] is None and delivered is not None:
            metrics["delivered_mbit"] = delivered / 1e6
            sources["delivered_mbit"] = "ee_timeseries.csv"
        if metrics["average_power_w"] is None and sim_time and energy is not None and sim_time > 0:
            metrics["average_power_w"] = energy / sim_time
            sources["average_power_w"] = "ee_timeseries.csv"
        if power is not None:
            # Średnia pozostaje E/t; wartość chwilowa jest dostępna tylko na wykresie.
            sources["latest_power_w"] = "ee_timeseries.csv"
        if metrics["throughput_mbit_per_s"] is None and delivered is not None and sim_time and sim_time > 0:
            metrics["throughput_mbit_per_s"] = delivered / sim_time / 1e6
            sources["throughput_mbit_per_s"] = "ee_timeseries.csv (DeliveredBits / Time_s)"
        # Nowy, pełny kontrakt szeregu EE zapisuje również rzeczywiste KPI
        # FlowMonitor.  Zachowujemy N/A dla starszych plików bez tych kolumn;
        # nie wyprowadzamy loss/delay z innych liczb ani z ruchu kontrolnego.
        self._set(
            metrics,
            "energy_efficiency_bit_per_j",
            row,
            ("CumulativeEE_bit_per_J", "cumulative_ee_bit_per_j", "EE_bit_per_J", "ee_bit_per_j"),
            sources,
            "ee_timeseries.csv",
        )
        self._set(
            metrics,
            "energy_per_mbit_j",
            row,
            ("EnergyCost_J_per_Mbit", "energy_cost_j_per_mbit", "j_per_mbit"),
            sources,
            "ee_timeseries.csv",
        )
        self._set(
            metrics,
            "packet_loss_pct",
            row,
            ("PacketLoss_pct", "packet_loss_pct", "loss_pct"),
            sources,
            "ee_timeseries.csv",
        )
        self._set(
            metrics,
            "delay_ms",
            row,
            ("MeanDelay_ms", "mean_delay_ms", "delay_ms", "average_delay_ms"),
            sources,
            "ee_timeseries.csv",
        )
        self._from_packet_counters(row, metrics, sources, "ee_timeseries.csv")

    def _from_ee_summary_rows(
        self,
        rows: Sequence[Dict[str, str]],
        metrics: Dict[str, Optional[float]],
        sources: Dict[str, str],
    ) -> None:
        if not rows:
            return
        row = rows[-1]
        self._from_summary(row, metrics, {}, sources, "ee_summary.csv")

    def _from_local_rows(
        self,
        rows: Sequence[Dict[str, str]],
        metrics: Dict[str, Optional[float]],
        quality: Dict[str, Optional[float]],
        sources: Dict[str, str],
    ) -> None:
        if not rows:
            return
        row = rows[-1]

        # ``metrics.csv`` jest zapisem KPI wygenerowanych przez backend.  Nie
        # może nadpisywać podsumowania scenariusza ani pełnego szeregu EE, ale
        # musi wypełniać ich brak — szczególnie dla starszych wariantów ns-3.
        energy = self._first(row, ("total_energy_j", "TotalEnergy_J", "totalEnergy_J"))
        delivered = self._first(row, ("delivered_bits", "DeliveredBits", "deliveredData_bits"))
        sim_time = self._duration(row)
        throughput = self._first(
            row,
            ("average_throughput_bps",),
        )

        if metrics["total_energy_j"] is None and energy is not None:
            metrics["total_energy_j"] = energy
            sources["total_energy_j"] = "metrics.csv"
        if metrics["delivered_mbit"] is None and delivered is not None:
            metrics["delivered_mbit"] = delivered / 1e6
            sources["delivered_mbit"] = "metrics.csv"
        if metrics["average_power_w"] is None:
            if energy is not None and sim_time is not None and sim_time > 0:
                # E/t zachowuje semantykę "średniej mocy" także wtedy, gdy
                # total_power_w jest jedynie chwilową próbką końcową.
                metrics["average_power_w"] = energy / sim_time
                sources["average_power_w"] = "metrics.csv (total_energy_j / time_s)"
        if metrics["throughput_mbit_per_s"] is None:
            if delivered is not None and sim_time is not None and sim_time > 0:
                metrics["throughput_mbit_per_s"] = delivered / sim_time / 1e6
                sources["throughput_mbit_per_s"] = "metrics.csv (delivered_bits / time_s)"
            elif throughput is not None:
                metrics["throughput_mbit_per_s"] = throughput / 1e6
                sources["throughput_mbit_per_s"] = "metrics.csv (average_throughput_bps)"

        self._set(
            metrics,
            "energy_efficiency_bit_per_j",
            row,
            ("ee_bit_per_j", "EE_bit_per_J", "cumulative_ee_bit_per_j", "CumulativeEE_bit_per_J"),
            sources,
            "metrics.csv",
        )
        self._set(
            metrics,
            "energy_per_mbit_j",
            row,
            ("j_per_mbit", "energy_cost_j_per_mbit", "energy_per_mbit_j", "EnergyCost_J_per_Mbit"),
            sources,
            "metrics.csv",
        )
        self._set(metrics, "packet_loss_pct", row, ("packet_loss_pct", "loss_pct"), sources, "metrics.csv")
        self._set(metrics, "delay_ms", row, ("delay_ms", "mean_delay_ms", "average_delay_ms"), sources, "metrics.csv")
        self._from_packet_counters(row, metrics, sources, "metrics.csv")
        self._set(quality, "mean_sinr_db", row, ("mean_sinr_db",), sources, "metrics.csv")
        self._set(quality, "p05_sinr_db", row, ("p05_sinr_db",), sources, "metrics.csv")
        self._set(quality, "mean_prb_pct", row, ("mean_prb_utilization_pct", "mean_prb_pct"), sources, "metrics.csv")
        self._set(quality, "active_ues", row, ("active_ues", "mean_active_ues"), sources, "metrics.csv")

    def _native_artifacts(self, directory: Path) -> Dict[str, Any]:
        """Build aligned native energy and KPM series without touching ns-3."""

        files_by_name = {path.name: path for path in directory.glob("energyfilecell*.csv")}
        files_by_name.update({path.name: path for path in (directory / "timeseries").glob("energyfilecell*.csv")})
        energy_files: List[Tuple[List[float], List[float]]] = []
        for path in files_by_name.values():
            points: List[Tuple[float, float]] = []
            for row in _read_csv(path):
                time_s = self._first(row, ("Time", "Time_s", "time_s"))
                energy_j = self._first(row, ("NetEnergy", "total_energy_j", "TotalEnergy_J"))
                if time_s is None or energy_j is None or time_s < 0 or energy_j < 0:
                    continue
                if points and time_s < points[-1][0]:
                    continue
                points.append((time_s, energy_j))
            if points:
                energy_files.append(([point[0] for point in points], [point[1] for point in points]))

        native: Dict[str, Any] = {
            "metrics": {}, "quality": {}, "series": {},
            "series_sources": {}, "metadata": {},
        }
        if energy_files:
            end_times = [times[-1] for times, _ in energy_files]
            skew = max(end_times) - min(end_times)
            native["metadata"]["energy_cell_max_timestamp_skew_s"] = skew
            if skew > 1e-6:
                # Licznik cichej komórki nie obejmuje jej ostatniego odcinka
                # pracy. Hold jest użyteczny live, ale nie daje energii końcowej.
                energy_files = []
        energy_series: List[Dict[str, float]] = []
        power_series: List[Dict[str, float]] = []
        energy_end: Optional[float] = None
        if energy_files:
            # Cell energy counters are cumulative and writers are event driven:
            # a quiet cell may stop appending while the others keep running.
            # Hold its last valid counter at the selected cutoff.
            energy_end = max(times[-1] for times, _ in energy_files)
            sample_times = sorted({
                time_s
                for times, _ in energy_files
                for time_s in times
                if time_s <= energy_end
            })
            if sample_times:
                stride = max(1, int(math.ceil(len(sample_times) / 3000.0)))
                selected_times = sample_times[::stride]
                if selected_times[-1] != energy_end:
                    selected_times.append(energy_end)
                for time_s in selected_times:
                    total = 0.0
                    for times, energies in energy_files:
                        index = bisect_right(times, time_s) - 1
                        if index < 0:
                            break
                        total += energies[index]
                    else:
                        energy_series.append({"x": time_s, "y": total})
                if energy_series:
                    for previous, current in zip(energy_series, energy_series[1:]):
                        elapsed = current["x"] - previous["x"]
                        if elapsed > 0:
                            power_series.append({
                                "x": current["x"],
                                "y": (current["y"] - previous["y"]) / elapsed,
                            })

        def energy_at(time_s: float) -> Optional[float]:
            if not energy_files:
                return None
            total = 0.0
            for times, energies in energy_files:
                index = bisect_right(times, time_s) - 1
                if index < 0:
                    return None
                total += energies[index]
            return total

        kpm_files: Dict[str, str] = {}
        for group in (directory, directory / "timeseries"):
            if not group.is_dir():
                continue
            for path in group.glob("*.txt"):
                if re.match(r"^(?:nr-)?cu-(?:up|cp)-cell-\d+\.txt$|^du-cell-\d+\.txt$", path.name, re.I):
                    try:
                        kpm_files[path.name] = path.read_text(encoding="utf-8-sig", errors="replace")
                    except OSError:
                        continue
        kpm_snapshot: Dict[str, Any] = {}
        if kpm_files:
            collector = KpmLiveCollector(max_points=3000)
            collector.ingest_files(kpm_files)
            kpm_snapshot = collector.snapshot()
        raw_kpm_series = kpm_snapshot.get("series")
        raw_kpm_series = raw_kpm_series if isinstance(raw_kpm_series, dict) else {}
        series_map = {
            "throughput_mbit_per_s": "throughput_mbit_per_s",
            "delivered_mbit": "delivered_mbit",
            "delay_ms": "mean_delay_ms",
            "mean_sinr_db": "mean_sinr_db",
            "p05_sinr_db": "p05_sinr_db",
            "mean_prb_pct": "mean_prb_utilization_pct",
            "active_ues": "active_ues",
        }
        kpm_series: Dict[str, List[Dict[str, float]]] = {}
        for target, source in series_map.items():
            points = raw_kpm_series.get(source)
            if not isinstance(points, list):
                continue
            valid = []
            for point in points:
                if not isinstance(point, dict):
                    continue
                time_s, value = _number(point.get("x")), _number(point.get("y"))
                if time_s is None or value is None or time_s < 0:
                    continue
                if energy_end is not None and time_s > energy_end:
                    continue
                valid.append({"x": time_s, "y": value})
            if valid:
                kpm_series[target] = valid

        start_s: Optional[float] = None
        cutoff_s = energy_end
        data_points = kpm_series.get("delivered_mbit") or []
        rate_points = kpm_series.get("throughput_mbit_per_s") or []
        if cutoff_s is not None and (data_points or rate_points):
            first_points = rate_points or data_points
            cutoff_s = min(cutoff_s, float(first_points[-1]["x"]))
            start_s = float(first_points[0]["x"])
            if cutoff_s <= start_s:
                start_s = None

        if cutoff_s is not None and energy_files:
            energy_series = [point for point in energy_series if point["x"] <= cutoff_s]
            cutoff_energy = energy_at(cutoff_s)
            if cutoff_energy is not None:
                if energy_series and energy_series[-1]["x"] == cutoff_s:
                    energy_series[-1] = {"x": cutoff_s, "y": cutoff_energy}
                else:
                    energy_series.append({"x": cutoff_s, "y": cutoff_energy})
            power_series = []
            for previous, current in zip(energy_series, energy_series[1:]):
                elapsed = current["x"] - previous["x"]
                if elapsed > 0:
                    power_series.append({
                        "x": current["x"],
                        "y": (current["y"] - previous["y"]) / elapsed,
                    })
            if start_s is not None:
                initial_energy = energy_at(start_s)
                if initial_energy is not None:
                    energy_series = [
                        {"x": point["x"], "y": max(0.0, point["y"] - initial_energy)}
                        for point in energy_series
                        if point["x"] >= start_s
                    ]
                    # The first sample marks the measurement window origin.
                    if not energy_series or energy_series[0]["x"] > start_s:
                        energy_series.insert(0, {"x": start_s, "y": 0.0})

        for points in kpm_series.values():
            if cutoff_s is not None:
                points[:] = [point for point in points if point["x"] <= cutoff_s]
            if start_s is not None:
                points[:] = [point for point in points if point["x"] >= start_s]

        native_series: Dict[str, List[Dict[str, float]]] = {}
        if energy_series:
            native_series["energy_j"] = energy_series
        if power_series:
            native_series["power_w"] = power_series
        native_series.update(kpm_series)

        total_energy = energy_series[-1]["y"] if energy_series else None
        duration = (cutoff_s - start_s) if cutoff_s is not None and start_s is not None else cutoff_s
        delivered = (data_points[-1]["y"] if data_points else None)
        # data_points may have been clipped to the common energy window above.
        delivered_points = kpm_series.get("delivered_mbit") or []
        if delivered_points:
            delivered = delivered_points[-1]["y"]
        if total_energy is None and cutoff_s is not None:
            total_energy = energy_at(cutoff_s)
        if total_energy is not None:
            native["metrics"]["total_energy_j"] = total_energy
            native["metadata"]["energy_scope"] = "suma energii NetEnergy wszystkich komórek"
            if cutoff_s is not None:
                cell_times = [
                    times[bisect_right(times, cutoff_s) - 1]
                    for times, _energies in energy_files
                    if bisect_right(times, cutoff_s) > 0
                ]
                if cell_times:
                    native["metadata"]["energy_cell_max_timestamp_skew_s"] = (
                        cutoff_s - min(cell_times)
                    )
        if duration is not None and duration > 0:
            native["metadata"].update({
                "measurement_start_s": start_s or 0.0,
                "measurement_end_s": cutoff_s,
                "measurement_duration_s": duration,
            })
            if total_energy is not None:
                native["metrics"]["average_power_w"] = total_energy / duration
        if delivered is not None:
            native["metrics"]["delivered_mbit"] = delivered
            native["metadata"]["data_layer"] = "KPM PDCP; całka bitrate'u, estymata danych PDCP"
            if duration is not None and duration > 0:
                native["metrics"]["throughput_mbit_per_s"] = delivered / duration
        if total_energy is not None and delivered is not None:
            native["metrics"]["energy_efficiency_bit_per_j"] = (
                delivered * 1_000_000.0 / total_energy if total_energy > 0 else None
            )
            native["metrics"]["energy_per_mbit_j"] = (
                total_energy / delivered if delivered > 0 else None
            )
            native["metadata"]["packet_loss_definition"] = None
            native["metadata"]["measurement_consistent"] = True
        if total_energy is not None:
            native["metadata"].setdefault("simulator_source", "ns-3 native energyfilecell*.csv")

        kpm_metrics = kpm_snapshot.get("current_metrics")
        kpm_metrics = kpm_metrics if isinstance(kpm_metrics, dict) else {}
        for result_key, series_key, source_key in (
            ("delay_ms", "delay_ms", "mean_delay_ms"),
            ("mean_sinr_db", "mean_sinr_db", "mean_sinr_db"),
            ("p05_sinr_db", "p05_sinr_db", "p05_sinr_db"),
            ("mean_prb_pct", "mean_prb_pct", "mean_prb_utilization_pct"),
            ("active_ues", "active_ues", "active_ues"),
        ):
            points = kpm_series.get(series_key) or []
            if points:
                value = sum(point["y"] for point in points) / len(points)
                if result_key == "delay_ms":
                    native["metrics"][result_key] = value
                else:
                    native["quality"][result_key] = value
                native.setdefault("sources", {})[result_key] = (
                    "KPM {}: średnia z {} raportów w oknie pomiarowym".format(
                        source_key, len(points)
                    )
                )
                native["series_sources"][series_key] = "KPM {}: {} raportów".format(
                    source_key, len(points)
                )
        if total_energy is not None:
            native.setdefault("sources", {})["total_energy_j"] = "energyfilecell*.csv aligned sample-and-hold"
            native.setdefault("sources", {})["average_power_w"] = "energyfilecell*.csv (energy / measurement duration)"
            native["series_sources"]["energy_j"] = "energyfilecell*.csv aligned sample-and-hold"
        if power_series:
            native["series_sources"]["power_w"] = "energyfilecell*.csv (interval power from sample-and-hold energy)"
        if delivered is not None:
            native.setdefault("sources", {})["delivered_mbit"] = "KPM PDCP throughput integrated (estimate)"
            native.setdefault("sources", {})["throughput_mbit_per_s"] = "integrated KPM PDCP data / measurement duration (estimate)"
            native["series_sources"]["delivered_mbit"] = "KPM PDCP throughput integrated (estimate)"
            native["series_sources"]["throughput_mbit_per_s"] = "KPM PDCP throughput reports"
        if total_energy is not None and delivered is not None:
            native.setdefault("sources", {})["energy_efficiency_bit_per_j"] = "energyfilecell*.csv + integrated KPM PDCP throughput (estimate)"
            native.setdefault("sources", {})["energy_per_mbit_j"] = "energyfilecell*.csv / integrated KPM PDCP throughput (estimate)"
            native["series_sources"]["energy_efficiency_bit_per_j"] = native["sources"]["energy_efficiency_bit_per_j"]
            native["series_sources"]["energy_per_mbit_j"] = native["sources"]["energy_per_mbit_j"]
            energy_times = [point["x"] for point in energy_series]
            for result_key, calculate in (
                ("energy_efficiency_bit_per_j", lambda bits, energy: bits * 1_000_000.0 / energy),
                ("energy_per_mbit_j", lambda bits, energy: energy / bits),
            ):
                points = []
                for data_point in delivered_points:
                    index = bisect_right(energy_times, data_point["x"]) - 1
                    if index < 0 or energy_series[index]["y"] <= 0:
                        continue
                    data_mbit = data_point["y"]
                    if result_key == "energy_per_mbit_j" and data_mbit <= 0:
                        continue
                    points.append({"x": data_point["x"], "y": calculate(data_mbit, energy_series[index]["y"])})
                if points:
                    native_series[result_key] = points
        native["series"] = native_series
        if kpm_files:
            native["metadata"].setdefault("kpm_source", ", ".join(sorted(kpm_files)))
        return native

    @staticmethod
    def _from_energy_files(
        native: Dict[str, Any],
        metrics: Dict[str, Optional[float]],
        quality: Dict[str, Optional[float]],
        sources: Dict[str, str],
    ) -> None:
        for key, value in dict(native.get("metrics") or {}).items():
            if key in metrics and metrics[key] is None:
                metrics[key] = value
                source = dict(native.get("sources") or {}).get(key)
                if source:
                    sources[key] = source
        for key, value in dict(native.get("quality") or {}).items():
            if key in quality and quality[key] is None:
                quality[key] = value
                source = dict(native.get("sources") or {}).get(key)
                if source:
                    sources[key] = source
        for key, source in dict(native.get("series_sources") or {}).items():
            sources.setdefault(key, source)

    @staticmethod
    def _first(source: Dict[str, Any], names: Iterable[str]) -> Optional[float]:
        for name in names:
            result = _number(source.get(name))
            if result is not None:
                return result
        return None

    def _set(
        self,
        target: Dict[str, Optional[float]],
        key: str,
        source: Dict[str, Any],
        names: Iterable[str],
        sources: Dict[str, str],
        source_name: str,
    ) -> None:
        if target.get(key) is not None:
            return
        value = self._first(source, names)
        if value is not None:
            target[key] = value
            sources[key] = source_name

    @staticmethod
    def _validate(metrics: Dict[str, Optional[float]], warnings: List[str]) -> None:
        for key, value in metrics.items():
            if value is not None and (value < 0 or (key == "packet_loss_pct" and value > 100)):
                metrics[key] = None
                warnings.append("Odrzucono niepoprawną wartość metryki {}.".format(key))

    @staticmethod
    def _complete_derived(metrics: Dict[str, Optional[float]], sources: Dict[str, str], warnings: List[str]) -> None:
        energy = metrics.get("total_energy_j")
        delivered_mbit = metrics.get("delivered_mbit")
        if energy is None or delivered_mbit is None:
            return
        ratios = {
            "energy_per_mbit_j": energy / delivered_mbit if delivered_mbit > 0 else None,
            "energy_efficiency_bit_per_j": delivered_mbit * 1e6 / energy if energy > 0 else None,
        }
        for key, value in ratios.items():
            old = metrics.get(key)
            if old is not None and (value is None or not math.isclose(old, value, rel_tol=1e-4, abs_tol=1e-9)):
                warnings.append("Skorygowano niespójne {} na podstawie energii i dostarczonych danych z tego samego źródła.".format(key))
            metrics[key] = value
            energy_source = sources.get("total_energy_j", "energy artifacts")
            delivered_source = sources.get("delivered_mbit", "delivered-data artifacts")
            sources[key] = "{} + {} (derived over the same measurement window)".format(
                energy_source, delivered_source
            )

    def _series(self, rows: Sequence[Dict[str, str]]) -> Dict[str, List[Dict[str, float]]]:
        result: Dict[str, List[Dict[str, float]]] = {
            "energy_j": [],
            "power_w": [],
            "throughput_mbit_per_s": [],
            "energy_efficiency_bit_per_j": [],
            "energy_per_mbit_j": [],
            "delivered_mbit": [],
            "packet_loss_pct": [],
            "delay_ms": [],
        }
        for row in rows:
            time_s = self._first(row, ("Time_s", "time_s", "Time"))
            if time_s is None or time_s < 0:
                continue
            energy = self._first(row, ("TotalEnergy_J", "total_energy_j", "totalEnergy_J"))
            power = self._first(row, ("IntervalPower_W", "interval_power_w", "total_power_w"))
            throughput = self._first(
                row,
                ("IntervalThroughput_bps", "interval_throughput_bps", "throughput_bps", "average_throughput_bps"),
            )
            delivered = self._first(row, ("DeliveredBits", "delivered_bits", "deliveredData_bits"))
            efficiency = self._first(
                row,
                ("CumulativeEE_bit_per_J", "cumulative_ee_bit_per_j", "ee_bit_per_j", "EE_bit_per_J"),
            )
            energy_cost = self._first(
                row,
                ("EnergyCost_J_per_Mbit", "energy_cost_j_per_mbit", "j_per_mbit", "energy_per_mbit_j"),
            )
            if energy is not None:
                result["energy_j"].append({"x": time_s, "y": energy})
            if power is not None:
                result["power_w"].append({"x": time_s, "y": power})
            if throughput is not None:
                result["throughput_mbit_per_s"].append({"x": time_s, "y": throughput / 1e6})
            if efficiency is not None:
                result["energy_efficiency_bit_per_j"].append({"x": time_s, "y": efficiency})
            elif delivered is not None and energy is not None and energy > 0:
                result["energy_efficiency_bit_per_j"].append({"x": time_s, "y": delivered / energy})
            if delivered is not None:
                delivered_mbit = delivered / 1e6
                result["delivered_mbit"].append({"x": time_s, "y": delivered_mbit})
            if energy_cost is not None:
                result["energy_per_mbit_j"].append({"x": time_s, "y": energy_cost})
            elif delivered is not None:
                delivered_mbit = delivered / 1e6
                if delivered_mbit > 0 and energy is not None:
                    result["energy_per_mbit_j"].append({"x": time_s, "y": energy / delivered_mbit})
            for key, aliases in (
                ("packet_loss_pct", ("PacketLoss_pct", "packet_loss_pct", "loss_pct")),
                ("delay_ms", ("MeanDelay_ms", "mean_delay_ms", "delay_ms", "average_delay_ms")),
            ):
                value = self._first(row, aliases)
                if value is not None and value >= 0 and (key != "packet_loss_pct" or value <= 100):
                    result[key].append({"x": time_s, "y": value})
        return result

    @staticmethod
    def _fill_missing_series(
        primary: Dict[str, List[Dict[str, float]]],
        fallback: Dict[str, List[Dict[str, float]]],
    ) -> Dict[str, List[Dict[str, float]]]:
        """Zachowuje serie z pliku EE, uzupełniając tylko realne braki KPI."""
        return {
            key: list(values) if values else list(fallback.get(key) or [])
            for key, values in primary.items()
        }

    @staticmethod
    def _warnings(metrics: Dict[str, Optional[float]]) -> List[str]:
        messages = []
        if metrics.get("total_energy_j") is None:
            messages.append("Brak danych energii w zapisanych wynikach.")
        if metrics.get("delivered_mbit") is None:
            messages.append("Brak danych o skutecznie dostarczonych danych.")
        if metrics.get("delay_ms") is None:
            messages.append("Opóźnienie: N/A — brak poprawnego pomiaru lub brak odebranych pakietów.")
        if metrics.get("packet_loss_pct") is None:
            messages.append("Packet loss: N/A — brak poprawnego pomiaru lub brak wysłanych pakietów.")
        if metrics.get("delivered_mbit") == 0:
            messages.append("Nie dostarczono danych: koszt J/Mbit jest nieokreślony; zerowe EE nie oznacza dobrej jakości usługi.")
        return messages
