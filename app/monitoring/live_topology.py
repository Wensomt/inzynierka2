"""Przyrostowy model rzeczywistej topologii zapisywanej przez ns-3.

Scenariusz ``my_energy_scenario_optimized.cc`` zapisuje ``gnbs.txt`` oraz
``ue_position.txt`` podczas działania.  Ten moduł wyciąga z tych plików
pozycje gNB, UE i zgłoszone przez scenariusz przypisanie UE do komórki.
Nie wylicza ani nie zgaduje asocjacji radiowej samodzielnie.
"""

from __future__ import annotations

import csv
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def _field_name(value: Any) -> str:
    return str(value).lstrip("\ufeff").strip().lower().replace(" ", "").replace("-", "_")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _entity_id(value: Any) -> Optional[str]:
    number = _finite(value)
    if number is not None:
        if number < 0:
            return None
        if number.is_integer():
            return str(int(number))
    text = str(value).strip()
    return text or None


def _boolean(value: Any) -> Optional[bool]:
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _index_for(header: Sequence[str], names: Iterable[str]) -> Optional[int]:
    known = set(names)
    for index, name in enumerate(header):
        if _field_name(name) in known:
            return index
    return None


def _safe_cell(row: Sequence[str], index: Optional[int]) -> Optional[str]:
    if index is None or index >= len(row):
        return None
    return row[index]


