from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.comparison.comparator import Comparison, MetricComparison, compare_simulations
from app.simulation.models import Simulation

from .scrollable import ScrollableFrame


class ComparisonView(ttk.Frame):
    _SERIES_KEYS = {
        "energy_efficiency_bit_per_j": "energy_efficiency_bit_per_j",
        "energy_per_mbit_j": "energy_per_mbit_j",
        "total_energy_j": "energy_j",
        "average_power_w": "power_w",
        "throughput_mbit_per_s": "throughput_mbit_per_s",
        "delivered_mbit": "delivered_mbit",
        "delay_ms": "delay_ms",
        "packet_loss_pct": "packet_loss_pct",
        "mean_sinr_db": "mean_sinr_db",
        "p05_sinr_db": "p05_sinr_db",
        "mean_prb_pct": "mean_prb_pct",
    }

    def __init__(
        self,
        parent: tk.Misc,
        candidates: Callable[[], Iterable[Simulation]],
        on_export: Optional[Callable[[List[Simulation]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._candidates = candidates
        self._on_export = on_export
        self._labels: Dict[str, Simulation] = {}
        self._metrics: Dict[str, MetricComparison] = {}
        self._selected_metric: Optional[str] = None
        self._shown_pair: Optional[Tuple[Simulation, Simulation]] = None
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.scroller = ScrollableFrame(self, content_padding=14)
        self.scroller.grid(row=0, column=0, sticky="nsew")
        content = self.scroller.content
        content.bind("<Configure>", self._update_warning_wrap, add=True)
        content.columnconfigure(0, weight=1)
        chooser = ttk.LabelFrame(content, text="PORÓWNAJ SYMULACJE", padding=10)
        chooser.grid(row=0, column=0, sticky="ew")
        chooser.columnconfigure(1, weight=1)
        chooser.columnconfigure(3, weight=1)
        ttk.Label(chooser, text="Porównaj:").grid(row=0, column=0, sticky="w")
        self.left_var = tk.StringVar()
        self.left_choice = ttk.Combobox(chooser, state="readonly", textvariable=self.left_var)
        self.left_choice.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(chooser, text="z:").grid(row=0, column=2, sticky="w")
        self.right_var = tk.StringVar()
        self.right_choice = ttk.Combobox(chooser, state="readonly", textvariable=self.right_var)
        self.right_choice.grid(row=0, column=3, sticky="ew", padx=(8, 12))
        for choice in (self.left_choice, self.right_choice):
            choice.bind("<<ComboboxSelected>>", lambda _event: self.refresh_comparison())
        ttk.Button(chooser, text="Pokaż porównanie", command=self.refresh_comparison).grid(
            row=0, column=4, padx=(0, 6)
        )
        if self._on_export:
            ttk.Button(chooser, text="Eksportuj parę CSV", command=self.export_selected_pair).grid(
                row=0, column=5
            )

        diff_box = ttk.LabelFrame(content, text="RÓŻNICE W KONFIGURACJI", padding=8)
        diff_box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.config_tree = self._tree(diff_box, ("parameter", "left", "right"), ("Parametr", "Pierwsza", "Druga"), height=3)

        tables = ttk.Frame(content)
        tables.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        tables.columnconfigure(0, weight=1)
        tables.columnconfigure(1, weight=1)
        energy_box = ttk.LabelFrame(tables, text="ENERGIA", padding=8)
        energy_box.grid(row=0, column=0, columnspan=2, sticky="nsew")
        quality_box = ttk.LabelFrame(tables, text="JAKOŚĆ TRANSMISJI", padding=8)
        quality_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.energy_tree = self._tree(energy_box, ("metric", "left", "right", "difference"), ("Metryka", "Pierwsza", "Druga", "Różnica"), height=4)
        self.quality_tree = self._tree(quality_box, ("metric", "left", "right", "difference"), ("Metryka", "Pierwsza", "Druga", "Różnica"))
        self.energy_tree.bind("<<TreeviewSelect>>", self._metric_selected, add=True)
        self.quality_tree.bind("<<TreeviewSelect>>", self._metric_selected, add=True)

        chart_box = ttk.LabelFrame(content, text="WYKRES PORÓWNAWCZY — kliknij metrykę w tabeli", padding=8)
        chart_box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        chart_box.columnconfigure(0, weight=1)
        self.chart_title = tk.StringVar(value="Wybierz metrykę, aby wyświetlić przebiegi obu symulacji.")
        ttk.Label(chart_box, textvariable=self.chart_title).grid(row=0, column=0, sticky="w")
        self.chart = tk.Canvas(chart_box, height=300, background="#ffffff", highlightthickness=0)
        self.chart.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.chart.bind("<Configure>", lambda _event: self._draw_selected_chart())

        qos_box = ttk.LabelFrame(content, text="KRYTERIA QoS (puste pole = względem pierwszej symulacji)", padding=8)
        qos_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.qos_vars = {}
        for column, (key, label) in enumerate((
            ("max_delay_ms", "Maks. średnie opóźnienie [ms]"),
            ("max_packet_loss_pct", "Maks. packet loss [%]"),
            ("min_throughput_mbit_per_s", "Min. przepływność [Mbit/s]"),
        )):
            variable = tk.StringVar(value="")
            self.qos_vars[key] = variable
            ttk.Label(qos_box, text=label).grid(row=0, column=column, sticky="w", padx=6)
            ttk.Entry(qos_box, textvariable=variable, width=20).grid(row=1, column=column, sticky="ew", padx=6)
            qos_box.columnconfigure(column, weight=1)
        self.assessment_var = tk.StringVar(value="")
        ttk.Label(qos_box, textvariable=self.assessment_var, wraplength=850, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.warning_var = tk.StringVar(value="")
        self.warning_label = ttk.Label(content, textvariable=self.warning_var, foreground="#8a5a12", justify="left", wraplength=700)
        self.warning_label.grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )

    def _update_warning_wrap(self, event: Any) -> None:
        try:
            self.warning_label.configure(wraplength=max(240, int(event.width) - 28))
        except tk.TclError:
            pass

    @staticmethod
    def _tree(parent: tk.Misc, columns: tuple, headings: tuple, height: int = 6) -> ttk.Treeview:
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=columns, show="headings", height=height)
        for column, heading in zip(columns, headings):
            tree.heading(column, text=heading)
            tree.column(column, width=350 if column in {"parameter", "metric"} else 150, anchor="w")
        vertical_scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        horizontal_scroll = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        return tree

    def refresh_candidates(self) -> None:
        previous_left, previous_right = self.left_var.get(), self.right_var.get()
        self._labels = {}
        values = []
        for item in self._candidates():
            if item.result is None:
                continue
            runtime = item.runtime if isinstance(item.runtime, dict) else {}
            termination = runtime.get("termination")
            termination = termination if isinstance(termination, dict) else {}
            partial = runtime.get("experiment_valid") is False or bool(termination.get("partial"))
            suffix = " · CZĘŚCIOWA" if partial else ""
            label = "{} [{}]{}".format(item.name, item.id[:8], suffix)
            self._labels[label] = item
            values.append(label)
        self.left_choice.configure(values=values)
        self.right_choice.configure(values=values)
        self.left_var.set(previous_left if previous_left in self._labels else (values[0] if values else ""))
        default_right = values[1] if len(values) > 1 else (values[0] if values else "")
        self.right_var.set(previous_right if previous_right in self._labels else default_right)
        self.refresh_comparison()

    def refresh_comparison(self) -> None:
        left, right = self._labels.get(self.left_var.get()), self._labels.get(self.right_var.get())
        if left is None or right is None or left.id == right.id:
            self._shown_pair = None
            self._selected_metric = None
            self._metrics.clear()
            for tree in (self.config_tree, self.energy_tree, self.quality_tree):
                self._clear(tree)
            self.chart.delete("all")
            self.chart_title.set("Wybierz dwa różne przebiegi z zapisanymi wynikami.")
            self.assessment_var.set("")
            self.warning_var.set("")
            return
        try:
            limits = {key: float(var.get().strip().replace(",", ".")) for key, var in self.qos_vars.items() if var.get().strip()}
            comparison = compare_simulations(left, right, limits)
        except ValueError as exc:
            self._shown_pair = None
            self._metrics.clear()
            self._selected_metric = None
            for tree in (self.config_tree, self.energy_tree, self.quality_tree):
                self._clear(tree)
            self.chart.delete("all")
            self.chart_title.set("Popraw progi QoS i ponów porównanie.")
            self.assessment_var.set("Niepoprawne progi QoS: {}".format(exc))
            return
        self._shown_pair = (left, right)
        self._show(comparison)

    def export_selected_pair(self) -> None:
        if self._on_export is None:
            return
        left = self._labels.get(self.left_var.get())
        right = self._labels.get(self.right_var.get())
        if left is None or right is None or left.id == right.id:
            return
        self._on_export([left, right])

    def _show(self, comparison: Comparison) -> None:
        self._clear(self.config_tree)
        self._clear(self.energy_tree)
        self._clear(self.quality_tree)
        self._metrics = {}
        self.chart.delete("all")
        self.chart_title.set("Wybierz metrykę w tabeli, aby zobaczyć jej serię czasową.")
        for tree in (self.config_tree, self.energy_tree, self.quality_tree):
            tree.heading("left", text=comparison.left_name)
            tree.heading("right", text=comparison.right_name)
        self.warning_var.set(" | ".join(comparison.warnings))
        self.assessment_var.set(comparison.assessment)
        if comparison.configuration_differences:
            for item in comparison.configuration_differences:
                self.config_tree.insert("", "end", values=(item.label, item.left, item.right))
        else:
            self.config_tree.insert("", "end", values=("Brak różnic", "", ""))
        self._add_metrics(self.energy_tree, comparison.energy)
        self._add_metrics(self.quality_tree, comparison.quality)
        self._selected_metric = self._selected_metric if self._selected_metric in self._metrics else "energy_efficiency_bit_per_j"
        self._draw_selected_chart()

    def _add_metrics(self, tree: ttk.Treeview, rows: Iterable[MetricComparison]) -> None:
        for item in rows:
            self._metrics[item.key] = item
            tree.insert("", "end", iid=item.key, values=(
                item.label,
                self._value(self._display_value(item.key, item.left), item.unit),
                self._value(self._display_value(item.key, item.right), item.unit),
                self._difference(item),
            ))

    def _metric_selected(self, event: Any) -> None:
        tree = event.widget
        selection = tree.selection()
        if not selection:
            return
        key = selection[0]
        self._selected_metric = key
        self._draw_metric_chart(key)

    def _draw_selected_chart(self) -> None:
        if self._selected_metric:
            self._draw_metric_chart(self._selected_metric)

    def _draw_metric_chart(self, key: str) -> None:
        item = self._metrics.get(key)
        if item is None:
            return
        if key == "active_ues":
            self.chart.delete("all")
            self.chart_title.set(
                "Aktywne UE: wartość skalarna; pominięto wykres przebiegu czasowego."
            )
            return
        if self._shown_pair is None:
            return
        left, right = self._shown_pair
        series_key = self._SERIES_KEYS.get(key, key)
        left_points = self._series_points(left, series_key)
        right_points = self._series_points(right, series_key)
        if key == "energy_efficiency_bit_per_j":
            left_points = [(x, y / 1_000_000.0) for x, y in left_points]
            right_points = [(x, y / 1_000_000.0) for x, y in right_points]
        chart_label = {"average_power_w": "Moc w przedziale próbkowania", "throughput_mbit_per_s": "Przepływność w przedziale próbkowania"}.get(key, item.label)
        self.chart_title.set("{} [{}] — {} / {}".format(chart_label, item.unit, left.name, right.name))
        canvas = self.chart
        canvas.delete("all")
        width = max(440, canvas.winfo_width())
        height = max(260, canvas.winfo_height())
        margin_left, margin_right, margin_top, margin_bottom = 70, 25, 18, 45
        plot_width = width - margin_left - margin_right
        plot_height = height - margin_top - margin_bottom
        points_by_run = [(left_points, "#2166a0"), (right_points, "#b85c17")]
        all_points = [point for points, _color in points_by_run for point in points]
        if not all_points:
            canvas.create_text(width / 2, height / 2, text="Brak zapisanej serii czasowej dla tej metryki.", fill="#59636e")
            return
        x_values = [point[0] for point in all_points]
        y_values = [point[1] for point in all_points]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        if math.isclose(x_min, x_max):
            x_min -= 0.5
            x_max += 0.5
        if math.isclose(y_min, y_max):
            padding = max(abs(y_min) * 0.05, 0.5)
            y_min -= padding
            y_max += padding
        else:
            padding = (y_max - y_min) * 0.08
            y_min -= padding
            y_max += padding

        for tick in range(5):
            fraction = tick / 4.0
            y = margin_top + fraction * plot_height
            value = y_max - fraction * (y_max - y_min)
            canvas.create_line(margin_left, y, width - margin_right, y, fill="#dddddd")
            canvas.create_text(margin_left - 9, y, text="{:.4g}".format(value), anchor="e", fill="#59636e", font=("Segoe UI", 8))
            x = margin_left + fraction * plot_width
            x_value = x_min + fraction * (x_max - x_min)
            canvas.create_line(x, margin_top, x, margin_top + plot_height, fill="#eaeaea")
            canvas.create_text(x, margin_top + plot_height + 17, text="{:.4g}".format(x_value), fill="#59636e", font=("Segoe UI", 8))
        canvas.create_line(margin_left, margin_top, margin_left, margin_top + plot_height, fill="#9aa5b1")
        canvas.create_line(margin_left, margin_top + plot_height, width - margin_right, margin_top + plot_height, fill="#9aa5b1")
        canvas.create_text(18, margin_top + plot_height / 2, text=item.unit, angle=90, fill="#202020")
        canvas.create_text(margin_left + plot_width / 2, height - 8, text="Czas symulacji [s]", fill="#202020")
        legend_x = margin_left + 8
        for (points, color), label in zip(points_by_run, (left.name, right.name)):
            if points:
                coords = []
                for time_s, value in points:
                    x = margin_left + (time_s - x_min) / (x_max - x_min) * plot_width
                    y = margin_top + (y_max - value) / (y_max - y_min) * plot_height
                    coords.extend((x, y))
                if len(points) > 1:
                    canvas.create_line(*coords, fill=color, width=2, smooth=False)
                for x, y in zip(coords[0::2], coords[1::2]):
                    canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)
            else:
                label += " (brak serii)"
            canvas.create_line(legend_x, margin_top + 4, legend_x + 22, margin_top + 4, fill=color, width=2)
            canvas.create_text(legend_x + 28, margin_top + 4, text=label, anchor="w", fill="#202020", font=("Segoe UI", 8))
            legend_x += min(270, max(150, len(label) * 7 + 50))

    @staticmethod
    def _series_points(simulation: Simulation, key: str) -> List[Tuple[float, float]]:
        if simulation.result is None:
            return []
        raw = simulation.result.series.get(key)
        if not raw:
            runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
            live = runtime.get("live") if isinstance(runtime.get("live"), Mapping) else {}
            live_series = live.get("series") if isinstance(live, Mapping) else {}
            raw = live_series.get(key) if isinstance(live_series, Mapping) else None
        points: List[Tuple[float, float]] = []
        for point in raw or []:
            if not isinstance(point, Mapping):
                continue
            try:
                x, y = float(point["x"]), float(point["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(x) and math.isfinite(y):
                points.append((x, y))
        return sorted(points)

    @staticmethod
    def _value(value: Optional[float], unit: str) -> str:
        return "N/A" if value is None else "{:.4g} {}".format(value, unit)

    @staticmethod
    def _display_value(key: str, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if key == "energy_efficiency_bit_per_j":
            return value / 1_000_000.0
        return value

    @staticmethod
    def _difference(item: MetricComparison) -> str:
        if item.difference is None:
            return "N/A"
        if item.difference_kind == "percentage_points":
            return "{:+.3g} pp".format(item.difference)
        if item.difference_kind == "absolute":
            return "{:+.4g} {}".format(item.difference, item.unit)
        return "{:+.3g}%".format(item.difference)

    @staticmethod
    def _clear(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)
