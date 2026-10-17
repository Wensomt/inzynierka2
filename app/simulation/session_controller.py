from __future__ import annotations

import copy
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from app.data.storage import SimulationStorage

from .models import Simulation, SimulationConfiguration, SimulationStatus, now_utc
from .queue_timing import QueueTimeBudget, QueueTimePlan, finite_positive_seconds
from .runner import SimulationRunner
from .validation import validate_configuration


@dataclass
class QueueEvent:
    simulation_id: Optional[str]
    phase: str
    message: str
    current: int = 0
    total: int = 0
    # Payload is a snapshot produced in the worker.  Tkinter only consumes it;
    # it never reaches into a live Docker object from the UI thread.
    payload: Dict[str, Any] = field(default_factory=dict)


def default_configuration(application_config: Dict[str, Any]) -> SimulationConfiguration:
    scenarios = dict(application_config.get("scenarios") or {})
    scenario = "optimized" if "optimized" in scenarios else next(iter(scenarios), "")
    allowed = set((scenarios.get(scenario) or {}).get("allowed_params") or [])
    defaults: Dict[str, Any] = {
        "simTime": 1000,
        "RngRun": 1,
        "N_MmWaveEnbNodes": 4,
        "N_Ues": 3,
        "Bandwidth": 20_000_000,
        "CenterFrequency": 3_500_000_000,
        "UdpPacketSizeBytes": 200,
        "UdpIntervalUs": 500,
        "IntersideDistanceUEs": 500,
        "IntersideDistanceCells": 600,
        "indicationPeriodicity": 0.1,
    }
    if allowed:
        defaults = {key: value for key, value in defaults.items() if key in allowed}
    return SimulationConfiguration(scenario=scenario, parameters=defaults, xapp="none")