class LiveTopologyCollector:
    """Pamięta ostatni kompletny stan topologii z plików scenariusza.

    Wiersze w obu plikach są dopisywane.  Kolektor wybiera najnowszy zapis dla
    danego obiektu; dzięki temu może dostać nakładający się fragment pliku po
    każdym odczycie i nie powiela punktów.  Limit chroni aplikację Tkinter
    przed nieograniczonym wzrostem przy nietypowo dużej liczbie UE.
    """

    DEFAULT_MAX_ENTITIES = 2000
    DEFAULT_MAX_TRAIL_POINTS = 16

    def __init__(
        self,
        max_entities: int = DEFAULT_MAX_ENTITIES,
        max_trail_points: int = DEFAULT_MAX_TRAIL_POINTS,
    ) -> None:
        try:
            capacity = int(max_entities)
        except (TypeError, ValueError):
            raise ValueError("max_entities musi być dodatnią liczbą całkowitą")
        if capacity < 1:
            raise ValueError("max_entities musi być dodatnią liczbą całkowitą")
        try:
            trail_capacity = int(max_trail_points)
        except (TypeError, ValueError):
            raise ValueError("max_trail_points musi być dodatnią liczbą całkowitą")
        if trail_capacity < 1:
            raise ValueError("max_trail_points musi być dodatnią liczbą całkowitą")
        self.max_entities = capacity
        self.max_trail_points = trail_capacity
        self._cells: Dict[str, Dict[str, Any]] = {}
        self._ues: Dict[str, Dict[str, Any]] = {}
        self._ue_trails: Dict[str, List[Dict[str, float]]] = {}
        self._cell_times: Dict[str, float] = {}
        self._ue_times: Dict[str, float] = {}
        self._latest_sample_time_s: Optional[float] = None
        self._bound_x: Optional[float] = None
        self._bound_y: Optional[float] = None
        self._saw_cells = False
        self._saw_enbs = False
        self._saw_ues = False
        self._stats: Dict[str, int] = {
            "cell_rows": 0,
            "ue_rows": 0,
            "invalid_rows": 0,
            "truncated_cells": 0,
            "truncated_ues": 0,
        }

    def reset(self) -> None:
        self._cells.clear()
        self._ues.clear()
        self._ue_trails.clear()
        self._cell_times.clear()
        self._ue_times.clear()
        self._latest_sample_time_s = None
        self._bound_x = None
        self._bound_y = None
        self._saw_cells = False
        self._saw_enbs = False
        self._saw_ues = False
        for key in self._stats:
            self._stats[key] = 0

    def ingest_cells_csv(self, text: str) -> int:
        """Przyjmuje fragment ``gnbs.txt`` i zwraca liczbę odświeżonych gNB."""

        return self._ingest(text, "cells")

    def ingest_enbs_csv(self, text: str) -> int:
        """Przyjmuje fragment ``enbs.txt`` z rzeczywistymi LTE anchorami."""

        return self._ingest(text, "enbs")

    def ingest_ues_csv(self, text: str) -> int:
        """Przyjmuje fragment ``ue_position.txt`` i zwraca liczbę odświeżonych UE."""

        return self._ingest(text, "ues")

    def snapshot(self) -> Dict[str, Any]:
        """Zwraca dane gotowe do przesłania do GUI bez danych wymyślonych."""

        bounds = None
        if self._bound_x is not None and self._bound_y is not None:
            bounds = (0.0, self._bound_x, 0.0, self._bound_y)
        source_parts = []
        if self._saw_cells:
            source_parts.append("gnbs.txt")
        if self._saw_enbs:
            source_parts.append("enbs.txt")
        if self._saw_ues:
            source_parts.append("ue_position.txt")
        cells = [dict(self._cells[key]) for key in self._sorted_keys(self._cells)]
        ues = [dict(self._ues[key]) for key in self._sorted_keys(self._ues)]
        if cells or ues:
            message = "Pozycje i przypisania pochodzą bezpośrednio z plików scenariusza."
            if ues and not any(item.get("serving_cell") for item in ues):
                message = "Scenariusz podał pozycje UE, ale nie zgłosił przypisania komórki w tej próbce."
        else:
            message = "Scenariusz nie zapisał jeszcze danych topologii."
        return {
            "cells": cells,
            "ues": ues,
            "sample_time_s": self._latest_sample_time_s,
            "source": " + ".join(source_parts) or "Brak danych topologii",
            "mode": "reported" if source_parts else "unavailable",
            "message": message,
            "bounds": bounds,
            "data_status": dict(self._stats),
        }

    @staticmethod
    def _sorted_keys(values: Mapping[str, Any]) -> List[str]:
        def order(key: str) -> Tuple[int, Any]:
            number = _finite(key)
            if number is not None:
                return (0, number)
            return (1, key)

        return sorted(values, key=order)

    def _ingest(self, text: str, kind: str) -> int:
        if not isinstance(text, str) or not text.strip():
            return 0
        try:
            rows = list(csv.reader(text.splitlines()))
        except csv.Error:
            self._stats["invalid_rows"] += 1
            return 0
        if not rows:
            return 0
        header = rows[0]
        timestamp_index = _index_for(header, ("timestamp", "time", "time_ms"))
        id_index = _index_for(header, ("id", "cell", "cell_id", "imsi", "ue_id"))
        x_index = _index_for(header, ("x", "position_x", "pos_x"))
        y_index = _index_for(header, ("y", "position_y", "pos_y"))
        if None in (timestamp_index, id_index, x_index, y_index):
            self._stats["invalid_rows"] += max(1, len(rows) - 1)
            return 0
        if kind in {"cells", "enbs"}:
            state_index = _index_for(header, ("esstate", "energy_state", "switched_off"))
            energy_index = _index_for(header, ("currec", "current_energy", "energy_j"))
            simulation_index = _index_for(header, ("simid", "simulation_id"))
            if kind == "cells":
                self._saw_cells = True
            else:
                self._saw_enbs = True
            return self._ingest_cells(
                rows[1:], timestamp_index, id_index, x_index, y_index,
                state_index, energy_index, simulation_index,
                "gNB" if kind == "cells" else "eNB",
            )
        serving_index = _index_for(header, ("cell", "serving_cell", "cell_id", "servingcell"))
        type_index = _index_for(header, ("type", "ue_type"))
        simulation_index = _index_for(header, ("simid", "simulation_id"))
        self._saw_ues = True
        return self._ingest_ues(
            rows[1:], timestamp_index, id_index, x_index, y_index,
            serving_index, type_index, simulation_index,
        )

    def _ingest_cells(
        self,
        rows: Iterable[Sequence[str]],
        timestamp_index: int,
        id_index: int,
        x_index: int,
        y_index: int,
        state_index: Optional[int],
        energy_index: Optional[int],
        simulation_index: Optional[int],
        role: str,
    ) -> int:
        updated = 0
        for row in rows:
            timestamp = _finite(_safe_cell(row, timestamp_index))
            identifier = _entity_id(_safe_cell(row, id_index))
            x = _finite(_safe_cell(row, x_index))
            y = _finite(_safe_cell(row, y_index))
            # id=0 is only the scenario's plot boundary, not a gNB.  It is
            # nevertheless useful as exact canvas bounds supplied by ns-3.
            if identifier == "0" and x is not None and y is not None:
                self._bound_x = max(self._bound_x or x, x)
                self._bound_y = max(self._bound_y or y, y)
                continue
            if timestamp is None or identifier is None or x is None or y is None:
                self._stats["invalid_rows"] += 1
                continue
            if identifier not in self._cells and len(self._cells) >= self.max_entities:
                self._stats["truncated_cells"] += 1
                continue
            existing = self._cells.get(identifier)
            # Zgodnie z parserem archiwalnym mmWave gNB ma pierwszeństwo nad
            # LTE anchorem przy ewentualnej kolizji identyfikatora.
            if role == "eNB" and existing and existing.get("role") == "gNB":
                continue
            previous = self._cell_times.get(identifier)
            if previous is not None and timestamp <= previous:
                continue
            record: Dict[str, Any] = {"id": identifier, "x": x, "y": y, "role": role}
            tx_sleeping = _boolean(_safe_cell(row, state_index))
            if tx_sleeping is not None:
                record["tx_sleeping"] = tx_sleeping
            energy = _finite(_safe_cell(row, energy_index))
            if energy is not None:
                record["current_energy_j"] = energy
            sample_time_s = self._sample_time(timestamp, _safe_cell(row, simulation_index))
            if sample_time_s is not None:
                record["sample_time_s"] = sample_time_s
                self._update_sample_time(sample_time_s)
            self._cells[identifier] = record
            self._cell_times[identifier] = timestamp
            self._stats["cell_rows"] += 1
            updated += 1
        return updated

    def _ingest_ues(
        self,
        rows: Iterable[Sequence[str]],
        timestamp_index: int,
        id_index: int,
        x_index: int,
        y_index: int,
        serving_index: Optional[int],
        type_index: Optional[int],
        simulation_index: Optional[int],
    ) -> int:
        updated = 0
        for row in rows:
            timestamp = _finite(_safe_cell(row, timestamp_index))
            identifier = _entity_id(_safe_cell(row, id_index))
            x = _finite(_safe_cell(row, x_index))
            y = _finite(_safe_cell(row, y_index))
            if timestamp is None or identifier is None or x is None or y is None:
                self._stats["invalid_rows"] += 1
                continue
            if identifier not in self._ues and len(self._ues) >= self.max_entities:
                self._stats["truncated_ues"] += 1
                continue
            previous = self._ue_times.get(identifier)
            if previous is not None and timestamp <= previous:
                continue
            record: Dict[str, Any] = {"id": identifier, "x": x, "y": y, "role": "UE"}
            serving_cell = _entity_id(_safe_cell(row, serving_index))
            if serving_cell is not None:
                record["serving_cell"] = serving_cell
            ue_type = _safe_cell(row, type_index)
            if ue_type is not None and ue_type.strip():
                record["type"] = ue_type.strip()
            sample_time_s = self._sample_time(timestamp, _safe_cell(row, simulation_index))
            if sample_time_s is not None:
                record["sample_time_s"] = sample_time_s
                self._update_sample_time(sample_time_s)
            trail = self._ue_trails.setdefault(identifier, [])
            trail_point: Dict[str, float] = {"x": x, "y": y}
            if sample_time_s is not None:
                trail_point["sample_time_s"] = sample_time_s
            if not trail or trail[-1] != trail_point:
                trail.append(trail_point)
                if len(trail) > self.max_trail_points:
                    del trail[:-self.max_trail_points]
            if len(trail) > 1:
                # Ślad składa się wyłącznie z pozycji kolejnych ramek ns-3.
                # Jest ograniczony, aby tysiące UE nie rozrosło payloadu GUI.
                record["trail"] = [dict(point) for point in trail]
            self._ues[identifier] = record
            self._ue_times[identifier] = timestamp
            self._stats["ue_rows"] += 1
            updated += 1
        return updated

    @staticmethod
    def _sample_time(timestamp: float, simulation_origin: Optional[str]) -> Optional[float]:
        origin = _finite(simulation_origin)
        if origin is None:
            return None
        seconds = (timestamp - origin) / 1000.0
        return seconds if seconds >= -0.001 else None

    def _update_sample_time(self, value: float) -> None:
        if self._latest_sample_time_s is None or value >= self._latest_sample_time_s:
            self._latest_sample_time_s = value
