"""Gęsty, pasywny widok danych bieżącej symulacji.

Moduł nie uruchamia Dockera ani nie odczytuje artefaktów. Dostaje gotowe
snapshoty z wątku roboczego i tylko je prezentuje w wątku Tkintera. Dzięki
temu wartości kart, wykresy i wynik końcowy korzystają z tych samych danych.
"""

from __future__ import annotations

import math
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .scrollable import ScrollableFrame
from .topology_view import TopologyView


# Serie pochodzą z odczytanego metrics.csv, ee_timeseries.csv albo
# istniejących plików energii/KPM. Puste wykresy pozostają jawnie puste —
# nie tworzymy danych, których backend nie raportuje.
LIVE_SERIES_ORDER = (
    "energy_efficiency_bit_per_j",
    "energy_per_mbit_j",
    "power_w",
    "energy_j",
    "throughput_mbit_per_s",
    "delivered_mbit",
    "mean_sinr_db",
    "p05_sinr_db",
    "mean_prb_pct",
    "packet_loss_pct",
    "delay_ms",
)

METRIC_META = {
    "energy_efficiency_bit_per_j": ("EE skumulowana", "Mbit/J"),
    "energy_per_mbit_j": ("Koszt energii danych", "J/Mbit"),
    "total_energy_j": ("Zużyta energia", "J"),
    "average_power_w": ("Średnia moc", "W"),
    "latest_power_w": ("Ostatnia moc", "W"),
    "delivered_mbit": ("Dostarczone dane", "Mbit"),
    "throughput_mbit_per_s": ("Średnia przepływność", "Mbit/s"),
    "delay_ms": ("Średnie opóźnienie", "ms"),
    "packet_loss_pct": ("Niedostarczone pakiety", "%"),
    "mean_sinr_db": ("Średni SINR", "dB"),
    "p05_sinr_db": ("SINR p05", "dB"),
    "mean_prb_pct": ("Średnie użycie PRB", "%"),
    "active_ues": ("UE w raportach KPM", ""),
}

SERIES_META = {
    "power_w": ("Moc w przedziale próbkowania", "W", "#287441"),
    "energy_j": ("Energia skumulowana", "J", "#8a5a12"),
    "energy_efficiency_bit_per_j": ("EE skumulowana", "Mbit/J", "#2166a0"),
    "energy_per_mbit_j": ("Koszt energii danych", "J/Mbit", "#b13939"),
    "throughput_mbit_per_s": ("Przepływność w przedziale próbkowania", "Mbit/s", "#4f6bb2"),
    "delivered_mbit": ("Dostarczone dane", "Mbit", "#845aa5"),
    "mean_sinr_db": ("Średni SINR", "dB", "#287e75"),
    "p05_sinr_db": ("SINR p05", "dB", "#287783"),
    "mean_prb_pct": ("Użycie PRB", "%", "#9d681d"),
    "active_ues": ("UE — suma raportów KPM komórek", "UE", "#507934"),
    "packet_loss_pct": ("Niedostarczone pakiety", "%", "#b13939"),
    "delay_ms": ("Średnie opóźnienie", "ms", "#7d55a5"),
}

MAJOR_METRICS = (
    "energy_efficiency_bit_per_j",
    "energy_per_mbit_j",
)
MINOR_METRICS = (
    "total_energy_j",
    "average_power_w",
    "throughput_mbit_per_s",
    "delivered_mbit",
)
QUALITY_METRICS = (
    "mean_sinr_db",
    "p05_sinr_db",
    "mean_prb_pct",
    "active_ues",
    "packet_loss_pct",
    "delay_ms",
)


def as_mapping(value: Any) -> Dict[str, Any]:
    """Zwraca płytką kopię słownika albo pusty słownik."""

    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def event_payload(event: Any) -> Dict[str, Any]:
    """Normalizuje słownik albo obiekt zdarzenia do snapshotu."""

    if isinstance(event, Mapping):
        base = as_mapping(event)
    else:
        base = {}
        for name in ("simulation_id", "phase", "message", "current", "total", "payload"):
            if hasattr(event, name):
                base[name] = getattr(event, name)
    payload = as_mapping(base.pop("payload", None))
    merged = dict(base)
    merged.update(payload)
    return merged


def finite_number(value: Any) -> Optional[float]:
    """Konwertuje skończoną liczbę; teksty i NaN pozostawia jako brak danych."""

    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def real_time_budget_progress(
    elapsed_real_s: Any, per_run_target_real_s: Any
) -> Optional[float]:
    """Zwraca postęp limitu zegara rzeczywistego, bez użycia ``simTime``."""

    elapsed = finite_number(elapsed_real_s)
    target = finite_number(per_run_target_real_s)
    if elapsed is None or target is None or target <= 0.0:
        return None
    return min(100.0, max(0.0, 100.0 * elapsed / target))


def format_estimated_completion(*sources: Any) -> Optional[str]:
    """Zwraca lokalny termin końca wyłącznie z danych podanych przez backend.

    ``estimated_completion_at`` może być już tekstem ISO, a
    ``estimated_completion_epoch`` liczbą sekund Unix. Brak lub niepoprawna
    wartość nie jest zastępowana zgadywaną godziną. Przed pierwszym pełnym
    przebiegiem backend może przekazać wyłącznie termin wynikający z ręcznie
    wybranego budżetu; taki zapis jest oznaczony prefiksem ``plan:``.
    """

    values_list = [as_mapping(source) for source in sources]

    def render(supplied: Any, epoch_value: Any) -> Optional[str]:
        if isinstance(supplied, str) and supplied.strip():
            raw = supplied.strip()
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                # To nadal jest termin przekazany przez backend, np. gdy
                # integracja stosuje własny, czytelny zapis daty.
                return raw
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone()
            return parsed.strftime("%d.%m.%Y %H:%M")
        epoch = finite_number(epoch_value)
        if epoch is None or epoch <= 0.0:
            return None
        try:
            return datetime.fromtimestamp(epoch).astimezone().strftime("%d.%m.%Y %H:%M")
        except (OSError, OverflowError, ValueError):
            return None

    # W pierwszej kolejności wybieramy faktyczną prognozę opartą na czasach
    # zakończonych runów. Nie może jej przesłonić wcześniejszy plan.
    for values in values_list:
        rendered = render(
            values.get("estimated_completion_at"),
            values.get("estimated_completion_epoch"),
        )
        if rendered is not None:
            return rendered

    # Plan nie jest udawanym ETA: jego pochodzenie jest jawne i oznaczone w
    # tekście, aby użytkownik widział datę końca już od startu kolejki.
    for values in values_list:
        rendered = render(
            values.get("planned_completion_at"),
            values.get("planned_completion_epoch"),
        )
        if rendered is not None:
            return "plan: {}".format(rendered)
    return None


