"""Planowanie twardego budżetu czasu rzeczywistego dla kolejki.

Moduł celowo nie wylicza czasu modelowanego z wydajności komputera.  Każdy
przebieg dostaje równy udział w *rzeczywistym* czasie aktywnej pracy ns-3 i po
tej chwili otrzymuje żądanie zatrzymania.  Odczytany z pliku live ``simTime``
jest wyłącznie obserwacją wyniku danego przebiegu; zegar rzeczywisty nigdy nie
staje się czasem modelowanym ani nie zmienia parametrów następnego przebiegu.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


def finite_positive_seconds(value: Any) -> Optional[float]:
    """Zwraca dodatnią, skończoną liczbę sekund albo ``None``.

    Wartość ``None`` oznacza wyłączony tryb.  Nie traktujemy zera jako
    domyślnego czasu, ponieważ dawałoby to pozornie poprawny plan bez
    rzeczywistego budżetu.
    """

    if value in (None, ""):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError("Łączny czas rzeczywisty musi być liczbą sekund większą od zera.")
    if not math.isfinite(seconds) or seconds <= 0.0:
        raise ValueError("Łączny czas rzeczywisty musi być liczbą sekund większą od zera.")
    return seconds


@dataclass(frozen=True)
class QueueTimeBudget:
    """Jawnie wybrany całkowity budżet zegara rzeczywistego kolejki.

    To nie jest bezwzględny limit całego procesu desktopowego: przygotowanie
    kontenera, RIC i archiwizacja pozostają poza budżetem.  Dzięki temu
    pierwszy, już gotowy przebieg dostaje twardy limit aktywnego czasu
    rzeczywistego.  Wynikowy czas modelowany pochodzi wyłącznie z ns-3.
    """

    total_real_time_s: float
    source: str = "session"

    def per_run_real_time_s(self, run_count: int) -> float:
        count = int(run_count)
        if count < 1:
            raise ValueError("Kolejka musi zawierać co najmniej jedną symulację.")
        return self.total_real_time_s / float(count)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "total_real_time_s": self.total_real_time_s,
            "source": self.source,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Optional["QueueTimeBudget"]:
        """Odczytuje opcjonalny blok ``queue_timing`` z config.json.

        Dopuszczamy zarówno ``total_real_time_s``, jak i wcześniejszą
        intuicyjną nazwę ``target_total_real_time_s``.  Brak bloku lub
        ``enabled: false`` oznacza zwykłą kolejkę bez kalibracji.
        """

        if not isinstance(value, Mapping) or not value:
            return None
        if value.get("enabled") is False:
            return None
        raw = value.get("total_real_time_s", value.get("target_total_real_time_s"))
        seconds = finite_positive_seconds(raw)
        if seconds is None:
            if value.get("enabled"):
                raise ValueError("Włączony budżet kolejki wymaga total_real_time_s.")
            return None
        return cls(seconds, source="config")


@dataclass
class QueueTimePlan:
    """Stan jednej kolejki z równym aktywnym limitem czasu dla każdego runu."""

    budget: QueueTimeBudget
    simulation_ids: List[str]
    original_rng_runs: Dict[str, Any]
    # Pola zachowane dla zgodności z zapisanymi sesjami.  W aktualnym trybie
    # nie kalibrują one ``simTime`` kolejnych runów.
    calibrated_sim_time_s: Optional[float] = None
    calibration_active_elapsed_s: Optional[float] = None
    calibration_state: str = "per_run_hard_real_time"
    calibration_error: Optional[str] = None
    # Brak pliku live nie może anulować kolejki ani zmienić ustawionego przez
    # użytkownika ``simTime``.  Jest raportowany wyłącznie przy danym runie.
    calibration_warning: Optional[str] = None
    calibration_duration_source: Optional[str] = None
    active_simulation_id: Optional[str] = None
    # Identyfikator elementu aktualnie przygotowywanego albo uruchomionego.
    # Nie steruje limitem ns-3; pozwala jedynie uczciwie oszacować pozostały
    # narzut rozruchu po zaobserwowaniu poprzednich runów.
    current_simulation_id: Optional[str] = None
    active_elapsed_real_s: Optional[float] = None
    active_simulated_time_s: Optional[float] = None
    observed_simulated_times_s: Dict[str, float] = field(default_factory=dict)
    completed_active_real_s: Dict[str, float] = field(default_factory=dict)
    completed_setup_overhead_real_s: Dict[str, float] = field(default_factory=dict)
    finished_simulation_ids: List[str] = field(default_factory=list)

    @property
    def run_count(self) -> int:
        return len(self.simulation_ids)

    @property
    def per_run_real_time_s(self) -> float:
        return self.budget.per_run_real_time_s(self.run_count)

    @property
    def calibration_simulation_id(self) -> Optional[str]:
        return self.simulation_ids[0] if self.simulation_ids else None

    @property
    def shared_rng_run(self) -> Any:
        simulation_id = self.calibration_simulation_id
        return self.original_rng_runs.get(simulation_id) if simulation_id else None

    @property
    def rng_was_consistent(self) -> bool:
        values = [
            self._rng_key(self.original_rng_runs.get(item_id))
            for item_id in self.simulation_ids
        ]
        return len(set(values)) <= 1

    def instruction_for(self, simulation_id: str, index: int) -> Dict[str, Any]:
        """Metadane zapisywane wraz z przebiegiem, także przed jego startem."""

        return {
            "mode": "per_run_real_time_budget",
            "state": "waiting",
            "queue_target_total_active_real_s": self.budget.total_real_time_s,
            "per_run_target_active_real_s": self.per_run_real_time_s,
            "run_index": int(index),
            "run_count": self.run_count,
            "original_rng_run": self.original_rng_runs.get(simulation_id),
            "rng_run": self.shared_rng_run,
            "rng_policy": "shared_first_run",
            "rng_was_consistent": self.rng_was_consistent,
            # To limit zegara rzeczywistego każdego gotowego przebiegu.  Nie
            # jest ani wartością simTime, ani miękką ETA.
            "cutoff_policy": "hard_real_time_each_run",
            "sim_time_policy": "observational_only",
        }

    def to_dict(self) -> Dict[str, Any]:
        result = self.budget.to_dict()
        result.update(
            {
                "run_count": self.run_count,
                "per_run_target_active_real_s": self.per_run_real_time_s,
                "calibration_simulation_id": self.calibration_simulation_id,
                "calibrated_sim_time_s": self.calibrated_sim_time_s,
                "calibration_active_elapsed_s": self.calibration_active_elapsed_s,
                "calibration_state": self.calibration_state,
                "calibration_error": self.calibration_error,
                "calibration_warning": self.calibration_warning,
                "calibration_duration_source": self.calibration_duration_source,
                "original_rng_runs": dict(self.original_rng_runs),
                "shared_rng_run": self.shared_rng_run,
                "rng_policy": "shared_first_run",
                "rng_was_consistent": self.rng_was_consistent,
                "active_simulation_id": self.active_simulation_id,
                "current_simulation_id": self.current_simulation_id,
                "active_elapsed_real_s": self.active_elapsed_real_s,
                "active_simulated_time_s": self.active_simulated_time_s,
                "observed_simulated_times_s": dict(self.observed_simulated_times_s),
                "completed_active_real_s": dict(self.completed_active_real_s),
                "completed_setup_overhead_real_s": dict(self.completed_setup_overhead_real_s),
                "finished_simulation_ids": list(self.finished_simulation_ids),
                "budget_mode": "per_run_hard_real_time",
                "sim_time_policy": "observational_only",
            }
        )
        return result

    @staticmethod
    def _rng_key(value: Any) -> str:
        # Nie normalizujemy wartości zapisanej przez użytkownika.  Klucz służy
        # wyłącznie do komunikatu, czy wszystkie przebiegi używają tego samego
        # numeru RngRun.
        return repr(value)
