from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app.config import RESOURCE_DIR
from app.infrastructure.file_browser import open_in_file_manager
from app.services.my_energy_sources import FILES, save_source, source_path
from app.simulation.models import Simulation
from app.simulation.session_controller import default_configuration

from .simulation_tab import SimulationTab


class MyEnergyView(ttk.Frame):
    """Parametry i dwa pliki źródłowe używane przy następnym uruchomieniu."""

    def __init__(self, parent, config_store, on_pair, can_edit):
        super().__init__(parent, padding=12)
        self.config_store = config_store
        self.on_pair = on_pair
        self.can_edit = can_edit
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text="My Energy: parametry i kod", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        pages = ttk.Notebook(self)
        pages.grid(row=1, column=0, sticky="nsew")
        settings = ttk.Frame(pages)
        settings.columnconfigure(0, weight=1)
        settings.rowconfigure(1, weight=1)
        pages.add(settings, text="Parametry i porównanie")
        toolbar = ttk.Frame(settings, padding=8)
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="3 komórki / 12 UE", command=lambda: self._preset(3, 12)).pack(side="left")
        ttk.Button(toolbar, text="6 komórek / 24 UE", command=lambda: self._preset(6, 24)).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Polityka energetyczna", command=self._energy_policy).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Dodaj parę porównawczą", command=self._pair).pack(side="right")
        cfg = default_configuration(config_store.as_dict())
        cfg.parameters["simTime"] = 12
        cfg.xapp = "optimized"
        cfg.xapp_environment["EE_MAX_UES_FOR_SHUTDOWN"] = 4
        self.form = SimulationTab(settings, Simulation(name="My Energy", configuration=cfg), config_store.as_dict(), None)
        self.form.grid(row=1, column=0, sticky="nsew")
        ttk.Label(settings, text="Para ma identyczny scenariusz, ruch i RngRun. Różni się włączeniem xAppa.\nKomórki to osobne gNB z antenami dookólnymi i kotwicą LTE; ten model nie odwzorowuje sektorów jednego masztu.", style="Muted.TLabel", justify="left").grid(row=2, column=0, sticky="w", padx=8, pady=8)
        self.editors = {}
        self.paths = {}
        self.searches = {}
        self.status = tk.StringVar(value="Zapisany kod zostanie skompilowany automatycznie przy następnym uruchomieniu.")
        for kind, title in (("xapp", "Kod xAppa (C)"), ("scenario", "Kod scenariusza (C++)")):
            page = ttk.Frame(pages, padding=8)
            page.columnconfigure(0, weight=1)
            page.rowconfigure(3, weight=1)
            pages.add(page, text=title)
            buttons = ttk.Frame(page)
            buttons.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            ttk.Button(buttons, text="Zapisz kod", command=lambda k=kind: self._save(k)).pack(side="left")
            ttk.Button(buttons, text="Wczytaj z dysku", command=lambda k=kind: self._load(k)).pack(side="left", padx=6)
            ttk.Button(buttons, text="Przywróć wzorzec", command=lambda k=kind: self._reset(k)).pack(side="left")
            ttk.Button(buttons, text="Otwórz folder i kopie", command=lambda k=kind: open_in_file_manager(source_path(self.config_store.as_dict(), k).parent)).pack(side="right")
            self.paths[kind] = tk.StringVar()
            ttk.Label(page, textvariable=self.paths[kind], style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 5))
            searchbar = ttk.Frame(page)
            searchbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
            self.searches[kind] = tk.StringVar()
            ttk.Label(searchbar, text="Znajdź:").pack(side="left")
            search = ttk.Entry(searchbar, textvariable=self.searches[kind], width=32)
            search.pack(side="left", padx=6)
            search.bind("<Return>", lambda _event, k=kind: self._find(k))
            ttk.Button(searchbar, text="Następny", command=lambda k=kind: self._find(k)).pack(side="left")
            jump = "void forEachCell(" if kind == "xapp" else "  // Position"
            ttk.Button(searchbar, text="Decyzje algorytmu" if kind == "xapp" else "Rozmieszczenie sieci",
                       command=lambda k=kind, term=jump: self._find(k, term)).pack(side="right")
            editor = tk.Text(page, wrap="none", undo=True, font=("Consolas", 10), padx=8, pady=8)
            editor.grid(row=3, column=0, sticky="nsew")
            sy = ttk.Scrollbar(page, orient="vertical", command=editor.yview)
            sx = ttk.Scrollbar(page, orient="horizontal", command=editor.xview)
            sy.grid(row=3, column=1, sticky="ns")
            sx.grid(row=4, column=0, sticky="ew")
            editor.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
            editor.bind("<Control-s>", lambda _event, k=kind: self._save(k))
            editor.bind("<Control-f>", lambda _event, field=search: field.focus_set())
            editor.tag_configure("found", background="#fff0a0", foreground="#202020")
            self.editors[kind] = editor
            self._load(kind)
        ttk.Label(self, textvariable=self.status, style="Muted.TLabel", wraplength=1050).grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _preset(self, cells, ues):
        cfg = default_configuration(self.config_store.as_dict())
        cfg.parameters.update(N_MmWaveEnbNodes=cells, N_Ues=ues)
        cfg.parameters["simTime"] = 12
        cfg.xapp = "optimized"
        cfg.xapp_environment["EE_MAX_UES_FOR_SHUTDOWN"] = 4
        self.form.apply_configuration(cfg)

    def _pair(self):
        try:
            self.form.commit()
            self.on_pair(self.form.simulation.configuration)
        except Exception as exc:
            messagebox.showerror("Nie można dodać porównania", str(exc), parent=self)

    def _energy_policy(self):
        try:
            self.form.commit()
            cfg = self.form.simulation.configuration
            cfg.xapp = "optimized"
            cfg.xapp_environment.update(EE_MIN_TARGET_SINR_DB=5, EE_MIN_HANDOVER_GAIN_DB=-100,
                                        EE_SHUTDOWN_CONFIRMATION_SEC=1, EE_MAX_UES_FOR_SHUTDOWN=4,
                                        EE_DECISION_PERIOD_SEC=1)
            self.form.apply_configuration(cfg)
            self.status.set("Polityka energetyczna: SINR celu ≥ 5 dB, dopuszczalny spadek do 100 dB, maks. 4 UE, potwierdzenie 1 s ns-3. Zweryfikuj końcowe straty i opóźnienie.")
        except Exception as exc:
            messagebox.showerror("Parametry polityki", str(exc), parent=self)

    def _load(self, kind):
        try:
            path = source_path(self.config_store.as_dict(), kind)
            editor = self.editors[kind]
            editor.delete("1.0", "end")
            editor.insert("1.0", path.read_text(encoding="utf-8"))
            editor.edit_reset()
            self.paths[kind].set(str(path))
        except Exception as exc:
            messagebox.showerror("Nie można wczytać kodu", str(exc), parent=self)

    def _find(self, kind, term=None):
        editor = self.editors[kind]
        start = "1.0" if term is not None else "insert +1c"
        if term is not None:
            self.searches[kind].set(term)
        term = self.searches[kind].get()
        if not term:
            return
        location = editor.search(term, start, stopindex="end", nocase=True)
        if not location:
            location = editor.search(term, "1.0", stopindex="end", nocase=True)
        editor.tag_remove("found", "1.0", "end")
        if location:
            editor.tag_add("found", location, "{}+{}c".format(location, len(term)))
            editor.mark_set("insert", location)
            editor.see(location)
        else:
            self.status.set("Nie znaleziono: " + term)

    def _save(self, kind, content=None):
        if not self.can_edit():
            messagebox.showinfo("Symulacja trwa", "Zapisz kod po zakończeniu bieżącej kolejki.", parent=self)
            return
        try:
            path = save_source(self.config_store.as_dict(), kind, content if content is not None else self.editors[kind].get("1.0", "end-1c"))
            self.status.set("Zapisano: {}. Poprzednia wersja jest w folderze backups. Następny przebieg przebuduje kod.".format(path))
            if content is not None:
                self._load(kind)
        except Exception as exc:
            messagebox.showerror("Nie można zapisać kodu", str(exc), parent=self)

    def _reset(self, kind):
        self._save(kind, (RESOURCE_DIR / "resources" / FILES[kind]).read_text(encoding="utf-8"))
