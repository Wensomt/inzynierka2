from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import posixpath
import re
import shlex
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from app.async_compat import run_in_thread
from app.config import ConfigStore, RESOURCE_DIR
from app.data.results_parser import ResultsParser
from app.data.storage import SimulationStorage
from app.infrastructure.docker_monitor import inspect_container
from app.infrastructure.docker_process_manager import ManagedDockerProcess
from app.infrastructure.lab_discovery import DockerLabDiscovery
from app.monitoring.live_metrics import LiveMetricsCollector
from app.monitoring.live_topology import LiveTopologyCollector
from app.monitoring.xapp_decisions import XappDecisionCollector
from app.services.xapp_service import XAppService
from app.simulation.kpm_live import KpmLiveCollector

from .docker_session import DockerSession, DockerSessionManager
from .models import Simulation, SimulationStatus, now_utc
from .scenario_sync import (
    packaged_scenario_path,
    packaged_scenario_source,
    source_declares_ee_timeseries,
    sync_packaged_scenario,
)
from .validation import INTEGER_PARAMETERS, validate_configuration


# Public callbacks historically accepted three arguments.  The fourth one is an
# optional immutable-like snapshot for the desktop UI; callers that still use
# the old form remain supported in ``_emit``.
ProgressCallback = Callable[..., None]


class SimulationStopped(RuntimeError):
    pass


class _SessionProcesses:
    """Adapter procesów RIC, ns-3 i xAppa wewnątrz kontenera sesji."""

    def __init__(self, session: DockerSession) -> None:
        self.session = session
        self.items = {
            name: ManagedDockerProcess(name, self._client_and_container)
            for name in ("ric", "ns3", "xapp")
        }

    def get(self, name: str) -> ManagedDockerProcess:
        return self.items[name]

    def snapshot(self) -> Dict[str, Any]:
        return {name: process.snapshot() for name, process in self.items.items()}

    def exec_text(self, script: str) -> Tuple[int, str]:
        raw = self.session.container.exec_run(
            ["/bin/bash", "-lc", script], stdout=True, stderr=True
        )
        if isinstance(raw, tuple):
            code, output = raw
        else:
            code = getattr(raw, "exit_code", 1)
            output = getattr(raw, "output", b"")
        text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        return int(code or 0), text

    def get_archive(self, path: str) -> Tuple[Any, Dict[str, Any]]:
        return self.session.container.get_archive(path)

    def _client_and_container(self) -> Tuple[Any, Any]:
        self.session.container.reload()
        if getattr(self.session.container, "status", "") != "running":
            raise RuntimeError("Kontener bieżącej sesji nie działa")
        return self.session.client, self.session.container


