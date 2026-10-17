"""Przyrostowy odczyt rzeczywistych plików KPM zapisywanych przez ns-3.

Kolektor nie uruchamia procesu E2, nie zmienia jego parametrów i nie ma
zależności od Dockera ani Tkintera.  Warstwa wykonawcza przekazuje wyłącznie
tekst już istniejących plików ``cu-up-cell-*.txt``, ``cu-cp-cell-*.txt`` i
``du-cell-*.txt``.  Dzięki temu brak pliku pozostaje brakiem danych, zamiast
zostać zastąpiony wartością domyślną.

Obsługiwane pola odpowiadają formatom KPM używanym przez ns-O-RAN/FlexRIC:

* ``L3 serving SINR`` z CU-CP,
* ``dlPrbUsage`` i ``DRB.MeanActiveUeDl`` z DU,
* ``DRB.PdcpSduBitRateDl`` i ``DRB.PdcpSduDelayDl`` z CU-UP.

Przepływność jest brana z jawnej wartości ``pdcpThroughput`` albo, gdy jej
brakuje, z wolumenu raportowego PDCP i rzeczywistego odstępu między raportami.
Nie jest zgadywana z konfiguracji ruchu.  Wartość opóźnienia KPM
``DRB.PdcpSduDelayDl`` jest zapisana w dziesiątych częściach milisekundy.
"""

from __future__ import annotations

import csv
import math
import os
import re
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


Point = Dict[str, float]
Observation = Dict[str, Any]


_KPM_FILE_RE = re.compile(
    r"^(?P<kind>nr-cu-up|cu-up|cu-cp|du)-cell-(?P<cell>[0-9]+)\.txt$",
    re.IGNORECASE,
)