class SessionController:
    """Zarządza zakładkami bieżącej sesji i prostą kolejką sekwencyjną."""

    # Procesy mogą wygenerować tysiące wierszy w krótkim czasie.  Pełna
    # historia należy do plików logu runu, a kolejka dla Tkintera potrzebuje
    # wyłącznie ostatniego, ograniczonego fragmentu.
    MAX_PENDING_LOG_RECORDS = 2000
    LOG_BATCH_SIZE = 250

    def __init__(
        self,
        application_config: Dict[str, Any],
        storage: SimulationStorage,
        runner: SimulationRunner,
    ) -> None:
        self.application_config = application_config
        self.storage = storage
        self.runner = runner
        self.simulations: List[Simulation] = []
        self.events: "queue.Queue[QueueEvent]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._progress_by_id: Dict[str, float] = {}
        self._pending_logs: Dict[Tuple[Optional[str], str], Deque[Dict[str, Any]]] = {}
        self._dropped_log_records: Dict[Tuple[Optional[str], str], int] = {}
        self._queue_time_budget: Optional[QueueTimeBudget] = None
        self._queue_time_budget_error: Optional[str] = None
        self._queue_time_plan: Optional[QueueTimePlan] = None
        try:
            configured = QueueTimeBudget.from_mapping(
                dict(application_config.get("queue_timing") or {})
            )
        except ValueError as exc:
            # Nie blokujemy otwarcia aplikacji przez opcjonalny, błędny wpis w
            # config.json.  Błąd jest zwracany dopiero przy uruchomieniu
            # kolejki, gdzie użytkownik może go poprawić w ustawieniach.
            self._queue_time_budget_error = str(exc)
        else:
            self._queue_time_budget = configured

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_total_real_time_budget(self, total_real_time_s: Any) -> Dict[str, Any]:
        """Włącza równy limit aktywnego czasu rzeczywistego dla każdego runu.

        API przyjmuje sekundy.  Interfejs może wygodnie przeliczyć wpisane
        minuty przed wywołaniem tej metody.  ``None`` lub pusty napis wyłącza
        tryb dla bieżącej sesji; nie zapisuje niczego globalnie do config.json.
        """

        if self.running:
            raise RuntimeError("Nie można zmienić budżetu czasu podczas działania kolejki")
        seconds = finite_positive_seconds(total_real_time_s)
        with self._lock:
            self._queue_time_plan = None
            self._queue_time_budget_error = None
            self._queue_time_budget = (
                QueueTimeBudget(seconds, source="session") if seconds is not None else None
            )
            return self._queue_time_budget_payload()

    def queue_time_budget(self) -> Dict[str, Any]:
        """Zwraca niezmienny snapshot ustawienia/przebiegu dla GUI."""

        with self._lock:
            return self._queue_time_budget_payload()

    def new_session(self, count: int) -> List[Simulation]:
        if self.running:
            raise RuntimeError("Nie można utworzyć nowej sesji podczas działania kolejki")
        if not 1 <= int(count) <= 20:
            raise ValueError("Podaj liczbę symulacji od 1 do 20.")
        with self._lock:
            self.simulations = []
            self._progress_by_id = {}
            self._pending_logs = {}
            self._dropped_log_records = {}
            self._queue_time_plan = None
            for index in range(1, count + 1):
                item = Simulation(
                    name="Symulacja {}".format(index),
                    configuration=copy.deepcopy(default_configuration(self.application_config)),
                )
                self.storage.create(item)
                self.simulations.append(item)
        self.events.put(QueueEvent(None, "SESSION", "Utworzono {} symulacje.".format(count), 0, count))
        return list(self.simulations)

    def replace_session(self, simulations: Iterable[Simulation]) -> List[Simulation]:
        if self.running:
            raise RuntimeError("Nie można wczytać konfiguracji podczas działania kolejki")
        with self._lock:
            self.simulations = list(simulations)
            self._progress_by_id = {}
            self._pending_logs = {}
            self._dropped_log_records = {}
            self._queue_time_plan = None
            for item in self.simulations:
                self.storage.create(item)
        self.events.put(QueueEvent(None, "SESSION", "Wczytano konfigurację.", 0, len(self.simulations)))
        return list(self.simulations)

    def duplicate(self, simulation_id: str) -> Simulation:
        if self.running:
            raise RuntimeError("Nie można duplikować konfiguracji podczas działania kolejki")
        with self._lock:
            source = self.get(simulation_id)
            item = Simulation(
                name="{} — kopia".format(source.name),
                configuration=copy.deepcopy(source.configuration),
            )
            self.storage.create(item)
            self.simulations.append(item)
            self._progress_by_id[item.id] = 0.0
            self._queue_time_plan = None
        self.events.put(QueueEvent(item.id, "SESSION", "Utworzono kopię konfiguracji.", 0, len(self.simulations)))
        return item

    def get(self, simulation_id: str) -> Simulation:
        for item in self.simulations:
            if item.id == simulation_id:
                return item
        raise KeyError(simulation_id)

    def start_all(self) -> List[Simulation]:
        if self.running:
            raise RuntimeError("Kolejka symulacji już działa")
        if not self.simulations:
            raise RuntimeError("Najpierw utwórz co najmniej jedną symulację")
        if self._queue_time_budget_error:
            raise ValueError("Nie można uruchomić budżetu czasu: {}".format(self._queue_time_budget_error))
        if self._queue_time_budget is not None:
            self._validate_queue_time_budget(self._queue_time_budget, len(self.simulations))
        configuration_errors = []
        shared_rng_run = (
            copy.deepcopy(self.simulations[0].configuration.parameters.get("RngRun"))
            if self._queue_time_budget is not None
            else None
        )
        for item in self.simulations:
            candidate = copy.deepcopy(item.configuration)
            if self._queue_time_budget is not None:
                # Ten tryb świadomie wymusza jeden RngRun w całej kolejce.
                # Walidujemy więc konfigurację, którą runner faktycznie
                # dostanie, zamiast odrzucać później nadpisywaną wartość.
                candidate.parameters["RngRun"] = copy.deepcopy(shared_rng_run)
            errors = validate_configuration(candidate, self.application_config)
            if errors:
                configuration_errors.append("{}: {}".format(item.name, "; ".join(errors)))
        if configuration_errors:
            raise ValueError("Nie można uruchomić symulacji.\n" + "\n".join(configuration_errors))
        # Obiekt Simulation jest pojedynczym uruchomieniem, nie trwałym
        # szablonem.  Ponowne uruchomienie zachowuje poprzedni wynik w
        # historii i tworzy dla tej samej konfiguracji nowy identyfikator.
        terminal = {
            SimulationStatus.COMPLETED,
            SimulationStatus.FAILED,
            SimulationStatus.STOPPED,
        }
        with self._lock:
            refreshed: List[Simulation] = []
            for item in self.simulations:
                if item.status in terminal:
                    item = Simulation(
                        name=item.name,
                        configuration=copy.deepcopy(item.configuration),
                    )
                    self.storage.create(item)
                refreshed.append(item)
            self.simulations = refreshed
            self._progress_by_id = {
                item.id: 100.0 if item.status == SimulationStatus.COMPLETED else 0.0
                for item in self.simulations
            }
            self._pending_logs = {}
            self._dropped_log_records = {}
            self._queue_time_plan = self._create_queue_time_plan()
            if self._queue_time_plan is not None:
                self._prepare_queue_time_plan(self._queue_time_plan)
            time_budget_payload = self._queue_time_budget_payload()
        self._stop.clear()
        reset_stop = getattr(self.runner, "reset_stop", None)
        if callable(reset_stop):
            reset_stop()
        if time_budget_payload.get("enabled"):
            message = (
                "Łączny limit czasu rzeczywistego: {:.1f} s; po gotowości "
                "środowiska każdy przebieg otrzyma równy udział limitu. "
                "Czas ns-3 pozostaje odczytem obserwacyjnym danego przebiegu."
            ).format(float(time_budget_payload["total_real_time_s"]))
            self.events.put(
                QueueEvent(
                    None,
                    "TIME_BUDGET_CONFIGURED",
                    message,
                    0,
                    len(self.simulations),
                    {"time_budget": time_budget_payload},
                )
            )
            if not time_budget_payload.get("rng_was_consistent", True):
                self.events.put(
                    QueueEvent(
                        None,
                        "TIME_BUDGET_RNG_NOTICE",
                        "RngRun różnił się między przebiegami; ustawiono wspólną wartość z pierwszej symulacji.",
                        0,
                        len(self.simulations),
                        {"time_budget": time_budget_payload},
                    )
                )
        self._thread = threading.Thread(target=self._run_queue, name="oran-lab-queue", daemon=True)
        self._thread.start()
        return list(self.simulations)

    def _create_queue_time_plan(self) -> Optional[QueueTimePlan]:
        budget = self._queue_time_budget
        if budget is None:
            return None
        simulation_ids = [item.id for item in self.simulations]
        original_rng_runs = {
            item.id: copy.deepcopy(item.configuration.parameters.get("RngRun"))
            for item in self.simulations
        }
        return QueueTimePlan(
            budget=budget,
            simulation_ids=simulation_ids,
            original_rng_runs=original_rng_runs,
        )

    def _validate_queue_time_budget(self, budget: QueueTimeBudget, run_count: int) -> None:
        """Nie pozwala ukrytemu timeoutowi przerwać kalibracji przed celem."""

        runtime = dict(self.application_config.get("runtime") or {})
        raw_timeout = runtime.get("wall_clock_timeout_s", runtime.get("wall_clock_limit_s", 0))
        technical_timeout = self._finite_positive(raw_timeout)
        if technical_timeout is not None and budget.per_run_real_time_s(run_count) >= technical_timeout:
            raise ValueError(
                "Budżet jednego przebiegu ({:.1f} s) musi być krótszy od technicznego wall_clock_timeout_s ({:.1f} s)."
                .format(budget.per_run_real_time_s(run_count), technical_timeout)
            )

    def _prepare_queue_time_plan(self, plan: QueueTimePlan) -> None:
        """Zapisuje audytowalne instrukcje zanim worker uruchomi pierwszy run."""

        for index, item in enumerate(self.simulations, start=1):
            timing = plan.instruction_for(item.id, index)
            timing["original_sim_time_s"] = copy.deepcopy(
                item.configuration.parameters.get("simTime")
            )
            # Porównanie wymaga identycznego przebiegu RNG. W tym opcjonalnym
            # trybie wartość pierwszego runu jest regułą całej kolejki, a
            # pierwotne wartości pozostają w metadanych audytowych planu.
            item.configuration.parameters["RngRun"] = copy.deepcopy(plan.shared_rng_run)
            item.runtime = dict(item.runtime or {})
            item.runtime["queue_timing"] = timing
            self.storage.save(item)

    def _queue_time_budget_payload(self) -> Dict[str, Any]:
        """Stan planu, który GUI może pokazać bez własnych obliczeń ETA.

        Pola ``planned_*`` opisują zadeklarowany budżet aktywnej pracy ns-3.
        Obliczenia opierają się wyłącznie na czasie rzeczywistym każdego runu;
        obserwowany czas modelowany nie steruje limitem ani ETA. Przygotowanie
        kontenera i archiwizacja nadal są poza tym zakresem.
        """

        plan = self._queue_time_plan
        if plan is None:
            if self._queue_time_budget is None:
                return {
                    "enabled": False,
                    "calibration_state": "disabled",
                    "estimated_queue_remaining_s": None,
                    "time_budget_progress_pct": None,
                }
            result = self._queue_time_budget.to_dict()
            run_count = len(self.simulations)
            per_run = (
                self._queue_time_budget.per_run_real_time_s(run_count)
                if run_count > 0
                else None
            )
            now_epoch = time.time()
            result.update(
                {
                    "run_count": run_count or None,
                    "per_run_target_active_real_s": per_run,
                    "calibration_state": "per_run_hard_real_time",
                    "estimated_queue_remaining_s": None,
                    "planned_remaining_active_real_s": self._queue_time_budget.total_real_time_s,
                    "planned_remaining_queue_real_s": self._queue_time_budget.total_real_time_s,
                    "planned_completion_epoch": (
                        now_epoch + self._queue_time_budget.total_real_time_s
                        if run_count > 0
                        else None
                    ),
                    "planned_completion_basis": "configured_active_ns3_budget_excludes_setup",
                    "time_budget_progress_pct": 0.0,
                    "budget_mode": "per_run_hard_real_time",
                    "sim_time_policy": "observational_only",
                }
            )
            return result

        result = plan.to_dict()
        per_run = plan.per_run_real_time_s
        completed = 0.0
        for raw_elapsed in plan.completed_active_real_s.values():
            elapsed = self._finite_nonnegative(raw_elapsed)
            if elapsed is not None:
                completed += min(per_run, elapsed)
        active_elapsed = self._finite_nonnegative(plan.active_elapsed_real_s)
        if active_elapsed is not None and plan.active_simulation_id not in plan.completed_active_real_s:
            completed += min(per_run, active_elapsed)
        planned_elapsed = min(plan.budget.total_real_time_s, completed)
        planned_remaining = max(0.0, plan.budget.total_real_time_s - planned_elapsed)
        # Równy limit każdego runu jest jedyną podstawą ETA. Nie używamy tu
        # tempa ani czasu modelowanego pierwszej symulacji.
        estimated_remaining, estimate_basis = self._queue_remaining_estimate(plan)
        has_observed_setup_overhead = bool(plan.completed_setup_overhead_real_s)
        # Dopóki nie skończy się żaden run, znamy tylko ręcznie ustawiony
        # limit aktywnej pracy ns-3. Pokazujemy go jako plan, a nie udajemy
        # dokładnej godziny zakończenia całej aplikacji bez danych o Docker/RIC.
        if not has_observed_setup_overhead and estimated_remaining not in (0.0,):
            estimated_remaining = None
            estimate_basis = None
        now_epoch = time.time()
        result.update(
            {
                "planned_elapsed_active_real_s": planned_elapsed,
                "planned_remaining_active_real_s": planned_remaining,
                "planned_remaining_queue_real_s": planned_remaining,
                "time_budget_progress_pct": (
                    100.0 * planned_elapsed / plan.budget.total_real_time_s
                    if plan.budget.total_real_time_s > 0.0
                    else None
                ),
                "active_remaining_active_real_s": (
                    max(0.0, per_run - active_elapsed)
                    if active_elapsed is not None and plan.active_simulation_id is not None
                    else None
                ),
                "active_time_budget_progress_pct": (
                    100.0 * min(per_run, active_elapsed) / per_run
                    if active_elapsed is not None and per_run > 0.0
                    else None
                ),
                # To jest orientacyjny czas aktywnej symulacji, nie obietnica
                # wall-clock całej aplikacji. Docker/RIC i archiwizacja nie są
                # objęte pomiarem ani prognozą.
                "estimated_queue_remaining_s": estimated_remaining,
                "estimated_completion_epoch": (
                    now_epoch + estimated_remaining
                    if estimated_remaining is not None
                    else None
                ),
                "estimated_completion_basis": estimate_basis,
                "estimated_completion_scope": (
                    "active_ns3_plus_observed_setup_overhead"
                    if estimated_remaining is not None and has_observed_setup_overhead
                    else "completed_queue"
                    if estimated_remaining == 0.0
                    else None
                ),
                # Przed pierwszym ukończonym runem GUI może pokazać tylko
                # plan wynikający z ręcznie ustawionego budżetu, wyraźnie
                # odróżniony od prognozy opartej na faktycznym wykonaniu.
                "planned_completion_epoch": now_epoch + planned_remaining,
                "planned_completion_basis": "configured_active_ns3_budget_excludes_setup",
                "eta_basis": estimate_basis,
            }
        )
        return result

    def _queue_remaining_estimate(
        self, plan: QueueTimePlan
    ) -> Tuple[Optional[float], Optional[str]]:
        """Szacuje resztę z równego, jawnie wybranego limitu każdego runu."""

        per_run_target = plan.per_run_real_time_s
        if per_run_target <= 0.0:
            return None, None
        remaining = 0.0
        finished_ids = set(plan.finished_simulation_ids)
        # Zgodność z planami zapisanymi przez wcześniejszą wersję aplikacji.
        if not finished_ids:
            finished_ids = set(plan.completed_active_real_s)
        active_id = plan.active_simulation_id
        active_elapsed = self._finite_nonnegative(plan.active_elapsed_real_s) or 0.0
        for simulation_id in plan.simulation_ids:
            if simulation_id in finished_ids:
                continue
            if simulation_id == active_id:
                remaining += max(0.0, per_run_target - active_elapsed)
            else:
                remaining += per_run_target
        if not remaining:
            return 0.0, "completed_queue"

        observed_setup = [
            value
            for value in (
                self._finite_nonnegative(raw)
                for raw in plan.completed_setup_overhead_real_s.values()
            )
            if value is not None
        ]
        if not observed_setup:
            return remaining, "configured_per_run_hard_real_time_budget_excludes_setup"

        # Po pierwszym zakończonym runie mamy faktyczny koszt rozruchu i
        # archiwizacji. Dodajemy jego średnią tylko do runów, które jeszcze nie
        # osiągnęły RUNNING; aktywny run ma już swój rozruch za sobą.
        runs_needing_setup = sum(
            1 for simulation_id in plan.simulation_ids if simulation_id not in finished_ids
        )
        if active_id and active_id not in finished_ids:
            runs_needing_setup = max(0, runs_needing_setup - 1)
        average_setup = sum(observed_setup) / float(len(observed_setup))
        return (
            remaining + average_setup * runs_needing_setup,
            "configured_per_run_hard_real_time_plus_observed_setup_overhead",
        )

    @staticmethod
    def _finite_nonnegative(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0.0 else None

    @staticmethod
    def _finite_positive(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0.0 else None

    @staticmethod
    def _wall_elapsed_real_s(simulation: Simulation) -> Optional[float]:
        """Zwraca rzeczywisty czas całego zakończonego runu bez simTime."""

        start = str(simulation.started_at or "").strip()
        end = str(simulation.ended_at or "").strip()
        if not start or not end:
            return None
        try:
            started = datetime.fromisoformat(start.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(end.replace("Z", "+00:00"))
            elapsed = (ended - started).total_seconds()
        except ValueError:
            return None
        return elapsed if math.isfinite(elapsed) and elapsed >= 0.0 else None

    def _complete_queue_time_run(
        self, simulation: Simulation
    ) -> Optional[Tuple[str, str]]:
        """Zapisuje rzeczywisty wynik czasu dla ukończonego runu.

        W trybie budżetu czas modelowany jest informacją diagnostyczną. Nie
        przepisujemy go do konfiguracji następnych elementów kolejki, ponieważ
        ich twardy limit jest niezależnym limitem zegara rzeczywistego.
        """

        with self._lock:
            plan = self._queue_time_plan
            if plan is None or simulation.id not in plan.simulation_ids:
                return None

            timing = simulation.runtime.get("queue_timing")
            timing = dict(timing) if isinstance(timing, dict) else {}
            elapsed = self._finite_nonnegative(timing.get("active_elapsed_real_s"))
            if elapsed is None:
                live = simulation.runtime.get("live")
                if isinstance(live, dict):
                    elapsed = self._finite_nonnegative(live.get("elapsed_real_s"))
            if elapsed is not None:
                plan.completed_active_real_s[simulation.id] = elapsed
            wall_elapsed = self._wall_elapsed_real_s(simulation)
            if wall_elapsed is not None:
                plan.completed_setup_overhead_real_s[simulation.id] = max(
                    0.0, wall_elapsed - (elapsed or 0.0)
                )
            if simulation.id not in plan.finished_simulation_ids:
                plan.finished_simulation_ids.append(simulation.id)
            if plan.active_simulation_id == simulation.id:
                plan.active_simulation_id = None
                plan.active_elapsed_real_s = None
                plan.active_simulated_time_s = None
            if plan.current_simulation_id == simulation.id:
                plan.current_simulation_id = None
            source = str(timing.get("measurement_source") or "")
            duration = self._finite_positive(timing.get("observed_simulated_time_s"))
            if duration is not None and source in {"ee_timeseries.csv", "metrics.csv"}:
                plan.observed_simulated_times_s[simulation.id] = duration
                timing["state"] = "completed_with_sim_time_observation"
            else:
                timing["state"] = "completed_without_sim_time_observation"
            timing["sim_time_policy"] = "observational_only"
            simulation.runtime["queue_timing"] = timing
            self.storage.save(simulation)
        return None

    def _is_calibration_completed(self, simulation_id: str) -> bool:
        # Zachowane jako neutralny punkt zgodności dla starszego emitera
        # zdarzeń. Budżet per-run nie posiada już kroku kalibracji simTime.
        return False

    def request_stop(self) -> None:
        self._stop.set()
        self.runner.request_stop()
        self.events.put(
            QueueEvent(
                None,
                "STOPPING",
                "Zatrzymywanie bieżącej symulacji.",
                0,
                len(self.simulations),
                self._queue_payload(0, len(self.simulations)),
            )
        )

    def close(self, timeout_s: float = 8.0) -> bool:
        self.request_stop()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout_s))
        return not self.running

    def drain_events(self, limit: int) -> List[QueueEvent]:
        """Pobiera zdarzenia bez oddawania każdej linii logu osobno.

        Najpierw przekazuje normalne zdarzenia (zwłaszcza status i metryki),
        a następnie co najwyżej jeden zbiorczy wpis na źródło logu. Dzięki
        temu aktualizacja live nie czeka za dużym strumieniem tekstu.
        """

        maximum = max(1, int(limit))
        result: List[QueueEvent] = []
        while len(result) < maximum:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        if len(result) >= maximum:
            return result
        with self._lock:
            for key in sorted(self._pending_logs, key=lambda item: (item[0] or "", item[1])):
                if len(result) >= maximum:
                    break
                records = self._pending_logs.get(key)
                if not records:
                    self._pending_logs.pop(key, None)
                    continue
                batch: List[Dict[str, Any]] = []
                dropped = self._dropped_log_records.pop(key, 0)
                if dropped:
                    batch.append(
                        {
                            "text": "[O-RAN LAB] Pominięto {} starszych wierszy w widoku. Pełny log zapisano na dysku.".format(
                                dropped
                            )
                        }
                    )
                for _ in range(min(self.LOG_BATCH_SIZE, len(records))):
                    batch.append(records.popleft())
                if not records:
                    self._pending_logs.pop(key, None)
                simulation_id, source = key
                result.append(
                    QueueEvent(
                        simulation_id,
                        "LOG",
                        "[{}] +{} nowych wierszy logu".format(source, len(batch)),
                        payload={"log_source": source, "logs": batch},
                    )
                )
        return result

    def _run_queue(self) -> None:
        # A callback, storage error or Docker adapter may raise outside the
        # runner's normal failure return. Always release the GUI queue state.
        try:
            self._run_queue_impl()
        except Exception as exc:
            self.events.put(QueueEvent(None, "QUEUE_FAILED", str(exc)))
            self.events.put(QueueEvent(None, "IDLE", "Kolejka zakończona z błędem: {}".format(exc)))

    def _run_queue_impl(self) -> None:
        preflight = self.runner.preflight()
        if not preflight.get("ok"):
            message = "; ".join(preflight.get("errors") or ["Walidacja Docker nie powiodła się"])
            # Preflight happens before SimulationRunner can create a Docker
            # session, so its retry loop cannot run here. Persist the exact
            # reason in every affected run instead of leaving DRAFT entries
            # with an empty log and no diagnostic trail.
            for item in self.simulations:
                if item.status == SimulationStatus.COMPLETED:
                    continue
                item.status = SimulationStatus.FAILED
                item.error = message
                item.ended_at = now_utc()
                item.runtime = dict(item.runtime or {})
                item.runtime.update({
                    "stage": "FAILED",
                    "failure_kind": "preflight",
                    "experiment_valid": False,
                    "retry": {"state": "not_started", "reason": "preflight_failed"},
                })
                log_path = Path(item.log_path) if item.log_path else self.storage.directory(item.id) / "logs" / "runner.log"
                item.log_path = str(log_path)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("[{}] VALIDATION_FAILED: {}\n".format(datetime.now().strftime("%H:%M:%S"), message))
                    handle.write("[{}] RETRY_SKIPPED: preflight zakończył się przed utworzeniem sesji Docker.\n".format(datetime.now().strftime("%H:%M:%S")))
                self.storage.save(item)
                self.events.put(QueueEvent(
                    item.id,
                    "FAILED",
                    message,
                    0,
                    len(self.simulations),
                    self._simulation_snapshot(item),
                ))
            self.events.put(
                QueueEvent(
                    None,
                    "VALIDATION_FAILED",
                    message,
                    0,
                    len(self.simulations),
                    self._queue_payload(0, len(self.simulations)),
                )
            )
            self.events.put(
                QueueEvent(
                    None,
                    "IDLE",
                    "Kolejka nie została uruchomiona.",
                    0,
                    len(self.simulations),
                    self._queue_payload(0, len(self.simulations)),
                )
            )
            return
        checks = "; ".join(preflight.get("checks") or [])
        self.events.put(
            QueueEvent(
                None,
                "VALIDATED",
                checks,
                0,
                len(self.simulations),
                self._queue_payload(0, len(self.simulations)),
            )
        )
        total = len(self.simulations)
        cleanup_failures: List[str] = []
        with self._lock:
            for item in self.simulations:
                if item.status != SimulationStatus.COMPLETED:
                    item.status = SimulationStatus.QUEUED
                    self.storage.save(item)
                    self._progress_by_id[item.id] = 0.0
        self.events.put(
            QueueEvent(
                None,
                "QUEUE_UPDATED",
                "Kolejka przygotowana.",
                0,
                total,
                self._queue_payload(0, total),
            )
        )
        for index, simulation in enumerate(list(self.simulations), start=1):
            if self._stop.is_set():
                break
            if simulation.status == SimulationStatus.COMPLETED:
                continue
            simulation.status = SimulationStatus.QUEUED
            self.storage.save(simulation)
            queued_payload = self._queue_payload(index, total)
            # Pełna tożsamość następnego runu jest ważna dla dashboardu:
            # pozwala usunąć serię/mapę poprzedniego przebiegu zanim nadejdzie
            # pierwsza próbka live nowego ns-3.
            queued_payload.update(self._simulation_snapshot(simulation))
            self.events.put(
                QueueEvent(
                    simulation.id,
                    "QUEUED",
                    "Oczekiwanie w kolejce.",
                    index,
                    total,
                    queued_payload,
                )
            )
            try:
                result = self.runner.run(
                    simulation,
                    lambda item, phase, message, payload=None: self._on_runner_event(
                        item, phase, message, index, total, payload
                    ),
                )
            except Exception as exc:
                # Preserve the failing item and continue with the next
                # isolated run instead of abandoning a half-running queue.
                simulation.status = SimulationStatus.FAILED
                simulation.error = str(exc)
                simulation.ended_at = now_utc()
                simulation.runtime["failure_kind"] = "runner_exception"
                simulation.runtime["experiment_valid"] = False
                self.storage.save(simulation)
                result = simulation
            calibration_outcome = self._complete_queue_time_run(result)
            self._progress_by_id[result.id] = 100.0 if result.status == SimulationStatus.COMPLETED else self._progress_by_id.get(result.id, 0.0)
            result_payload = self._queue_payload(index, total)
            result_payload.update(self._simulation_snapshot(result))
            self.events.put(
                QueueEvent(
                    result.id,
                    result.status.value,
                    result.error or "Zakończono.",
                    index,
                    total,
                    result_payload,
                )
            )
            if calibration_outcome:
                calibration_phase, calibration_message = calibration_outcome
                self.events.put(
                    QueueEvent(
                        result.id,
                        calibration_phase,
                        calibration_message,
                        index,
                        total,
                        self._queue_payload(index, total),
                    )
                )
            if self._is_calibration_completed(result.id):
                timing = result.runtime.get("queue_timing")
                timing = dict(timing) if isinstance(timing, dict) else {}
                source = str(timing.get("calibration_duration_source") or "pełnego pliku metryk live")
                self.events.put(
                    QueueEvent(
                        result.id,
                        "TIME_BUDGET_CALIBRATED",
                        "Ustalono wspólny czas symulowany na podstawie {}.".format(source),
                        index,
                        total,
                        self._queue_payload(index, total),
                    )
                )
            if result.runtime.get("cleanup_failed"):
                detail = str(result.runtime.get("cleanup_failed"))
                cleanup_failures.append(detail)
                self.events.put(
                    QueueEvent(
                        result.id,
                        "CLEANUP_FAILED",
                        "Nie potwierdzono cleanupu kontenera bieżącej symulacji: {}. "
                        "Następna symulacja zostanie mimo to uruchomiona w osobnym kontenerze sesji.".format(
                            detail
                        ),
                        index,
                        total,
                        self._queue_payload(index, total),
                    )
                )
        final_message = (
            "Kolejka zakończona z ostrzeżeniem cleanupu ({} problemów). "
            "Sprawdź zakładkę Docker / logi.".format(len(cleanup_failures))
            if cleanup_failures
            else "Kolejka zakończona."
        )
        self.events.put(
            QueueEvent(
                None,
                "IDLE",
                final_message,
                total,
                total,
                self._queue_payload(total, total),
            )
        )

    def _on_runner_event(
        self,
        simulation: Simulation,
        phase: str,
        message: str,
        current: int,
        total: int,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        event_payload = copy.deepcopy(payload) if isinstance(payload, dict) else {}
        if phase == "LOG":
            self._enqueue_log(simulation.id, event_payload)
            return
        # Statusy przygotowania nie zawsze niosą własny snapshot z runnera.
        # Wstrzykujemy minimalny, aktualny opis runu, aby GUI rozpoznało
        # przejście z poprzedniej symulacji zanim spłyną metryki live.
        event_payload.setdefault("simulation", self._simulation_snapshot(simulation)["simulation"])
        structured_progress = event_payload.get("progress")
        real_time_progress: Optional[float] = None
        with self._lock:
            plan = self._queue_time_plan
            if plan is not None and simulation.id in plan.simulation_ids:
                if phase in {"VALIDATING", "SESSION_STARTED", "STARTING", "RUNNING", "LIVE_UPDATE"}:
                    plan.current_simulation_id = simulation.id
                # ns-3 starts producing useful samples before E2/xApp
                # readiness. Those startup samples are shown in the dashboard,
                # but the user-selected budget begins only once the run is
                # explicitly RUNNING (after xApp, which is started last).
                budget_clock_active = phase == "RUNNING" or (
                    phase == "LIVE_UPDATE"
                    and simulation.status == SimulationStatus.RUNNING
                )
                if budget_clock_active:
                    plan.active_simulation_id = simulation.id
                if budget_clock_active and isinstance(structured_progress, dict):
                    elapsed = self._finite_nonnegative(
                        structured_progress.get(
                            "real_elapsed_s", structured_progress.get("elapsed_real_s")
                        )
                    )
                    simulated = self._finite_positive(
                        structured_progress.get(
                            "simulated_time_s", structured_progress.get("sim_time_s")
                        )
                    )
                    if elapsed is not None:
                        plan.active_elapsed_real_s = elapsed
                    if simulated is not None:
                        plan.active_simulated_time_s = simulated
                    if elapsed is not None:
                        target = plan.per_run_real_time_s
                        if target > 0.0:
                            real_time_progress = min(
                                100.0, max(0.0, 100.0 * elapsed / target)
                            )
                            updated_progress = dict(structured_progress)
                            updated_progress.update(
                                {
                                    "percent": real_time_progress,
                                    "progress_pct": real_time_progress,
                                    "per_run_target_active_real_s": target,
                                    "remaining_real_s": max(0.0, target - elapsed),
                                    "progress_basis": "real_time_budget",
                                }
                            )
                            event_payload["progress"] = updated_progress
                            event_payload["progress_pct"] = real_time_progress
        progress = real_time_progress
        if progress is None:
            progress = event_payload.get("progress_pct")
        if progress is None and isinstance(structured_progress, dict):
            progress = structured_progress.get("percent", structured_progress.get("progress_pct"))
        try:
            numeric = float(progress)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            self._progress_by_id[simulation.id] = min(100.0, max(0.0, numeric))
        if phase == "COMPLETED":
            self._progress_by_id[simulation.id] = 100.0
        event_payload.update(self._queue_payload(current, total))
        self.events.put(QueueEvent(simulation.id, phase, message, current, total, event_payload))

    def _enqueue_log(self, simulation_id: str, payload: Dict[str, Any]) -> None:
        """Odkłada nowe wpisy logu do ograniczonego bufora per źródło."""

        source = str(payload.get("log_source") or "log")
        raw = payload.get("log")
        if isinstance(raw, dict):
            record = dict(raw)
        elif raw is None:
            return
        else:
            record = {"text": str(raw)}
        if not str(record.get("text") or "").strip():
            return
        key = (simulation_id, source)
        with self._lock:
            records = self._pending_logs.get(key)
            if records is None:
                records = deque()
                self._pending_logs[key] = records
            if len(records) >= self.MAX_PENDING_LOG_RECORDS:
                records.popleft()
                self._dropped_log_records[key] = self._dropped_log_records.get(key, 0) + 1
            records.append(record)

    def _queue_payload(self, current: int, total: int) -> Dict[str, Any]:
        """Tworzy dane kolejki bez przekazywania obiektów modelu do Tkintera."""

        with self._lock:
            items = []
            for index, item in enumerate(self.simulations, start=1):
                progress = self._progress_by_id.get(item.id, 0.0)
                if item.status == SimulationStatus.COMPLETED:
                    progress = 100.0
                items.append(
                    {
                        "id": item.id,
                        "index": index,
                        "name": item.name,
                        "status": item.status.value,
                        "progress_pct": progress,
                        "error": item.error,
                    }
                )
        completed = sum(1 for item in items if item["status"] == SimulationStatus.COMPLETED.value)
        active_progress = 0.0
        if current and current <= len(items):
            active_progress = float(items[current - 1].get("progress_pct") or 0.0)
        overall = ((max(0, current - 1) + active_progress / 100.0) / total * 100.0) if total else 0.0
        if completed == len(items) and items:
            overall = 100.0
        with self._lock:
            time_budget = self._queue_time_budget_payload()
        return {
            "queue": items,
            "overall_progress_pct": min(100.0, max(0.0, overall)),
            "time_budget": time_budget,
        }

    @staticmethod
    def _simulation_snapshot(simulation: Simulation) -> Dict[str, Any]:
        """Końcowy stan używany przez dashboard po zwolnieniu kontenera."""

        runtime = dict(simulation.runtime or {})
        payload: Dict[str, Any] = {
            "simulation": {
                "id": simulation.id,
                "name": simulation.name,
                "status": simulation.status.value,
                "configuration": simulation.configuration.to_dict(),
            },
            "status": simulation.status.value,
            "stage": runtime.get("stage") or simulation.status.value,
        }
        processes = runtime.get("processes")
        if isinstance(processes, dict):
            payload["processes"] = copy.deepcopy(processes)
        container = runtime.get("container_snapshot")
        if isinstance(container, dict):
            payload["container"] = copy.deepcopy(container)
        live = runtime.get("live")
        if isinstance(live, dict):
            for key in (
                "current_metrics",
                "data_collection",
                "data_status",
                "xapp_decisions",
            ):
                if key in live:
                    payload[key] = copy.deepcopy(live[key])
        queue_timing = runtime.get("queue_timing")
        if isinstance(queue_timing, dict):
            payload["queue_timing"] = copy.deepcopy(queue_timing)
        if simulation.result is not None:
            payload["result"] = simulation.result.to_dict()
        return payload