class SimulationRunner:
    """Wykonuje jedną symulację i zawsze sprząta jej kontener."""

    # Jeden retry jest wystarczający do odzyskania po przejściowym problemie
    # rozruchu, ale nie ukrywa trwałej wady obrazu lub konfiguracji za serią
    # niekontrolowanych ponowień.
    MAX_AUTOMATIC_RETRIES = 1
    LIVE_TIMESERIES_FILE = "ee_timeseries.csv"
    LIVE_TIMESERIES_COLUMNS = (
        "Time_s",
        "DeliveredBits",
        "TotalEnergy_J",
        "IntervalPower_W",
        "IntervalThroughput_bps",
        "CumulativeEE_bit_per_J",
        "PacketLoss_pct",
        "MeanDelay_ms",
    )
    # Oba pliki są realnymi artefaktami backendu.  ``metrics.csv`` ma
    # pierwszeństwo, bo w starszych sprawdzonych przebiegach zawiera komplet
    # wskaźników energii, danych i jakości; nowy scenariusz może natomiast
    # zapisywać wyłącznie bezpośrednią serię EE.
    LIVE_METRICS_FILES = ("metrics.csv", LIVE_TIMESERIES_FILE)

    OUTPUT_GROUPS = {
        "timeseries": (
            "ee_summary.csv", "ee_timeseries.csv", "metrics.csv",
            "cell_metrics.csv", "ue_metrics.csv", "energyfilecell*.csv",
            "cu-cp-cell-*.txt", "cu-up-cell-*.txt", "du-cell-*.txt",
        ),
        "summary": ("summary.json", "summary.csv", "analysis.json"),
        "topology": ("gnbs.txt", "enbs.txt", "ue_position.txt", "ues.txt"),
        "decisions": (
            "xapp_ee_decisions*.csv",
            "xapp_decisions*.csv",
            "ml_xapp_*_decisions*.csv",
        ),
        "raw": ("ns3_run.log", "flow-monitor.xml"),
    }

    def __init__(
        self,
        config: ConfigStore,
        storage: SimulationStorage,
        parser: Optional[ResultsParser] = None,
        sessions: Optional[DockerSessionManager] = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.parser = parser or ResultsParser()
        self.sessions = sessions or DockerSessionManager(config.as_dict().get("lab", {}))
        self._stop_requested = threading.Event()
        self._current_session: Optional[DockerSession] = None
        self._live_sequences: Dict[str, int] = {}

    def preflight(self) -> Dict[str, Any]:
        return self.sessions.preflight()

    def request_stop(self) -> None:
        self._stop_requested.set()

    def reset_stop(self) -> None:
        """Rozpoczyna nową kolejkę; nie jest wywoływane wewnątrz pojedynczego run."""

        self._stop_requested.clear()

    def run(self, simulation: Simulation, progress: Optional[ProgressCallback] = None) -> Simulation:
        """Wersja blokująca, przeznaczona dla wątku kolejki GUI."""

        return asyncio.run(self._run_with_retry(simulation, progress))

    async def _run_with_retry(
        self,
        simulation: Simulation,
        progress: Optional[ProgressCallback],
    ) -> Simulation:
        """Wykonuje run i najwyżej raz ponawia go w nowej sesji Dockera.

        Pojedyncze wywołanie ``_run`` posiada pełny cykl życia kontenera i w
        jego ``finally`` usuwa własną sesję.  Orkiestracja retry tutaj daje
        więc naturalną granicę: druga próba nie ma dostępu do procesów ani
        plików roboczych pierwszej.  Przed nią dodatkowo potwierdzamy cleanup
        po identyfikatorze sesji i odkładamy częściowe artefakty osobno.
        """

        maximum_attempts = self.MAX_AUTOMATIC_RETRIES + 1
        retry_state: Dict[str, Any] = {
            "state": "running",
            "attempt": 0,
            "maximum_attempts": maximum_attempts,
            "attempts": [],
        }
        for attempt in range(1, maximum_attempts + 1):
            simulation.runtime = dict(simulation.runtime or {})
            simulation.runtime["run_retry"] = retry_state
            result = await self._run(simulation, progress)
            retry_state = self._append_retry_attempt(retry_state, result, attempt)
            result.runtime["run_retry"] = retry_state
            self._save_if_available(result)

            if result.status != SimulationStatus.FAILED:
                retry_state["state"] = "finished"
                self._save_if_available(result)
                return result
            if self._stop_requested.is_set():
                retry_state["state"] = "stopped"
                self._save_if_available(result)
                return result
            if self._failure_is_not_retryable(result):
                retry_state["state"] = "not_retryable"
                self._emit(
                    result,
                    "RUN_RETRY_SKIPPED",
                    "Nie ponawiam symulacji, ponieważ błąd dotyczy konfiguracji przed uruchomieniem Dockera.",
                    progress,
                    {"retry": dict(retry_state)},
                )
                self._save_if_available(result)
                return result
            if attempt >= maximum_attempts:
                retry_state["state"] = "exhausted"
                self._emit(
                    result,
                    "RUN_RETRY_EXHAUSTED",
                    "Automatyczna druga próba również nie powiodła się.",
                    progress,
                    {"retry": dict(retry_state)},
                )
                self._save_if_available(result)
                return result

            cleanup_ok, cleanup_detail = await self._confirm_cleanup_before_retry(result)
            retry_state["cleanup_before_retry"] = {
                "state": "confirmed" if cleanup_ok else "failed",
                "detail": cleanup_detail,
            }
            if not cleanup_ok:
                retry_state["state"] = "cleanup_blocked"
                self._record_runtime_warning(
                    result,
                    "Nie uruchomiono automatycznej drugiej próby: {}".format(cleanup_detail),
                )
                self._emit(
                    result,
                    "RUN_RETRY_BLOCKED",
                    "Nie można potwierdzić cleanupu przed drugą próbą: {}".format(cleanup_detail),
                    progress,
                    {"retry": dict(retry_state)},
                )
                self._save_if_available(result)
                return result

            try:
                archive_path = await run_in_thread(
                    self.storage.prepare_retry, result, attempt
                )
            except Exception as exc:
                retry_state["state"] = "artifact_cleanup_failed"
                detail = "Nie można przygotować czystych artefaktów retry: {}".format(exc)
                self._record_runtime_warning(result, detail)
                self._emit(
                    result,
                    "RUN_RETRY_BLOCKED",
                    detail,
                    progress,
                    {"retry": dict(retry_state)},
                )
                self._save_if_available(result)
                return result

            retry_state["state"] = "retrying"
            retry_state["attempt"] = attempt + 1
            retry_state["previous_artifacts"] = str(archive_path)
            result.runtime["run_retry"] = retry_state
            result.status = SimulationStatus.QUEUED
            result.error = None
            result.result = None
            self._emit(
                result,
                "RUN_RETRYING",
                "Pierwsza próba nie powiodła się; cleanup potwierdzony. Uruchamianie świeżej sesji Docker (próba {}/{}).".format(
                    attempt + 1, maximum_attempts
                ),
                progress,
                {
                    "retry": dict(retry_state),
                    "container": {"state": "starting", "role": "Nowa sesja retry"},
                },
            )
            self._save_if_available(result)
        return simulation

    @staticmethod
    def _append_retry_attempt(
        retry_state: Mapping[str, Any],
        simulation: Simulation,
        attempt: int,
    ) -> Dict[str, Any]:
        state = dict(retry_state)
        history = list(state.get("attempts") or [])
        history.append(
            {
                "attempt": attempt,
                "status": simulation.status.value,
                "error": simulation.error,
                "failure_kind": simulation.runtime.get("failure_kind"),
                "session_id": simulation.runtime.get("session_id"),
                "started_at": simulation.started_at,
                "ended_at": simulation.ended_at,
                "termination": dict(simulation.runtime.get("termination") or {}),
                "processes": dict(simulation.runtime.get("processes") or {}),
                "failure_processes": dict(simulation.runtime.get("failure_processes") or {}),
            }
        )
        state["attempts"] = history
        state["attempt"] = attempt
        return state

    @staticmethod
    def _failure_is_not_retryable(simulation: Simulation) -> bool:
        return str(simulation.runtime.get("failure_kind") or "") == "validation"

    async def _confirm_cleanup_before_retry(
        self, simulation: Simulation
    ) -> Tuple[bool, str]:
        """Potwierdza, że retry nie odziedziczyło kontenera poprzedniej próby."""

        session_id = str(simulation.runtime.get("session_id") or "")
        if not session_id:
            # Gdy nie udało się nawet utworzyć sesji, nie ma kontrolowanego
            # kontenera do usunięcia. Kolejna próba nadal startuje od zera.
            return True, "Poprzednia próba nie utworzyła kontenera sesji."
        if self._current_session is not None:
            return False, "Poprzednia sesja nadal jest oznaczona jako aktywna."

        ensure_cleanup = getattr(self.sessions, "ensure_session_cleanup", None)
        if not callable(ensure_cleanup):
            detail = simulation.runtime.get("cleanup_failed")
            if detail:
                return False, str(detail)
            # Adaptery testowe i starsze integracje, które wykonały już
            # normalne ``stop`` bez błędu, nie mają dodatkowego API kontroli.
            return True, "Cleanup potwierdzony przez zakończenie poprzedniej sesji."
        try:
            removed = await run_in_thread(ensure_cleanup, session_id)
        except Exception as exc:
            detail = str(exc)
            simulation.runtime["cleanup_failed"] = detail
            return False, detail
        simulation.runtime.pop("cleanup_failed", None)
        names = ", ".join(str(item) for item in removed if str(item))
        return True, "Brak pozostałych kontenerów sesji{}.".format(
            "; usunięto " + names if names else ""
        )

    def _save_if_available(self, simulation: Simulation) -> None:
        storage = getattr(self, "storage", None)
        save = getattr(storage, "save", None)
        if callable(save):
            save(simulation)

    async def _run(self, simulation: Simulation, progress: Optional[ProgressCallback]) -> Simulation:
        cfg = self.config.as_dict()
        self._live_sequences = {}
        # Only queue/retry metadata belongs to the next attempt. A failed
        # start must not retain the previous container ID or live samples.
        simulation.runtime = {
            key: dict(value)
            for key in ("queue_timing", "run_retry")
            for value in [simulation.runtime.get(key)]
            if isinstance(value, dict)
        }
        simulation.ended_at = None
        simulation.result = None
        errors = validate_configuration(simulation.configuration, cfg)
        if errors:
            simulation.runtime = dict(simulation.runtime or {})
            simulation.runtime["failure_kind"] = "validation"
            simulation.status = SimulationStatus.FAILED
            simulation.error = "; ".join(errors)
            simulation.ended_at = now_utc()
            self.storage.save(simulation)
            self._emit(simulation, "FAILED", simulation.error, progress)
            return simulation

        directory = self.storage.directory(simulation.id)
        session: Optional[DockerSession] = None
        processes: Optional[_SessionProcesses] = None
        root: Optional[str] = None
        outputs_archived = False
        simulation.started_at = now_utc()
        simulation.status = SimulationStatus.VALIDATING
        simulation.error = None
        self.storage.save(simulation)
        self._emit(simulation, "VALIDATING", "Sprawdzanie Docker i konfiguracji.", progress)
        try:
            self._raise_if_stopped()
            session = await run_in_thread(self.sessions.start)
            self._current_session = session
            processes = _SessionProcesses(session)
            pending_queue_timing = simulation.runtime.get("queue_timing")
            pending_queue_timing = (
                dict(pending_queue_timing) if isinstance(pending_queue_timing, dict) else None
            )
            pending_retry = simulation.runtime.get("run_retry")
            pending_retry = (
                dict(pending_retry) if isinstance(pending_retry, dict) else None
            )
            simulation.runtime = {
                "session_id": session.id,
                "container": str(getattr(session.container, "name", "")),
                "started_at": simulation.started_at,
            }
            if pending_queue_timing is not None:
                simulation.runtime["queue_timing"] = pending_queue_timing
            if pending_retry is not None:
                simulation.runtime["run_retry"] = pending_retry
            self.storage.save(simulation)
            self._emit(
                simulation,
                "SESSION_STARTED",
                "Uruchomiono kontener bieżącej sesji.",
                progress,
                {
                    "simulation": {
                        "id": simulation.id,
                        "name": simulation.name,
                        "status": simulation.status.value,
                        "configuration": simulation.configuration.to_dict(),
                    },
                    "container": {
                        "name": str(getattr(session.container, "name", "")),
                        "state": str(getattr(session.container, "status", "running") or "running"),
                        "role": "Kontener sesji",
                    },
                },
            )

            discovery = DockerLabDiscovery(self.config).discover(
                save=False, container=session.container, client=session.client
            )
            self._validate_inventory(discovery)
            root = self._component_path(discovery, "ns3_root")
            ric_path = self._component_path(discovery, "near_rt_ric")
            # Świeżo zbudowany obraz może zawierać ns-3 i katalog scratch,
            # ale jeszcze nie dostarczony przez aplikację wariant scenariusza.
            # W takim przypadku znamy bezpieczną docelową ścieżkę pod rootem
            # ns-3 i synchronizujemy plik zanim wrapper spróbuje go odnaleźć.
            remote_scenario = self._container_scenario_source_path(
                cfg, discovery, simulation, ns3_root=root
            )
            local_scenario = packaged_scenario_source(cfg, simulation.configuration.scenario)
            expected_local_scenario = packaged_scenario_path(
                cfg, simulation.configuration.scenario
            )
            live_contract = self._live_metrics_contract(
                cfg, simulation, local_scenario
            )
            live_contract["container_path"] = remote_scenario
            if expected_local_scenario is not None:
                live_contract["local_path"] = str(expected_local_scenario)
            simulation.runtime["live_metrics_contract"] = live_contract
            if live_contract["required"] and local_scenario is None:
                live_contract["state"] = "packaged_source_missing"
                self.storage.save(simulation)
                raise RuntimeError(
                    "Brak dostarczonego źródła scenariusza z pełnymi metrykami live: {}. "
                    "Przebieg nie został uruchomiony, aby nie użyć starszego wariantu bez {}.".format(
                        expected_local_scenario or "nieznana ścieżka",
                        self.LIVE_TIMESERIES_FILE,
                    )
                )
            if local_scenario is not None:
                synced = await run_in_thread(
                    sync_packaged_scenario, processes, local_scenario, remote_scenario
                )
                simulation.runtime["scenario_source"] = {
                    "state": synced.state,
                    "local_path": synced.local_path,
                    "container_path": synced.remote_path,
                    "sha256": synced.digest,
                }
                live_contract["source_sha256"] = synced.digest
                live_contract["state"] = "source_verified"
                self.storage.save(simulation)
                if live_contract["required"]:
                    self._emit(
                        simulation,
                        "STARTING",
                        "Potwierdzono źródło pełnych metryk live w kontenerze sesji ({}); ns-3 wymusi jego przebudowę przed uruchomieniem.".format(
                            synced.state
                        ),
                        progress,
                    )
                elif synced.state == "updated":
                    self._emit(
                        simulation,
                        "STARTING",
                        "Zaktualizowano źródło scenariusza w kontenerze sesji; ns-3 przebuduje je przed uruchomieniem.",
                        progress,
                    )
            scenario_expression = self._scenario_expression(
                cfg, discovery, root, simulation, remote_scenario=remote_scenario
            )
            if local_scenario is not None and live_contract["required"]:
                # ``ns3 run`` normally builds its target, but an explicit
                # build here turns a stale/invalid scratch target into a
                # visible start failure before ns-3 can silently emit only
                # old energyfilecell*.csv artifacts. It also gives each new
                # Docker session a deterministic proof that the synchronised
                # source was the target selected for this run.
                build_target = await run_in_thread(
                    self._rebuild_synced_scenario,
                    processes,
                    root,
                    remote_scenario,
                )
                source_runtime = dict(simulation.runtime.get("scenario_source") or {})
                source_runtime["build_target"] = build_target
                source_runtime["build_state"] = "verified"
                simulation.runtime["scenario_source"] = source_runtime
                self.storage.save(simulation)
                self._emit(
                    simulation,
                    "STARTING",
                    "Potwierdzono przebudowę źródła scenariusza z pełnymi metrykami live ({}).".format(
                        build_target
                    ),
                    progress,
                )
            prepared_xapp_command = None
            if simulation.configuration.xapp == "optimized":
                self._emit(simulation, "STARTING", "Synchronizacja i przebudowa źródła xAppa My Energy.", progress)
                prepared_xapp_command = await run_in_thread(
                    self._prepare_packaged_xapp, processes, cfg, discovery, directory, simulation
                )
                self.storage.save(simulation)
            await run_in_thread(self._clean_outputs, processes, root)
            # The collector observes the same ee_timeseries.csv that is copied
            # to the run directory at the end. It is deliberately not a second
            # source of result data.
            live_metrics = LiveMetricsCollector()
            # Tak samo odczytujemy realne pliki położeń zapisywane przez
            # scenariusz. Kolektor nie wyznacza asocjacji samodzielnie.
            live_topology = LiveTopologyCollector()
            # KPM jest opcjonalny: zbieramy tylko pliki, które backend już
            # rzeczywiście zapisał, bez przełączania scenariusza na tryb
            # offline i bez ingerencji w E2/RI C.
            live_kpm = KpmLiveCollector()
            # Historia decyzji jest opcjonalna i pochodzi wyłącznie z CSV
            # wskazanego przez rzeczywiście uruchamiany xApp. Kolektor ma
            # stały limit, więc długi run nie rozdyma pamięci GUI.
            live_xapp_decisions = (
                XappDecisionCollector()
                if simulation.configuration.xapp != "none"
                else None
            )

            # ns-3 zaczyna zapisywać metryki i pozycje zaraz po starcie.  Nie
            # czekamy z ich prezentacją aż przejdzie cała negocjacja E2 i
            # uruchomi się xApp: przy długim setupie użytkownik widziałby
            # przez ten czas pusty dashboard mimo działającego symulatora.
            # Hook przekazywany do _wait_for_logs jest ograniczony tym samym
            # interwałem co zwykły polling w _wait_for_end i nie blokuje
            # krytycznej kontroli gotowości, jeśli sam odczyt diagnostyczny
            # chwilowo zawiedzie.
            startup_live_started: Optional[float] = None
            startup_live_next_poll: Optional[float] = None

            async def publish_startup_live() -> None:
                nonlocal startup_live_next_poll
                if startup_live_started is None:
                    return
                loop = asyncio.get_running_loop()
                current = loop.time()
                if (
                    startup_live_next_poll is not None
                    and current < startup_live_next_poll
                ):
                    return
                interval = max(
                    0.5,
                    float((cfg.get("runtime") or {}).get("live_poll_interval_s", 1.0)),
                )
                startup_live_next_poll = current + interval
                container_snapshot = await self._container_snapshot(processes)
                await self._publish_live_snapshot(
                    processes,
                    root,
                    live_metrics,
                    simulation,
                    progress,
                    max(0.0, current - startup_live_started),
                    container_snapshot,
                    live_topology=live_topology,
                    live_kpm=live_kpm,
                    live_xapp_decisions=live_xapp_decisions,
                )

            simulation.status = SimulationStatus.STARTING
            self.storage.save(simulation)
            self._emit(simulation, "STARTING", "Uruchamianie nearRT-RIC.", progress)
            ric = processes.get("ric")
            await ric.start(
                ["./" + posixpath.basename(ric_path)],
                posixpath.dirname(ric_path),
                directory / "logs" / "ric.log",
            )
            readiness = dict(cfg.get("readiness") or {})
            await self._wait_for_sctp(
                processes,
                int(readiness.get("ric_port", 36421)),
                float(readiness.get("ric_timeout_s", 30)),
                ric,
            )
            await self._wait_for_logs(
                processes,
                ("ric",),
                readiness.get("ric_log_patterns", ["nearRT-RIC", "listening"]),
                float(readiness.get("ric_log_grace_s", 2)),
                required=bool(readiness.get("ric_log_required", False)),
                simulation=simulation,
                callback=progress,
            )
            ric.mark_ready()

            self._emit(simulation, "STARTING", "RIC gotowy. Uruchamianie ns-3.", progress)
            ns3 = processes.get("ns3")
            # Dodany lokalny wrapper ns-3 odmawia startu wyłącznie na
            # podstawie zmiennej USER=root, mimo że proces działa w
            # odizolowanym kontenerze sesji i musi móc zapisywać artefakty w
            # katalogu builda. Ustawiamy neutralną nazwę użytkownika tylko w
            # środowisku ns-3; nie zmieniamy UID kontenera ani uprawnień
            # plików. Dzięki temu działa zarówno ten wrapper, jak i starszy
            # obraz, który takiej kontroli nie posiada.
            ns3_environment = {"USER": "oranlab"}
            await ns3.start(
                ["./ns3", "run", scenario_expression],
                root,
                directory / "logs" / "ns3.log",
                ns3_environment,
            )
            startup_live_started = asyncio.get_running_loop().time()
            startup_live_next_poll = startup_live_started
            await self._wait_for_logs(
                processes,
                ("ric", "ns3"),
                readiness.get("e2_setup_patterns", ["E2[- ]SETUP[- ]REQUEST"]),
                float(readiness.get("e2_timeout_s", 180)),
                simulation=simulation,
                callback=progress,
                live_poll=publish_startup_live,
            )
            await self._wait_for_logs(
                processes,
                ("ric", "ns3"),
                readiness.get("e2_node_patterns", ["SETUP[- ]RESPONSE[- ]SUCCESS"]),
                float(readiness.get("e2_timeout_s", 180)),
                simulation=simulation,
                callback=progress,
                live_poll=publish_startup_live,
            )
            ns3.mark_ready()

            xapp_key = simulation.configuration.xapp
            xapp_restart_plan: Optional[Dict[str, Any]] = None
            if xapp_key != "none":
                xapp_cwd, xapp_command = prepared_xapp_command or self._xapp_command(cfg, discovery, xapp_key)
                environment = {
                    key: str(value)
                    for key, value in simulation.configuration.xapp_environment.items()
                    if value not in (None, "")
                }
                environment["EE_DECISION_LOG"] = posixpath.join(root, "xapp_ee_decisions-1.csv")
                self._emit(
                    simulation,
                    "STARTING",
                    "Węzeł E2 gotowy. Uruchamianie xAppa jako ostatniego komponentu.",
                    progress,
                )
                await processes.get("xapp").start(
                    xapp_command,
                    xapp_cwd,
                    directory / "logs" / "xapp.log",
                    environment,
                )
                xapp_restart_plan = {
                    "command": xapp_command,
                    "cwd": xapp_cwd,
                    "environment": environment,
                    "decision_log_root": root,
                }

            simulation.status = SimulationStatus.RUNNING
            self.storage.save(simulation)
            self._emit(simulation, "RUNNING", "Symulacja jest uruchomiona.", progress)
            await self._wait_for_end(
                processes,
                xapp_restart_plan,
                directory,
                cfg,
                simulation,
                progress,
                root=root,
                live_metrics=live_metrics,
                live_topology=live_topology,
                live_kpm=live_kpm,
                live_xapp_decisions=live_xapp_decisions,
                real_time_budget_target_active_s=self._real_time_budget_target_active_s(simulation),
            )
            self._raise_if_stopped()
            await self._stop_processes(processes)
            await run_in_thread(self._archive_outputs, processes, root, directory)
            outputs_archived = True
            simulation.result = self.parser.parse(directory)
            self._verify_required_live_timeseries(directory, simulation)
            self._apply_runtime_warnings(simulation)
            simulation.status = SimulationStatus.COMPLETED
            simulation.error = None
            self._emit(simulation, "COMPLETED", "Wyniki zostały zapisane.", progress)
        except SimulationStopped:
            simulation.runtime["failure_kind"] = "stopped"
            simulation.runtime["experiment_valid"] = False
            simulation.runtime["termination"] = {"reason": "user_stop", "partial": True}
            simulation.status = SimulationStatus.STOPPED
            simulation.error = "Symulacja została zatrzymana przez użytkownika."
            self._emit(simulation, "STOPPED", simulation.error, progress)
        except Exception as exc:
            simulation.runtime["failure_kind"] = "runtime"
            if processes is not None:
                # Cleanup changes process statuses and sends TERM/KILL.
                # Retain the observations which actually triggered failure.
                simulation.runtime["failure_processes"] = processes.snapshot()
            failed_session_id = str(getattr(exc, "session_id", "") or "")
            if failed_session_id:
                simulation.runtime["session_id"] = failed_session_id
            simulation.runtime["experiment_valid"] = False
            simulation.runtime.setdefault("termination", {"reason": "error", "partial": True})
            simulation.status = SimulationStatus.FAILED
            simulation.error = str(exc)
            self._emit(simulation, "FAILED", simulation.error, progress)
        finally:
            if processes is not None:
                try:
                    await self._stop_processes(processes)
                except Exception as exc:
                    self._append_log(simulation, "Błąd sprzątania procesów: {}".format(exc))
                if root and not outputs_archived:
                    try:
                        await run_in_thread(self._archive_outputs, processes, root, directory)
                        outputs_archived = True
                        partial = self.parser.parse(directory)
                        if self._has_result_data(partial):
                            if simulation.status == SimulationStatus.STOPPED:
                                self._record_runtime_warning(
                                    simulation, "Wynik niepełny: symulacja została zatrzymana przed zakończeniem."
                                )
                            elif simulation.status == SimulationStatus.FAILED:
                                self._record_runtime_warning(
                                    simulation, "Wynik niepełny: symulacja zakończyła się błędem."
                                )
                            simulation.result = partial
                            self._apply_runtime_warnings(simulation)
                    except Exception as exc:
                        self._append_log(simulation, "Błąd archiwizacji częściowych danych: {}".format(exc))
                simulation.runtime["processes"] = processes.snapshot()
            if session is not None:
                try:
                    removed = await run_in_thread(self.sessions.stop, session)
                    simulation.runtime["removed_containers"] = removed
                except Exception as exc:
                    simulation.runtime["cleanup_failed"] = str(exc)
                    self._append_log(simulation, "Błąd sprzątania kontenera: {}".format(exc))
            self._current_session = None
            simulation.ended_at = now_utc()
            self.storage.save(simulation)
        return simulation

    def _emit(
        self,
        simulation: Simulation,
        phase: str,
        message: str,
        callback: Optional[ProgressCallback],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if phase in {
            "VALIDATING",
            "STARTING",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "STOPPED",
        }:
            simulation.runtime["stage"] = phase
        # Per-process log files already contain the full stream.  Repeating
        # every line in runner.log would turn a long live session into an
        # unnecessary second unbounded log file.
        if phase != "LOG":
            self._append_log(simulation, "{}: {}".format(phase, message))
        self._notify(callback, simulation, phase, message, payload)

    @staticmethod
    def _notify(
        callback: Optional[ProgressCallback],
        simulation: Simulation,
        phase: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not callback:
            return
        try:
            callback(simulation, phase, message, dict(payload or {}))
        except TypeError:
            # Integrations predating structured events still receive the
            # original event form.  The application-owned controller uses four
            # arguments.
            callback(simulation, phase, message)

    def _append_log(self, simulation: Simulation, message: str) -> None:
        if not simulation.log_path:
            return
        path = Path(simulation.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write("[{}] {}\n".format(stamp, message))

    @staticmethod
    def _has_result_data(result: Any) -> bool:
        return any(value is not None for value in result.metrics.values()) or any(
            value is not None for value in result.quality.values()
        ) or any(result.series.values())

    @staticmethod
    def _record_runtime_warning(simulation: Simulation, message: str) -> None:
        warnings = simulation.runtime.setdefault("warnings", [])
        if message not in warnings:
            warnings.append(message)

    @staticmethod
    def _apply_runtime_warnings(simulation: Simulation) -> None:
        if simulation.result is None:
            return
        for message in simulation.runtime.get("warnings", []):
            if message not in simulation.result.warnings:
                simulation.result.warnings.append(message)

    def _raise_if_stopped(self) -> None:
        if self._stop_requested.is_set():
            raise SimulationStopped()

    @staticmethod
    def _component_path(snapshot: Mapping[str, Any], key: str) -> str:
        value = ((snapshot.get("components") or {}).get(key) or {}).get("path")
        if not value:
            raise RuntimeError("Brak wymaganego komponentu w kontenerze: {}".format(key))
        return str(value)

    @staticmethod
    def _rebuild_synced_scenario(
        processes: _SessionProcesses,
        ns3_root: str,
        remote_scenario: str,
    ) -> str:
        """Wymusza build dokładnie synchronizowanego targetu scratch.

        Jest to celowo mała warstwa nad istniejącym wrapperem ``./ns3``. Nie
        zmienia obrazu, CMake ani innych targetów; odrzuca ścieżki poza
        ``scratch/`` i zatrzymuje run przy błędzie kompilacji zamiast odpalać
        pozostawiony binarny artefakt z poprzedniej wersji scenariusza.
        """

        root = posixpath.normpath(str(ns3_root))
        remote = posixpath.normpath(str(remote_scenario))
        prefix = root.rstrip("/") + "/"
        if not root.startswith("/") or not remote.startswith(prefix):
            raise RuntimeError("Scenariusz do przebudowy nie należy do katalogu ns-3")
        relative = posixpath.relpath(remote, root)
        if not relative.startswith("scratch/") or relative.startswith("../"):
            raise RuntimeError("Do przebudowy dozwolony jest tylko target scratch")
        target, extension = posixpath.splitext(relative)
        if extension != ".cc" or not target or target.startswith("../"):
            raise RuntimeError("Niepoprawny target scenariusza do przebudowy: {}".format(relative))
        command = "cd {} && env USER=oranlab ./ns3 build {}".format(
            shlex.quote(root), shlex.quote(target)
        )
        code, output = processes.exec_text(command)
        if code != 0:
            detail = str(output or "").strip()
            if len(detail) > 3000:
                detail = detail[-3000:]
            raise RuntimeError(
                "Nie udało się zbudować synchronizowanego scenariusza {}: {}".format(
                    target, detail or "brak szczegółów z ns-3"
                )
            )
        return target

    @staticmethod
    def _validate_inventory(snapshot: Mapping[str, Any]) -> None:
        docker = snapshot.get("docker") or {}
        simulator = snapshot.get("simulator") or {}
        if not docker.get("available"):
            raise RuntimeError("Docker nie jest dostępny w czasie uruchamiania")
        if not simulator.get("image_found"):
            raise RuntimeError("Nie znaleziono obrazu symulatora")
        if not simulator.get("container_found"):
            raise RuntimeError("Kontener sesji nie został wykryty")
        for key in ("near_rt_ric", "ns3_root"):
            component = (snapshot.get("components") or {}).get(key) or {}
            if not component.get("detected"):
                raise RuntimeError("Brak w kontenerze: {}".format(component.get("label") or key))

    def _scenario_expression(
        self,
        config: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        ns3_root: str,
        simulation: Simulation,
        remote_scenario: Optional[str] = None,
    ) -> str:
        scenario_key = simulation.configuration.scenario
        item = (config.get("scenarios") or {}).get(scenario_key)
        if not isinstance(item, dict):
            raise RuntimeError("Nieznany scenariusz: {}".format(scenario_key))
        configured_path = str(item.get("path") or "")
        detected = remote_scenario or self._container_scenario_source_path(
            config, snapshot, simulation, ns3_root=ns3_root
        )
        relative = posixpath.relpath(detected, ns3_root) if detected.startswith(ns3_root.rstrip("/") + "/") else configured_path
        allowed = {str(name) for name in item.get("allowed_params") or []}
        parameters = {
            key: value
            for key, value in simulation.configuration.parameters.items()
            if value not in (None, "") and (not allowed or key in allowed)
        }
        args = []
        for key, value in parameters.items():
            if isinstance(value, bool):
                value = int(value)
            elif key in INTEGER_PARAMETERS:
                value = int(float(value))
            args.append("--{}={}".format(key, value))
        return " ".join([relative] + args)

    @staticmethod
    def _container_scenario_source_path(
        config: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        simulation: Simulation,
        ns3_root: Optional[str] = None,
    ) -> str:
        """Zwraca wykryty plik albo bezpieczny cel synchronizacji w ns-3.

        Obraz po zbudowaniu z lokalnych źródeł ma katalog ``scratch``, lecz
        może nie mieć jeszcze scenariusza dostarczanego razem z GUI.  Nie
        wolno wtedy kończyć runu przed synchronizacją pliku.  Fallback jest
        dozwolony wyłącznie dla względnej ścieżki skonfigurowanej pod znanym
        rootem ns-3; nie może wskazać niczego poza nim.
        """

        scenario_key = simulation.configuration.scenario
        item = (config.get("scenarios") or {}).get(scenario_key)
        if not isinstance(item, Mapping):
            raise RuntimeError("Nieznany scenariusz: {}".format(scenario_key))
        configured_path = str(item.get("path") or "")
        base = posixpath.basename(configured_path.replace("\\", "/"))
        for candidate in snapshot.get("scenarios") or []:
            source = str((candidate or {}).get("source_path") or "")
            if base and posixpath.basename(source) == base and source.startswith("/"):
                return source
        if ns3_root:
            root = posixpath.normpath(str(ns3_root))
            relative = posixpath.normpath(configured_path.replace("\\", "/"))
            if (
                not relative
                or relative in {".", ".."}
                or relative.startswith("../")
                or relative.startswith("/")
            ):
                raise RuntimeError(
                    "Niepoprawna względna ścieżka scenariusza: {}".format(configured_path)
                )
            destination = posixpath.normpath(posixpath.join(root, relative))
            if not destination.startswith(root.rstrip("/") + "/"):
                raise RuntimeError(
                    "Ścieżka scenariusza wychodzi poza katalog ns-3: {}".format(configured_path)
                )
            return destination
        raise RuntimeError("Scenariusz nie istnieje w kontenerze: {}".format(configured_path))

    @staticmethod
    def _prepare_packaged_xapp(
        processes: _SessionProcesses,
        config: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        directory: Path,
        simulation: Simulation,
    ) -> Tuple[str, List[str]]:
        """Build the shipped controller in this session, before traffic starts.

        A scenario update alone does not update the xApp inside an old image.
        Use the same CMake ownership rules as the explicit xApp build service,
        and launch the binary actually produced by this build.
        """
        source = RESOURCE_DIR / "resources" / "xapp_my_energy_optimized.c"
        if not source.is_file():
            raise RuntimeError("Brak dostarczonego źródła xAppa My Energy: {}".format(source))
        entry = (config.get("xapps") or {}).get("optimized") or {}
        target = posixpath.basename(str(entry.get("executable") or ""))
        if not re.fullmatch(r"xapp_[A-Za-z0-9_]+", target):
            raise RuntimeError("Niepoprawny target xAppa My Energy: {}".format(target))
        flexric_root = SimulationRunner._component_path(snapshot, "flexric_root")
        container = processes.session.container
        source_dir = XAppService._find_source_dir(container, flexric_root, target)
        if not source_dir:
            raise RuntimeError("Nie wykryto katalogu CMake dla dostarczonego xAppa.")
        remote_source = posixpath.join(source_dir, target + ".c")
        synced = sync_packaged_scenario(processes, source, remote_source)
        simulation.runtime["xapp_source"] = {
            "local_path": str(source), "container_path": remote_source,
            "sha256": synced.digest, "sync_state": synced.state, "build_state": "building",
        }
        log_path = directory / "logs" / "xapp-build.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        build_root = posixpath.join(flexric_root, "build")
        commands = [
            XAppService._cmake_target_script(posixpath.join(source_dir, "CMakeLists.txt"), target),
            "cmake --build {} --target {} -j 2".format(shlex.quote(build_root), shlex.quote(target)),
        ]
        with log_path.open("w", encoding="utf-8") as handle:
            for command in commands:
                code, output = processes.exec_text(command)
                handle.write("$ {}\n{}\n".format(command, output))
                handle.flush()
                if code != 0:
                    simulation.runtime["xapp_source"]["build_state"] = "failed"
                    raise RuntimeError("Build dostarczonego xAppa nie powiódł się (kod {}): {}".format(code, output[-2000:]))
        cwd = XAppService._find_built_executable(container, build_root, target)
        if not cwd:
            raise RuntimeError("Build xAppa nie utworzył wykonywalnej binarki.")
        simulation.runtime["xapp_source"].update({
            "build_state": "verified", "build_target": target,
            "executable_path": posixpath.join(cwd, target), "build_log": str(log_path),
        })
        return cwd, ["./" + target]

    @staticmethod
    def _xapp_command(
        config: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        xapp_key: str,
    ) -> Tuple[str, List[str]]:
        item = (config.get("xapps") or {}).get(xapp_key)
        if not isinstance(item, dict):
            raise RuntimeError("Nieznany xApp: {}".format(xapp_key))
        configured = str(item.get("executable") or "")
        base = posixpath.basename(configured.replace("\\", "/").replace("./", ""))
        configured_cwd = str(item.get("cwd") or "")
        cwd_candidates = [configured_cwd] if configured_cwd else []
        if configured_cwd.startswith("~/"):
            cwd_candidates.append(posixpath.join("/root", configured_cwd[2:]))
        for cwd in cwd_candidates:
            normalized_cwd = posixpath.normpath(cwd)
            expected = posixpath.join(normalized_cwd, base)
            for candidate in snapshot.get("xapps") or []:
                path = str((candidate or {}).get("executable_path") or "")
                if base and posixpath.normpath(path) == expected:
                    return normalized_cwd, ["./" + base]
        for candidate in snapshot.get("xapps") or []:
            path = str((candidate or {}).get("executable_path") or "")
            if base and posixpath.basename(path) == base and path.startswith("/"):
                return posixpath.dirname(path), ["./" + posixpath.basename(path)]
        raise RuntimeError("xApp nie istnieje w kontenerze: {}".format(configured or xapp_key))

    def _clean_outputs(self, processes: _SessionProcesses, root: str) -> None:
        groups = json.dumps(self.OUTPUT_GROUPS)
        script = (
            "python3 -c "
            + shlex.quote(
                "import glob,json,os,sys; root=sys.argv[1]; groups=json.loads(sys.argv[2]); "
                "[os.remove(path) for patterns in groups.values() for pattern in patterns "
                "for path in glob.glob(os.path.join(root, pattern)) if os.path.isfile(path)]"
            )
            + " " + shlex.quote(root) + " " + shlex.quote(groups)
        )
        code, output = processes.exec_text(script)
        if code != 0:
            raise RuntimeError("Nie można wyczyścić plików roboczych: {}".format(output))

    async def _wait_for_sctp(
        self,
        processes: _SessionProcesses,
        port: int,
        timeout_s: float,
        ric: ManagedDockerProcess,
    ) -> None:
        script = (
            "python3 -c "
            + shlex.quote(
                "import os,sys; port=int(sys.argv[1]); path='/proc/net/sctp/eps'; "
                "text=open(path,encoding='utf-8',errors='replace').read() if os.path.isfile(path) else ''; "
                "lines=text.splitlines(); header=lines[0].split() if lines else []; "
                "idx=header.index('LPORT') if 'LPORT' in header else 5; ports=[]; "
                "[ports.append(int(parts[idx],0)) for parts in (line.split() for line in lines[1:]) "
                "if len(parts)>idx and parts[0] != 'ENDPT']; raise SystemExit(0 if port in ports else 1)"
            )
            + " " + shlex.quote(str(port))
        )
        deadline = asyncio.get_running_loop().time() + max(0.1, timeout_s)
        while True:
            self._raise_if_stopped()
            code, _ = await run_in_thread(processes.exec_text, script)
            if not ric.alive:
                snapshot = ric.snapshot()
                raise RuntimeError(
                    "nearRT-RIC zakończył się przed otwarciem SCTP {} "
                    "(status={}, exit_code={}, error={}, log={})".format(
                        port,
                        snapshot.get("status"),
                        snapshot.get("exit_code"),
                        snapshot.get("error"),
                        snapshot.get("log_path"),
                    )
                )
            if code == 0:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("nearRT-RIC nie otworzył SCTP {} w wymaganym czasie".format(port))
            await asyncio.sleep(0.25)

    async def _wait_for_logs(
        self,
        processes: _SessionProcesses,
        names: Iterable[str],
        patterns: Iterable[str],
        timeout_s: float,
        required: bool = True,
        simulation: Optional[Simulation] = None,
        callback: Optional[ProgressCallback] = None,
        live_poll: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        names = tuple(names)
        sequences = {name: 0 for name in names}
        deadline = asyncio.get_running_loop().time() + max(0.1, timeout_s)
        while True:
            self._raise_if_stopped()
            if live_poll is not None:
                # Dane live są obserwacją procesu, nie warunkiem gotowości
                # E2. Błąd pojedynczego odczytu nie może anulować działającej
                # negocjacji ani ukryć jej właściwego błędu.
                try:
                    await live_poll()
                except Exception:
                    pass
            if simulation is not None:
                self._publish_process_logs(processes, simulation, callback)
            exited: List[Tuple[str, Dict[str, Any]]] = []
            for name in names:
                process = processes.get(name)
                logs = process.logs_after(sequences[name], 500)
                sequences[name] = int(logs.get("last_seq") or sequences[name])
                for item in logs.get("lines") or []:
                    text = str(item.get("text") or "")
                    if any(pattern.search(text) for pattern in compiled):
                        return
                if not process.alive:
                    exited.append((name, process.snapshot()))
            if exited:
                name, snapshot = exited[0]
                raise RuntimeError(
                    "{} zakończył się przed wymaganym komunikatem "
                    "(status={}, exit_code={}, error={}, log={})".format(
                        name,
                        snapshot.get("status"),
                        snapshot.get("exit_code"),
                        snapshot.get("error"),
                        snapshot.get("log_path"),
                    )
                )
            if asyncio.get_running_loop().time() >= deadline:
                if required:
                    raise TimeoutError("Nie wykryto wymaganego komunikatu gotowości")
                return
            await asyncio.sleep(0.25)

    @staticmethod
    def _configured_simulation_duration(simulation: Simulation) -> Optional[float]:
        try:
            duration = float(simulation.configuration.parameters.get("simTime"))
        except (TypeError, ValueError):
            return None
        return duration if duration > 0.0 else None

    @classmethod
    def _live_metrics_contract(
        cls,
        config: Mapping[str, Any],
        simulation: Simulation,
        local_source: Optional[Path],
    ) -> Dict[str, Any]:
        """Opisuje wymagania realnego źródła metryk bieżącego scenariusza.

        Konfiguracja może oznaczyć wymóg wprost.  Dla zapisanej konfiguracji
        z poprzedniej wersji zachowujemy ten sam rezultat, odczytując markery
        z dostarczonego źródła C++.  Dzięki temu brak nowego pola JSON nie
        pozwoli przypadkowo ominąć kontroli przy aktualizacji aplikacji.
        """

        scenarios = config.get("scenarios")
        item = (
            scenarios.get(simulation.configuration.scenario)
            if isinstance(scenarios, Mapping)
            else None
        )
        configured_required = (
            item.get("requires_live_timeseries")
            if isinstance(item, Mapping)
            else None
        )
        source_declares = source_declares_ee_timeseries(local_source)
        required = bool(configured_required) or source_declares
        return {
            "required": required,
            "file": cls.LIVE_TIMESERIES_FILE,
            "accepted_files": list(cls.LIVE_METRICS_FILES),
            "required_columns": list(cls.LIVE_TIMESERIES_COLUMNS),
            "source_declares_timeseries": source_declares,
            "state": "awaiting_source" if required else "not_required",
        }

    @classmethod
    def _verify_required_live_timeseries(
        cls,
        directory: Path,
        simulation: Simulation,
    ) -> None:
        """Nie pozwala oznaczyć pełnego runu jako poprawnego bez jego danych.

        Awaryjne pliki energii są wartościowe dla starych scenariuszy, ale nie
        zawierają ruchu i nie mogą potwierdzić EE, kosztu J/Mbit ani
        przepływności.  Dla źródła, które deklaruje pełną serię, brak artefaktu
        jest błędem wykonania, a nie częściowym sukcesem.
        """

        contract = simulation.runtime.get("live_metrics_contract")
        if not isinstance(contract, dict) or not contract.get("required"):
            return
        configured_files = contract.get("accepted_files")
        if isinstance(configured_files, (list, tuple)):
            filenames = tuple(
                str(item).strip() for item in configured_files if str(item).strip()
            )
        else:
            filenames = ()
        if not filenames:
            filenames = (str(contract.get("file") or cls.LIVE_TIMESERIES_FILE),)

        candidates = []
        for filename in filenames:
            # Nowe runy archiwizują metryki w timeseries, ale odczyt katalogu
            # głównego zachowuje zgodność z istniejącymi wynikami.
            for path in (directory / "timeseries" / filename, directory / filename):
                if path.is_file() and path not in candidates:
                    candidates.append(path)
        if not candidates:
            contract["state"] = "artifact_missing"
            raise RuntimeError(
                "Nie powstał żaden wymagany plik pełnych metryk live ({}) . "
                "Przebieg oznaczono jako FAILED, aby nie zapisać niepełnego wyniku jako poprawnego.".format(
                    ", ".join(filenames)
                )
            )

        checked = []
        required_metrics = (
            "total_energy_j",
            "average_power_w",
            "throughput_mbit_per_s",
            "delivered_mbit",
        )
        # Zero delivered traffic is a legitimate (poor QoS) outcome. J/Mbit
        # is undefined in that case and must not turn a finished run into an
        # infrastructure failure or trigger a duplicate experiment.
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                checked.append("{}: nie można odczytać".format(path.name))
                continue
            collector = LiveMetricsCollector()
            accepted = collector.ingest_csv_text(text, source=path.name)
            metrics = collector.snapshot().get("current_metrics") or {}
            missing_metrics = [
                key for key in required_metrics if metrics.get(key) is None
            ]
            if accepted > 0 and not missing_metrics:
                contract["state"] = "artifact_verified"
                contract["accepted_rows"] = accepted
                contract["artifact_path"] = str(path)
                contract["artifact_file"] = path.name
                return
            if accepted < 1:
                checked.append("{}: brak poprawnej próbki".format(path.name))
            else:
                checked.append(
                    "{}: brakuje {}".format(path.name, ", ".join(missing_metrics))
                )

        contract["state"] = "artifact_incomplete"
        contract["checked_artifacts"] = checked
        raise RuntimeError(
            "Żaden dostępny plik metryk live nie zawiera pełnego wyniku ({}).".format(
                "; ".join(checked)
            )
        )

    @staticmethod
    def _real_time_budget_target_active_s(simulation: Simulation) -> Optional[float]:
        """Odczytuje twardy aktywny limit dla dowolnego runu kolejki.

        Nie korzystamy z technicznego ``wall_clock_timeout_s``: ten limit ma
        nadal chronić zawieszony backend i powodować błąd. Po budżecie
        wysyłamy do ns-3 żądanie zatrzymania niezależnie od stanu pliku live.
        ``simTime`` odczytany z CSV jest wyłącznie obserwacją, nigdy wartością
        wyprowadzoną z zegara Pythona ani warunkiem kolejnego przebiegu.
        """

        timing = simulation.runtime.get("queue_timing")
        if not isinstance(timing, dict):
            return None
        if timing.get("cutoff_policy") not in {
            "hard_real_time_each_run",
            # Zapisane wcześniej sesje zachowują dotychczasową semantykę
            # pierwszego przebiegu zamiast otrzymać nieoczekiwany cutoff.
            "hard_real_time",
        }:
            return None
        try:
            value = float(timing.get("per_run_target_active_real_s"))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value > 0.0 else None

    @staticmethod
    def _record_real_time_budget_observation(
        simulation: Simulation,
        target_active_s: float,
        elapsed_active_s: float,
        collector: Optional[LiveMetricsCollector],
        stop_reason: str,
        cutoff_requested: bool,
    ) -> Optional[float]:
        """Zapisuje wyłącznie obserwację z rzeczywistego pełnego CSV metryk."""

        timing = simulation.runtime.get("queue_timing")
        timing = dict(timing) if isinstance(timing, dict) else {}
        observed = collector.last_time_s if collector is not None else None
        if observed is not None:
            try:
                observed = float(observed)
            except (TypeError, ValueError):
                observed = None
        if observed is not None and (not math.isfinite(observed) or observed <= 0.0):
            observed = None
        timing.update(
            {
                "state": "observed" if observed is not None else "observation_unavailable",
                "per_run_target_active_real_s": target_active_s,
                "active_elapsed_real_s": max(0.0, elapsed_active_s),
                "stop_reason": stop_reason,
                "cutoff_requested": bool(cutoff_requested),
                "sim_time_policy": "observational_only",
            }
        )
        if observed is not None:
            timing["observed_simulated_time_s"] = observed
            timing["measurement_source"] = (
                collector.primary_source if collector is not None else None
            ) or "pełny CSV metryk"
        simulation.runtime["queue_timing"] = timing
        return observed

    @staticmethod
    def _read_live_csv_tail(
        processes: _SessionProcesses,
        root: str,
        filename: str,
        lines: int = 4096,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Czyta ogon jednego dozwolonego CSV bez kopiowania całego pliku.

        Plik jest otwierany przez ns-3 na pojedynczy zapis, dlatego odrzucenie
        niedomkniętego ostatniego wiersza jest wystarczające, aby nie policzyć
        częściowej próbki. Następne odpytywanie ponownie ją pobierze.
        """

        allowed = {"metrics.csv", "ee_timeseries.csv"}
        if filename not in allowed:
            raise ValueError("Niedozwolony plik metryk live: {}".format(filename))
        path = posixpath.join(root, filename)
        script_source = "\n".join(
            [
                "import collections, os, sys",
                "path = sys.argv[1]",
                "limit = int(sys.argv[2])",
                "if not os.path.isfile(path):",
                "    raise SystemExit(3)",
                "with open(path, 'r', encoding='utf-8', errors='replace') as handle:",
                "    header = handle.readline()",
                "    rows = collections.deque(handle, maxlen=limit)",
                "while rows and not rows[-1].endswith(('\\n', '\\r')):",
                "    rows.pop()",
                "sys.stdout.write(header)",
                "sys.stdout.writelines(rows)",
            ]
        )
        script = "python3 -c {} {} {}".format(
            shlex.quote(script_source), shlex.quote(path), shlex.quote(str(max(1, lines)))
        )
        code, output = processes.exec_text(script)
        if code == 0:
            return output, None
        if code == 3:
            return None, "Plik {} nie został jeszcze utworzony.".format(filename)
        return None, "Nie można odczytać {}: {}".format(filename, output.strip())

    @staticmethod
    def _read_live_timeseries_tail(
        processes: _SessionProcesses,
        root: str,
        lines: int = 4096,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Zgodny wstecz skrót odczytu bezpośredniej serii EE."""

        return SimulationRunner._read_live_csv_tail(
            processes, root, "ee_timeseries.csv", lines
        )

    @staticmethod
    def _read_live_metrics_tail(
        processes: _SessionProcesses,
        root: str,
        lines: int = 4096,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Czyta faktyczny ``metrics.csv`` zapisywany przez obecny backend."""

        return SimulationRunner._read_live_csv_tail(processes, root, "metrics.csv", lines)

    @staticmethod
    def _read_live_energy_file_tails(
        processes: _SessionProcesses,
        root: str,
        lines: int = 4096,
        max_files: int = 256,
    ) -> Dict[str, str]:
        """Czyta ograniczone ogony realnych ``energyfilecell*.csv``.

        Starsze binaria scenariusza mogą nie mieć pełnej serii EE, ale nadal
        zapisują energię każdej komórki. To pomocnicze źródło dostarcza tylko
        energię i moc; nie jest używane do kalibracji czasu ani do tworzenia
        danych ruchowych. Wynik zawiera nazwy bazowe, więc nie ujawniamy GUI
        arbitralnych ścieżek z kontenera.
        """

        script_source = "\n".join(
            [
                "import collections, glob, json, os, sys",
                "root = sys.argv[1]",
                "limit = int(sys.argv[2])",
                "maximum = int(sys.argv[3])",
                "result = {}",
                "pattern = os.path.join(root, 'energyfilecell*.csv')",
                "for path in sorted(glob.glob(pattern))[:maximum]:",
                "    if not os.path.isfile(path):",
                "        continue",
                "    with open(path, 'r', encoding='utf-8', errors='replace') as handle:",
                "        header = handle.readline()",
                "        rows = collections.deque(handle, maxlen=limit)",
                "    while rows and not rows[-1].endswith(('\\n', '\\r')):",
                "        rows.pop()",
                "    result[os.path.basename(path)] = header + ''.join(rows)",
                "print(json.dumps(result))",
            ]
        )
        script = "python3 -c {} {} {} {}".format(
            shlex.quote(script_source),
            shlex.quote(root),
            shlex.quote(str(max(1, lines))),
            shlex.quote(str(max(1, max_files))),
        )
        code, output = processes.exec_text(script)
        if code != 0:
            return {}
        try:
            result = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(result, dict):
            return {}
        return {
            str(name): str(text)
            for name, text in result.items()
            if str(name).startswith("energyfilecell") and str(name).endswith(".csv")
        }

    @staticmethod
    def _read_live_kpm_file_tails(
        processes: _SessionProcesses,
        root: str,
        lines: int = 4096,
        max_files: int = 256,
    ) -> Dict[str, str]:
        """Czyta ograniczone ogony istniejących plików KPM ns-3.

        Nie tworzymy plików ani nie włączamy ``enableE2FileLogging``. Ten
        odczyt jest aktywny tylko wtedy, gdy bieżący backend sam raportuje
        KPM obok połączenia E2. Wynik zawiera wyłącznie nazwy bazowe z
        ścisłej listy wzorców, żeby nie przekazać dalej arbitralnych ścieżek.
        """

        script_source = "\n".join(
            [
                "import collections, glob, json, os, sys",
                "root = sys.argv[1]",
                "limit = int(sys.argv[2])",
                "maximum = int(sys.argv[3])",
                "patterns = ('cu-up-cell-*.txt', 'nr-cu-up-cell-*.txt', 'cu-cp-cell-*.txt', 'du-cell-*.txt')",
                "paths = []",
                "seen = set()",
                "for pattern in patterns:",
                "    for path in sorted(glob.glob(os.path.join(root, pattern))):",
                "        if path not in seen and os.path.isfile(path):",
                "            seen.add(path)",
                "            paths.append(path)",
                "result = {}",
                "for path in paths[:maximum]:",
                "    with open(path, 'r', encoding='utf-8', errors='replace') as handle:",
                "        header = handle.readline()",
                "        rows = collections.deque(handle, maxlen=limit)",
                "    while rows and not rows[-1].endswith(('\\n', '\\r')):",
                "        rows.pop()",
                "    result[os.path.basename(path)] = header + ''.join(rows)",
                "print(json.dumps(result))",
            ]
        )
        script = "python3 -c {} {} {} {}".format(
            shlex.quote(script_source),
            shlex.quote(root),
            shlex.quote(str(max(1, lines))),
            shlex.quote(str(max(1, max_files))),
        )
        code, output = processes.exec_text(script)
        if code != 0:
            return {}
        try:
            result = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(result, dict):
            return {}
        pattern = re.compile(
            r"^(?:nr-cu-up|cu-up|cu-cp|du)-cell-[0-9]+\.txt$",
            re.IGNORECASE,
        )
        return {
            str(name): str(text)
            for name, text in result.items()
            if pattern.match(str(name))
        }

    @staticmethod
    def _read_live_xapp_decision_tails(
        processes: _SessionProcesses,
        root: str,
        lines: int = 1024,
        max_files: int = 16,
    ) -> Dict[str, str]:
        """Czyta ograniczone ogony realnych CSV decyzji xAppa.

        Nie używamy ``docker logs`` ani nie odczytujemy dowolnej ścieżki
        dostarczonej przez GUI. Dozwolone są wyłącznie pliki, które tworzą
        dostarczone xAppy. Pełna historia pozostaje archiwizowanym artefaktem;
        dashboard dostaje jedynie ograniczony ogon i kolektor go deduplikuje.
        """

        script_source = "\n".join(
            [
                "import collections, glob, json, os, sys",
                "root = sys.argv[1]",
                "limit = int(sys.argv[2])",
                "maximum = int(sys.argv[3])",
                "patterns = ('xapp_ee_decisions*.csv', 'xapp_decisions*.csv', 'ml_xapp_*_decisions*.csv')",
                "paths = []",
                "seen = set()",
                "for pattern in patterns:",
                "    for path in sorted(glob.glob(os.path.join(root, pattern))):",
                "        if path not in seen and os.path.isfile(path):",
                "            seen.add(path)",
                "            paths.append(path)",
                "result = {}",
                "for path in paths[:maximum]:",
                "    with open(path, 'r', encoding='utf-8', errors='replace') as handle:",
                "        header = handle.readline()",
                "        rows = collections.deque(handle, maxlen=limit)",
                "    while rows and not rows[-1].endswith(('\\n', '\\r')):",
                "        rows.pop()",
                "    result[os.path.basename(path)] = header + ''.join(rows)",
                "print(json.dumps(result))",
            ]
        )
        script = "python3 -c {} {} {} {}".format(
            shlex.quote(script_source),
            shlex.quote(root),
            shlex.quote(str(max(1, lines))),
            shlex.quote(str(max(1, max_files))),
        )
        code, output = processes.exec_text(script)
        if code != 0:
            return {}
        try:
            result = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(result, dict):
            return {}
        allowed = re.compile(
            r"^(?:xapp_ee_decisions|xapp_decisions|ml_xapp_[a-z0-9_-]+_decisions)[^/\\]*\.csv$",
            re.IGNORECASE,
        )
        return {
            str(name): str(text)
            for name, text in result.items()
            if allowed.match(str(name))
        }

    @staticmethod
    def _read_live_topology_tail(
        processes: _SessionProcesses,
        root: str,
        filename: str,
        lines: int = 8192,
    ) -> Optional[str]:
        """Czyta aktualny fragment jednego faktycznego pliku topologii.

        Scenariusz zapisuje wiersze przyrostowo. Dostarczamy nagłówek i ogon
        pliku, a ``LiveTopologyCollector`` wybiera najnowszy rekord danego UE
        lub gNB. Brak pliku jest normalny dla scenariuszy, które nie tworzą
        mapy, więc nie zamieniamy go w błąd zbierania metryk energii.
        """

        allowed = {"gnbs.txt", "enbs.txt", "ue_position.txt"}
        if filename not in allowed:
            raise ValueError("Niedozwolony plik topologii: {}".format(filename))
        path = posixpath.join(root, filename)
        script_source = "\n".join(
            [
                "import collections, os, sys",
                "path = sys.argv[1]",
                "limit = int(sys.argv[2])",
                "if not os.path.isfile(path):",
                "    raise SystemExit(3)",
                "with open(path, 'r', encoding='utf-8', errors='replace') as handle:",
                "    header = handle.readline()",
                "    rows = collections.deque(handle, maxlen=limit)",
                "while rows and not rows[-1].endswith(('\\n', '\\r')):",
                "    rows.pop()",
                "sys.stdout.write(header)",
                "sys.stdout.writelines(rows)",
            ]
        )
        script = "python3 -c {} {} {}".format(
            shlex.quote(script_source), shlex.quote(path), shlex.quote(str(max(1, lines)))
        )
        code, output = processes.exec_text(script)
        return output if code == 0 else None

    async def _container_snapshot(self, processes: _SessionProcesses) -> Dict[str, Any]:
        """Pobiera jeden snapshot kontenera sesji poza pętlą Tkintera."""

        try:
            result = await run_in_thread(inspect_container, processes.session.container)
        except Exception as exc:
            return {
                "name": str(getattr(processes.session.container, "name", "")),
                "state": "unknown",
                "errors": ["Nie można pobrać stanu kontenera: {}".format(exc)],
            }
        result["role"] = "Kontener sesji"
        return result

    @staticmethod
    def _process_snapshot(
        processes: _SessionProcesses,
        xapp_enabled: bool,
    ) -> Dict[str, Dict[str, Any]]:
        roles = {
            "ric": "nearRT-RIC",
            "ns3": "ns-3",
            "xapp": "xApp",
        }
        snapshot = processes.snapshot()
        result: Dict[str, Dict[str, Any]] = {}
        for name, raw in snapshot.items():
            record = dict(raw or {})
            record["name"] = record.get("name") or name
            record["role"] = roles.get(name, name)
            if name == "xapp" and not xapp_enabled:
                record["status"] = "unused"
                record["state"] = "unused"
                record["role"] = "xApp (wyłączony)"
            result[name] = record
        return result

    @staticmethod
    def _merge_kpm_live_data(
        current_metrics: Dict[str, Any],
        series: Dict[str, List[Dict[str, Any]]],
        series_sources: Dict[str, str],
        kpm_snapshot: Mapping[str, Any],
    ) -> None:
        """Łączy strumienie KPM i energii, nie nadpisując pełnego źródła CSV.

        Gdy scenariusz udostępnia tylko liczniki energii komórek, pochodne EE,
        J/Mbit i średnia moc muszą używać tej samej chwili co ostatnia próbka
        KPM. Pełny metrics.csv pozostaje nadrzędny, gdy jest dostępny.
        """

        kpm_metrics = kpm_snapshot.get("current_metrics")
        kpm_metrics = dict(kpm_metrics) if isinstance(kpm_metrics, Mapping) else {}
        kpm_series = kpm_snapshot.get("series")
        kpm_series = dict(kpm_series) if isinstance(kpm_series, Mapping) else {}
        kpm_sources = kpm_snapshot.get("metric_sources")
        kpm_sources = dict(kpm_sources) if isinstance(kpm_sources, Mapping) else {}
        mappings = (
            ("throughput_mbit_per_s", "throughput_mbit_per_s"),
            ("delivered_mbit", "delivered_mbit"),
            ("delay_ms", "mean_delay_ms"),
            ("mean_sinr_db", "mean_sinr_db"),
            ("p05_sinr_db", "p05_sinr_db"),
            ("mean_prb_pct", "mean_prb_utilization_pct"),
            ("active_ues", "active_ues"),
        )
        for target, source in mappings:
            value = kpm_metrics.get(source)
            if current_metrics.get(target) is None and value is not None:
                current_metrics[target] = value
            existing = series.get(target)
            kpm_points = kpm_series.get(source)
            if not existing and isinstance(kpm_points, list) and kpm_points:
                series[target] = list(kpm_points)
                files = kpm_sources.get(source)
                if isinstance(files, (list, tuple)):
                    origin = ", ".join(str(item) for item in files if str(item).strip())
                else:
                    origin = str(files or "KPM")
                series_sources[target] = "KPM{}".format(": " + origin if origin else "")

        delivered_mbit = kpm_metrics.get("delivered_mbit")
        if delivered_mbit is None:
            delivered_mbit = kpm_snapshot.get("integrated_delivered_mbit")
        if current_metrics.get("delivered_mbit") is None and delivered_mbit is not None:
            current_metrics["delivered_mbit"] = delivered_mbit
        energy_j = current_metrics.get("total_energy_j")
        sample_time_s = kpm_snapshot.get("sample_time_s")
        energy_points_for_current = series.get("energy_j")
        energy_source = str(series_sources.get("energy_j") or "").lower()
        energy_is_cell_fallback = "energyfilecell" in energy_source
        if isinstance(sample_time_s, (int, float)) and isinstance(energy_points_for_current, list):
            aligned = [
                point for point in energy_points_for_current
                if isinstance(point, Mapping)
                and isinstance(point.get("x"), (int, float))
                and point["x"] <= sample_time_s
            ]
            if aligned:
                energy_j = aligned[-1].get("y")
                if energy_is_cell_fallback:
                    # Headline metrics must describe one common observation
                    # time. Otherwise the UI can pair KPM at t=0.8 s with
                    # energy at t=1.0 s and show an EE that cannot be
                    # reconstructed from the visible energy/data cards.
                    current_metrics["total_energy_j"] = energy_j
                    current_metrics["average_power_w"] = (
                        energy_j / float(sample_time_s)
                        if energy_j is not None and sample_time_s > 0
                        else None
                    )
            elif energy_is_cell_fallback:
                # No energy point at or before the KPM sample means there is
                # no valid common window for the derived live metrics yet.
                energy_j = None
                current_metrics["total_energy_j"] = None
                current_metrics["average_power_w"] = None
        if delivered_mbit is not None and energy_j is not None and energy_j > 0:
            if energy_is_cell_fallback or current_metrics.get("energy_efficiency_bit_per_j") is None:
                current_metrics["energy_efficiency_bit_per_j"] = (
                    delivered_mbit * 1_000_000.0 / energy_j
                )
            if delivered_mbit > 0 and (
                energy_is_cell_fallback or current_metrics.get("energy_per_mbit_j") is None
            ):
                current_metrics["energy_per_mbit_j"] = energy_j / delivered_mbit
        elif energy_is_cell_fallback:
            current_metrics["energy_efficiency_bit_per_j"] = None
            current_metrics["energy_per_mbit_j"] = None

        delivered_points = kpm_series.get("delivered_mbit")
        energy_points = series.get("energy_j")
        if isinstance(delivered_points, list) and isinstance(energy_points, list):
            aligned_energy = sorted(
                (point for point in energy_points if isinstance(point, Mapping)),
                key=lambda point: float(point.get("x", 0.0)),
            )
            for metric_key, calculate in (
                ("energy_efficiency_bit_per_j", lambda data, energy: data * 1_000_000.0 / energy),
                ("energy_per_mbit_j", lambda data, energy: energy / data),
            ):
                if series.get(metric_key):
                    continue
                points: List[Dict[str, Any]] = []
                energy_index = 0
                energy_at_time: Optional[float] = None
                for data_point in delivered_points:
                    if not isinstance(data_point, Mapping):
                        continue
                    try:
                        time_s = float(data_point["x"])
                        data_mbit = float(data_point["y"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    while energy_index < len(aligned_energy):
                        try:
                            energy_time = float(aligned_energy[energy_index]["x"])
                            candidate_energy = float(aligned_energy[energy_index]["y"])
                        except (KeyError, TypeError, ValueError):
                            energy_index += 1
                            continue
                        if energy_time > time_s:
                            break
                        energy_at_time = candidate_energy
                        energy_index += 1
                    if energy_at_time is None or energy_at_time <= 0:
                        continue
                    if metric_key == "energy_per_mbit_j" and data_mbit <= 0:
                        continue
                    points.append({"x": time_s, "y": calculate(data_mbit, energy_at_time)})
                if points:
                    series[metric_key] = points
                    series_sources[metric_key] = (
                        "energia energyfilecell*.csv i całka przepływności PDCP z KPM (szacunek)"
                    )
        if delivered_mbit is not None and energy_j is not None:
            suffix = "energia energyfilecell*.csv + całka przepływności PDCP KPM (szacunek)"
            if current_metrics.get("energy_efficiency_bit_per_j") is not None:
                series_sources.setdefault("energy_efficiency_bit_per_j", suffix)
            if current_metrics.get("energy_per_mbit_j") is not None:
                series_sources.setdefault("energy_per_mbit_j", suffix)

    async def _publish_live_snapshot(
        self,
        processes: _SessionProcesses,
        root: str,
        collector: LiveMetricsCollector,
        simulation: Simulation,
        callback: Optional[ProgressCallback],
        elapsed_real_s: float,
        container: Optional[Dict[str, Any]],
        live_topology: Optional[LiveTopologyCollector] = None,
        live_kpm: Optional[KpmLiveCollector] = None,
        live_xapp_decisions: Optional[XappDecisionCollector] = None,
    ) -> None:
        """Łączy prawdziwe próbki ns-3 i status komponentów w jedno zdarzenie."""

        collection_error: Optional[str] = None
        accepted = 0
        metrics_error: Optional[str] = None
        timeseries_error: Optional[str] = None
        # Najpierw wykorzystujemy metrics.csv, jeśli backend faktycznie go
        # tworzy podczas runu. Historyczne pełne przebiegi pokazują, że to
        # plik z kompletem EE, kosztu energii, danych i KPI jakości. Gdy go
        # nie ma albo nie ma jeszcze próbki, zachowujemy bezpośrednią serię EE
        # jako prawdziwy fallback. Po wybraniu źródła nie mieszamy różnych
        # nagłówków ani punktów o tym samym czasie symulowanym.
        if collector.primary_source in (None, "metrics.csv"):
            try:
                text, metrics_error = await run_in_thread(
                    self._read_live_metrics_tail, processes, root
                )
                if text:
                    metrics_accepted = collector.ingest_csv_text(
                        text, source="metrics.csv"
                    )
                    if metrics_accepted and (
                        not collector.has_full_metric_schema
                        or not collector.has_complete_primary_sample
                    ):
                        # Częściowy metrics.csv nie może zablokować pełnej
                        # serii EE. Nie wystarcza także sam pełny nagłówek:
                        # starszy backend potrafi zostawić w pierwszych
                        # wierszach puste pola danych. Nie pokazujemy jego
                        # domyślnych/ubogich pól jako kompletnego widoku live.
                        collector.reset()
                        metrics_error = (
                            "metrics.csv nie zawiera pełnej próbki energii i danych."
                        )
                    else:
                        accepted += metrics_accepted
            except Exception as exc:
                metrics_error = "Nie można przetworzyć metrics.csv: {}".format(exc)
        if collector.primary_source is None:
            try:
                text, timeseries_error = await run_in_thread(
                    self._read_live_timeseries_tail, processes, root
                )
                if text:
                    accepted += collector.ingest_csv_text(
                        text, source=self.LIVE_TIMESERIES_FILE
                    )
            except Exception as exc:
                timeseries_error = "Nie można przetworzyć danych live: {}".format(exc)
        if collector.primary_source is None:
            # Jeden z plików może być nieobecny całkowicie normalnie; błąd
            # pokazujemy dopiero wtedy, gdy nie udało się pobrać żadnego
            # kompletnego źródła metryk.
            collection_error = timeseries_error or metrics_error

        # Starsze warianty scenariusza zapisują jedynie energyfilecell*.csv.
        # Dopóki nie dostaliśmy pełnego CSV, pokazujemy z nich wyłącznie
        # energię i moc. Kolektor zachowuje oba źródła rozdzielnie.
        if collector.last_time_s is None:
            try:
                energy_files = await run_in_thread(
                    self._read_live_energy_file_tails, processes, root
                )
                if energy_files:
                    collector.ingest_energy_files(energy_files)
            except Exception:
                # Brak pomocniczych plików jest normalny dla scenariuszy,
                # które nie raportują energii komórek w tej postaci.
                pass

        if live_topology is not None:
            # Nie mieszamy topologii z krytycznym odczytem energy metrics:
            # część scenariuszy może nie tworzyć tych plików, a widok wtedy
            # pozostaje na potwierdzonym planie zamiast zgłaszać błąd runu.
            try:
                cells_text = await run_in_thread(
                    self._read_live_topology_tail, processes, root, "gnbs.txt"
                )
                if cells_text:
                    live_topology.ingest_cells_csv(cells_text)
                enbs_text = await run_in_thread(
                    self._read_live_topology_tail, processes, root, "enbs.txt"
                )
                if enbs_text:
                    # LTE anchor może być raportowaną komórką UE; bez jego
                    # pozycji linia UE → cell 1 znikałaby z mapy mimo danych
                    # zapisanych przez ten sam scenariusz.
                    live_topology.ingest_enbs_csv(enbs_text)
                ues_text = await run_in_thread(
                    self._read_live_topology_tail, processes, root, "ue_position.txt"
                )
                if ues_text:
                    live_topology.ingest_ues_csv(ues_text)
            except Exception:
                # Jedynym źródłem prawdy pozostają pliki ns-3. Brak/awaria
                # opcjonalnego odczytu nie ma wpływu na metryki ani proces.
                pass

        kpm_snapshot: Optional[Dict[str, Any]] = None
        if live_kpm is not None:
            # Ten odczyt nie steruje E2 ani trybem scenariusza. Pobiera tylko
            # ogony plików KPM, jeżeli bieżące ns-3 już je udostępniło.
            try:
                kpm_files = await run_in_thread(
                    self._read_live_kpm_file_tails, processes, root
                )
                if kpm_files:
                    live_kpm.ingest_files(kpm_files)
            except Exception:
                # KPM jest dodatkowym, rzeczywistym kanałem jakości. Jego
                # brak nie może zatrzymać pomiaru energii i danych.
                pass
            candidate = live_kpm.snapshot()
            candidate_status = dict(candidate.get("data_status") or {})
            if candidate_status.get("source_file_count"):
                kpm_snapshot = candidate

        xapp_decision_snapshot: Optional[Dict[str, Any]] = None
        if live_xapp_decisions is not None:
            try:
                decision_files = await run_in_thread(
                    self._read_live_xapp_decision_tails, processes, root
                )
                if decision_files:
                    live_xapp_decisions.ingest_files(decision_files)
            except Exception:
                # Brak pliku jest normalny zanim xApp wykona pierwszą decyzję
                # albo gdy dany xApp nie oferuje zapisu CSV. Nie mieszamy tego
                # stanu z błędem metryk energii.
                pass
            xapp_decision_snapshot = live_xapp_decisions.snapshot()

        duration = self._configured_simulation_duration(simulation)
        live = collector.snapshot(duration, elapsed_real_s)
        current_metrics = dict(live.get("current_metrics") or {})
        series = {
            str(key): list(points or [])
            for key, points in dict(live.get("series") or {}).items()
        }
        series_sources = dict(live.get("series_sources") or {})
        if kpm_snapshot is not None:
            self._merge_kpm_live_data(
                current_metrics, series, series_sources, kpm_snapshot
            )
        data_status = dict(live.get("data_status") or {})
        kpm_status = dict(kpm_snapshot.get("data_status") or {}) if kpm_snapshot else {}
        if kpm_snapshot is not None:
            data_status["kpm"] = dict(kpm_status)
        if xapp_decision_snapshot is not None:
            data_status["xapp_decisions"] = dict(
                xapp_decision_snapshot.get("data_status") or {}
            )
        # The base collector only knows about its primary CSV or energy
        # fallback. Merge KPM's actual streams so the status strip does not
        # report useful live measurements as unavailable.
        known_series = {
            "energy_j", "power_w", "energy_efficiency_bit_per_j",
            "energy_per_mbit_j", "throughput_mbit_per_s", "delivered_mbit",
            "packet_loss_pct", "mean_prb_pct", "p05_sinr_db", "delay_ms",
            "mean_sinr_db", "active_ues",
        }
        available_series = {
            str(key) for key, points in series.items() if isinstance(points, list) and points
        }
        available_series.update(
            str(key) for key in (data_status.get("available_series") or [])
        )
        if kpm_snapshot is not None:
            kpm_available = set(kpm_status.get("available_metrics") or [])
            available_series.update(
                key for key in (
                    "throughput_mbit_per_s", "delivered_mbit", "mean_sinr_db",
                    "p05_sinr_db", "mean_prb_pct", "active_ues", "delay_ms",
                ) if key in kpm_available
            )
        data_status["available_series"] = sorted(available_series)
        data_status["unavailable_series"] = sorted(known_series - available_series)
        data_status["has_live_data"] = bool(available_series)
        data_status["series_sources"] = dict(series_sources)
        if kpm_snapshot is not None:
            data_status["aligned_sample_time_s"] = kpm_snapshot.get("sample_time_s")
        data_status["available_metrics"] = sorted(
            key for key, value in current_metrics.items() if value is not None
        )
        data_status["unavailable_metrics"] = [
            key for key in ("delay_ms", "packet_loss_pct")
            if current_metrics.get(key) is None
        ]
        source_names = []
        raw_source = str(data_status.get("data_source") or "")
        if raw_source:
            source_names.extend(part.strip() for part in raw_source.split("+") if part.strip())
        if kpm_snapshot is not None:
            source_names.append("KPM")
        data_status["data_source"] = " + ".join(dict.fromkeys(source_names)) or None

        accepted_rows = int(data_status.get("accepted_rows") or 0)
        has_live_data = bool(data_status.get("has_live_data"))
        primary_available = bool(data_status.get("primary_available"))
        live_contract = simulation.runtime.get("live_metrics_contract")
        live_contract = dict(live_contract) if isinstance(live_contract, dict) else {}
        primary_required = bool(live_contract.get("required"))
        fallback_streaming = (
            int(data_status.get("energy_file_accepted_rows") or 0) > 0
            or bool(kpm_status.get("has_live_data"))
        )
        if (
            not primary_required
            and fallback_streaming
            and collection_error
            and "nie został jeszcze utworzony" in collection_error.lower()
        ):
            # Orange's original scenario intentionally does not create the
            # optional full metrics CSV. Real energy/KPM fallback streams mean
            # this is an unavailable source, not a collection failure.
            collection_error = None
        if primary_required:
            data_status["primary_required"] = True
            accepted_files = live_contract.get("accepted_files")
            if isinstance(accepted_files, (list, tuple)):
                data_status["required_file"] = ", ".join(
                    str(item) for item in accepted_files if str(item).strip()
                )
            else:
                data_status["required_file"] = str(
                    live_contract.get("file") or self.LIVE_TIMESERIES_FILE
                )
            if primary_available:
                live_contract["state"] = "streaming"
            elif live_contract.get("state") in {
                "source_verified",
                "awaiting_source",
                "awaiting_samples",
            }:
                live_contract["state"] = "awaiting_samples"
            simulation.runtime["live_metrics_contract"] = live_contract
        if primary_available and (accepted or accepted_rows):
            collection_state = "OK"
        elif fallback_streaming and not primary_required:
            collection_state = "LIVE"
        elif has_live_data:
            collection_state = "PARTIAL"
        elif primary_required:
            collection_state = "CZEKA NA PEŁNE METRYKI"
        elif collection_error:
            collection_state = "WAITING"
        else:
            collection_state = "WAITING"
        process_data = self._process_snapshot(
            processes, simulation.configuration.xapp != "none"
        )
        xapp_state = (process_data.get("xapp") or {}).get("status")
        progress = {
            "simulated_time_s": live.get("sim_time_s"),
            "simulation_time_s": live.get("sim_duration_s"),
            "percent": live.get("progress_pct"),
            "real_elapsed_s": live.get("elapsed_real_s"),
            "estimated_remaining_s": live.get("eta_s"),
        }
        runtime_live = {
            "updated_at": now_utc(),
            "sim_time_s": live.get("sim_time_s"),
            "progress_pct": live.get("progress_pct"),
            "elapsed_real_s": live.get("elapsed_real_s"),
            "eta_s": live.get("eta_s"),
            "current_metrics": dict(current_metrics),
            "data_status": dict(data_status),
            "data_collection": collection_state,
        }
        if kpm_snapshot is not None:
            # Nie zapisujemy pełnych serii przy każdym snapshotcie runtime —
            # one i tak są archiwizowane jako artefakty, a GUI dostaje je w
            # bieżącym payloadzie LIVE_UPDATE.
            runtime_live["kpm_live"] = {
                "sample_time_s": kpm_snapshot.get("sample_time_s"),
                "current_metrics": dict(kpm_snapshot.get("current_metrics") or {}),
                "per_cell": dict(kpm_snapshot.get("per_cell") or {}),
                "metric_sources": dict(kpm_snapshot.get("metric_sources") or {}),
                "data_status": dict(kpm_snapshot.get("data_status") or {}),
            }
        if xapp_decision_snapshot is not None:
            # 200 rekordów to celowy limit kolektora; payload daje dashboardowi
            # ostatnie decyzje bez przechowywania całego logu w widgetach.
            runtime_live["xapp_decisions"] = xapp_decision_snapshot
        topology_snapshot: Optional[Dict[str, Any]] = None
        if live_topology is not None:
            candidate = live_topology.snapshot()
            if candidate.get("cells") or candidate.get("ues"):
                topology_snapshot = candidate
                runtime_live["topology_status"] = dict(candidate.get("data_status") or {})
        if collection_error:
            runtime_live["collection_error"] = collection_error
        simulation.runtime["live"] = runtime_live
        simulation.runtime["processes"] = process_data
        if container is not None:
            simulation.runtime["container_snapshot"] = dict(container)

        payload: Dict[str, Any] = {
            "simulation": {
                "id": simulation.id,
                "name": simulation.name,
                "status": simulation.status.value,
                "configuration": simulation.configuration.to_dict(),
            },
            "status": simulation.status.value,
            "stage": simulation.runtime.get("stage") or simulation.status.value,
            "progress": progress,
            "current_metrics": current_metrics,
            "series": series,
            "processes": process_data,
            "xapp_status": xapp_state,
            "data_collection": collection_state,
            "data_status": data_status,
            "series_sources": series_sources,
        }
        if live_contract:
            payload["live_metrics_contract"] = dict(live_contract)
        if topology_snapshot is not None:
            payload["topology"] = topology_snapshot
        if kpm_snapshot is not None:
            payload["kpm_live"] = kpm_snapshot
        if xapp_decision_snapshot is not None:
            payload["xapp_decisions"] = xapp_decision_snapshot
        if collection_error:
            payload["data_collection_error"] = collection_error
        if container is not None:
            payload["container"] = container
        self._notify(callback, simulation, "LIVE_UPDATE", "Aktualizacja danych live.", payload)

    async def _wait_for_end(
        self,
        processes: _SessionProcesses,
        xapp_plan: Optional[Dict[str, Any]],
        directory: Path,
        config: Mapping[str, Any],
        simulation: Simulation,
        callback: Optional[ProgressCallback],
        root: Optional[str] = None,
        live_metrics: Optional[LiveMetricsCollector] = None,
        live_topology: Optional[LiveTopologyCollector] = None,
        live_kpm: Optional[KpmLiveCollector] = None,
        live_xapp_decisions: Optional[XappDecisionCollector] = None,
        real_time_budget_target_active_s: Optional[float] = None,
    ) -> None:
        runtime = dict(config.get("runtime") or config.get("automation") or {})
        max_restarts = max(0, int(runtime.get("xapp_max_restarts", 0)))
        restart_delay = max(0.0, float(runtime.get("xapp_restart_delay_s", 1.0)))
        technical_timeout = max(0.0, float(runtime.get("wall_clock_timeout_s", runtime.get("wall_clock_limit_s", 0))))
        started = asyncio.get_running_loop().time()
        live_interval_s = max(0.5, float(runtime.get("live_poll_interval_s", 1.0)))
        container_interval_s = max(live_interval_s, float(runtime.get("container_poll_interval_s", 2.0)))
        next_live_poll = started
        next_container_poll = started
        next_runtime_save = started + 5.0
        latest_container: Optional[Dict[str, Any]] = None
        restarts = 0
        real_time_budget_cutoff_requested = False
        waits: Dict[asyncio.Task, str] = {
            asyncio.create_task(processes.get("ns3").wait()): "ns3",
            asyncio.create_task(processes.get("ric").wait()): "ric",
        }
        if xapp_plan is not None:
            waits[asyncio.create_task(processes.get("xapp").wait())] = "xapp"

        async def publish_live(elapsed_s: float) -> None:
            """Zachowuje zgodność z testowymi/starszymi callbackami metody."""

            if live_metrics is None or root is None:
                return
            arguments = (
                processes,
                root,
                live_metrics,
                simulation,
                callback,
                elapsed_s,
                latest_container,
            )
            if (
                live_topology is None
                and live_kpm is None
                and live_xapp_decisions is None
            ):
                await self._publish_live_snapshot(*arguments)
            else:
                await self._publish_live_snapshot(
                    *arguments,
                    live_topology=live_topology,
                    live_kpm=live_kpm,
                    live_xapp_decisions=live_xapp_decisions,
                )
        try:
            while waits:
                self._raise_if_stopped()
                now = asyncio.get_running_loop().time()
                if live_metrics is not None and root and now >= next_live_poll:
                    if now >= next_container_poll:
                        latest_container = await self._container_snapshot(processes)
                        next_container_poll = now + container_interval_s
                    await publish_live(max(0.0, now - started))
                    next_live_poll = now + live_interval_s
                if now >= next_runtime_save:
                    # Persist only the small current-state snapshot.  Full
                    # series remain in the artifact and are copied once after
                    # the run, avoiding repeated large writes during a run.
                    storage = getattr(self, "storage", None)
                    if storage is not None:
                        storage.save(simulation)
                    next_runtime_save = now + 5.0
                self._publish_process_logs(processes, simulation, callback)
                if technical_timeout and asyncio.get_running_loop().time() - started > technical_timeout:
                    raise TimeoutError("Przekroczono techniczny limit czasu rzeczywistego")
                elapsed_active_s = max(0.0, now - started)
                if (
                    real_time_budget_target_active_s is not None
                    and not real_time_budget_cutoff_requested
                    and elapsed_active_s >= real_time_budget_target_active_s
                ):
                    # Ostatni odczyt robimy przed wysłaniem SIGTERM. Wartość
                    # czasu modelowanego pozostaje obserwacją pliku ns-3, a
                    # nie jest wyprowadzana z zegara Pythona.
                    if live_metrics is not None and root:
                        await publish_live(elapsed_active_s)
                    # Twardy limit dotyczy zegara rzeczywistego po pełnym
                    # rozruchu środowiska.  Wysyłamy SIGTERM także wtedy,
                    # gdy plik live jeszcze nie zawiera wiersza; brak pomiaru
                    # zostanie później pokazany jawnie, ale nie może wydłużyć
                    # bieżącego przebiegu poza wybrany limit.
                    real_time_budget_cutoff_requested = True
                    self._emit(
                        simulation,
                        "TIME_BUDGET_CUTOFF",
                        "Osiągnięto rzeczywisty limit aktywnej pracy ns-3 {:.1f} s; wysyłanie żądania zatrzymania bieżącego przebiegu.".format(
                            real_time_budget_target_active_s
                        ),
                        callback,
                        {
                            "queue_timing": {
                                "state": "stopping_at_real_time_budget",
                                "per_run_target_active_real_s": real_time_budget_target_active_s,
                                "active_elapsed_real_s": elapsed_active_s,
                                "cutoff_policy": "hard_real_time_each_run",
                            }
                        },
                    )
                    await processes.get("ns3").stop(
                        max(0.1, float(runtime.get("stop_timeout_s", 5.0)))
                    )
                # Wake up no later than the hard cutoff instead of letting a
                # fixed polling interval add up to 250 ms of avoidable delay.
                wait_timeout = 0.25
                if real_time_budget_target_active_s is not None and not real_time_budget_cutoff_requested:
                    until_cutoff = max(0.0, real_time_budget_target_active_s - elapsed_active_s)
                    wait_timeout = min(wait_timeout, until_cutoff)
                done, _ = await asyncio.wait(
                    waits, timeout=wait_timeout, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    continue
                priority = {"ns3": 0, "ric": 1, "xapp": 2}
                for task in sorted(done, key=lambda item: priority[waits[item]]):
                    name = waits.pop(task)
                    code = task.result()
                    if name == "ns3":
                        elapsed_active_s = max(0.0, asyncio.get_running_loop().time() - started)
                        # ns-3 mógł dopisać ostatnią próbkę tuż przed swoim
                        # zakończeniem, między zwykłymi odpytywaniami. Ten
                        # końcowy odczyt jest potrzebny także przy limicie
                        # aby dashboard zdążył zobaczyć endpoint serii,
                        # pełną energię, EE, koszt i dostarczone dane przed
                        # przejściem do widoku wyniku.
                        if live_metrics is not None and root:
                            await publish_live(elapsed_active_s)
                        if real_time_budget_target_active_s is not None:
                            # Po zamknięciu procesu plik nie rośnie, więc
                            # ponowny odczyt daje ostatni kompletny punkt bez
                            # ryzyka odczytu niedopisanego wiersza.
                            observed = self._record_real_time_budget_observation(
                                simulation,
                                real_time_budget_target_active_s,
                                elapsed_active_s,
                                live_metrics,
                                "active_budget_reached"
                                if real_time_budget_cutoff_requested
                                else "ns3_completed_before_budget",
                                real_time_budget_cutoff_requested,
                            )
                            if observed is None:
                                self._record_runtime_warning(
                                simulation,
                                "Nie uzyskano dodatniego czasu modelowanego z pełnych metryk live dla tego przebiegu."
                                )
                            elif not real_time_budget_cutoff_requested:
                                self._record_runtime_warning(
                                simulation,
                                "Przebieg zakończył się przed aktywnym limitem czasu rzeczywistego; czas ns-3 zapisano wyłącznie jako obserwację."
                                )
                            else:
                                self._record_runtime_warning(
                                    simulation,
                                    "Przebieg zakończono po limicie czasu rzeczywistego; wynik jest częściowy i nie potwierdza pełnego eksperymentu."
                                )
                        simulation.runtime["termination"] = {
                            "reason": "real_time_budget" if real_time_budget_cutoff_requested else "natural",
                            "partial": real_time_budget_cutoff_requested,
                            "exit_code": code,
                        }
                        if real_time_budget_cutoff_requested:
                            simulation.runtime["experiment_valid"] = False
                            # Python uses negative signal codes; bash adds
                            # 128; setsid --wait can return the raw wait status
                            # (15/9). Accept these only after our own cutoff.
                            # A concurrent abort (e.g. 134) remains a failure.
                            if code in {0, -15, 15, 143, -9, 9, 137}:
                                return
                        if code != 0:
                            self._emit(
                                simulation,
                                "COMPONENT_FAILED",
                                "ns-3 zakończył się kodem {}.".format(code),
                                callback,
                                {"component": "ns3", "exit_code": code},
                            )
                            raise RuntimeError("ns-3 zakończył się kodem {}".format(code))
                        return
                    if name == "ric":
                        self._emit(
                            simulation,
                            "COMPONENT_FAILED",
                            "nearRT-RIC zakończył się nieoczekiwanie (kod {}).".format(code),
                            callback,
                            {"component": "ric", "exit_code": code},
                        )
                        raise RuntimeError("nearRT-RIC zakończył się nieoczekiwanie (kod {})".format(code))
                    if name == "xapp":
                        # Restarting loses controller state: retain the data,
                        # but do not treat it as an intact intervention arm.
                        simulation.runtime["experiment_valid"] = False
                        crash_at = now_utc()
                        unavailable_started = asyncio.get_running_loop().time()
                        self._record_xapp_event(
                            simulation, "crash", crash_at, code, restart=restarts
                        )
                        self._emit(
                            simulation,
                            "XAPP_CRASHED",
                            "xApp zakończył się kodem {}.".format(code),
                            callback,
                            {
                                "component": "xapp",
                                "exit_code": code,
                                "restart": restarts,
                                "xapp_event": "crash",
                            },
                        )
                        if xapp_plan is None:
                            continue
                        if restarts >= max_restarts:
                            message = (
                                "xApp zakończył się kodem {} o {}. "
                                "Symulacja trwa dalej bez xAppa."
                            ).format(code, crash_at)
                            self._record_runtime_warning(simulation, message)
                            self._emit(
                                simulation,
                                "XAPP_FAILED",
                                message,
                                callback,
                                {
                                    "component": "xapp",
                                    "exit_code": code,
                                    "restart": restarts,
                                    "xapp_event": "restart_exhausted",
                                },
                            )
                            continue
                        restarts += 1
                        simulation.runtime["xapp_restarts"] = restarts
                        self._emit(
                            simulation,
                            "XAPP_RESTARTING",
                            "xApp zakończył się kodem {} o {}. Restart {}.".format(
                                code, crash_at, restarts
                            ),
                            callback,
                            {
                                "component": "xapp",
                                "exit_code": code,
                                "restart": restarts,
                                "xapp_event": "restarting",
                            },
                        )
                        if restart_delay:
                            await asyncio.sleep(restart_delay)
                        self._raise_if_stopped()
                        if not processes.get("ns3").alive:
                            # ns-3 may finish normally during the restart
                            # delay. Its pending wait task owns exit-code
                            # validation and final metrics collection.
                            continue
                        restart_environment = dict(xapp_plan["environment"])
                        restart_environment["EE_DECISION_LOG"] = posixpath.join(
                            str(xapp_plan["decision_log_root"]),
                            "xapp_ee_decisions-{}.csv".format(restarts + 1),
                        )
                        await processes.get("xapp").start(
                            list(xapp_plan["command"]),
                            xapp_plan["cwd"],
                            directory / "logs" / "xapp-restart-{}.log".format(restarts),
                            restart_environment,
                        )
                        downtime_s = asyncio.get_running_loop().time() - unavailable_started
                        self._record_xapp_event(
                            simulation,
                            "restart",
                            now_utc(),
                            None,
                            restart=restarts,
                            downtime_s=downtime_s,
                        )
                        self._emit(
                            simulation,
                            "XAPP_RESTARTED",
                            "xApp uruchomiono ponownie po {:.2f} s niedostępności.".format(
                                downtime_s
                            ),
                            callback,
                            {
                                "component": "xapp",
                                "restart": restarts,
                                "downtime_s": downtime_s,
                                "xapp_event": "restart",
                            },
                        )
                        waits[asyncio.create_task(processes.get("xapp").wait())] = "xapp"
        finally:
            for task in waits:
                task.cancel()
            if waits:
                await asyncio.gather(*waits, return_exceptions=True)

    async def _stop_processes(self, processes: _SessionProcesses) -> None:
        timeout_s = float((self.config.as_dict().get("runtime") or {}).get("stop_timeout_s", 5.0))
        stopped = await asyncio.gather(
            *(processes.get(name).stop(timeout_s) for name in ("xapp", "ns3", "ric")),
            return_exceptions=True,
        )
        errors = [str(item) for item in stopped if isinstance(item, Exception)]
        if errors:
            raise RuntimeError("; ".join(errors))

    @staticmethod
    def _record_xapp_event(
        simulation: Simulation,
        event: str,
        at: str,
        exit_code: Optional[int],
        **details: Any,
    ) -> None:
        item: Dict[str, Any] = {"event": event, "at": at, "exit_code": exit_code}
        item.update(details)
        simulation.runtime.setdefault("xapp_events", []).append(item)

    def _publish_process_logs(
        self,
        processes: _SessionProcesses,
        simulation: Simulation,
        callback: Optional[ProgressCallback],
    ) -> None:
        for name in ("ric", "ns3", "xapp"):
            process = processes.get(name)
            after = self._live_sequences.get(name, 0)
            records = process.logs_after(after, 50)
            self._live_sequences[name] = int(records.get("last_seq") or after)
            for item in records.get("lines") or []:
                line = str(item.get("text") or "").strip()
                if line:
                    self._emit(
                        simulation,
                        "LOG",
                        "[{}] {}".format(name, line),
                        callback,
                        {
                            "log_source": name,
                            "log": {
                                "seq": item.get("seq"),
                                "generation": item.get("generation", records.get("generation")),
                                "text": line,
                            },
                        },
                    )

    def _archive_outputs(self, processes: _SessionProcesses, root: str, directory: Path) -> None:
        groups = json.dumps(self.OUTPUT_GROUPS)
        script = (
            "python3 -c "
            + shlex.quote(
                "import glob,json,os,sys; root=sys.argv[1]; groups=json.loads(sys.argv[2]); result=[]; "
                "[result.append({'group': group, 'path': path}) for group, patterns in groups.items() "
                "for pattern in patterns for path in glob.glob(os.path.join(root, pattern)) if os.path.isfile(path)]; "
                "print(json.dumps(result))"
            )
            + " " + shlex.quote(root) + " " + shlex.quote(groups)
        )
        code, output = processes.exec_text(script)
        if code != 0:
            raise RuntimeError("Nie można odczytać listy wyników z kontenera: {}".format(output))
        try:
            files = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kontener zwrócił niepoprawną listę wyników") from exc
        for item in files:
            if not isinstance(item, dict):
                continue
            group, source = item.get("group"), item.get("path")
            if group not in self.OUTPUT_GROUPS or not isinstance(source, str):
                continue
            target_directory = directory if group == "summary" else directory / group
            target_directory.mkdir(parents=True, exist_ok=True)
            target = target_directory / posixpath.basename(source)
            stream, _ = processes.get_archive(source)
            payload = b"".join(stream)
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
                member = next((entry for entry in archive.getmembers() if entry.isfile()), None)
                if member is None:
                    continue
                extracted = archive.extractfile(member)
                if extracted is not None:
                    target.write_bytes(extracted.read())
