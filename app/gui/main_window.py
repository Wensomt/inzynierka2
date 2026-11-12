from __future__ import annotations

import json
import os
import copy
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List

from app.config import APP_DATA_DIR, APP_TITLE, ConfigStore
from app.data.results_parser import ResultsParser
from app.data.storage import SimulationStorage
from app.infrastructure.file_browser import open_in_file_manager
from app.services.scenario_service import SavedScenario, ScenarioService
from app.services.xapp_service import XAppService
from app.simulation.runner import SimulationRunner
from app.simulation.models import Simulation
from app.simulation.session_controller import QueueEvent, SessionController

from .comparison_view import ComparisonView
from .configuration_library import ConfigurationLibraryDialog
from .docker_view import DockerView
from .live_dashboard import LiveDashboard, format_estimated_completion
from .results_view import ResultsView
from .run_data_dialog import RunDataDialog
from .run_trash_dialog import RunTrashDialog
from .scenarios_view import ScenariosView
from .simulation_tab import SimulationTab
from .topology_view import parse_topology_artifacts
from .xapps_view import XAppsView
from .theme import COLORS, configure_theme
from .my_energy_view import MyEnergyView


class MainWindow:
    """Główne okno prostego laboratorium desktopowego."""

    CLOSE_GRACE_S = 30.0
    MAX_LIVE_LOG_LINES = 1200
    MAX_EVENTS_PER_TICK = 150

    def __init__(self, root: tk.Tk, config: ConfigStore) -> None:
        self.root = root
        self.config_store = config
        self.application_config = config.as_dict()
        self.storage = SimulationStorage(APP_DATA_DIR / "simulations")
        self.scenario_service = ScenarioService(APP_DATA_DIR / "scenarios", self.application_config)
        self.xapp_service = XAppService(APP_DATA_DIR / "xapps", config)
        self.runner = SimulationRunner(config, self.storage, ResultsParser())
        self.controller = SessionController(self.application_config, self.storage, self.runner)
        self.simulation_tabs: Dict[str, SimulationTab] = {}
        self._visible_live_log_lines = 0
        self._closing = False
        self._history_cache: List[Any] = []
        self._history_loading = False
        self._history_refresh_pending = False
        self._close_requested_at = 0.0
        self._build_window()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_events)
        if os.environ.get("ORAN_LAB_Q3_ISOLATED") != "1":
            threading.Thread(target=self._recover_orphans, name="oran-lab-recovery", daemon=True).start()

    def _build_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.minsize(1040, 700)
        self.root.geometry("1260x840")
        self._configure_style()

        shell = ttk.Frame(self.root, padding=(12, 10, 12, 12))
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Symulator sieci O-RAN", style="Title.TLabel").pack(side="left")
        self.state_var = tk.StringVar(value="Laboratorium gotowe")
        ttk.Label(header, textvariable=self.state_var, style="Status.TLabel").pack(side="right")

        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        content = ttk.Frame(body)
        content.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(content)
        self.notebook.pack(fill="both", expand=True)
        self.dashboard_frame = LiveDashboard(self.notebook)
        self.home_frame = ttk.Frame(self.notebook, padding=24)
        self.session_frame = ttk.Frame(self.notebook, padding=12)
        self.scenarios_frame = ScenariosView(
            self.notebook,
            self.scenario_service,
            self.application_config,
            self._apply_saved_scenario,
        )
        self.xapps_frame = XAppsView(
            self.notebook,
            self.xapp_service,
            self._reload_application_configuration,
            lambda: not self.controller.running,
        )
        self.comparison_frame = ComparisonView(
            self.notebook,
            self._comparison_candidates,
            on_export=self._export_comparison_csv,
        )
        self.my_energy_frame = MyEnergyView(
            self.notebook, self.config_store, self._add_my_energy_pair,
            lambda: not self.controller.running,
        )
        self.results_frame = ResultsView(
            self.notebook,
            self._show_details,
            self._show_log,
            on_export_csv=self._export_csv,
            on_export_json=self._export_json,
            on_delete=self._delete_saved_run,
            on_export_bundle=self._export_bundle,
            on_data=self._show_run_data,
            on_import_bundle=self._import_result_bundle,
            on_deleted_runs=self._show_deleted_runs,
            on_charts=self._show_saved_charts,
            on_open_folder=self._open_run_folder,
        )
        self.docker_frame = DockerView(self.notebook)
        self.settings_frame = ttk.Frame(self.notebook, padding=18)
        self.notebook.add(self.home_frame, text="Start")
        self.notebook.add(self.session_frame, text="Symulacje")
        self.notebook.add(self.dashboard_frame, text="Na żywo")
        self.notebook.add(self.results_frame, text="Wyniki")
        self.notebook.add(self.comparison_frame, text="Porównanie")
        self.notebook.add(self.my_energy_frame, text="My Energy")
        self.notebook.add(self.scenarios_frame, text="Scenariusze")
        self.notebook.add(self.xapps_frame, text="xApps")
        self.notebook.add(self.docker_frame, text="Docker i logi")
        self.notebook.add(self.settings_frame, text="Ustawienia")
        self._build_home()
        self._build_session()
        self._build_settings()
        self._refresh_views()

    def _configure_style(self) -> None:
        self._colors = COLORS
        self._fixed_font = configure_theme(self.root)

    def _build_home(self) -> None:
        self.home_frame.columnconfigure(0, weight=1)
        content = ttk.Frame(self.home_frame, padding=16)
        content.grid(row=0, column=0, sticky="nw")
        ttk.Label(content, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            content,
            text="Skonfiguruj kilka symulacji, uruchom je kolejno i porównaj zapisane wyniki.",
        ).grid(row=1, column=0, sticky="w", pady=(8, 18))
        count_box = ttk.Frame(content)
        count_box.grid(row=2, column=0, sticky="w", pady=(0, 12))
        ttk.Label(count_box, text="Liczba symulacji:").pack(side="left")
        self.count_var = tk.StringVar(value="2")
        ttk.Spinbox(count_box, from_=1, to=20, width=5, textvariable=self.count_var).pack(side="left", padx=(8, 0))
        ttk.Button(content, text="Nowa sesja", style="Accent.TButton", command=self._new_session).grid(row=3, column=0, sticky="w", pady=4)
        ttk.Button(content, text="Wczytaj konfigurację", command=self._load_configuration).grid(row=4, column=0, sticky="w", pady=4)
        ttk.Button(content, text="Zapisane wyniki", command=lambda: self.notebook.select(self.results_frame)).grid(row=5, column=0, sticky="w", pady=4)
        ttk.Label(content, text="1. Utwórz sesję i ustaw parametry w zakładce Symulacje.\n2. Uruchom kolejkę i obserwuj Na żywo.\n3. Otwórz Wyniki i wybierz dwa przebiegi w Porównaniu.\n\nNowe symulacje mają domyślnie 5 s czasu ns-3.\nNa tym modelu kilka sekund symulacji może oznaczać wiele minut pracy komputera.", justify="left", style="Muted.TLabel").grid(row=6, column=0, sticky="w", pady=(20, 0))

    def _build_session(self) -> None:
        self.session_frame.columnconfigure(0, weight=1)
        self.session_frame.rowconfigure(1, weight=1)
        controls = ttk.Frame(self.session_frame)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(0, weight=1)
        setup_controls = ttk.Frame(controls)
        setup_controls.grid(row=0, column=0, sticky="w")
        ttk.Label(setup_controls, text="Liczba symulacji:").pack(side="left")
        ttk.Spinbox(setup_controls, from_=1, to=20, width=5, textvariable=self.count_var).pack(side="left", padx=(8, 10))
        self.new_session_button = ttk.Button(setup_controls, text="Utwórz zakładki", command=self._new_session)
        self.new_session_button.pack(side="left")
        file_controls = ttk.Frame(controls)
        file_controls.grid(row=0, column=1, sticky="e")
        ttk.Button(file_controls, text="Zapisz konfigurację", command=self._save_configuration).pack(side="right")
        ttk.Button(file_controls, text="Wczytaj konfigurację", command=self._load_configuration).pack(side="right", padx=(0, 7))

        timing_controls = ttk.Frame(controls)
        timing_controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Label(timing_controls, text="Łączny budżet czasu rzeczywistego ns-3:").pack(side="left")
        self.total_budget_minutes_var = tk.StringVar(value=self._initial_budget_minutes())
        ttk.Spinbox(
            timing_controls,
            from_=0.1,
            to=24 * 60,
            increment=1,
            width=7,
            textvariable=self.total_budget_minutes_var,
        ).pack(side="left", padx=(8, 4))
        ttk.Label(timing_controls, text="min").pack(side="left")
        ttk.Button(timing_controls, text="Ustaw budżet", command=self._apply_time_budget).pack(side="left", padx=(8, 12))
        self.time_budget_status_var = tk.StringVar(value="")
        ttk.Label(timing_controls, textvariable=self.time_budget_status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        self._update_time_budget_status(self.controller.queue_time_budget())

        self.session_panes = ttk.Panedwindow(self.session_frame, orient="vertical")
        self.session_panes.grid(row=1, column=0, sticky="nsew", pady=(0, 0))
        self.simulation_notebook = ttk.Notebook(self.session_panes)
        runner_box = ttk.LabelFrame(self.session_panes, text="URUCHAMIANIE I KOLEJKA", padding=10)
        self.session_panes.add(self.simulation_notebook)
        self.session_panes.add(runner_box)
        self._session_sash_positioned = False
        self.session_panes.bind("<Configure>", self._position_session_sash, add=True)
        runner_box.columnconfigure(1, weight=1)
        runner_box.rowconfigure(2, weight=1)
        self.run_button = ttk.Button(runner_box, text="URUCHOM WSZYSTKIE", style="Accent.TButton", command=self._start_all)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(runner_box, text="STOP", command=self._request_stop, state="disabled")
        self.stop_button.grid(row=0, column=2, sticky="e")
        self.progress = ttk.Progressbar(runner_box, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=0, column=1, sticky="ew", padx=12)
        self.progress_var = tk.StringVar(value="Brak uruchomionej kolejki")
        ttk.Label(runner_box, textvariable=self.progress_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(7, 5))

        # W małym oknie log i kolejka nie mogą konkurować o tę samą, stałą
        # wysokość. Zakładki zachowują oba widoki pod ręką, bez obcinania
        # ostatnich wierszy kolejki przez Panedwindow.
        runner_notebook = ttk.Notebook(runner_box)
        runner_notebook.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(2, 0))
        queue_page = ttk.Frame(runner_notebook, padding=4)
        log_page = ttk.Frame(runner_notebook, padding=4)
        runner_notebook.add(queue_page, text="KOLEJKA")
        runner_notebook.add(log_page, text="LOG SESJI")

        log_page.columnconfigure(0, weight=1)
        log_page.rowconfigure(0, weight=1)
        self.live_log = tk.Text(
            log_page,
            height=5,
            wrap="word",
            state="disabled",
            font=self._fixed_font,
            background=self._colors["field"],
            foreground=self._colors["fg"],
            insertbackground=self._colors["fg"],
            relief="flat",
        )
        self.live_log.grid(row=0, column=0, sticky="nsew")
        live_log_y = ttk.Scrollbar(log_page, orient="vertical", command=self.live_log.yview)
        live_log_x = ttk.Scrollbar(log_page, orient="horizontal", command=self.live_log.xview)
        self.live_log.configure(yscrollcommand=live_log_y.set, xscrollcommand=live_log_x.set)
        live_log_y.grid(row=0, column=1, sticky="ns")
        live_log_x.grid(row=1, column=0, sticky="ew")

        queue_page.columnconfigure(0, weight=1)
        queue_page.rowconfigure(0, weight=1)
        self.queue_tree = ttk.Treeview(
            queue_page,
            columns=("index", "name", "status", "progress"),
            show="headings",
            height=4,
        )
        for key, text, width in (
            ("index", "#", 42),
            ("name", "Symulacja", 240),
            ("status", "Stan", 112),
            ("progress", "Postęp", 78),
        ):
            self.queue_tree.heading(key, text=text)
            self.queue_tree.column(key, width=width, anchor="w", stretch=key == "name")
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        queue_y = ttk.Scrollbar(queue_page, orient="vertical", command=self.queue_tree.yview)
        queue_x = ttk.Scrollbar(queue_page, orient="horizontal", command=self.queue_tree.xview)
        self.queue_tree.configure(yscrollcommand=queue_y.set, xscrollcommand=queue_x.set)
        queue_y.grid(row=0, column=1, sticky="ns")
        queue_x.grid(row=1, column=0, sticky="ew")
        self.queue_tree.bind("<<TreeviewSelect>>", self._select_queue_item)

    def _position_session_sash(self, _event: Any = None) -> None:
        """Pierwszy układ faworyzuje konfigurację; użytkownik może go przeciągnąć."""

        if self._session_sash_positioned:
            return
        self._session_sash_positioned = True

        def position() -> None:
            try:
                height = self.session_panes.winfo_height()
                if height > 100:
                    # Formularz konfiguracji jest przewijany, a kolejka i log
                    # wymagają kilku faktycznie widocznych wierszy nawet przy
                    # minimalnym oknie.
                    self.session_panes.sashpos(0, max(190, int(height * 0.53)))
            except tk.TclError:
                pass

        self.root.after_idle(position)

    def _initial_budget_minutes(self) -> str:
        """Czytelna wartość wejściowa dla opcjonalnego budżetu kolejki."""

        snapshot = self.controller.queue_time_budget()
        try:
            seconds = float(snapshot.get("total_real_time_s"))
        except (TypeError, ValueError):
            return ""
        return "{:g}".format(seconds / 60.0) if seconds > 0.0 else ""

    def _apply_time_budget(self) -> bool:
        """Przekazuje wybrany przez użytkownika budżet minut do kontrolera."""

        raw = self.total_budget_minutes_var.get().strip().replace(",", ".")
        try:
            seconds = None if not raw else float(raw) * 60.0
            snapshot = self.controller.set_total_real_time_budget(seconds)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Niepoprawny budżet czasu", str(exc), parent=self.root)
            return False
        self._update_time_budget_status(snapshot)
        return True

    def _update_time_budget_status(self, snapshot: Any) -> None:
        """Pokazuje obliczenia kontrolera, bez udawania pełnego ETA aplikacji."""

        if not hasattr(self, "time_budget_status_var") or not isinstance(snapshot, dict):
            return
        if not snapshot.get("enabled"):
            self.time_budget_status_var.set(
                "Puste pole: pełny przebieg do simTime. Limit rzeczywisty daje wyniki częściowe."
            )
            return
        try:
            total = float(snapshot.get("total_real_time_s"))
        except (TypeError, ValueError):
            self.time_budget_status_var.set("Budżet czasu jest ustawiony.")
            return
        try:
            per_run = float(snapshot.get("per_run_target_active_real_s"))
        except (TypeError, ValueError):
            try:
                count = max(1, len(self.controller.simulations) or int(self.count_var.get()))
            except (TypeError, ValueError):
                count = 1
            per_run = total / float(count)
        remaining = snapshot.get("estimated_queue_remaining_s")
        if remaining is None:
            remaining = snapshot.get("planned_remaining_active_real_s")
        try:
            remaining_text = " | pozostało aktywnie: ~{}".format(self._duration_text(float(remaining)))
        except (TypeError, ValueError):
            remaining_text = ""
        completion = format_estimated_completion(snapshot)
        completion_text = " | koniec kolejki: {}".format(completion or "brak danych")
        self.time_budget_status_var.set(
            "Limit rzeczywisty: {} łącznie (każdy run: ok. {}); simTime jest tylko odczytem ns-3{}".format(
                self._duration_text(total), self._duration_text(per_run), remaining_text
            )
            + completion_text
        )

    @staticmethod
    def _duration_text(seconds: float) -> str:
        value = max(0, int(round(seconds)))
        hours, remainder = divmod(value, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        if hours:
            return "{} h {:02d} min".format(hours, minutes)
        if minutes:
            return "{} min {:02d} s".format(minutes, seconds_part)
        return "{} s".format(seconds_part)

    def _build_settings(self) -> None:
        self.settings_frame.columnconfigure(0, weight=1)
        ttk.Label(self.settings_frame, text="ŚRODOWISKO LABORATORIUM", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.settings_frame,
            text="Aplikacja sprawdza Docker i obraz symulatora przed uruchomieniem kolejki.",
        ).grid(row=1, column=0, sticky="w", pady=(8, 12))
        self.settings_status = tk.StringVar(value="Nie sprawdzono.")
        ttk.Label(self.settings_frame, textvariable=self.settings_status, justify="left").grid(row=2, column=0, sticky="w")
        ttk.Button(self.settings_frame, text="Sprawdź środowisko", command=self._check_environment).grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Button(
            self.settings_frame,
            text="Odśwież konfigurację dostarczoną z aplikacją",
            command=self._restore_bundled_configuration,
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        image_name = str((self.application_config.get("lab") or {}).get("simulator_image") or "wcnc_tap")
        runtime = dict(self.application_config.get("runtime") or {})
        ttk.Label(
            self.settings_frame,
            text=(
                "Obraz symulatora: {}\n"
                "Dane aplikacji: {}\n"
                "Plik konfiguracji: {}\n"
                "Odczyt live: co {} s; stan kontenera: co {} s.\n"
                "Kontenery są oznaczane identyfikatorem sesji i sprzątane tylko w jej zakresie.\n"
                "Odświeżenie konfiguracji zachowuje kopię poprzedniego pliku."
            ).format(
                image_name,
                APP_DATA_DIR,
                self.config_store.path,
                runtime.get("live_poll_interval_s", 1),
                runtime.get("container_poll_interval_s", 2),
            ),
            style="Muted.TLabel",
        ).grid(row=5, column=0, sticky="w", pady=(16, 0))

    def _new_session(self) -> None:
        try:
            count = int(self.count_var.get())
        except ValueError:
            messagebox.showerror("Niepoprawna liczba", "Podaj liczbę symulacji od 1 do 20.")
            return
        if not 1 <= count <= 20:
            messagebox.showerror("Niepoprawna liczba", "Podaj liczbę symulacji od 1 do 20.")
            return
        if self.controller.running:
            messagebox.showwarning("Kolejka działa", "Najpierw zatrzymaj bieżącą kolejkę.")
            return
        if self.controller.simulations and not messagebox.askyesno(
            "Nowa sesja", "Bieżące zakładki zostaną zastąpione. Zapisane wyniki pozostaną w historii. Kontynuować?"
        ):
            return
        try:
            self.controller.new_session(count)
        except Exception as exc:
            messagebox.showerror("Nie można utworzyć sesji", str(exc))
            return
        self._build_simulation_tabs()
        self._update_time_budget_status(self.controller.queue_time_budget())
        self.dashboard_frame.clear()
        self.docker_frame.clear()
        self._clear_session_live_log()
        self.notebook.select(self.session_frame)
        self._refresh_views()

    def _build_simulation_tabs(self) -> None:
        for tab in self.simulation_notebook.winfo_children():
            tab.destroy()
        self.simulation_tabs = {}
        for index, simulation in enumerate(self.controller.simulations, start=1):
            tab = SimulationTab(
                self.simulation_notebook,
                simulation,
                self.application_config,
                self._duplicate,
            )
            self.simulation_tabs[simulation.id] = tab
            self.simulation_notebook.add(tab, text="Symulacja {}".format(index))

    def _duplicate(self, simulation_id: str) -> None:
        try:
            self._commit_tabs()
            item = self.controller.duplicate(simulation_id)
        except Exception as exc:
            messagebox.showerror("Nie można utworzyć kopii", str(exc))
            return
        self._build_simulation_tabs()
        self._update_time_budget_status(self.controller.queue_time_budget())
        self.simulation_notebook.select(self.simulation_tabs[item.id])

    def _apply_saved_scenario(self, saved: SavedScenario) -> None:
        if self.controller.running:
            messagebox.showwarning("Kolejka działa", "Nie można zmieniać konfiguracji podczas działania kolejki.", parent=self.root)
            return
        selected = self.simulation_notebook.select()
        target = next((tab for tab in self.simulation_tabs.values() if str(tab) == str(selected)), None)
        if target is None:
            messagebox.showwarning("Brak symulacji", "Najpierw utwórz zakładkę symulacji.", parent=self.root)
            return
        target.apply_configuration(saved.configuration)
        self.storage.save(target.simulation)
        self.notebook.select(self.session_frame)

    def _add_my_energy_pair(self, configuration) -> None:
        if self.controller.running:
            raise ValueError("Najpierw zakończ bieżącą kolejkę.")
        if len(self.controller.simulations) > 18:
            raise ValueError("Para wymaga dwóch wolnych miejsc w sesji (limit 20).")
        self._commit_tabs()
        items = list(self.controller.simulations)
        for xapp, name in (("none", "Odniesienie — bez xApp"), ("optimized", "My Energy")):
            cfg = copy.deepcopy(configuration)
            cfg.xapp = xapp
            if xapp == "none":
                cfg.xapp_environment = {}
            items.append(Simulation(name=name, configuration=cfg))
        self.controller.replace_session(items)
        self.count_var.set(str(len(items)))
        self._build_simulation_tabs()
        self.simulation_notebook.select(self.simulation_tabs[items[-2].id])
        self.notebook.select(self.session_frame)

    def _reload_application_configuration(self) -> None:
        """Synchronizuje widoki po buildzie, który dodał lokalny obraz/xApp."""

        if self.controller.running:
            return
        self.application_config = self.config_store.as_dict()
        self.controller.application_config = self.application_config
        self.scenario_service.application_config = dict(self.application_config)
        self.scenarios_frame.reload_application_config(self.application_config)
        self._build_simulation_tabs()
        self._refresh_views()

    def _restore_bundled_configuration(self) -> None:
        """Przywraca aktualny config dołączony do EXE z kopią zapasową."""

        if self.controller.running:
            messagebox.showwarning(
                "Kolejka działa",
                "Nie można odświeżyć konfiguracji podczas działania kolejki.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Odświeżyć konfigurację?",
            "Bieżący plik konfiguracji zostanie zastąpiony wersją dostarczoną "
            "z aplikacją. Przed zmianą zostanie utworzona kopia zapasowa. "
            "Bieżące zakładki symulacji zostaną odświeżone. Kontynuować?",
            parent=self.root,
        ):
            return
        try:
            # Nie gubimy wartości wpisanych w aktualne zakładki tylko dlatego,
            # że użytkownik chce odświeżyć opis backendu i xAppów.
            self._commit_tabs()
            backup = self.config_store.restore_bundled_default()
            self._reload_application_configuration()
        except Exception as exc:
            messagebox.showerror(
                "Nie można odświeżyć konfiguracji", str(exc), parent=self.root
            )
            return
        detail = "Konfiguracja została odświeżona."
        if backup is not None:
            detail += " Kopia poprzedniej wersji: {}".format(backup)
        self.settings_status.set(detail)
        messagebox.showinfo("Konfiguracja odświeżona", detail, parent=self.root)

    def _commit_tabs(self) -> None:
        for tab in self.simulation_tabs.values():
            simulation = tab.commit()
            self.storage.save(simulation)

    def _start_all(self) -> None:
        if not self.controller.simulations:
            messagebox.showwarning("Brak symulacji", "Najpierw utwórz sesję.")
            return
        try:
            if not self._apply_time_budget():
                return
            self._commit_tabs()
            self.controller.start_all()
        except Exception as exc:
            messagebox.showerror("Nie można uruchomić", str(exc))
            return
        # Nowa kolejka nie może przez pierwszy interwał pokazywać terminalnego
        # snapshotu albo wykresów poprzedniego uruchomienia tej samej sesji.
        self.dashboard_frame.clear()
        self.docker_frame.clear()
        self._clear_session_live_log()
        self._build_simulation_tabs()
        self._update_time_budget_status(self.controller.queue_time_budget())
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.state_var.set("Kolejka symulacji działa")

    def _request_stop(self) -> None:
        self.controller.request_stop()
        self.stop_button.configure(state="disabled")
        self.state_var.set("Zatrzymywanie bieżącej symulacji")

    def _save_configuration(self) -> None:
        self._open_configuration_library()

    def _load_configuration(self) -> None:
        self._open_configuration_library()

    def _open_configuration_library(self) -> None:
        if self.controller.running:
            messagebox.showwarning(
                "Kolejka działa",
                "Zapisami konfiguracji można zarządzać po zatrzymaniu kolejki.",
                parent=self.root,
            )
            return
        ConfigurationLibraryDialog(
            self.root,
            self.storage,
            self._configuration_snapshot,
            self._apply_loaded_configuration,
        )

    def _configuration_snapshot(self) -> List[Any]:
        if not self.controller.simulations:
            raise ValueError("Najpierw utwórz co najmniej jedną kartę symulacji.")
        self._commit_tabs()
        return list(self.controller.simulations)

    def _apply_loaded_configuration(self, simulations: List[Any]) -> None:
        try:
            self.controller.replace_session(simulations)
        except Exception as exc:
            raise RuntimeError("Nie można wczytać: {}".format(exc))
        self.count_var.set(str(len(simulations)))
        self._build_simulation_tabs()
        self._update_time_budget_status(self.controller.queue_time_budget())
        self.dashboard_frame.clear()
        self.docker_frame.clear()
        self._clear_session_live_log()
        self._refresh_views()
        self.notebook.select(self.session_frame)

    def _check_environment(self) -> None:
        self.settings_status.set("Sprawdzanie Docker…")

        def work() -> None:
            try:
                result = self.runner.preflight()
                text = "\n".join(("✓ " + line) for line in result.get("checks") or [])
                errors = "\n".join(("✗ " + line) for line in result.get("errors") or [])
                if errors:
                    text = (text + "\n" if text else "") + errors
            except Exception as exc:
                text = "✗ Nie można sprawdzić środowiska Docker: {}".format(exc)
            self.controller.events.put(
                QueueEvent(None, "ENVIRONMENT", text or "Brak informacji.")
            )

        threading.Thread(target=work, name="oran-lab-preflight", daemon=True).start()

    def _recover_orphans(self) -> None:
        try:
            removed = self.runner.sessions.cleanup_orphans()
            if removed:
                self.controller.events.put(QueueEvent(None, "CLEANUP", "Usunięto osierocone kontenery: {}".format(", ".join(removed))))
        except Exception as exc:
            self.controller.events.put(
                QueueEvent(None, "CLEANUP_FAILED", "Nie można sprawdzić osieroconych kontenerów: {}".format(exc))
            )

    def _poll_events(self) -> None:
        events = self.controller.drain_events(self.MAX_EVENTS_PER_TICK)
        for event in events:
            self._handle_event(event)
        processed = len(events)
        if not self._closing:
            # A burst of process logs must not monopolise Tcl's event loop.
            self.root.after(10 if processed >= self.MAX_EVENTS_PER_TICK else 100, self._poll_events)

    def _handle_event(self, event: QueueEvent) -> None:
        if event.phase == "HISTORY_LOADED":
            self._history_cache = event.payload["history"]
            self._history_loading = False
            self._render_results()
            if self._history_refresh_pending:
                self._history_refresh_pending = False
                self._refresh_views()
            return
        if event.phase == "HISTORY_FAILED":
            self._history_loading = False
            self.state_var.set(event.message)
            return
        if event.phase == "ENVIRONMENT":
            self.settings_status.set(event.message)
            return
        payload = self._normalise_event_payload(event, dict(event.payload or {}))
        # Retry dostaje całkowicie nowy kontener i nowy zestaw artefaktów.
        # Nie zostawiamy na dashboardzie częściowych metryk poprzedniej
        # próby, nawet na czas między komunikatem retry a startem sesji.
        if event.phase in {"SESSION_STARTED", "RUN_RETRYING"}:
            self.dashboard_frame.clear()
            self.docker_frame.clear()
        if event.phase == "LOG":
            source = str(payload.get("log_source") or "log")
            records = payload.get("logs")
            if records is None:
                records = [payload.get("log") or {"text": event.message}]
            self.docker_frame.append_logs(source, records)
        else:
            self.dashboard_frame.update_snapshot(payload)
            self.docker_frame.update_snapshot(payload)
        time_budget = payload.get("time_budget")
        if isinstance(time_budget, dict):
            self._update_time_budget_status(time_budget)
        if event.total:
            overall = payload.get("overall_progress_pct")
            try:
                overall_value = float(overall)
            except (TypeError, ValueError):
                overall_value = 0.0
            self.progress.configure(maximum=100, value=max(0.0, min(100.0, overall_value)))
            details = "Kolejka: {:.1f}%  |  Symulacja {} / {} — {}".format(
                max(0.0, min(100.0, overall_value)), event.current or 1, event.total, event.phase
            )
            if isinstance(time_budget, dict) and time_budget.get("enabled"):
                remaining = time_budget.get("active_remaining_active_real_s")
                try:
                    details += "  |  limit bieżącego runu: ~{}".format(
                        self._duration_text(float(remaining))
                    )
                except (TypeError, ValueError):
                    pass
                queue_remaining = time_budget.get("estimated_queue_remaining_s")
                try:
                    details += "  |  pozostało w kolejce: ~{}".format(
                        self._duration_text(float(queue_remaining))
                    )
                except (TypeError, ValueError):
                    pass
            completion = format_estimated_completion(
                payload,
                time_budget if isinstance(time_budget, dict) else None,
                payload.get("queue_summary"),
            )
            details += "  |  Koniec kolejki: {}".format(completion or "brak danych")
            self.progress_var.set(details)
        self._refresh_queue(payload)
        if event.phase == "LOG":
            self._append_live_log(event.message or event.phase)
        elif event.phase != "LIVE_UPDATE":
            self.state_var.set(event.message or event.phase)
            self._append_live_log(event.message or event.phase)
        if event.simulation_id and event.simulation_id in self.simulation_tabs:
            self.simulation_tabs[event.simulation_id].update_status()
        if event.phase in {"COMPLETED", "FAILED", "STOPPED", "IDLE", "SESSION", "XAPP_CRASHED", "XAPP_RESTARTED", "XAPP_FAILED"}:
            self._refresh_views()
        if event.phase == "IDLE":
            self.run_button.configure(state="normal")
            self.stop_button.configure(state="disabled")

    def _append_live_log(self, text: str) -> None:
        if not text:
            return
        self.live_log.configure(state="normal")
        self.live_log.insert("end", text.rstrip() + "\n")
        self._visible_live_log_lines += max(1, text.count("\n") + 1)
        excess = self._visible_live_log_lines - self.MAX_LIVE_LOG_LINES
        if excess > 0:
            self.live_log.delete("1.0", "{}.0".format(excess + 1))
            self._visible_live_log_lines -= excess
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    def _refresh_queue(self, payload: Dict[str, Any]) -> None:
        records = payload.get("queue")
        if not isinstance(records, list):
            return
        selected = self.queue_tree.selection()
        selected_id = selected[0] if selected else ""
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
        for record in records:
            if not isinstance(record, dict):
                continue
            item_id = str(record.get("id") or "")
            progress = record.get("progress_pct")
            try:
                progress_text = "{:.0f}%".format(float(progress))
            except (TypeError, ValueError):
                progress_text = "—"
            self.queue_tree.insert(
                "",
                "end",
                iid=item_id or None,
                values=(
                    record.get("index", ""),
                    record.get("name", ""),
                    record.get("status", ""),
                    progress_text,
                ),
            )
        if selected_id and self.queue_tree.exists(selected_id):
            self.queue_tree.selection_set(selected_id)

    def _select_queue_item(self, _event: Any) -> None:
        selected = self.queue_tree.selection()
        if not selected:
            return
        simulation_id = selected[0]
        try:
            simulation = self.controller.get(simulation_id)
        except KeyError:
            return
        if simulation.status.value in {"COMPLETED", "FAILED", "STOPPED"}:
            self._refresh_views()
            self.notebook.select(self.results_frame)
            self.results_frame.select_simulation(simulation_id)
            return
        tab = self.simulation_tabs.get(simulation_id)
        if tab is not None:
            self.notebook.select(self.session_frame)
            self.simulation_notebook.select(tab)

    @staticmethod
    def _normalise_event_payload(event: QueueEvent, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ujednolica punktowe zdarzenia ze snapshotem dla wszystkich widoków."""

        result = dict(payload)
        phase = str(event.phase or "").upper()
        result.setdefault("phase", phase)
        result.setdefault("message", event.message)
        # Dashboard dostaje pełny, prosty kontekst kolejki razem z każdym
        # zdarzeniem; nie musi odtwarzać indeksu runu z widżetu Treeview.
        if event.current:
            result.setdefault("current", event.current)
        if event.total:
            result.setdefault("total", event.total)
            queue_summary = result.get("queue_summary")
            queue_summary = dict(queue_summary) if isinstance(queue_summary, dict) else {}
            queue_summary.setdefault("current", event.current)
            queue_summary.setdefault("total", event.total)
            if "overall_progress_pct" in result:
                queue_summary.setdefault("overall_progress_pct", result.get("overall_progress_pct"))
            result["queue_summary"] = queue_summary
        if phase in {"COMPLETED", "FAILED", "STOPPED"}:
            result["status"] = phase
            result["stage"] = phase
            simulation = result.get("simulation")
            simulation_data = dict(simulation) if isinstance(simulation, dict) else {}
            if event.simulation_id:
                simulation_data.setdefault("id", event.simulation_id)
            simulation_data["status"] = phase
            result["simulation"] = simulation_data
        elif phase == "IDLE":
            # IDLE opisuje całą kolejkę, nie nową symulację. Jawny znacznik
            # chroni dashboard przed pozostawieniem starej nazwy runu po
            # scaleniu zdarzeń przyrostowych.
            result["status"] = "IDLE"
            result["stage"] = "IDLE"
            result["queue_terminal"] = True
            message_lower = str(event.message or "").lower()
            result["queue_terminal_status"] = (
                "ZAKOŃCZONA" if "zakończona" in message_lower else "WYMAGA UWAGI"
            )

        component = str(result.get("component") or "")
        component_states = {
            "COMPONENT_FAILED": "failed",
            "XAPP_CRASHED": "failed",
            "XAPP_FAILED": "failed",
            "XAPP_RESTARTING": "restarting",
            "XAPP_RESTARTED": "running",
        }
        component_state = component_states.get(phase)
        if component in {"ric", "ns3", "xapp"} and component_state:
            raw_processes = result.get("processes")
            processes = {
                str(key): dict(value)
                for key, value in raw_processes.items()
                if isinstance(value, dict)
            } if isinstance(raw_processes, dict) else {}
            process = dict(processes.get(component) or {})
            process.setdefault("name", component)
            process["status"] = component_state
            process["state"] = component_state
            if result.get("exit_code") is not None:
                process["exit_code"] = result.get("exit_code")
            processes[component] = process
            result["processes"] = processes
            if component == "xapp":
                result["xapp_status"] = component_state
        return result

    def _clear_session_live_log(self) -> None:
        self.live_log.configure(state="normal")
        self.live_log.delete("1.0", "end")
        self.live_log.configure(state="disabled")
        self._visible_live_log_lines = 0
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)

    def _refresh_views(self) -> None:
        self._render_results()
        if self._history_loading:
            self._history_refresh_pending = True
            return
        self._history_loading = True

        def read_history() -> None:
            try:
                history = self.storage.history(finished_only=True)
                self.controller.events.put(QueueEvent(None, "HISTORY_LOADED", "", payload={"history": history}))
            except Exception as exc:
                self.controller.events.put(QueueEvent(None, "HISTORY_FAILED", "Nie można odczytać historii: {}".format(exc)))

        threading.Thread(target=read_history, name="oran-lab-history", daemon=True).start()

    def _render_results(self) -> None:
        runs = {item.id: item for item in self._history_cache}
        runs.update({item.id: item for item in self.controller.simulations})
        self.results_frame.refresh(runs.values())
        self.comparison_frame.refresh_candidates()

    def _comparison_candidates(self) -> Iterable[Any]:
        merged = {item.id: item for item in self._history_cache}
        merged.update({item.id: item for item in self.controller.simulations})
        return list(merged.values())

    def _show_details(self, simulation: Any) -> None:
        window = tk.Toplevel(self.root)
        window.title("Szczegóły — {}".format(simulation.name))
        window.geometry("760x600")
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        text_area = ttk.Frame(frame)
        text_area.pack(fill="both", expand=True)
        text_area.columnconfigure(0, weight=1)
        text_area.rowconfigure(0, weight=1)
        text = tk.Text(
            text_area,
            wrap="word",
            font=self._fixed_font,
            background=self._colors["field"],
            foreground=self._colors["fg"],
            insertbackground=self._colors["fg"],
            relief="flat",
        )
        text.grid(row=0, column=0, sticky="nsew")
        details_scroll = ttk.Scrollbar(text_area, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=details_scroll.set)
        details_scroll.grid(row=0, column=1, sticky="ns")
        payload = simulation.to_dict()
        text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        text.configure(state="disabled")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Pliki i dane", command=lambda: self._show_run_data(simulation)).pack(
            side="left"
        )
        ttk.Button(buttons, text="Eksport CSV", command=lambda: self._export_csv(simulation)).pack(side="right")
        ttk.Button(buttons, text="Eksport JSON", command=lambda: self._export_json(simulation)).pack(
            side="right", padx=(0, 7)
        )
        ttk.Button(buttons, text="Pełne archiwum…", command=lambda: self._export_bundle(simulation)).pack(
            side="right", padx=(0, 7)
        )

    def _export_csv(self, simulation: Any) -> None:
        destination = self._result_export_destination(simulation, "csv", "Eksportuj wynik CSV")
        if destination is None:
            return
        try:
            path = self.storage.export_result_csv(simulation, destination)
            series_path = self.storage.export_result_timeseries_csv(simulation, destination)
        except Exception as exc:
            messagebox.showerror("Eksport nieudany", str(exc), parent=self.root)
            return
        message = "Zapisano podsumowanie: {}".format(path)
        if series_path:
            message += "\n\nZapisano próbki czasowe: {}".format(series_path)
        else:
            message += "\n\nTen zapis nie ma szeregów czasowych; CSV zawiera końcowe metryki oraz dostępną telemetrię live."
        runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
        termination = runtime.get("termination")
        if runtime.get("experiment_valid") is False or (
            isinstance(termination, dict) and termination.get("partial")
        ):
            message += "\n\nUwaga: ten przebieg jest częściowy. Ostatnia telemetria nie zastępuje końcowych metryk eksperymentu."
        messagebox.showinfo("Eksport CSV", message, parent=self.root)
        self._reveal_exported_path(path)

    def _export_comparison_csv(self, simulations: Any) -> None:
        if len(simulations) < 2:
            return
        initial = "{}-vs-{}-porownanie.csv".format(
            str(simulations[0].name).replace(" ", "-"),
            str(simulations[1].name).replace(" ", "-"),
        )
        value = filedialog.asksaveasfilename(
            parent=self.root,
            title="Eksportuj porównanie i szeregi czasowe",
            initialfile=initial,
            defaultextension=".csv",
            filetypes=(("Plik CSV", "*.csv"),),
        )
        if not value:
            return
        try:
            path = self.storage.export_comparison_csv(simulations, Path(value))
        except Exception as exc:
            messagebox.showerror("Eksport porównania nieudany", str(exc), parent=self.root)
            return
        messagebox.showinfo(
            "Eksport porównania",
            "Zapisano metryki, metadane i próbki czasowe wybranej pary:\n{}".format(path),
            parent=self.root,
        )
        self._reveal_exported_path(path)

    def _export_json(self, simulation: Any) -> None:
        destination = self._result_export_destination(simulation, "json", "Eksportuj wynik JSON")
        if destination is None:
            return
        try:
            path = self.storage.export_result_json(simulation, destination)
        except Exception as exc:
            messagebox.showerror("Eksport nieudany", str(exc), parent=self.root)
            return
        messagebox.showinfo("Eksport JSON", "Zapisano: {}".format(path), parent=self.root)
        self._reveal_exported_path(path)

    def _export_bundle(self, simulation: Any) -> None:
        destination = self._result_export_destination(
            simulation,
            "zip",
            "Eksportuj pełne archiwum runu",
        )
        if destination is None:
            return
        try:
            path = self.storage.export_result_bundle(simulation, destination)
        except Exception as exc:
            messagebox.showerror("Eksport nieudany", str(exc), parent=self.root)
            return
        messagebox.showinfo(
            "Pełne archiwum",
            "Zapisano pliki, serie czasowe i logi runu:\n{}".format(path),
            parent=self.root,
        )
        self._reveal_exported_path(path)

    def _reveal_exported_path(self, path: Path) -> None:
        """Pokazuje świeżo wyeksportowany plik w menedżerze plików."""

        try:
            open_in_file_manager(path)
        except Exception as exc:
            messagebox.showwarning(
                "Eksport zapisany",
                "Plik zapisano, ale nie można otworzyć jego lokalizacji:\n{}".format(exc),
                parent=self.root,
            )

    def _result_export_destination(self, simulation: Any, extension: str, title: str) -> Any:
        safe_name = "".join(
            char for char in str(simulation.name) if char.isalnum() or char in " -_"
        ).strip()
        initial = "{}-{}-wynik.{}".format(
            safe_name or "symulacja", str(simulation.id)[:8], extension
        )
        value = filedialog.asksaveasfilename(
            parent=self.root,
            title=title,
            initialfile=initial,
            defaultextension="." + extension,
            filetypes=(("Plik {}".format(extension.upper()), "*.{}".format(extension)),),
        )
        return Path(value) if value else None

    def _show_run_data(self, simulation: Any) -> None:
        try:
            RunDataDialog(self.root, self.storage, simulation)
        except Exception as exc:
            messagebox.showerror("Nie można otworzyć danych", str(exc), parent=self.root)

    def _open_run_folder(self, simulation: Any) -> None:
        try:
            open_in_file_manager(self.storage.directory(simulation.id))
        except Exception as exc:
            messagebox.showerror("Nie można otworzyć folderu runu", str(exc), parent=self.root)

    @staticmethod
    def _saved_result_dashboard_payload(simulation: Any) -> Dict[str, Any]:
        """Przygotowuje wyłącznie zarchiwizowane dane do wspólnego widoku.

        Ten sam komponent wykresów służy dla przebiegu live i Historii. Nie
        liczymy drugiego zestawu metryk ani nie uzupełniamy brakujących serii:
        do widoku trafiają dokładnie serie zapisane w ``result.json``.
        """

        result = getattr(simulation, "result", None)
        result_payload = result.to_dict() if result is not None else {}
        raw_series = result_payload.get("series")
        series = dict(raw_series) if isinstance(raw_series, dict) else {}
        raw_sources = result_payload.get("sources")
        sources = dict(raw_sources) if isinstance(raw_sources, dict) else {}
        # ``SimulationResult`` zapamiętuje źródła końcowych metryk. Dla serii
        # przypisujemy tylko odpowiadające im źródło albo uczciwie wskazujemy
        # artefakt runu — nie nadajemy brakującej serii nazwy pliku.
        source_key = {
            "energy_j": "total_energy_j",
            "power_w": "latest_power_w",
            "energy_efficiency_bit_per_j": "energy_efficiency_bit_per_j",
            "energy_per_mbit_j": "energy_per_mbit_j",
            "throughput_mbit_per_s": "throughput_mbit_per_s",
            "delivered_mbit": "delivered_mbit",
            "packet_loss_pct": "packet_loss_pct",
            "mean_prb_pct": "mean_prb_pct",
            "p05_sinr_db": "p05_sinr_db",
            "delay_ms": "delay_ms",
        }
        series_sources = {
            key: str(sources.get(source_key.get(key, key)) or "artefakt runu")
            for key, points in series.items()
            if isinstance(points, list) and points
        }
        result_payload["series"] = series
        result_payload["series_sources"] = series_sources
        runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
        live = runtime.get("live")
        live = live if isinstance(live, dict) else {}
        current_metrics = live.get("current_metrics")
        current_metrics = dict(current_metrics) if isinstance(current_metrics, dict) else {}
        termination = runtime.get("termination")
        partial = runtime.get("experiment_valid") is False or (
            isinstance(termination, dict) and bool(termination.get("partial"))
        )
        data_status = dict(live.get("data_status") or {})
        if partial:
            data_status["data_source"] = "ostatnia próbka live — przebieg częściowy"
        return {
            "phase": str(getattr(getattr(simulation, "status", None), "value", "COMPLETED")),
            "status": str(getattr(getattr(simulation, "status", None), "value", "COMPLETED")),
            "stage": "ZARCHIWIZOWANY WYNIK — CZĘŚCIOWY" if partial else "ZARCHIWIZOWANY WYNIK",
            "current_metrics": current_metrics,
            "sim_time_s": live.get("sim_time_s"),
            "simulation": {
                "id": str(getattr(simulation, "id", "")),
                "name": str(getattr(simulation, "name", "Symulacja")),
                "status": str(getattr(getattr(simulation, "status", None), "value", "COMPLETED")),
                "configuration": getattr(simulation, "configuration").to_dict(),
            },
            "result": result_payload,
            "data_collection": "ZARCHIWIZOWANE",
            "data_status": data_status or {"data_source": "artefakty runu"},
        }

    def _show_saved_charts(self, simulation: Any) -> None:
        """Otwiera zapisane serie i mapę bez blokowania głównego okna.

        Po otwarciu wykresy są od razu dostępne z ``result.json``. Większe
        pliki topologii odczytujemy w zwykłym wątku, a wynik przekazujemy przez
        kolejkę do głównej pętli Tkintera. Dzięki temu kliknięcie Historii nie
        zamraża interfejsu przy długim przebiegu z wieloma UE.
        """

        window = tk.Toplevel(self.root)
        window.title("Wykresy i mapa — {}".format(simulation.name))
        window.geometry("1460x900")
        window.minsize(900, 620)
        window.transient(self.root)
        dashboard = LiveDashboard(window)
        dashboard.pack(fill="both", expand=True)
        dashboard.update_snapshot(self._saved_result_dashboard_payload(simulation))

        received: "queue.Queue[Any]" = queue.Queue(maxsize=1)

        def read_topology() -> None:
            try:
                directory = self.storage.directory(simulation.id)
                received.put(("snapshot", parse_topology_artifacts(directory)))
            except Exception as exc:
                received.put(("error", str(exc)))

        def apply_topology() -> None:
            try:
                kind, value = received.get_nowait()
            except queue.Empty:
                try:
                    if window.winfo_exists():
                        window.after(60, apply_topology)
                except tk.TclError:
                    pass
                return
            try:
                if not window.winfo_exists():
                    return
                if kind == "snapshot":
                    dashboard.topology_view.set_snapshot(value)
                else:
                    dashboard.topology_view.detail_var.set(
                        "Nie można odczytać mapy z artefaktów: {}".format(value)
                    )
            except tk.TclError:
                pass

        threading.Thread(
            target=read_topology,
            name="oran-history-topology",
            daemon=True,
        ).start()
        window.after(60, apply_topology)

    def _import_result_bundle(self) -> None:
        if self.controller.running:
            messagebox.showwarning(
                "Kolejka działa",
                "Import wyników jest dostępny po zakończeniu bieżącej kolejki.",
                parent=self.root,
            )
            return
        source = filedialog.askopenfilename(
            parent=self.root,
            title="Importuj pełne archiwum runu",
            filetypes=(("Wyniki symulatora O-RAN", "*.zip"), ("Wszystkie pliki", "*.*")),
        )
        if not source:
            return
        try:
            simulation = self.storage.import_result_bundle(Path(source))
        except Exception as exc:
            messagebox.showerror("Nie można zaimportować", str(exc), parent=self.root)
            return
        self._history_cache.append(simulation)
        self._refresh_views()
        self.results_frame.select_simulation(simulation.id)
        messagebox.showinfo(
            "Zaimportowano wynik",
            "Zaimportowano „{}” do Wyników.".format(simulation.name),
            parent=self.root,
        )

    def _delete_saved_run(self, simulation: Any) -> None:
        if self.controller.running:
            messagebox.showwarning(
                "Kolejka działa",
                "Nie usuwaj zapisów wyników podczas działania kolejki.",
                parent=self.root,
            )
            return
        prompt = (
            "Przenieść zapis runu „{}” do kosza wyników?\n\n"
            "Wyniki, wykresy i logi nie zostaną usunięte trwale — można je potem przywrócić z przycisku „Kosz wyników…”."
        ).format(simulation.name)
        if not messagebox.askyesno("Usuń zapis runu", prompt, parent=self.root):
            return
        try:
            self.storage.delete(simulation.id)
        except Exception as exc:
            messagebox.showerror("Nie można przenieść do kosza", str(exc), parent=self.root)
            return
        self._history_cache = [item for item in self._history_cache if item.id != simulation.id]
        if any(item.id == simulation.id for item in self.controller.simulations):
            self.controller.simulations = [item for item in self.controller.simulations if item.id != simulation.id]
            self._build_simulation_tabs()
        self._refresh_views()
        messagebox.showinfo(
            "Przeniesiono do kosza",
            "Run „{}” można przywrócić z Kosza wyników.".format(simulation.name),
            parent=self.root,
        )

    def _show_deleted_runs(self) -> None:
        def restored(simulation: Any) -> None:
            self._history_cache.append(simulation)
            self._refresh_views()
            self.results_frame.select_simulation(simulation.id)

        RunTrashDialog(self.root, self.storage, restored)

    def _show_log(self, simulation: Any) -> None:
        window = tk.Toplevel(self.root)
        window.title("Log — {}".format(simulation.name))
        window.geometry("900x620")
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(
            frame,
            wrap="none",
            font=self._fixed_font,
            background=self._colors["field"],
            foreground=self._colors["fg"],
            insertbackground=self._colors["fg"],
            relief="flat",
        )
        text.grid(row=0, column=0, sticky="nsew")
        log_y = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        log_x = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=log_y.set, xscrollcommand=log_x.set)
        log_y.grid(row=0, column=1, sticky="ns")
        log_x.grid(row=1, column=0, sticky="ew")
        chunks: List[str] = []
        directory = self.storage.directory(simulation.id)
        log_directory = directory / "logs"
        ordered = ["runner.log", "ric.log", "ns3.log", "xapp.log"]
        names = [name for name in ordered if (log_directory / name).is_file()]
        names.extend(path.name for path in sorted(log_directory.glob("xapp-restart-*.log")))
        for name in names:
            path = log_directory / name
            if path.is_file():
                chunks.append("===== {} =====\n{}".format(name, path.read_text(encoding="utf-8", errors="replace")))
        if not chunks:
            chunks.append("Brak zapisanych logów.")
        text.insert("1.0", "\n\n".join(chunks))
        text.configure(state="disabled")

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.controller.running:
            accepted = messagebox.askyesno(
                "Zamknąć laboratorium?",
                "Bieżąca symulacja zostanie zatrzymana, a zasoby Docker posprzątane. Zamknąć?",
                parent=self.root,
            )
            if not accepted:
                return
            self._closing = True
            self._close_requested_at = time.monotonic()
            self.controller.request_stop()
            self.state_var.set("Zamykanie i sprzątanie zasobów…")
            self._finish_close_when_safe()
            return
        self.root.destroy()

    def _finish_close_when_safe(self) -> None:
        if not self.controller.running or time.monotonic() - self._close_requested_at >= self.CLOSE_GRACE_S:
            # Proces ochronny dokończy sprzątanie, jeśli aplikacja została zatrzymana twardo.
            self.root.destroy()
            return
        self.root.after(150, self._finish_close_when_safe)
