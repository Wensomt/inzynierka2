from __future__ import annotations

import tkinter as tk
import math
from tkinter import messagebox, ttk
from typing import Callable, Iterable, List, Optional

from app.simulation.models import Simulation


class ResultsView(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        on_details: Callable[[Simulation], None],
        on_log: Callable[[Simulation], None],
        on_export_csv: Optional[Callable[[Simulation], None]] = None,
        on_export_json: Optional[Callable[[Simulation], None]] = None,
        on_delete: Optional[Callable[[Simulation], None]] = None,
        on_export_bundle: Optional[Callable[[Simulation], None]] = None,
        on_data: Optional[Callable[[Simulation], None]] = None,
        on_charts: Optional[Callable[[Simulation], None]] = None,
        on_import_bundle: Optional[Callable[[], None]] = None,
        on_deleted_runs: Optional[Callable[[], None]] = None,
        on_open_folder: Optional[Callable[[Simulation], None]] = None,
    ) -> None:
        super().__init__(parent, padding=14)
        self._on_details = on_details
        self._on_log = on_log
        self._on_export_csv = on_export_csv
        self._on_export_json = on_export_json
        self._on_delete = on_delete
        self._on_export_bundle = on_export_bundle
        self._on_data = on_data
        self._on_charts = on_charts
        self._on_import_bundle = on_import_bundle
        self._on_deleted_runs = on_deleted_runs
        self._on_open_folder = on_open_folder
        self._items = {}
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        columns = ("name", "status", "ee", "energy_mbit", "energy", "power", "throughput", "delivered", "delay", "loss")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=16)
        for key, title, width in (
            ("name", "Nazwa", 260),
            ("status", "Stan", 110),
            ("ee", "EE [Mbit/J]", 115),
            ("energy_mbit", "Energia/Mbit [J]", 125),
            ("energy", "Energia [J]", 115),
            ("power", "Moc [W]", 105),
            ("throughput", "Śr. przepływność [Mbit/s]", 155),
            ("delivered", "Dostarczone dane [Mbit]", 155),
            ("delay", "Śr. opóźnienie [ms]", 135),
            ("loss", "Packet loss [%]", 115),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        horizontal_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Pokaż szczegóły", command=self._details).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Pokaż log", command=self._log).pack(side="left", padx=(0, 14))
        if self._on_data:
            ttk.Button(buttons, text="Pliki i dane", command=self._data).pack(side="left", padx=(0, 14))
        if self._on_charts:
            ttk.Button(buttons, text="Wykresy i mapa", command=self._charts).pack(
                side="left", padx=(0, 14)
            )
        for label, actions in (
            ("Eksportuj", (("CSV", self._on_export_csv, self._export_csv), ("JSON", self._on_export_json, self._export_json), ("Pełne archiwum…", self._on_export_bundle, self._export_bundle))),
            ("Więcej", (("Otwórz folder", self._on_open_folder, self._open_folder), ("Importuj archiwum…", self._on_import_bundle, self._import_bundle), ("Kosz wyników…", self._on_deleted_runs, self._deleted_runs), ("Usuń zapis", self._on_delete, self._delete))),
        ):
            menu_button = ttk.Menubutton(buttons, text=label)
            menu = tk.Menu(menu_button, tearoff=False)
            for title, available, command in actions:
                if available:
                    menu.add_command(label=title, command=command)
            menu_button.configure(menu=menu)
            menu_button.pack(side="left", padx=(0, 8))

    def refresh(self, simulations: Iterable[Simulation]) -> None:
        selected = self.tree.selection()
        self._items = {item.id: item for item in simulations}
        for entry in self.tree.get_children():
            self.tree.delete(entry)
        for item in self._items.values():
            metrics = item.result.metrics if item.result else {}
            runtime = item.runtime if isinstance(item.runtime, dict) else {}
            live = runtime.get("live")
            live = live if isinstance(live, dict) else {}
            live_metrics = live.get("current_metrics")
            live_metrics = live_metrics if isinstance(live_metrics, dict) else {}
            termination = runtime.get("termination")
            termination = termination if isinstance(termination, dict) else {}
            partial = runtime.get("experiment_valid") is False or bool(termination.get("partial"))

            def metric_value(key: str) -> object:
                return metrics.get(key) if item.result is not None else live_metrics.get(key)

            self.tree.insert("", "end", iid=item.id, values=(
                item.name,
                "CZĘŚCIOWA" if partial else item.status.value,
                self._number_scaled(metric_value("energy_efficiency_bit_per_j"), 1_000_000.0),
                self._number(metric_value("energy_per_mbit_j")),
                self._number(metric_value("total_energy_j")),
                self._number(metric_value("average_power_w")),
                self._number(metric_value("throughput_mbit_per_s")),
                self._number(metric_value("delivered_mbit")),
                self._number(metric_value("delay_ms")),
                self._number(metric_value("packet_loss_pct")),
            ))
        if selected and self.tree.exists(selected[0]):
            self.select_simulation(selected[0])

    def select_simulation(self, simulation_id: str) -> None:
        """Wybiera run otwarty z kolejki bez wymagania drugiego kliknięcia."""

        if self.tree.exists(simulation_id):
            self.tree.selection_set(simulation_id)
            self.tree.focus(simulation_id)
            self.tree.see(simulation_id)

    def _selected(self) -> Optional[Simulation]:
        selected = self.tree.selection()
        return self._items.get(selected[0]) if selected else None

    def _details(self) -> None:
        item = self._selected()
        if item:
            self._on_details(item)

    def _log(self) -> None:
        item = self._selected()
        if item:
            self._on_log(item)

    def _export_csv(self) -> None:
        self._run_selected(self._on_export_csv, "eksportu CSV")

    def _export_json(self) -> None:
        self._run_selected(self._on_export_json, "eksportu JSON")

    def _export_bundle(self) -> None:
        self._run_selected(self._on_export_bundle, "eksportu pełnego archiwum")

    def _data(self) -> None:
        self._run_selected(self._on_data, "przeglądania plików")

    def _open_folder(self) -> None:
        self._run_selected(self._on_open_folder, "otwarcia folderu runu")

    def _charts(self) -> None:
        self._run_selected(self._on_charts, "oglądania wykresów")

    def _delete(self) -> None:
        self._run_selected(self._on_delete, "usunięcia")

    def _import_bundle(self) -> None:
        if self._on_import_bundle:
            self._on_import_bundle()

    def _deleted_runs(self) -> None:
        if self._on_deleted_runs:
            self._on_deleted_runs()

    def _run_selected(
        self,
        callback: Optional[Callable[[Simulation], None]],
        action: str,
    ) -> None:
        if callback is None:
            return
        item = self._selected()
        if item is None:
            messagebox.showinfo("Wyniki", "Wybierz zapis do {}.".format(action), parent=self.winfo_toplevel())
            return
        callback(item)

    @staticmethod
    def _number(value: object) -> str:
        try:
            number = float(value)
            return "{:.4g}".format(number) if math.isfinite(number) else "N/A"
        except (TypeError, ValueError):
            return "N/A"

    @classmethod
    def _number_scaled(cls, value: object, divisor: float) -> str:
        try:
            return cls._number(float(value) / divisor)
        except (TypeError, ValueError, ZeroDivisionError):
            return "N/A"