def series_points(value: Any) -> List[Tuple[float, float]]:
    """Czyta punkty wykresu bez zgadywania brakujących osi."""

    source: Any = value
    if isinstance(source, Mapping):
        source = source.get("points", source.get("data", []))
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        return []
    result: List[Tuple[float, float]] = []
    for item in source:
        x_value: Any = None
        y_value: Any = None
        if isinstance(item, Mapping):
            x_value = item.get("x", item.get("time_s", item.get("Time_s")))
            y_value = item.get("y", item.get("value", item.get("Value")))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
            x_value, y_value = item[0], item[1]
        x = finite_number(x_value)
        y = finite_number(y_value)
        if x is not None and y is not None:
            result.append((x, y))
    return result


def starts_new_simulation(previous: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    """Czy snapshot należy już do innego elementu kolejki.

    Dashboard scala kolejne zdarzenia, co jest właściwe wewnątrz jednego
    runu. Między runami pozostawienie starej serii, mapy i ``queue_terminal``
    byłoby jednak mylące, dlatego identyfikator symulacji jest twardą granicą
    stanu prezentacji.
    """

    old_id = as_mapping(previous.get("simulation")).get("id")
    next_id = as_mapping(incoming.get("simulation")).get("id")
    return bool(old_id and next_id and str(old_id) != str(next_id))


def chart_bounds(
    points: Sequence[Tuple[float, float]],
    viewport: Optional[Sequence[Any]] = None,
) -> Optional[Tuple[float, float, float, float]]:
    """Wyznacza stabilny widok osi albo zwraca jawnie ustawiony viewport.

    Funkcja nie interpoluje ani nie dodaje punktów. Dzięki temu obsługa zoomu
    może być testowana bez Tkintera, a brak poprawnej serii pozostaje brakiem
    danych na wykresie.
    """

    if viewport is not None and len(viewport) == 4:
        values = [finite_number(item) for item in viewport]
        if all(item is not None for item in values):
            x_min, x_max, y_min, y_max = values  # type: ignore[misc]
            if x_max > x_min and y_max > y_min:
                return (x_min, x_max, y_min, y_max)
    if not points:
        return None
    x_values = [item[0] for item in points]
    y_values = [item[1] for item in points]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)
    if x_min == x_max:
        x_min -= 0.5
        x_max += 0.5
    if y_min == y_max:
        padding = max(0.5, abs(y_min) * 0.05)
        y_min -= padding
        y_max += padding
    return (x_min, x_max, y_min, y_max)