def _finite_number(value: Any) -> Optional[float]:
    """Zwraca skończoną liczbę albo ``None`` dla niepoprawnej wartości."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp_number(value: Any) -> Optional[float | int]:
    """Keep large integer KPM timestamps exact until the relative delta exists."""

    raw = str(value).strip()
    if re.fullmatch(r"[+-]?\d+", raw):
        try:
            return int(raw)
        except ValueError:
            return None
    return _finite_number(raw)


def _column_name(value: Any) -> str:
    """Normalizuje nazwę kolumny bez uzależniania się od spacji i znaków."""

    return "".join(
        character
        for character in str(value).lstrip("\ufeff").strip().lower()
        if character.isalnum()
    )


def _complete_lines(payload: str) -> Iterable[Tuple[str, bool]]:
    """Dzieli tekst, zachowując niedopisaną końcówkę do kolejnego odczytu."""

    for item in payload.splitlines(True):
        yield item, item.endswith("\n") or item.endswith("\r")


def _mean(values: Iterable[float]) -> Optional[float]:
    items = list(values)
    return sum(items) / len(items) if items else None


def _p05(values: Iterable[float]) -> Optional[float]:
    """Zwraca rzeczywisty percentyl 5 metodą najbliższej rangi."""

    items = sorted(values)
    if not items:
        return None
    rank = max(1, int(math.ceil(len(items) * 0.05)))
    return items[rank - 1]


class KpmLiveCollector:
    """Ograniczony, przyrostowy model metryk jakości z plików KPM.

    ``ingest_files`` przyjmuje mapę ``nazwa pliku -> tekst CSV``.  Może
    wielokrotnie otrzymać cały ogon pliku: rekordy już widziane są odrzucane
    według czasu i kompletnej zawartości wiersza.  Kilka rekordów CU-CP dla
    różnych UE w tym samym czasie pozostaje poprawnych i jest zachowywanych.
    """

    DEFAULT_MAX_POINTS = 3000
    # ``timestamp`` w referencyjnym mmWave jest ``m_startTime + Now(ms)``.
    # Wartość rzędu miliarda nie może być czasem zwykłego przebiegu, więc
    # można bezpiecznie wykresować ją względem pierwszej próbki KPM.
    ABSOLUTE_TIMESTAMP_MIN = 1_000_000_000.0

    _SERIES_KEYS = (
        "throughput_mbit_per_s",
        "mean_delay_ms",
        "mean_sinr_db",
        "p05_sinr_db",
        "mean_prb_utilization_pct",
        "active_ues",
        "delivered_mbit",
    )

    _METRIC_KEYS = _SERIES_KEYS
    _TIME_FIELDS = ("timestamp", "timestampms", "time", "times", "simtime", "simtimes")
    _UE_ID_FIELDS = ("ueimsi", "imsi", "ueid", "ueimsicomplete", "id")
    _SINR_FIELDS = ("l3servingsinr", "servingsinr", "l3servingsinr3gpp")
    _PRB_FIELDS = ("dlprbusage", "dlprbusagepercentage")
    _ACTIVE_UE_FIELDS = ("drbmeanactiveuedl",)
    _DU_THROUGHPUT_KBIT_FIELDS = (
        "drbuethpdlpdcpbasedueid",
        "drbuethpdlueid",
    )
    _UE_THROUGHPUT_KBIT_FIELDS = (
        "drbpdcpsdubitratedlueidpdcpthroughput",
    )
    _UE_INTERVAL_VOLUME_KBIT_FIELDS = (
        "qosflowpdcppduvolumedlfilterueidtxpdcppdubytesnrrlc",
    )
    _CELL_DELAY_FIELDS = ("drbpdcpsdudelaydlcellaveragelatency",)
    _UE_DELAY_FIELDS = ("drbpdcpsdudelaydlueidpdcplatency",)
    _METRIC_NOTES = {
        "throughput_mbit_per_s": "KPM: PDCP bitrate lub DRB.UEThpDl [kbps] przeliczone na Mbit/s",
        "mean_delay_ms": "DRB.PdcpSduDelayDl: 0.1 ms na jednostkę KPM",
        "mean_sinr_db": "L3 serving SINR",
        "p05_sinr_db": "L3 serving SINR, percentyl 5 metodą najbliższej rangi",
        "mean_prb_utilization_pct": "dlPrbUsage",
        "active_ues": "suma DRB.MeanActiveUeDl per komórka",
        "delivered_mbit": "całka KPM PDCP bitrate; estymata danych PDCP",
    }

    def __init__(self, max_points: int = DEFAULT_MAX_POINTS) -> None:
        try:
            capacity = int(max_points)
        except (TypeError, ValueError):
            raise ValueError("max_points musi być dodatnią liczbą całkowitą")
        if capacity < 1:
            raise ValueError("max_points musi być dodatnią liczbą całkowitą")

        self.max_points = capacity
        self._file_states: Dict[str, Dict[str, Any]] = {}
        self._observations: Dict[Tuple[str, str, str, str], Observation] = {}
        self._ingest_events: Optional[List[Tuple[float, Tuple[str, str, str, str], Observation]]] = None
        self._delivered_mbit = 0.0
        self._last_integrated_time_s: Optional[float] = None
        self._last_integrated_throughput: Optional[float] = None
        self._interval_time_states: Dict[Tuple[str, str], float] = {}
        self._timestamp_origin_raw: Optional[float] = None
        self._uses_relative_kpm_time = False
        self._series: Dict[str, Deque[Point]] = {
            key: deque(maxlen=capacity) for key in self._SERIES_KEYS
        }
        self._last_snapshot: Dict[str, Any] = self._empty_snapshot_values()
        self._stats: Dict[str, int] = {
            "accepted_rows": 0,
            "stale_rows": 0,
            "invalid_rows": 0,
            "partial_rows": 0,
            "header_changes": 0,
            "unsupported_files": 0,
            "invalid_files": 0,
        }

    @property
    def last_sample_time_s(self) -> Optional[float]:
        """Ostatni czas symulowany widoczny w poprawnym rekordzie KPM."""

        value = self._last_snapshot.get("sample_time_s")
        return value if isinstance(value, float) else None

    def reset(self) -> None:
        """Czyści stan bieżącej symulacji, pozostawiając limit retencji."""

        self._file_states.clear()
        self._observations.clear()
        self._ingest_events = None
        self._delivered_mbit = 0.0
        self._last_integrated_time_s = None
        self._last_integrated_throughput = None
        self._interval_time_states.clear()
        self._timestamp_origin_raw = None
        self._uses_relative_kpm_time = False
        for points in self._series.values():
            points.clear()
        self._last_snapshot = self._empty_snapshot_values()
        for key in self._stats:
            self._stats[key] = 0

    def ingest_files(self, files: Mapping[str, str]) -> int:
        """Wczytuje nowe rekordy z istniejących plików KPM.

        Puste mapowanie jest poprawne i nie tworzy danych.  Nazwy spoza trzech
        obsługiwanych rodzin plików są zliczane diagnostycznie, lecz ich treść
        nigdy nie wpływa na metryki.
        """

        if not isinstance(files, Mapping):
            raise TypeError("pliki KPM muszą być mapą nazwa -> tekst CSV")

        accepted = 0
        initial_observations = {
            key: dict(value) for key, value in self._observations.items()
        }
        self._ingest_events = []
        for raw_name, text in sorted(files.items(), key=lambda item: str(item[0])):
            filename = self._filename(raw_name)
            descriptor = self._describe_file(filename)
            if descriptor is None:
                self._stats["unsupported_files"] += 1
                continue
            if not isinstance(text, str):
                self._stats["invalid_files"] += 1
                continue
            accepted += self._ingest_file(filename, descriptor, text)

        events = self._ingest_events or []
        self._ingest_events = None
        if accepted:
            final_observations = {
                key: dict(value) for key, value in self._observations.items()
            }
            if events:
                # Replay each simulated-time instant in order. The collector
                # still exposes the final snapshot, while charts retain every
                # point from a batch read (including initial file backfill).
                self._observations = initial_observations
                events.sort(key=lambda item: item[0])
                grouped: Dict[float, List[Tuple[Tuple[str, str, str, str], Observation]]] = {}
                for event_time, key, observation in events:
                    grouped.setdefault(event_time, []).append((key, observation))
                for event_time in sorted(grouped):
                    for key, observation in grouped[event_time]:
                        self._observations[key] = observation
                    self._refresh_snapshot(append_series=True)
                self._observations = final_observations
                self._refresh_snapshot(append_series=False)
            else:
                self._refresh_snapshot(append_series=False)
        return accepted

    def snapshot(self) -> Dict[str, Any]:
        """Zwraca wyłącznie wartości pochodzące z odebranych plików KPM."""

        self._refresh_snapshot(append_series=False)
        current_metrics = dict(self._last_snapshot["current_metrics"])
        available = [key for key, value in current_metrics.items() if value is not None]
        integrated_delivered = self._last_snapshot.get("integrated_delivered_mbit")
        if integrated_delivered is not None:
            available.append("delivered_mbit")
        source_files = sorted(
            filename
            for filename, state in self._file_states.items()
            if state.get("header")
        )
        return {
            "sample_time_s": self._last_snapshot["sample_time_s"],
            "integrated_delivered_mbit": self._last_snapshot.get("integrated_delivered_mbit"),
            "current_metrics": current_metrics,
            "per_cell": {
                cell: dict(values)
                for cell, values in self._last_snapshot["per_cell"].items()
            },
            "series": {
                key: [dict(point) for point in values]
                for key, values in self._series.items()
            },
            "metric_sources": {
                key: list(values)
                for key, values in self._last_snapshot["metric_sources"].items()
            },
            "metric_notes": {
                key: self._METRIC_NOTES[key]
                for key in available
                if key in self._METRIC_NOTES
            },
            "series_sources": {
                key: ", ".join(values)
                for key, values in self._last_snapshot["metric_sources"].items()
                if key in self._SERIES_KEYS
            },
            "data_status": {
                **self._stats,
                "source_files": source_files,
                "source_file_count": len(source_files),
                "data_source": "KPM" if source_files else None,
                "timestamp_mode": (
                    "relative_to_first_kpm_sample"
                    if self._uses_relative_kpm_time
                    else "simulated_timestamp"
                ),
                "has_live_data": bool(available),
                "available_metrics": available,
                "unavailable_metrics": [
                    key for key in self._METRIC_KEYS if key not in available
                ],
                "retention_limit": self.max_points,
            },
        }

    @staticmethod
    def _filename(raw_name: Any) -> str:
        """Wyciąga samą nazwę, niezależnie od separatora użytego przez host."""

        return os.path.basename(str(raw_name).replace("\\", "/")).lower()

    @staticmethod
    def _describe_file(filename: str) -> Optional[Tuple[str, str]]:
        match = _KPM_FILE_RE.match(filename)
        if not match:
            return None
        raw_kind = match.group("kind").lower()
        kind = "cu_up" if raw_kind in {"nr-cu-up", "cu-up"} else raw_kind.replace("-", "_")
        cell = str(int(match.group("cell")))
        return kind, cell

    def _ingest_file(
        self,
        filename: str,
        descriptor: Tuple[str, str],
        text: str,
    ) -> int:
        if not text:
            return 0
        state = self._file_states.setdefault(
            filename,
            {
                "kind": descriptor[0],
                "cell": descriptor[1],
                "header": (),
                "columns": {},
                "time_divisor": 1.0,
                "pending": "",
                "last_timestamp": None,
                "last_signatures": set(),
            },
        )
        if state["kind"] != descriptor[0] or state["cell"] != descriptor[1]:
            self._stats["invalid_files"] += 1
            return 0

        pending = str(state.get("pending", ""))
        if pending and self._starts_with_header(text):
            # Ponowny ograniczony odczyt zawiera nagłówek, więc niedopisana
            # końcówka starego ogona nie może zostać z nim sklejona.
            pending = ""
        payload = pending + text
        state["pending"] = ""
        accepted = 0
        for raw_line, complete in _complete_lines(payload):
            if not complete:
                state["pending"] = raw_line
                self._stats["partial_rows"] += 1
                continue
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            try:
                row = next(csv.reader([line]))
            except (csv.Error, StopIteration):
                self._stats["invalid_rows"] += 1
                continue
            if self._set_header_if_present(state, row):
                continue
            if self._ingest_row(filename, state, row):
                accepted += 1
        return accepted

    def _starts_with_header(self, text: str) -> bool:
        for raw_line, complete in _complete_lines(text):
            if not complete:
                return False
            try:
                row = next(csv.reader([raw_line.rstrip("\r\n")]))
            except (csv.Error, StopIteration):
                return False
            names = {_column_name(value) for value in row}
            return bool(names.intersection(self._TIME_FIELDS))
        return False

    def _set_header_if_present(self, state: Dict[str, Any], row: Sequence[str]) -> bool:
        header = tuple(_column_name(value) for value in row)
        columns: Dict[str, int] = {}
        for index, name in enumerate(header):
            if name and name not in columns:
                columns[name] = index
        time_name = next((name for name in self._TIME_FIELDS if name in columns), None)
        if time_name is None:
            return False
        if state["header"] and state["header"] != header:
            self._stats["header_changes"] += 1
        state["header"] = header
        state["columns"] = columns
        state["time_divisor"] = self._time_divisor(time_name)
        return True

    @staticmethod
    def _time_divisor(field: str) -> float:
        if field.endswith("ns"):
            return 1_000_000_000.0
        if field.endswith("us"):
            return 1_000_000.0
        if field in {"timestamp", "timestampms"} or field.endswith("ms"):
            return 1000.0
        return 1.0

    def _ingest_row(self, filename: str, state: Dict[str, Any], row: Sequence[str]) -> bool:
        header = state.get("header")
        if not header or len(row) != len(header):
            self._stats["invalid_rows"] += 1
            return False
        columns = state["columns"]
        time_index = next((columns[name] for name in self._TIME_FIELDS if name in columns), None)
        if time_index is None:
            self._stats["invalid_rows"] += 1
            return False
        raw_timestamp = _timestamp_number(row[time_index])
        if raw_timestamp is None or raw_timestamp < 0.0:
            self._stats["invalid_rows"] += 1
            return False
        signature = tuple(str(value).strip() for value in row)
        previous_timestamp = state.get("last_timestamp")
        seen: Set[Tuple[str, ...]] = state["last_signatures"]
        if previous_timestamp is not None:
            if raw_timestamp < previous_timestamp:
                self._stats["stale_rows"] += 1
                return False
            if raw_timestamp == previous_timestamp and signature in seen:
                self._stats["stale_rows"] += 1
                return False
        if previous_timestamp is None or raw_timestamp > previous_timestamp:
            state["last_timestamp"] = raw_timestamp
            state["last_signatures"] = {signature}
        else:
            seen.add(signature)

        sample_time_s = self._sample_time_s(raw_timestamp, float(state["time_divisor"]))
        values = {
            name: value
            for name, index in columns.items()
            for value in [_finite_number(row[index])]
            if value is not None
        }
        self._apply_kpm_values(
            filename=filename,
            kind=state["kind"],
            cell=state["cell"],
            sample_time_s=sample_time_s,
            row=row,
            columns=columns,
            values=values,
        )
        self._stats["accepted_rows"] += 1
        return True

    def _sample_time_s(self, raw_timestamp: float | int, divisor: float) -> float:
        """Zamienia znacznik KPM na czas symulowany użyteczny na wykresie."""

        if raw_timestamp < self.ABSOLUTE_TIMESTAMP_MIN:
            return raw_timestamp / divisor
        self._uses_relative_kpm_time = True
        if self._timestamp_origin_raw is None:
            self._timestamp_origin_raw = raw_timestamp
        return max(0.0, (raw_timestamp - self._timestamp_origin_raw) / divisor)

    def _apply_kpm_values(
        self,
        filename: str,
        kind: str,
        cell: str,
        sample_time_s: float,
        row: Sequence[str],
        columns: Mapping[str, int],
        values: Mapping[str, float],
    ) -> None:
        ue_id = self._entity_id(row, columns, values)
        if kind == "cu_cp":
            sinr = self._first(values, self._SINR_FIELDS)
            if sinr is not None:
                self._observe("sinr", cell, ue_id or "cell", filename, sample_time_s, sinr)
            return
        if kind == "du":
            prb = self._first(values, self._PRB_FIELDS)
            if prb is not None and prb >= 0.0:
                self._observe("prb", cell, "cell", filename, sample_time_s, prb)
            active_ues = self._first(values, self._ACTIVE_UE_FIELDS)
            if active_ues is not None and active_ues >= 0.0:
                self._observe("active", cell, "cell", filename, sample_time_s, active_ues)
            throughput_kbit = self._first(values, self._DU_THROUGHPUT_KBIT_FIELDS)
            if throughput_kbit is not None and throughput_kbit >= 0.0:
                self._observe(
                    "throughput_direct",
                    cell,
                    ue_id or "cell",
                    filename,
                    sample_time_s,
                    throughput_kbit / 1000.0,
                )
            return
        if kind != "cu_up":
            return

        direct_throughput_kbit = self._first(values, self._UE_THROUGHPUT_KBIT_FIELDS)
        entity = ue_id or "cell"
        if direct_throughput_kbit is not None and direct_throughput_kbit >= 0.0:
            self._observe(
                "throughput_direct",
                cell,
                entity,
                filename,
                sample_time_s,
                direct_throughput_kbit / 1000.0,
            )
        else:
            interval_volume_kbit = self._first(values, self._UE_INTERVAL_VOLUME_KBIT_FIELDS)
            if interval_volume_kbit is not None and interval_volume_kbit >= 0.0:
                self._update_interval_volume(
                    filename,
                    cell,
                    entity,
                    sample_time_s,
                    interval_volume_kbit,
                )

        cell_delay = self._first(values, self._CELL_DELAY_FIELDS)
        if cell_delay is not None and cell_delay >= 0.0:
            self._observe(
                "delay_cell",
                cell,
                "cell",
                filename,
                sample_time_s,
                cell_delay * 0.1,
            )
        else:
            ue_delay = self._first(values, self._UE_DELAY_FIELDS)
            if ue_delay is not None and ue_delay >= 0.0:
                self._observe(
                    "delay_ue",
                    cell,
                    ue_id or "cell",
                    filename,
                    sample_time_s,
                    ue_delay * 0.1,
                )

    @staticmethod
    def _first(values: Mapping[str, float], names: Sequence[str]) -> Optional[float]:
        for name in names:
            value = values.get(name)
            if value is not None:
                return value
        return None

    def _entity_id(
        self,
        row: Sequence[str],
        columns: Mapping[str, int],
        values: Mapping[str, float],
    ) -> Optional[str]:
        for field in self._UE_ID_FIELDS:
            if field not in columns:
                continue
            index = columns[field]
            raw = str(row[index]).strip()
            if not raw:
                continue
            value = values.get(field)
            if value is not None:
                if value < 0.0:
                    continue
                if value.is_integer():
                    return str(int(value))
                return str(value)
            return raw
        return None

    def _update_interval_volume(
        self,
        filename: str,
        cell: str,
        entity: str,
        sample_time_s: float,
        volume_kbit: float,
    ) -> None:
        key = (filename, entity)
        previous_time_s = self._interval_time_states.get(key)
        self._interval_time_states[key] = sample_time_s
        if previous_time_s is None:
            return
        elapsed_s = sample_time_s - previous_time_s
        if elapsed_s <= 0.0:
            return
        rate_mbit_per_s = volume_kbit / elapsed_s / 1000.0
        self._observe(
            "throughput_interval",
            cell,
            entity,
            filename,
            sample_time_s,
            rate_mbit_per_s,
        )

    def _observe(
        self,
        metric: str,
        cell: str,
        entity: str,
        filename: str,
        sample_time_s: float,
        value: float,
    ) -> None:
        key = (metric, cell, entity, filename)
        existing = self._observations.get(key)
        if existing is not None and sample_time_s < existing["time_s"]:
            return
        self._observations[key] = {
            "value": value,
            "time_s": sample_time_s,
            "source": filename,
            "entity": entity,
            "cell": cell,
            "metric": metric,
        }
        if self._ingest_events is not None:
            observation = dict(self._observations[key])
            self._ingest_events.append((sample_time_s, key, observation))

    @staticmethod
    def _empty_snapshot_values() -> Dict[str, Any]:
        return {
            "sample_time_s": None,
            "current_metrics": {
                "throughput_mbit_per_s": None,
                "mean_delay_ms": None,
                "mean_sinr_db": None,
                "p05_sinr_db": None,
                "mean_prb_utilization_pct": None,
                "active_ues": None,
            },
            "per_cell": {},
            "metric_sources": {},
        }

    def _refresh_snapshot(self, append_series: bool) -> None:
        per_cell: Dict[str, Dict[str, Any]] = {}
        metric_sources: Dict[str, List[str]] = {}

        sinr_by_cell = self._latest_entities("sinr")
        prb_by_cell = self._latest_cells("prb")
        active_by_cell = self._latest_cells("active")
        direct_throughput_by_cell = self._latest_entities("throughput_direct")
        interval_throughput_by_cell = self._latest_entities("throughput_interval")
        throughput_by_cell = self._prefer_entities(
            direct_throughput_by_cell, interval_throughput_by_cell
        )
        delay_cell_by_cell = self._latest_cells("delay_cell")
        delay_ue_by_cell = self._latest_entities("delay_ue")

        all_cells: Set[str] = set()
        for values in (
            sinr_by_cell,
            prb_by_cell,
            active_by_cell,
            throughput_by_cell,
            delay_cell_by_cell,
            delay_ue_by_cell,
        ):
            all_cells.update(values)

        for cell in sorted(all_cells, key=self._cell_sort_key):
            cell_values: Dict[str, Any] = {}
            samples: List[float] = []

            sinr = sinr_by_cell.get(cell, [])
            if sinr:
                cell_values["mean_sinr_db"] = _mean(item["value"] for item in sinr)
                cell_values["p05_sinr_db"] = _p05(item["value"] for item in sinr)
                samples.extend(item["time_s"] for item in sinr)

            prb = prb_by_cell.get(cell)
            if prb is not None:
                cell_values["prb_utilization_pct"] = prb["value"]
                samples.append(prb["time_s"])

            active = active_by_cell.get(cell)
            if active is not None:
                cell_values["active_ues"] = active["value"]
                samples.append(active["time_s"])

            throughput_ues = throughput_by_cell.get(cell, [])
            if throughput_ues:
                cell_values["throughput_mbit_per_s"] = sum(
                    item["value"] for item in throughput_ues
                )
                samples.extend(item["time_s"] for item in throughput_ues)

            delay_cell = delay_cell_by_cell.get(cell)
            delay_ues = delay_ue_by_cell.get(cell, [])
            if delay_cell is not None:
                cell_values["mean_delay_ms"] = delay_cell["value"]
                samples.append(delay_cell["time_s"])
            elif delay_ues:
                cell_values["mean_delay_ms"] = _mean(item["value"] for item in delay_ues)
                samples.extend(item["time_s"] for item in delay_ues)

            if samples:
                cell_values["sample_time_s"] = max(samples)
                per_cell[cell] = cell_values

        metrics = self._aggregate_metrics(
            per_cell,
            sinr_by_cell,
            prb_by_cell,
            active_by_cell,
            throughput_by_cell,
            delay_cell_by_cell,
            delay_ue_by_cell,
        )
        for metric, observations in self._metric_observations(
            sinr_by_cell,
            prb_by_cell,
            active_by_cell,
            throughput_by_cell,
            delay_cell_by_cell,
            delay_ue_by_cell,
        ).items():
            if metrics.get(metric) is not None:
                metric_sources[metric] = sorted(
                    {item["source"] for item in observations}
                )
        if self._last_integrated_time_s is not None:
            metric_sources["delivered_mbit"] = sorted(
                set(metric_sources.get("throughput_mbit_per_s", []))
            )

        times = [
            value["sample_time_s"]
            for value in per_cell.values()
            if value.get("sample_time_s") is not None
        ]
        sample_time_s = max(times) if times else None
        throughput = metrics.get("throughput_mbit_per_s")
        if append_series and sample_time_s is not None and throughput is not None:
            previous_time = self._last_integrated_time_s
            previous_rate = self._last_integrated_throughput
            if previous_time is None:
                self._last_integrated_time_s = sample_time_s
                self._last_integrated_throughput = throughput
            elif sample_time_s > previous_time:
                # Trapezoidal integration of the observed PDCP bitrate. This
                # is an estimate of delivered PDCP data, not UDP sink bytes.
                self._delivered_mbit += (
                    (previous_rate + throughput) * 0.5 * (sample_time_s - previous_time)
                    if previous_rate is not None
                    else throughput * (sample_time_s - previous_time)
                )
                self._last_integrated_time_s = sample_time_s
                self._last_integrated_throughput = throughput
        self._last_snapshot = {
            "sample_time_s": sample_time_s,
            "integrated_delivered_mbit": (
                self._delivered_mbit if self._last_integrated_time_s is not None else None
            ),
            "current_metrics": metrics,
            "per_cell": per_cell,
            "metric_sources": metric_sources,
        }
        if append_series and sample_time_s is not None:
            for metric, value in metrics.items():
                if value is not None and metric in self._series:
                    self._append_point(metric, sample_time_s, value)
            if self._last_integrated_time_s is not None:
                self._append_point("delivered_mbit", sample_time_s, self._delivered_mbit)

    @staticmethod
    def _cell_sort_key(cell: str) -> Tuple[int, Any]:
        numeric = _finite_number(cell)
        return (0, numeric) if numeric is not None else (1, cell)

    def _latest_cells(self, metric: str) -> Dict[str, Observation]:
        result: Dict[str, Observation] = {}
        for observation in self._observations.values():
            if observation["metric"] != metric:
                continue
            cell = observation["cell"]
            current = result.get(cell)
            if current is None or self._newer(observation, current):
                result[cell] = observation
        return result

    def _latest_entities(self, metric: str) -> Dict[str, List[Observation]]:
        by_entity: Dict[Tuple[str, str], Observation] = {}
        for observation in self._observations.values():
            if observation["metric"] != metric:
                continue
            key = (observation["cell"], observation["entity"])
            current = by_entity.get(key)
            if current is None or self._newer(observation, current):
                by_entity[key] = observation
        result: Dict[str, List[Observation]] = {}
        for observation in by_entity.values():
            result.setdefault(observation["cell"], []).append(observation)
        return result

    @staticmethod
    def _prefer_entities(
        primary: Mapping[str, Sequence[Observation]],
        fallback: Mapping[str, Sequence[Observation]],
    ) -> Dict[str, List[Observation]]:
        """Wybiera jawny bitrate, a dla brakujących UE wolumen raportowy."""

        result: Dict[str, List[Observation]] = {}
        for cell in set(primary).union(fallback):
            selected: Dict[str, Observation] = {
                item["entity"]: item for item in primary.get(cell, [])
            }
            for item in fallback.get(cell, []):
                selected.setdefault(item["entity"], item)
            if selected:
                result[cell] = list(selected.values())
        return result

    @staticmethod
    def _newer(left: Observation, right: Observation) -> bool:
        return (left["time_s"], left["source"]) >= (right["time_s"], right["source"])

    def _aggregate_metrics(
        self,
        per_cell: Mapping[str, Mapping[str, Any]],
        sinr_by_cell: Mapping[str, Sequence[Observation]],
        prb_by_cell: Mapping[str, Observation],
        active_by_cell: Mapping[str, Observation],
        throughput_by_cell: Mapping[str, Sequence[Observation]],
        delay_cell_by_cell: Mapping[str, Observation],
        delay_ue_by_cell: Mapping[str, Sequence[Observation]],
    ) -> Dict[str, Optional[float]]:
        sinr = [
            item["value"]
            for values in sinr_by_cell.values()
            for item in values
        ]
        prb = [item["value"] for item in prb_by_cell.values()]
        active = [item["value"] for item in active_by_cell.values()]

        throughput = [
            sum(item["value"] for item in values)
            for values in throughput_by_cell.values()
        ]

        delay = []
        for cell in set(delay_cell_by_cell).union(delay_ue_by_cell):
            direct = delay_cell_by_cell.get(cell)
            if direct is not None:
                delay.append(direct["value"])
            else:
                delay.extend(item["value"] for item in delay_ue_by_cell[cell])

        return {
            "throughput_mbit_per_s": sum(throughput) if throughput else None,
            "mean_delay_ms": _mean(delay),
            "mean_sinr_db": _mean(sinr),
            "p05_sinr_db": _p05(sinr),
            "mean_prb_utilization_pct": _mean(prb),
            # To jest suma raportów DRB.MeanActiveUeDl per komórka.  Przy
            # wielołączności nie oznacza liczby unikalnych IMSI.
            "active_ues": sum(active) if active else None,
        }

    @staticmethod
    def _metric_observations(
        sinr_by_cell: Mapping[str, Sequence[Observation]],
        prb_by_cell: Mapping[str, Observation],
        active_by_cell: Mapping[str, Observation],
        throughput_by_cell: Mapping[str, Sequence[Observation]],
        delay_cell_by_cell: Mapping[str, Observation],
        delay_ue_by_cell: Mapping[str, Sequence[Observation]],
    ) -> Dict[str, List[Observation]]:
        throughput = [item for values in throughput_by_cell.values() for item in values]
        delay: List[Observation] = []
        for cell in set(delay_cell_by_cell).union(delay_ue_by_cell):
            direct = delay_cell_by_cell.get(cell)
            delay.extend([direct] if direct is not None else delay_ue_by_cell[cell])
        sinr = [item for values in sinr_by_cell.values() for item in values]
        return {
            "throughput_mbit_per_s": throughput,
            "mean_delay_ms": delay,
            "mean_sinr_db": sinr,
            "p05_sinr_db": sinr,
            "mean_prb_utilization_pct": list(prb_by_cell.values()),
            "active_ues": list(active_by_cell.values()),
        }

    def _append_point(self, metric: str, sample_time_s: float, value: float) -> None:
        points = self._series[metric]
        point = {"x": sample_time_s, "y": value}
        if points and sample_time_s < points[-1]["x"]:
            return
        if points and points[-1]["x"] == sample_time_s:
            points[-1] = point
        else:
            points.append(point)
