"""Ograniczony, przyrostowy odczyt rzeczywistych decyzji xAppa.

Zasób ``xapp_my_energy_optimized.c`` zapisuje decyzje do CSV wskazanego przez
``EE_DECISION_LOG``.  Ten moduł nie uruchamia procesu ani nie interpretuje
polityki; przechowuje wyłącznie ostatnie, poprawnie zapisane rekordy.  Dzięki
temu dashboard nie musi pobierać ani trzymać pełnej historii długiego runu.
"""

from __future__ import annotations

import csv
import io
import math
from collections import deque
from typing import Any, Deque, Dict, Mapping, Optional, Sequence, Tuple


Decision = Dict[str, Any]


def _number(value: Any) -> Optional[float]:
    """Zwraca skończoną liczbę lub ``None`` dla niepoprawnego pola CSV."""

    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _header_key(value: Any) -> str:
    return str(value or "").lstrip("\ufeff").strip().lower().replace("-", "_").replace(" ", "_")


class XappDecisionCollector:
    """Przechowuje ograniczoną historię wierszy decyzji z plików CSV xAppa."""

    DEFAULT_MAX_EVENTS = 200

    _FIELDS = {
        "time_s": ("time", "timestamp", "time_s"),
        "event": ("event", "action", "decision"),
        "cell_id": ("cell_id", "cell"),
        "ue_id": ("ue_id", "ue"),
        "ue_count": ("ue_count", "connected_ues"),
        "serving_sinr_db": ("serving_sinr_db", "serving_sinr"),
        "target_cell": ("target_cell", "target"),
        "target_sinr_db": ("target_sinr_db", "target_sinr"),
        "reason": ("reason", "message"),
    }

    def __init__(self, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        try:
            capacity = int(max_events)
        except (TypeError, ValueError) as exc:
            raise ValueError("Limit historii decyzji musi być liczbą całkowitą") from exc
        if capacity < 1:
            raise ValueError("Limit historii decyzji musi być dodatni")
        self.max_events = capacity
        self._events: Deque[Decision] = deque(maxlen=capacity)
        # Klucze deduplikacji są ograniczone tym samym rzędem wielkości co
        # widoczna historia. Nie pozwalamy powtarzanemu odczytowi ogona pliku
        # rosnąć w pamięci bez końca.
        self._seen: set[Tuple[Any, ...]] = set()
        self._seen_order: Deque[Tuple[Any, ...]] = deque(maxlen=capacity * 3)
        self._accepted_rows = 0
        self._duplicate_rows = 0
        self._invalid_rows = 0
        self._source_files: set[str] = set()

    def reset(self) -> None:
        self._events.clear()
        self._seen.clear()
        self._seen_order.clear()
        self._accepted_rows = 0
        self._duplicate_rows = 0
        self._invalid_rows = 0
        self._source_files.clear()

    def ingest_files(self, files: Mapping[str, str]) -> int:
        """Przyjmuje ogony dozwolonych plików decyzji i zwraca nowe rekordy."""

        if not isinstance(files, Mapping):
            raise TypeError("Pliki decyzji xAppa muszą być mapą nazwa -> tekst")
        accepted = 0
        for raw_source, raw_text in sorted(files.items(), key=lambda item: str(item[0])):
            source = str(raw_source or "").strip()
            if not source or not isinstance(raw_text, str) or not raw_text:
                continue
            accepted += self._ingest_file(source, raw_text)
        return accepted

    def snapshot(self) -> Dict[str, Any]:
        return {
            "events": [dict(item) for item in self._events],
            "data_status": {
                "accepted_rows": self._accepted_rows,
                "duplicate_rows": self._duplicate_rows,
                "invalid_rows": self._invalid_rows,
                "source_file_count": len(self._source_files),
                "source_files": sorted(self._source_files),
                "retention_limit": self.max_events,
            },
        }

    def _ingest_file(self, source: str, text: str) -> int:
        # Odczytujący przekazuje wyłącznie pełne wiersze. Mimo to csv.Error
        # pozostaje lokalnym błędem pojedynczego pliku, a nie błędem runu.
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error:
            self._invalid_rows += 1
            return 0
        if not rows:
            return 0
        self._source_files.add(source)
        accepted = 0
        for raw in rows:
            if not isinstance(raw, Mapping):
                self._invalid_rows += 1
                continue
            decision = self._decision_from_row(source, raw)
            if decision is None:
                self._invalid_rows += 1
                continue
            key = self._event_key(decision)
            if key in self._seen:
                self._duplicate_rows += 1
                continue
            if len(self._seen_order) == self._seen_order.maxlen:
                expired = self._seen_order.popleft()
                self._seen.discard(expired)
            self._seen_order.append(key)
            self._seen.add(key)
            self._events.append(decision)
            self._accepted_rows += 1
            accepted += 1
        return accepted

    def _decision_from_row(self, source: str, raw: Mapping[str, Any]) -> Optional[Decision]:
        normalized = {_header_key(key): value for key, value in raw.items()}

        def value(name: str) -> Any:
            for alias in self._FIELDS[name]:
                if alias in normalized:
                    return normalized[alias]
            return None

        # The energy xApp uses ``time`` in seconds.  The supplied ML xApp
        # records ``time_us`` instead; converting that real field is safe and
        # lets the same bounded history present both existing formats.
        if "time_us" in normalized:
            raw_time_us = _number(normalized.get("time_us"))
            time_s = raw_time_us / 1_000_000.0 if raw_time_us is not None else None
        else:
            time_s = _number(value("time_s"))
        event = str(value("event") or "").strip()
        if time_s is None or time_s < 0.0 or not event:
            return None
        result: Decision = {
            "time_s": time_s,
            "event": event,
            "source": source,
        }
        for key in ("cell_id", "ue_id", "ue_count", "serving_sinr_db", "target_cell", "target_sinr_db"):
            number = _number(value(key))
            if number is not None:
                result[key] = number
        reason = value("reason")
        if reason is not None and str(reason).strip():
            result["reason"] = str(reason).strip()
        return result

    @staticmethod
    def _event_key(event: Mapping[str, Any]) -> Tuple[Any, ...]:
        return (
            event.get("source"),
            event.get("time_s"),
            event.get("event"),
            event.get("cell_id"),
            event.get("ue_id"),
            event.get("ue_count"),
            event.get("serving_sinr_db"),
            event.get("target_cell"),
            event.get("target_sinr_db"),
            event.get("reason"),
        )


__all__ = ["XappDecisionCollector"]