def metric_rows(snapshot: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    """Wyciąga tylko rzeczywiście przekazane metryki i dane jakościowe."""

    collected: Dict[str, Any] = {}
    containers: List[Mapping[str, Any]] = [snapshot]
    result = as_mapping(snapshot.get("result"))
    if result:
        containers.append(result)
    for container in containers:
        for key in ("metrics", "current_metrics", "live_metrics", "quality"):
            values = as_mapping(container.get(key))
            for metric, value in values.items():
                if value is not None:
                    collected[metric] = value
                elif container is result:
                    collected.pop(metric, None)
    return [(key, collected[key]) for key in sorted(collected)]


def component_rows(snapshot: Mapping[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Zbiera opisane przez backend komponenty bez tworzenia stanów domyślnych."""

    result: Dict[str, Dict[str, Any]] = {}
    for key in ("processes", "components"):
        values = as_mapping(snapshot.get(key))
        for name, item in values.items():
            record = as_mapping(item)
            if record:
                result[name] = record
    runtime = as_mapping(snapshot.get("runtime"))
    for key in ("processes", "components"):
        values = as_mapping(runtime.get(key))
        for name, item in values.items():
            record = as_mapping(item)
            if record:
                result[name] = record
    return [(name, result[name]) for name in sorted(result)]


def _deep_merge(base: Mapping[str, Any], incoming: Mapping[str, Any]) -> Dict[str, Any]:
    result = as_mapping(base)
    for key, value in incoming.items():
        old = result.get(key)
        if isinstance(old, Mapping) and isinstance(value, Mapping):
            result[key] = _deep_merge(as_mapping(old), as_mapping(value))
        else:
            result[key] = value
    return result


class LiveDashboard(ttk.Frame):
    """Gęsty widok postępu, realnych metryk, serii i topologii radiowej.

    ``update_snapshot`` przyjmuje pełny albo przyrostowy słownik. Wykresy są
    ograniczone do serii aktualnie zbieranych z prawdziwych plików metryk.
    ``TopologyView`` otrzymuje ten sam snapshot i rysuje pozycje/powiązania
    wyłącznie wtedy, gdy backend je przekazał.
    """

    CHART_COLUMNS = 2

    def __init__(self, parent: tk.Misc, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self._snapshot: Dict[str, Any] = {}
        self._series: Dict[str, List[Tuple[float, float]]] = {}
        self._series_sources: Dict[str, str] = {}
        self._chart_draw_pending = False
        self._chart_viewports: Dict[str, Tuple[float, float, float, float]] = {}
        self._chart_pan: Optional[Tuple[str, int, int]] = None
        self._metric_vars: Dict[str, tk.StringVar] = {}
        self._status_vars: Dict[str, tk.StringVar] = {}
        self._chart_canvases: Dict[str, tk.Canvas] = {}
        self._chart_source_vars: Dict[str, tk.StringVar] = {}
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._configure_local_styles()

        self.scroller = ScrollableFrame(self, content_padding=12)
        self.scroller.grid(row=0, column=0, sticky="nsew")
        content = self.scroller.content
        content.columnconfigure(0, weight=1)

        header = ttk.Frame(content)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        header.columnconfigure(0, weight=1)
        self.title_var = tk.StringVar(value="BIEŻĄCA SYMULACJA")
        self.status_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.title_var, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(
            header,
            text="Mapa radiowa",
            command=self.show_topology,
        ).grid(row=0, column=1, sticky="e", padx=(8, 8))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=2, sticky="e")

        self._build_progress(content)
        self._build_metric_cards(content)
        self._build_quality_cards(content)
        self.detail_notebook = ttk.Notebook(content)
        self.detail_notebook.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        charts = ttk.Frame(self.detail_notebook)
        runtime = ttk.Frame(self.detail_notebook)
        self.map_frame = ttk.Frame(self.detail_notebook)
        for page, label in ((charts, "Wykresy"), (runtime, "Procesy i decyzje xApp"), (self.map_frame, "Mapa sieci")):
            page.columnconfigure(0, weight=1)
            self.detail_notebook.add(page, text=label)
        self._build_charts(charts)
        self._build_runtime_strip(runtime)
        self._build_xapp_decisions(runtime)
        self._build_topology(self.map_frame)

    def _configure_local_styles(self) -> None:
        style = ttk.Style(self)
        style.configure(
            "LiveMajorValue.TLabel",
            background="#f3f3f3",
            foreground="#2166a0",
            font=("TkDefaultFont", 17, "bold"),
        )
        style.configure(
            "LiveMinorValue.TLabel",
            background="#f3f3f3",
            foreground="#202020",
            font=("TkDefaultFont", 12, "bold"),
        )
        style.configure(
            "LiveStateValue.TLabel",
            background="#f3f3f3",
            foreground="#59636e",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.configure(
            "LiveChartTitle.TLabel",
            background="#f3f3f3",
            foreground="#59636e",
            font=("TkDefaultFont", 9, "bold"),
        )
        style.configure(
            "LiveHint.TLabel",
            background="#f3f3f3",
            foreground="#59636e",
            font=("TkDefaultFont", 9),
        )

    def _build_progress(self, parent: tk.Misc) -> None:
        progress_box = ttk.LabelFrame(parent, text="POSTĘP BIEŻĄCEGO PRZEBIEGU", padding=(8, 7))
        progress_box.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        progress_box.columnconfigure(0, weight=1)
        self.progress_var = tk.StringVar(value="Brak danych postępu")
        self.progress = ttk.Progressbar(progress_box, maximum=100.0, value=0.0, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")

        facts = ttk.Frame(progress_box)
        facts.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for index in range(6):
            facts.columnconfigure(index, weight=1, uniform="live-progress")
        self._progress_fact_vars: Dict[str, tk.StringVar] = {}
        for index, (key, label) in enumerate((
            ("sim_time", "CZAS SYMULOWANY"),
            ("run_progress", "POSTĘP BIEŻĄCEGO RUNU"),
            ("elapsed", "CZAS RZECZYWISTY"),
            ("eta", "POZOSTAŁY LIMIT / ETA"),
            ("queue", "KOLEJKA"),
            ("queue_finish", "KONIEC KOLEJKI"),
        )):
            value = tk.StringVar(value="—")
            self._progress_fact_vars[key] = value
            cell = ttk.Frame(facts)
            cell.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 7, 0))
            ttk.Label(cell, text=label, style="LiveHint.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=value, style="LiveStateValue.TLabel").pack(anchor="w")

    def _build_metric_cards(self, parent: tk.Misc) -> None:
        metrics_box = ttk.LabelFrame(parent, text="ENERGIA I DANE — NA ŻYWO", padding=(7, 6))
        metrics_box.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        metrics_box.columnconfigure(0, weight=1)

        major = ttk.Frame(metrics_box)
        major.grid(row=0, column=0, sticky="ew")
        major.columnconfigure(0, weight=1, uniform="major-metrics")
        major.columnconfigure(1, weight=1, uniform="major-metrics")
        for index, key in enumerate(MAJOR_METRICS):
            self._metric_card(major, key, index, "major")

        minor = ttk.Frame(metrics_box)
        minor.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for index in range(len(MINOR_METRICS)):
            minor.columnconfigure(index, weight=1, uniform="minor-metrics")
        for index, key in enumerate(MINOR_METRICS):
            self._metric_card(minor, key, index, "minor")

    def _build_quality_cards(self, parent: tk.Misc) -> None:
        quality_box = ttk.LabelFrame(parent, text="JAKOŚĆ SIECI — NA ŻYWO", padding=(7, 6))
        quality_box.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        for index in range(len(QUALITY_METRICS)):
            quality_box.columnconfigure(index, weight=1, uniform="quality-metrics")
        for index, key in enumerate(QUALITY_METRICS):
            self._metric_card(quality_box, key, index, "minor")
        ttk.Label(quality_box, text="Na żywo: niedostarczone obejmują też pakiety w drodze. Wynik końcowy uwzględnia czas opróżniania kolejek.", style="LiveHint.TLabel").grid(row=1, column=0, columnspan=len(QUALITY_METRICS), sticky="w", pady=(4, 0))

    def _metric_card(self, parent: tk.Misc, key: str, column: int, size: str) -> None:
        label, _unit = METRIC_META[key]
        card = ttk.Frame(parent, padding=(7, 4))
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
        value = tk.StringVar(value="—")
        self._metric_vars[key] = value
        style = "LiveMajorValue.TLabel" if size == "major" else "LiveMinorValue.TLabel"
        ttk.Label(card, text=label, style="LiveHint.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=value, style=style).pack(anchor="w")

    def _build_runtime_strip(self, parent: tk.Misc) -> None:
        state_box = ttk.LabelFrame(parent, text="STAN OPERACYJNY", padding=(7, 5))
        state_box.grid(row=4, column=0, sticky="ew", pady=(0, 7))
        labels = (
            ("stage", "ETAP"),
            ("xapp", "xAPP"),
            ("collector", "DANE LIVE"),
            ("components", "KOMPONENTY"),
        )
        for index, (key, label) in enumerate(labels):
            state_box.columnconfigure(index, weight=1, uniform="runtime-status")
            value = tk.StringVar(value="—")
            self._status_vars[key] = value
            cell = ttk.Frame(state_box)
            cell.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 7, 0))
            ttk.Label(cell, text=label, style="LiveHint.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=value, style="LiveStateValue.TLabel").pack(anchor="w")

    def _build_xapp_decisions(self, parent: tk.Misc) -> None:
        """Mały, ograniczony widok faktycznie zapisanych decyzji xAppa."""

        box = ttk.LabelFrame(parent, text="HISTORIA DECYZJI xAPP — NA ŻYWO", padding=(7, 5))
        box.grid(row=5, column=0, sticky="ew", pady=(0, 7))
        box.columnconfigure(0, weight=1)
        self._decision_status_var = tk.StringVar(value="Brak danych decyzji xAppa.")
        ttk.Label(box, textvariable=self._decision_status_var, style="LiveHint.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )

        columns = ("time", "event", "cell", "ue", "target", "reason")
        tree = ttk.Treeview(box, columns=columns, show="headings", height=5)
        headings = {
            "time": "t [s]",
            "event": "ZDARZENIE",
            "cell": "KOMÓRKA",
            "ue": "UE",
            "target": "CEL",
            "reason": "POWÓD",
        }
        widths = {"time": 65, "event": 140, "cell": 75, "ue": 60, "target": 65, "reason": 280}
        for key in columns:
            tree.heading(key, text=headings[key])
            tree.column(key, width=widths[key], minwidth=50, stretch=key in {"event", "reason"})
        tree.grid(row=1, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(5, 0))
        tree.configure(yscrollcommand=scrollbar.set)
        self._decision_tree = tree

    def _build_charts(self, parent: tk.Misc) -> None:
        chart_box = ttk.LabelFrame(
            parent,
            text="Przebiegi czasowe",
            padding=(7, 6),
        )
        chart_box.grid(row=6, column=0, sticky="ew", pady=(0, 7))
        for column in range(self.CHART_COLUMNS):
            chart_box.columnconfigure(column, weight=1, uniform="live-charts")

        toolbar = ttk.Frame(chart_box)
        toolbar.grid(row=0, column=0, columnspan=self.CHART_COLUMNS, sticky="ew", pady=(0, 5))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(
            toolbar,
            text="Kółko: przybliż czas · Shift + kółko: przybliż wartości · przeciągnij: przesuń · dwuklik: dopasuj",
            style="LiveHint.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Dopasuj wszystkie", command=self._reset_chart_viewports).grid(
            row=0, column=1, sticky="e"
        )

        chart_tabs = ttk.Notebook(chart_box)
        chart_tabs.grid(row=1, column=0, columnspan=self.CHART_COLUMNS, sticky="ew")
        groups = (
            ("Energia", LIVE_SERIES_ORDER[:4]),
            ("Ruch", LIVE_SERIES_ORDER[4:6]),
            ("Jakość transmisji", LIVE_SERIES_ORDER[6:]),
        )
        chart_positions = []
        for label, keys in groups:
            page = ttk.Frame(chart_tabs, padding=6)
            chart_tabs.add(page, text=label)
            for column in range(self.CHART_COLUMNS):
                page.columnconfigure(column, weight=1, uniform="charts")
            chart_positions.extend((page, index, key) for index, key in enumerate(keys))
        for page, index, key in chart_positions:
            row, column = divmod(index, self.CHART_COLUMNS)
            title, unit, _color = SERIES_META[key]
            card = ttk.Frame(page)
            card.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 6, 0),
                pady=(0 if row == 0 else 6, 0),
            )
            card.columnconfigure(0, weight=1)
            title_row = ttk.Frame(card)
            title_row.grid(row=0, column=0, sticky="ew", pady=(0, 2))
            title_row.columnconfigure(0, weight=1)
            ttk.Label(title_row, text="{} [{}]".format(title, unit), style="LiveChartTitle.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            source = tk.StringVar(value="")
            self._chart_source_vars[key] = source
            ttk.Label(card, textvariable=source, style="LiveHint.TLabel", wraplength=430).grid(
                row=2, column=0, sticky="w", pady=(3, 0)
            )
            canvas = tk.Canvas(
                card,
                height=175,
                highlightthickness=1,
                highlightbackground="#c7c7c7",
                background="#ffffff",
            )
            canvas.grid(row=1, column=0, sticky="ew")
            canvas.bind("<Configure>", lambda _event: self._request_chart_draw(), add=True)
            canvas.bind("<MouseWheel>", lambda event, chart_key=key: self._on_chart_wheel(chart_key, event), add=True)
            canvas.bind("<Button-4>", lambda event, chart_key=key: self._on_chart_wheel(chart_key, event), add=True)
            canvas.bind("<Button-5>", lambda event, chart_key=key: self._on_chart_wheel(chart_key, event), add=True)
            canvas.bind("<ButtonPress-1>", lambda event, chart_key=key: self._start_chart_pan(chart_key, event), add=True)
            canvas.bind("<B1-Motion>", lambda event, chart_key=key: self._drag_chart_pan(chart_key, event), add=True)
            canvas.bind("<ButtonRelease-1>", lambda event, chart_key=key: self._stop_chart_pan(chart_key, event), add=True)
            canvas.bind("<Double-Button-1>", lambda event, chart_key=key: self._reset_one_chart_viewport(chart_key), add=True)
            self._chart_canvases[key] = canvas

    def _build_topology(self, parent: tk.Misc) -> None:
        # TopologyView ma własny, sprawdzony Canvas i tabelę UE → komórka. Ten
        # dashboard jedynie przekazuje mu ten sam snapshot co kartom live.
        self.topology_view = TopologyView(parent)
        self.topology_view.grid(row=7, column=0, sticky="ew", pady=(1, 0))

    def show_topology(self) -> None:
        """Otwiera mapę radiową bez ukrywania wykresów albo zmiany zakładki."""

        self.detail_notebook.select(self.map_frame)
        self.scroller.scroll_to_widget(self.detail_notebook)

    def clear(self) -> None:
        """Czyści wyłącznie stan prezentacji dashboardu."""

        self._snapshot = {}
        self._series = {}
        self._series_sources = {}
        self._chart_viewports = {}
        self._chart_pan = None
        self.title_var.set("BIEŻĄCA SYMULACJA")
        self.status_var.set("")
        self.progress.configure(value=0.0)
        self.progress_var.set("Brak danych postępu")
        for value in self._progress_fact_vars.values():
            value.set("—")
        for value in self._metric_vars.values():
            value.set("—")
        for value in self._status_vars.values():
            value.set("—")
        for value in self._chart_source_vars.values():
            value.set("")
        for item in self._decision_tree.get_children():
            self._decision_tree.delete(item)
        self._decision_status_var.set("Brak danych decyzji xAppa.")
        self.topology_view.clear()
        self._request_chart_draw()
        self.scroller.scroll_to_top()

    def handle_event(self, event: Any) -> None:
        """Aktualizuje widok z obiektu zdarzenia lub słownika payloadu."""

        self.update_snapshot(event_payload(event))

    def update_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Scala i pokazuje przyrostowy albo pełny snapshot stanu."""

        incoming = as_mapping(snapshot)
        if not incoming:
            return
        if starts_new_simulation(self._snapshot, incoming):
            # Drugi run kolejki zaczyna własną historię metryk/topologii.
            # Nie może dziedziczyć serii ani terminalnego statusu runu 1.
            self.clear()
        self._snapshot = _deep_merge(self._snapshot, incoming)
        self._replace_series(incoming)
        self._refresh_chart_sources()
        self._refresh_header()
        self._refresh_progress()
        self._refresh_metric_cards()
        self._refresh_runtime_status()
        self._refresh_xapp_decisions()
        self.topology_view.update_snapshot(self._snapshot)
        self._request_chart_draw()

    def append_series(self, key: str, points: Any) -> None:
        """Dopisuje punkty otrzymane przyrostowo dla jednej istniejącej serii."""

        parsed = series_points(points)
        if not parsed:
            return
        existing = list(self._series.get(str(key), []))
        existing.extend(parsed)
        deduplicated: Dict[float, float] = {}
        for x, y in existing:
            deduplicated[x] = y
        self._series[str(key)] = sorted(deduplicated.items())

    def _replace_series(self, incoming: Mapping[str, Any]) -> None:
        sources: List[Mapping[str, Any]] = [incoming]
        result = as_mapping(incoming.get("result"))
        if result:
            sources.append(result)
        for source in sources:
            source_labels = as_mapping(source.get("series_sources"))
            for key, label in source_labels.items():
                if label:
                    self._series_sources[str(key)] = str(label)
            values = as_mapping(source.get("series"))
            for key, raw_points in values.items():
                parsed = series_points(raw_points)
                if parsed:
                    self._series[key] = parsed
                elif isinstance(raw_points, Sequence) and not isinstance(raw_points, (str, bytes)):
                    self._series.pop(key, None)
            deltas = as_mapping(source.get("series_delta"))
            for key, raw_points in deltas.items():
                self.append_series(key, raw_points)

    def _refresh_chart_sources(self) -> None:
        """Pokazuje źródło każdej serii bez dopisywania go do samych danych."""

        for key, variable in self._chart_source_vars.items():
            source = self._series_sources.get(key)
            variable.set("źródło: {}".format(source) if source else "")

    def _refresh_header(self) -> None:
        # Ostatnie zdarzenie IDLE nie ma własnego Simulation, więc po scaleniu
        # snapshotów nadal widoczna byłaby nazwa poprzedniego runu. Zamiast
        # sugerować, że ten run jest "Symulacją 2/2", pokazujemy stan całej
        # kolejki.
        if self._snapshot.get("queue_terminal"):
            terminal_status = str(
                self._snapshot.get("queue_terminal_status") or "ZAKOŃCZONA"
            )
            self.title_var.set("KOLEJKA — {}".format(terminal_status))
            self.status_var.set(terminal_status)
            return

        simulation = as_mapping(self._snapshot.get("simulation"))
        name = self._first_value(simulation, "name", "simulation_name")
        if name is None:
            name = self._first_value(self._snapshot, "simulation_name", "name")
        current = self._first_number(self._snapshot, "current")
        total = self._first_number(self._snapshot, "total")
        queue_info = as_mapping(self._snapshot.get("queue_summary"))
        if current is None:
            current = self._first_number(queue_info, "current", "index")
        if total is None:
            total = self._first_number(queue_info, "total", "count")
        prefix = ""
        if current is not None and total is not None:
            prefix = "Symulacja {} / {}".format(int(current), int(total))
        if name is not None:
            self.title_var.set("{}{}".format(prefix + " — " if prefix else "", str(name)))
        elif prefix:
            self.title_var.set(prefix)
        else:
            self.title_var.set("BIEŻĄCA SYMULACJA")

        # Zdarzenie końcowe ma pierwszeństwo przed starszym snapshotem RUNNING.
        phase = str(self._snapshot.get("phase") or "").upper()
        if phase in {"COMPLETED", "FAILED", "STOPPED"}:
            status = phase
        else:
            status = self._first_value(simulation, "status", "state")
            if status is None:
                status = self._first_value(self._snapshot, "status", "state", "phase")
        self.status_var.set(str(status) if status is not None else "")

    def _refresh_progress(self) -> None:
        progress = as_mapping(self._snapshot.get("progress"))
        runtime = as_mapping(self._snapshot.get("runtime"))
        simulation = as_mapping(self._snapshot.get("simulation"))
        configuration = as_mapping(simulation.get("configuration"))
        parameters = as_mapping(configuration.get("parameters"))
        current = self._first_number(
            progress,
            "simulated_time_s",
            "current_simulated_time_s",
            "completed_simulation_time_s",
            "time_s",
        )
        if current is None:
            current = self._first_number(self._snapshot, "simulated_time_s", "current_simulated_time_s")
        elapsed = self._first_number(progress, "real_elapsed_s", "elapsed_real_s")
        if elapsed is None:
            elapsed = self._first_number(runtime, "real_elapsed_s", "elapsed_real_s")
        budget = as_mapping(self._snapshot.get("time_budget"))
        per_run_target = self._first_number(
            budget, "per_run_target_active_real_s"
        )
        active_budget_id = str(budget.get("active_simulation_id") or "")
        simulation_id = str(simulation.get("id") or "")
        real_time_mode = (
            bool(budget.get("enabled"))
            and per_run_target is not None
            and bool(active_budget_id)
            and active_budget_id == simulation_id
        )
        phase_or_status = str(
            self._first_value(self._snapshot, "phase", "status")
            or self._first_value(simulation, "status", "state")
            or ""
        ).upper()
        budget_waiting_for_ready = (
            bool(budget.get("enabled"))
            and per_run_target is not None
            and not real_time_mode
            and phase_or_status not in {"COMPLETED", "FAILED", "STOPPED"}
        )
        if real_time_mode:
            # W tym trybie ``simTime`` jest realnym odczytem z ns-3, lecz nie
            # może decydować o pasku ani ETA: 0.5 s modelowane może kosztować
            # wiele minut zegara rzeczywistego.
            percent = real_time_budget_progress(elapsed, per_run_target)
            target = None
        elif budget_waiting_for_ready:
            # Startup metrics can appear before xApp is ready. They are useful
            # diagnostic observations, but must not consume a user's hard
            # real-time allocation or turn the main bar into simTime/target.
            target = None
            percent = None
        else:
            target = self._first_number(progress, "simulation_time_s", "target_simulation_time_s", "sim_time_s")
            if target is None:
                target = self._first_number(parameters, "simTime", "simulation_time_s")
            if target is None:
                target = self._first_number(self._snapshot, "simulation_time_s", "sim_time_s")
            percent = self._first_number(progress, "percent", "progress_pct", "progress_percent")
            if percent is None and current is not None and target is not None and target > 0:
                percent = 100.0 * current / target
        remaining = self._first_number(progress, "estimated_remaining_s", "eta_s", "remaining_real_s")

        details: List[str] = []
        if (real_time_mode or budget_waiting_for_ready) and current is not None:
            time_text = "{} s (odczyt ns-3)".format(self._number_text(current))
            details.append("Czas symulowany: {}".format(time_text))
        elif current is not None and target is not None:
            time_text = "{} / {} s".format(self._number_text(current), self._number_text(target))
            details.append("Czas symulowany: {}".format(time_text))
        elif current is not None:
            time_text = "{} s".format(self._number_text(current))
            details.append("Czas symulowany: {}".format(time_text))
        else:
            time_text = "—"
        self._progress_fact_vars["sim_time"].set(time_text)

        if percent is not None:
            bounded = max(0.0, min(100.0, percent))
            self.progress.configure(value=bounded)
            run_text = "{:.1f}%".format(bounded)
            details.append(
                "Postęp limitu rzeczywistego: {}".format(run_text)
                if real_time_mode
                else "Postęp: {}".format(run_text)
            )
        else:
            self.progress.configure(value=0.0)
            run_text = "przygotowanie" if budget_waiting_for_ready else "—"
        self._progress_fact_vars["run_progress"].set(run_text)

        elapsed_text = self._duration_text(elapsed) if elapsed is not None else "—"
        self._progress_fact_vars["elapsed"].set(elapsed_text)
        if elapsed is not None:
            details.append("Czas rzeczywisty: {}".format(elapsed_text))

        # Ten element opisuje wyłącznie bieżący run. Cały budżet kolejki
        # pokazujemy osobno w polu KOLEJKA, aby nie mylić np. 20 min całej
        # kolejki z czasem pozostałym pojedynczej symulacji.
        budget_remaining = self._first_number(
            budget, "active_remaining_active_real_s"
        )
        if real_time_mode and budget_remaining is not None:
            eta_value = budget_remaining
            eta_prefix = "limit rzeczywisty ~"
        elif budget_waiting_for_ready:
            eta_value = None
            eta_prefix = ""
        else:
            eta_value = remaining
            eta_prefix = "~"
        eta_text = (
            "{}{}".format(eta_prefix, self._duration_text(eta_value))
            if eta_value is not None
            else "start po gotowości" if budget_waiting_for_ready else "brak danych"
        )
        self._progress_fact_vars["eta"].set(eta_text)
        if eta_value is not None:
            details.append("ETA ns-3: {}".format(eta_text))

        self._progress_fact_vars["queue"].set(self._queue_progress_text(budget))
        completion = format_estimated_completion(
            self._snapshot,
            budget,
            as_mapping(self._snapshot.get("queue_summary")),
        )
        self._progress_fact_vars["queue_finish"].set(completion or "brak danych")
        self.progress_var.set("   |   ".join(details) if details else "Brak danych postępu")

    def _queue_progress_text(self, budget: Mapping[str, Any]) -> str:
        """Pokazuje ogólny ETA tylko wtedy, gdy backend rzeczywiście go poda."""

        queue_summary = as_mapping(self._snapshot.get("queue_summary"))
        overall = self._first_number(self._snapshot, "overall_progress_pct", "queue_progress_pct")
        if overall is None:
            overall = self._first_number(budget, "time_budget_progress_pct", "overall_progress_pct", "progress_pct")
        if overall is None:
            overall = self._first_number(queue_summary, "overall_progress_pct", "progress_pct", "percent")
        eta = self._first_number(
            budget,
            "estimated_queue_remaining_s",
            "planned_remaining_queue_real_s",
            "queue_eta_s",
        )
        if eta is None:
            eta = self._first_number(
                self._snapshot,
                "overall_estimated_remaining_s",
                "queue_estimated_remaining_s",
                "overall_eta_s",
                "queue_eta_s",
            )
        if eta is None:
            eta = self._first_number(queue_summary, "estimated_remaining_s", "overall_estimated_remaining_s", "eta_s")
        parts: List[str] = []
        if overall is not None:
            parts.append("{:.1f}%".format(max(0.0, min(100.0, overall))))
        if eta is not None:
            parts.append("ETA ~{}".format(self._duration_text(eta)))
        return " · ".join(parts) if parts else "—"

    def _refresh_metric_cards(self) -> None:
        values = dict(metric_rows(self._snapshot))
        for key, variable in self._metric_vars.items():
            variable.set(self._metric_value_text(key, values.get(key)))

    def _refresh_runtime_status(self) -> None:
        progress = as_mapping(self._snapshot.get("progress"))
        runtime = as_mapping(self._snapshot.get("runtime"))
        stage = self._first_value(self._snapshot, "stage", "phase")
        if stage is None:
            stage = self._first_value(progress, "stage", "phase")
        if stage is None:
            stage = self._first_value(runtime, "stage", "phase")
        self._status_vars["stage"].set(str(stage) if stage is not None else "—")

        xapp = self._first_value(self._snapshot, "xapp_status", "xapp_state")
        if xapp is None:
            xapp = self._first_value(progress, "xapp_status", "xapp_state")
        if xapp is None:
            xapp = self._first_value(runtime, "xapp_status", "xapp_state")
        self._status_vars["xapp"].set(str(xapp) if xapp is not None else "—")

        collector = self._first_value(self._snapshot, "data_collection", "collector_status")
        if collector is None:
            collector = self._first_value(progress, "data_collection", "collector_status")
        if collector is None:
            collector = self._first_value(runtime, "data_collection", "collector_status")
        data_status = as_mapping(self._snapshot.get("data_status"))
        if not data_status:
            data_status = as_mapping(runtime.get("data_status"))
        source = data_status.get("data_source")
        if collector is not None and source:
            collector = "{} · {}".format(collector, source)
        sample_time = self._first_number(data_status, "aligned_sample_time_s")
        if collector is not None and sample_time is not None:
            collector = "{} · {:.2f} s".format(collector, sample_time)
        missing_metrics = data_status.get("unavailable_metrics")
        if data_status.get("has_live_data") and isinstance(missing_metrics, (list, tuple)) and missing_metrics:
            labels = {
                "delay_ms": "opóźnienie",
                "packet_loss_pct": "packet loss",
            }
            missing_text = ", ".join(labels.get(str(key), str(key)) for key in missing_metrics)
            collector = "{} · brak: {}".format(collector or "DANE LIVE", missing_text)
        result_metadata = as_mapping(as_mapping(self._snapshot.get("result")).get("metadata"))
        coverage = self._first_number(result_metadata, "measurement_coverage_pct")
        if collector is not None and coverage is not None and coverage < 99.9:
            collector = "{} · okno {:.1f}%".format(collector, coverage)
        self._status_vars["collector"].set(str(collector) if collector is not None else "—")

        components = component_rows(self._snapshot)
        normal_shutdown = bool(self._snapshot.get("queue_terminal")) or str(
            self._snapshot.get("phase") or ""
        ).upper() == "COMPLETED"
        if not components:
            component_text = "—"
        elif normal_shutdown:
            # RIC, ns-3 i xApp są celowo kończone przed archiwizacją wyniku.
            # Ich końcowy stan "stopped" nie jest awarią, więc nie wolno
            # zostawiać po poprawnym runie czerwonego alarmu.
            component_text = "Zatrzymane po zakończeniu runu"
        else:
            failures = []
            for name, record in components:
                status = str(self._first_value(record, "status", "state", "health") or "").lower()
                if status in {"failed", "error", "stopped", "exited", "crashed"}:
                    failures.append(name)
            component_text = "Błąd: {}".format(", ".join(failures)) if failures else "{} widoczne".format(len(components))
        self._status_vars["components"].set(component_text)

    def _refresh_xapp_decisions(self) -> None:
        """Wyświetla tylko rekordy przekazane przez limitowany kolektor."""

        snapshot = as_mapping(self._snapshot.get("xapp_decisions"))
        if not snapshot:
            runtime = as_mapping(self._snapshot.get("runtime"))
            live = as_mapping(runtime.get("live"))
            snapshot = as_mapping(live.get("xapp_decisions"))
        raw_events = snapshot.get("events") if snapshot else []
        events = raw_events if isinstance(raw_events, list) else []
        for item in self._decision_tree.get_children():
            self._decision_tree.delete(item)
        # GUI ma swój niższy limit widocznych rekordów. Pełny CSV jest
        # archiwizowany w runie, a kolektor już ogranicza cały payload.
        for raw in events[-60:]:
            record = as_mapping(raw)
            if not record:
                continue
            self._decision_tree.insert(
                "",
                "end",
                values=(
                    self._decision_text(record.get("time_s"), numeric=True),
                    self._decision_text(record.get("event")),
                    self._decision_text(record.get("cell_id"), numeric=True),
                    self._decision_text(record.get("ue_id"), numeric=True),
                    self._decision_text(record.get("target_cell"), numeric=True),
                    self._decision_text(record.get("reason")),
                ),
            )
        status = as_mapping(snapshot.get("data_status")) if snapshot else {}
        source_count = finite_number(status.get("source_file_count"))
        accepted = finite_number(status.get("accepted_rows"))
        limit = finite_number(status.get("retention_limit"))
        if events:
            detail = "{} realnych decyzji".format(int(accepted or len(events)))
            if limit is not None:
                detail += " · historia ograniczona do {} rekordów".format(int(limit))
            self._decision_status_var.set(detail)
        elif source_count:
            self._decision_status_var.set("Plik decyzji xAppa jest dostępny; oczekiwanie na pierwszy rekord.")
        elif snapshot:
            self._decision_status_var.set("xApp nie zapisał jeszcze CSV decyzji.")
        else:
            self._decision_status_var.set("Brak danych decyzji xAppa.")

    @staticmethod
    def _decision_text(value: Any, numeric: bool = False) -> str:
        if value is None or str(value).strip() == "":
            return "—"
        if numeric:
            number = finite_number(value)
            if number is None:
                return "—"
            if number.is_integer():
                return str(int(number))
            return "{:.4g}".format(number)
        return str(value)

    @staticmethod
    def _chart_geometry(canvas: tk.Canvas) -> Tuple[int, int, int, int, int, int]:
        """Zwraca wymiary wykresu i obszaru danych w pikselach."""

        width = max(canvas.winfo_width(), 180)
        height = max(canvas.winfo_height(), 110)
        left, right, top, bottom = 43, 9, 10, 22
        return (
            width,
            height,
            left,
            top,
            max(1, width - left - right),
            max(1, height - top - bottom),
        )

    def _display_chart_points(self, key: str) -> Tuple[List[Tuple[float, float]], str]:
        points = list(self._series.get(key, []))
        points.sort(key=lambda item: item[0])
        return self._display_series(key, points) if points else ([], SERIES_META[key][1])

    def _chart_current_bounds(
        self,
        key: str,
        points: Sequence[Tuple[float, float]],
    ) -> Optional[Tuple[float, float, float, float]]:
        return chart_bounds(points, self._chart_viewports.get(key))

    @staticmethod
    def _event_in_chart(
        event: tk.Event,
        geometry: Tuple[int, int, int, int, int, int],
    ) -> bool:
        _width, _height, left, top, plot_width, plot_height = geometry
        return (
            left <= int(event.x) <= left + plot_width
            and top <= int(event.y) <= top + plot_height
        )

    @staticmethod
    def _wheel_direction(event: tk.Event) -> int:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            return 1 if delta > 0 else -1
        number = int(getattr(event, "num", 0) or 0)
        return 1 if number == 4 else -1 if number == 5 else 0

    def _on_chart_wheel(self, key: str, event: tk.Event) -> Optional[str]:
        canvas = self._chart_canvases.get(key)
        if canvas is None:
            return None
        points, _unit = self._display_chart_points(key)
        geometry = self._chart_geometry(canvas)
        bounds = self._chart_current_bounds(key, points)
        direction = self._wheel_direction(event)
        if not points or bounds is None or not direction or not self._event_in_chart(event, geometry):
            return None
        _width, _height, left, top, plot_width, plot_height = geometry
        x_min, x_max, y_min, y_max = bounds
        factor = 0.80 if direction > 0 else 1.25
        shift_pressed = bool(int(getattr(event, "state", 0) or 0) & 0x0001)
        if shift_pressed:
            focus = y_max - (float(event.y) - top) / plot_height * (y_max - y_min)
            new_y_min = focus - (focus - y_min) * factor
            new_y_max = focus + (y_max - focus) * factor
            viewport = (x_min, x_max, new_y_min, new_y_max)
        else:
            focus = x_min + (float(event.x) - left) / plot_width * (x_max - x_min)
            new_x_min = focus - (focus - x_min) * factor
            new_x_max = focus + (x_max - focus) * factor
            viewport = (new_x_min, new_x_max, y_min, y_max)
        self._chart_viewports[key] = viewport
        self._request_chart_draw()
        return "break"

    def _start_chart_pan(self, key: str, event: tk.Event) -> Optional[str]:
        canvas = self._chart_canvases.get(key)
        if canvas is None or not self._event_in_chart(event, self._chart_geometry(canvas)):
            return None
        self._chart_pan = (key, int(event.x), int(event.y))
        try:
            canvas.configure(cursor="fleur")
        except tk.TclError:
            pass
        return "break"

    def _drag_chart_pan(self, key: str, event: tk.Event) -> Optional[str]:
        if self._chart_pan is None or self._chart_pan[0] != key:
            return None
        canvas = self._chart_canvases.get(key)
        if canvas is None:
            return None
        points, _unit = self._display_chart_points(key)
        bounds = self._chart_current_bounds(key, points)
        if not points or bounds is None:
            return None
        _old_key, old_x, old_y = self._chart_pan
        geometry = self._chart_geometry(canvas)
        _width, _height, _left, _top, plot_width, plot_height = geometry
        x_min, x_max, y_min, y_max = bounds
        delta_x = int(event.x) - old_x
        delta_y = int(event.y) - old_y
        shift_x = -float(delta_x) / plot_width * (x_max - x_min)
        shift_y = float(delta_y) / plot_height * (y_max - y_min)
        self._chart_viewports[key] = (
            x_min + shift_x,
            x_max + shift_x,
            y_min + shift_y,
            y_max + shift_y,
        )
        self._chart_pan = (key, int(event.x), int(event.y))
        self._request_chart_draw()
        return "break"

    def _stop_chart_pan(self, key: str, _event: tk.Event) -> Optional[str]:
        if self._chart_pan is None or self._chart_pan[0] != key:
            return None
        canvas = self._chart_canvases.get(key)
        if canvas is not None:
            try:
                canvas.configure(cursor="")
            except tk.TclError:
                pass
        self._chart_pan = None
        return "break"

    def _reset_one_chart_viewport(self, key: str) -> str:
        self._chart_viewports.pop(key, None)
        self._request_chart_draw()
        return "break"

    def _reset_chart_viewports(self) -> None:
        self._chart_viewports.clear()
        self._chart_pan = None
        self._request_chart_draw()

    def _request_chart_draw(self) -> None:
        if self._chart_draw_pending:
            return
        self._chart_draw_pending = True
        self.after_idle(self._draw_charts)

    def _draw_charts(self) -> None:
        self._chart_draw_pending = False
        for key, canvas in self._chart_canvases.items():
            self._draw_chart(canvas, key)

    def _draw_chart(self, canvas: tk.Canvas, key: str) -> None:
        canvas.delete("all")
        width, height, left, top, plot_width, plot_height = self._chart_geometry(canvas)
        points, unit = self._display_chart_points(key)
        if not points:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Oczekiwanie na realne próbki",
                fill="#59636e",
                font=("TkDefaultFont", 9),
            )
            return
        bounds = self._chart_current_bounds(key, points)
        if bounds is None:
            return
        x_min, x_max, y_min, y_max = bounds
        right = width - left - plot_width
        bottom = height - top - plot_height
        for fraction in (0.0, 0.5, 1.0):
            y = top + plot_height * fraction
            canvas.create_line(left, y, width - right, y, fill="#e5e5e5")
        canvas.create_line(left, top, left, height - bottom, fill="#77818d")
        canvas.create_line(left, height - bottom, width - right, height - bottom, fill="#77818d")
        coordinates: List[float] = []
        for x, y in points:
            px = left + (x - x_min) / (x_max - x_min) * plot_width
            py = top + (y_max - y) / (y_max - y_min) * plot_height
            coordinates.extend((px, py))
        _title, _source_unit, color = SERIES_META[key]
        if len(coordinates) >= 4:
            canvas.create_line(*coordinates, fill=color, width=2, smooth=False)
        elif coordinates:
            canvas.create_oval(
                coordinates[0] - 2,
                coordinates[1] - 2,
                coordinates[0] + 2,
                coordinates[1] + 2,
                fill=color,
                outline="",
            )
        canvas.create_text(left - 4, top, text=self._number_text(y_max), anchor="e", fill="#59636e", font=("TkDefaultFont", 8))
        canvas.create_text(left - 4, height - bottom, text=self._number_text(y_min), anchor="e", fill="#59636e", font=("TkDefaultFont", 8))
        canvas.create_text(left, height - 8, text="{} s".format(self._number_text(x_min)), anchor="w", fill="#59636e", font=("TkDefaultFont", 8))
        canvas.create_text(width - right, height - 8, text="{} s".format(self._number_text(x_max)), anchor="e", fill="#59636e", font=("TkDefaultFont", 8))
        latest = "{} {}".format(self._number_text(points[-1][1]), unit)
        canvas.create_text(width - right, top, text=latest, anchor="ne", fill="#202020", font=("TkDefaultFont", 8, "bold"))
        if key in self._chart_viewports:
            canvas.create_text(
                left + 3,
                top + 2,
                text="widok ręczny",
                anchor="nw",
                fill="#59636e",
                font=("TkDefaultFont", 7),
            )

    def _display_series(self, key: str, points: List[Tuple[float, float]]) -> Tuple[List[Tuple[float, float]], str]:
        """Dopasowuje jednostkę wykresu bez zmieniania danych modelu."""

        if key == "energy_efficiency_bit_per_j":
            return [(x, y / 1_000_000.0) for x, y in points], "Mbit/J"
        return points, SERIES_META[key][1]

    def _metric_value_text(self, key: str, value: Any) -> str:
        number = finite_number(value)
        if number is None:
            return "—"
        if key == "energy_efficiency_bit_per_j":
            return "{} Mbit/J".format(self._number_text(number / 1_000_000.0))
        if key == "total_energy_j":
            if abs(number) >= 1_000.0:
                return "{} kJ".format(self._number_text(number / 1_000.0))
            return "{} J".format(self._number_text(number))
        if key == "delivered_mbit":
            if abs(number) >= 1_000.0:
                return "{} Gbit".format(self._number_text(number / 1_000.0))
            return "{} Mbit".format(self._number_text(number))
        _label, unit = METRIC_META.get(key, (key.replace("_", " "), ""))
        return "{}{}".format(self._number_text(number), " {}".format(unit) if unit else "")

    @staticmethod
    def _first_value(source: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in source and source.get(key) is not None:
                return source.get(key)
        return None

    def _first_number(self, source: Mapping[str, Any], *keys: str) -> Optional[float]:
        return next((number for number in (finite_number(self._first_value(source, key)) for key in keys) if number is not None), None)

    @staticmethod
    def _number_text(value: float) -> str:
        return "{:.4g}".format(value)

    @staticmethod
    def _duration_text(value: float) -> str:
        seconds = max(0, int(round(value)))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)
