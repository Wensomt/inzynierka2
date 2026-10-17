"""Widok faktycznej topologii radiowej zapisywanej przez scenariusz ns-3.

Moduł nie wyznacza połączenia UE z najbliższą komórką.  Linie UE--komórka
powstają wyłącznie, gdy scenariusz albo backend przekazał identyfikator
``serving_cell``.  Dzięki temu mapa pozostaje użyteczna również wtedy, gdy
przypisania nie są jeszcze dostępne.

``TopologyView`` można zasilać podczas działania przez::

    view.update_snapshot({
        "topology": {
            "cells": [{"id": 2, "x": 2000, "y": 2000,
                       "tx_sleeping": False}],
            "ues": [{"id": 1, "x": 2010, "y": 1990,
                     "serving_cell": 2}],
            "sample_time_s": 12.4,
            "source": "gnbs.txt + ue_position.txt",
        }
    })

Po zakończeniu uruchomienia ``parse_topology_artifacts`` odczytuje te same,
zarchiwizowane pliki.  Układ planowany jest dostępny tylko dla scenariusza
``optimized``, którego kod rzeczywiście definiuje geometrię komórek.
"""

from __future__ import annotations

import csv
import math
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


DEFAULT_AREA_SIZE_M = 4000.0
MAP_PADDING_PX = 30.0
MIN_VIEWPORT_SPAN_M = 1.0


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _identifier(value: Any) -> str:
    """Zachowuje identyfikator, ale normalizuje CSV-owe ``2.0`` do ``2``."""

    text = str(value if value is not None else "").strip()
    number = _finite_number(text)
    if number is not None and number.is_integer():
        return str(int(number))
    return text


def _truth_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_value(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)
    return None


def normalize_viewport(bounds: Sequence[float], minimum_span_m: float = MIN_VIEWPORT_SPAN_M) -> Tuple[float, float, float, float]:
    """Zwraca poprawne granice widoku o niezerowym rozmiarze.

    Funkcja nie zależy od Tkintera, dzięki czemu zasady powiększania mapy
    można sprawdzić bez otwierania okna.  Gdy backend przekaże punktową albo
    odwróconą granicę, widok nadal pozostaje używalny zamiast dzielić przez
    zero podczas rysowania.
    """

    if len(bounds) != 4:
        raise ValueError("Widok mapy wymaga czterech granic.")
    values = [_finite_number(value) for value in bounds]
    if any(value is None for value in values):
        raise ValueError("Granice widoku muszą być skończonymi liczbami.")
    x_min, x_max, y_min, y_max = values  # type: ignore[misc]
    if x_max < x_min:
        x_min, x_max = x_max, x_min
    if y_max < y_min:
        y_min, y_max = y_max, y_min
    minimum = max(0.001, float(minimum_span_m))
    if x_max - x_min < minimum:
        center = (x_min + x_max) / 2.0
        x_min, x_max = center - minimum / 2.0, center + minimum / 2.0
    if y_max - y_min < minimum:
        center = (y_min + y_max) / 2.0
        y_min, y_max = center - minimum / 2.0, center + minimum / 2.0
    return (x_min, x_max, y_min, y_max)


def zoom_viewport(
    bounds: Sequence[float],
    focus_x: float,
    focus_y: float,
    factor: float,
    minimum_span_m: float = MIN_VIEWPORT_SPAN_M,
) -> Tuple[float, float, float, float]:
    """Powiększa widok wokół punktu świata, zachowując go pod kursorem.

    ``factor > 1`` przybliża, a ``factor < 1`` oddala.  Jest to operacja na
    metrach świata, nie na pikselach, więc rezultat nie zależy od bieżącego
    rozmiaru okna.
    """

    x_min, x_max, y_min, y_max = normalize_viewport(bounds, minimum_span_m)
    zoom = _finite_number(factor)
    if zoom is None or zoom <= 0.0:
        raise ValueError("Współczynnik przybliżenia musi być dodatni.")
    focus_x_value = _finite_number(focus_x)
    focus_y_value = _finite_number(focus_y)
    if focus_x_value is None or focus_y_value is None:
        raise ValueError("Punkt przybliżenia musi mieć skończone współrzędne.")
    width, height = x_max - x_min, y_max - y_min
    target_width = max(float(minimum_span_m), width / zoom)
    target_height = max(float(minimum_span_m), height / zoom)
    relative_x = (focus_x_value - x_min) / width
    relative_y = (focus_y_value - y_min) / height
    return normalize_viewport(
        (
            focus_x_value - relative_x * target_width,
            focus_x_value + (1.0 - relative_x) * target_width,
            focus_y_value - relative_y * target_height,
            focus_y_value + (1.0 - relative_y) * target_height,
        ),
        minimum_span_m,
    )


def pan_viewport(
    bounds: Sequence[float],
    delta_x_m: float,
    delta_y_m: float,
) -> Tuple[float, float, float, float]:
    """Przesuwa widok o wektor w metrach, nie zmieniając skali."""

    x_min, x_max, y_min, y_max = normalize_viewport(bounds)
    dx = _finite_number(delta_x_m)
    dy = _finite_number(delta_y_m)
    if dx is None or dy is None:
        raise ValueError("Przesunięcie widoku musi mieć skończone współrzędne.")
    return (x_min + dx, x_max + dx, y_min + dy, y_max + dy)


@dataclass(frozen=True)
class TopologyCell:
    """Pojedyncza komórka o pozycji podanej przez scenariusz."""

    id: str
    x: float
    y: float
    role: str = "gNB"
    tx_sleeping: Optional[bool] = None
    sample_time_s: Optional[float] = None


@dataclass(frozen=True)
class TopologyUe:
    """Pozycja UE oraz raportowane przez scenariusz przypisanie komórki."""

    id: str
    x: float
    y: float
    role: str = "UE"
    serving_cell: Optional[str] = None
    sample_time_s: Optional[float] = None
    # Ograniczony ślad z rzeczywistych, kolejnych ramek ns-3. Nie tworzymy
    # interpolacji ani przewidywania położenia.
    trail: Tuple[Tuple[float, float], ...] = ()


@dataclass
class TopologySnapshot:
    """Jedna spójna próbka mapy do renderowania w Tkinterze."""

    cells: List[TopologyCell] = field(default_factory=list)
    ues: List[TopologyUe] = field(default_factory=list)
    sample_time_s: Optional[float] = None
    source: str = "Brak danych topologii"
    mode: str = "unavailable"  # reported, planned, unavailable
    message: str = ""
    bounds: Optional[Tuple[float, float, float, float]] = None

    @property
    def has_reported_associations(self) -> bool:
        return any(ue.serving_cell for ue in self.ues)

    def to_payload(self) -> Dict[str, Any]:
        """Prosty format, który może zostać przekazany w zdarzeniu live."""

        return {
            "cells": [
                {
                    "id": cell.id,
                    "x": cell.x,
                    "y": cell.y,
                    "role": cell.role,
                    "tx_sleeping": cell.tx_sleeping,
                    "sample_time_s": cell.sample_time_s,
                }
                for cell in self.cells
            ],
            "ues": [
                {
                    "id": ue.id,
                    "x": ue.x,
                    "y": ue.y,
                    "role": ue.role,
                    "serving_cell": ue.serving_cell,
                    "sample_time_s": ue.sample_time_s,
                    "trail": [{"x": x, "y": y} for x, y in ue.trail],
                }
                for ue in self.ues
            ],
            "sample_time_s": self.sample_time_s,
            "source": self.source,
            "mode": self.mode,
            "message": self.message,
            "bounds": self.bounds,
        }


def _read_csv(path: Optional[Path]) -> List[Dict[str, str]]:
    if path is None or not path.is_file():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            return [
                {
                    str(key).strip(): "" if value is None else str(value).strip()
                    for key, value in row.items()
                    if key is not None
                }
                for row in csv.DictReader(handle)
            ]
    except (OSError, csv.Error):
        return []


def _artifact_path(directory: Union[str, Path], name: str) -> Optional[Path]:
    root = Path(directory)
    candidates = (root / "topology" / name, root / name)
    return next((path for path in candidates if path.is_file()), None)


def _row_sample_time(row: Mapping[str, Any]) -> Optional[float]:
    """Wyprowadza czas ns-3 z ``timestamp`` i ``simid`` zapisanych przez C++."""

    direct = _finite_number(_first_value(row, "Time_s", "time_s", "time"))
    if direct is not None:
        return direct
    timestamp = _finite_number(row.get("timestamp"))
    simid = _finite_number(row.get("simid"))
    if timestamp is None or simid is None:
        return None
    seconds = (timestamp - simid) / 1000.0
    return seconds if seconds >= -0.001 else None


def _latest_records(
    records: Iterable[Tuple[str, Optional[float], Any]],
    at_sim_time_s: Optional[float],
) -> Dict[str, Any]:
    """Wybiera ostatni rekord każdego obiektu, nie wykraczając poza próbkę."""

    chosen: Dict[str, Tuple[Optional[float], Any]] = {}
    for identifier, sample_time, record in records:
        if not identifier:
            continue
        if at_sim_time_s is not None and sample_time is not None and sample_time > at_sim_time_s:
            continue
        previous = chosen.get(identifier)
        if previous is None:
            chosen[identifier] = (sample_time, record)
            continue
        previous_time = previous[0]
        if sample_time is not None and (previous_time is None or sample_time >= previous_time):
            chosen[identifier] = (sample_time, record)
    return {identifier: value[1] for identifier, value in chosen.items()}


def _snapshot_time(cells: Sequence[TopologyCell], ues: Sequence[TopologyUe]) -> Optional[float]:
    values = [
        item.sample_time_s
        for item in list(cells) + list(ues)
        if item.sample_time_s is not None
    ]
    return max(values) if values else None


def parse_topology_artifacts(
    directory: Union[str, Path],
    at_sim_time_s: Optional[float] = None,
) -> TopologySnapshot:
    """Odczytuje ``gnbs.txt``, ``enbs.txt`` i ``ue_position.txt`` z runu.

    Pliki są zapisem backendu, nie modelem przybliżonym.  Wiersz granicy
    ``id=0`` z plików stacji jest wykorzystywany wyłącznie do ustalenia skali
    mapy i nigdy nie jest prezentowany jako komórka.
    """

    gnb_path = _artifact_path(directory, "gnbs.txt")
    enb_path = _artifact_path(directory, "enbs.txt")
    ue_path = _artifact_path(directory, "ue_position.txt")
    gnb_rows, enb_rows, ue_rows = _read_csv(gnb_path), _read_csv(enb_path), _read_csv(ue_path)

    bounds_x: List[float] = []
    bounds_y: List[float] = []

    def parse_cells(rows: Sequence[Mapping[str, str]], role: str) -> List[Tuple[str, Optional[float], TopologyCell]]:
        parsed: List[Tuple[str, Optional[float], TopologyCell]] = []
        for row in rows:
            identifier = _identifier(_first_value(row, "id", "cell", "cell_id"))
            x, y = _finite_number(row.get("x")), _finite_number(row.get("y"))
            if identifier == "0":
                if x is not None:
                    bounds_x.append(x)
                if y is not None:
                    bounds_y.append(y)
                continue
            if not identifier or x is None or y is None:
                continue
            state = _truth_value(row.get("ESstate") or row.get("es_state") or row.get("tx_sleeping"))
            parsed.append(
                (
                    identifier,
                    _row_sample_time(row),
                    TopologyCell(
                        id=identifier,
                        x=x,
                        y=y,
                        role=role,
                        # W my_energy_scenario_optimized.cc ESstate jest
                        # zapisywane jako (TxPower == 0), czyli stan uśpienia TX.
                        tx_sleeping=state,
                        sample_time_s=_row_sample_time(row),
                    ),
                )
            )
        return parsed

    # ``gnbs.txt`` opisuje mmWave gNB wraz ze stanem TX.  ``enbs.txt`` jest
    # uzupełnieniem dla LTE/anchorów, więc gNB ma pierwszeństwo przy tym samym ID.
    gnb_selected = _latest_records(parse_cells(gnb_rows, "gNB"), at_sim_time_s)
    enb_selected = _latest_records(parse_cells(enb_rows, "eNB"), at_sim_time_s)
    cells_by_id: Dict[str, TopologyCell] = dict(enb_selected)
    cells_by_id.update(gnb_selected)
    cells = sorted(cells_by_id.values(), key=lambda item: _sort_identifier(item.id))

    ue_records: List[Tuple[str, Optional[float], TopologyUe]] = []
    for row in ue_rows:
        identifier = _identifier(_first_value(row, "id", "imsi"))
        x, y = _finite_number(row.get("x")), _finite_number(row.get("y"))
        if not identifier or x is None or y is None:
            continue
        serving = _identifier(_first_value(row, "cell", "serving_cell", "serving_cell_id"))
        if not serving or serving == "0":
            serving = None
        sample_time = _row_sample_time(row)
        ue_records.append(
            (
                identifier,
                sample_time,
                TopologyUe(
                    id=identifier,
                    x=x,
                    y=y,
                    role=str(row.get("type") or "UE"),
                    serving_cell=serving,
                    sample_time_s=sample_time,
                ),
            )
        )
    ues = sorted(_latest_records(ue_records, at_sim_time_s).values(), key=lambda item: _sort_identifier(item.id))

    source_names = [
        name for name, path in (("gnbs.txt", gnb_path), ("enbs.txt", enb_path), ("ue_position.txt", ue_path))
        if path is not None
    ]
    if not source_names:
        return TopologySnapshot(message="Nie zapisano jeszcze artefaktów topologii.")
    bounds: Optional[Tuple[float, float, float, float]] = None
    if bounds_x and bounds_y:
        max_x, max_y = max(bounds_x), max(bounds_y)
        if max_x > 0.0 and max_y > 0.0:
            bounds = (0.0, max_x, 0.0, max_y)
    message = "Pozycje i przypisania pochodzą z plików scenariusza."
    if ues and not any(ue.serving_cell for ue in ues):
        message = "Scenariusz zapisał pozycje UE, ale nie raportuje przypisania komórki w tej próbce."
    return TopologySnapshot(
        cells=cells,
        ues=ues,
        sample_time_s=_snapshot_time(cells, ues),
        source=" + ".join(source_names),
        mode="reported",
        message=message,
        bounds=bounds,
    )


def planned_topology(configuration: Any) -> TopologySnapshot:
    """Tworzy wyłącznie potwierdzony w kodzie plan dla scenariusza optimized.

    Losowe pozycje UE oraz przypisania nie są tworzone w tym trybie.  Ich
    konkretne wartości pojawiają się dopiero w ``ue_position.txt``.
    """

    raw = _mapping(configuration)
    parameters = _mapping(raw.get("parameters"))
    if not parameters and hasattr(configuration, "parameters"):
        parameters = _mapping(getattr(configuration, "parameters"))
    scenario = str(raw.get("scenario") or getattr(configuration, "scenario", ""))
    if scenario != "optimized":
        return TopologySnapshot(
            source="Konfiguracja scenariusza",
            mode="unavailable",
            message="Ten scenariusz nie udostępnia w konfiguracji potwierdzonego modelu położeń."
        )
    count_value = _finite_number(parameters.get("N_MmWaveEnbNodes"))
    distance_value = _finite_number(parameters.get("IntersideDistanceCells"))
    if count_value is None or distance_value is None:
        return TopologySnapshot(
            source="Konfiguracja scenariusza",
            mode="unavailable",
            message="Brakuje N_MmWaveEnbNodes lub IntersideDistanceCells potrzebnych do planu mapy."
        )
    count = max(0, int(count_value))
    if count < 1:
        return TopologySnapshot(
            source="Konfiguracja scenariusza",
            mode="unavailable",
            message="Liczba gNB musi być dodatnia, aby narysować układ."
        )
    center = DEFAULT_AREA_SIZE_M / 2.0
    cells = [TopologyCell(id="gNB #1", x=center, y=center, role="gNB")]
    outer_count = count - 1
    if outer_count:
        for index in range(outer_count):
            angle = (2.0 * math.pi * index) / outer_count
            cells.append(
                TopologyCell(
                    id="gNB #{}".format(index + 2),
                    x=center + distance_value * math.cos(angle),
                    y=center + distance_value * math.sin(angle),
                    role="gNB",
                )
            )
    return TopologySnapshot(
        cells=cells,
        source="Konfiguracja optimized",
        mode="planned",
        message=(
            "Plan rozmieszczenia gNB z kodu scenariusza; pozycje UE i przypisania "
            "pojawią się dopiero po odebraniu danych ns-3."
        ),
        bounds=(0.0, DEFAULT_AREA_SIZE_M, 0.0, DEFAULT_AREA_SIZE_M),
    )


def _reported_trail(value: Any, limit: int = 16) -> Tuple[Tuple[float, float], ...]:
    """Waliduje ograniczoną historię rzeczywiście raportowanych pozycji."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    points: List[Tuple[float, float]] = []
    for raw_point in value[-max(1, int(limit)):]:
        point = _mapping(raw_point)
        x, y = _finite_number(point.get("x")), _finite_number(point.get("y"))
        if x is None or y is None:
            continue
        candidate = (x, y)
        if not points or points[-1] != candidate:
            points.append(candidate)
    return tuple(points)


def topology_from_payload(payload: Mapping[str, Any]) -> TopologySnapshot:
    """Waliduje lekki payload przeznaczony dla zdarzeń live.

    Rekord bez prawidłowego ``x`` i ``y`` nie jest renderowany.  Brak pola
    ``serving_cell`` pozostaje brakiem danych — widok nie zastępuje go komórką
    najbliższą geograficznie.
    """

    raw = _mapping(payload)
    cells: List[TopologyCell] = []
    for raw_cell in raw.get("cells") or []:
        item = _mapping(raw_cell)
        identifier = _identifier(_first_value(item, "id", "cell_id", "name"))
        x, y = _finite_number(item.get("x")), _finite_number(item.get("y"))
        if not identifier or x is None or y is None:
            continue
        cells.append(
            TopologyCell(
                id=identifier,
                x=x,
                y=y,
                role=str(item.get("role") or item.get("kind") or "gNB"),
                tx_sleeping=_truth_value(
                    item.get("tx_sleeping", item.get("sleeping", item.get("es_state")))
                ),
                sample_time_s=_finite_number(item.get("sample_time_s") or item.get("time_s")),
            )
        )
    ues: List[TopologyUe] = []
    for raw_ue in raw.get("ues") or []:
        item = _mapping(raw_ue)
        identifier = _identifier(_first_value(item, "id", "imsi", "name"))
        x, y = _finite_number(item.get("x")), _finite_number(item.get("y"))
        if not identifier or x is None or y is None:
            continue
        serving = _identifier(_first_value(item, "serving_cell", "serving_cell_id", "cell"))
        if serving == "0":
            serving = ""
        ues.append(
            TopologyUe(
                id=identifier,
                x=x,
                y=y,
                role=str(item.get("role") or item.get("type") or "UE"),
                serving_cell=serving or None,
                sample_time_s=_finite_number(item.get("sample_time_s") or item.get("time_s")),
                trail=_reported_trail(item.get("trail")),
            )
        )
    raw_bounds = raw.get("bounds")
    bounds: Optional[Tuple[float, float, float, float]] = None
    if isinstance(raw_bounds, Sequence) and not isinstance(raw_bounds, (str, bytes)) and len(raw_bounds) == 4:
        values = [_finite_number(value) for value in raw_bounds]
        if all(value is not None for value in values):
            bounds = (values[0], values[1], values[2], values[3])  # type: ignore[arg-type]
    mode = str(raw.get("mode") or "reported")
    payload_time = _finite_number(_first_value(raw, "sample_time_s", "time_s"))
    return TopologySnapshot(
        cells=sorted(cells, key=lambda item: _sort_identifier(item.id)),
        ues=sorted(ues, key=lambda item: _sort_identifier(item.id)),
        sample_time_s=payload_time if payload_time is not None else _snapshot_time(cells, ues),
        source=str(raw.get("source") or "Dane live ns-3"),
        mode=mode,
        message=str(raw.get("message") or "Pozycje i przypisania przekazane przez backend."),
        bounds=bounds,
    )


def _sort_identifier(value: str) -> Tuple[int, Union[int, str]]:
    number = _finite_number(value)
    if number is not None and number.is_integer():
        return (0, int(number))
    return (1, value.lower())


class TopologyView(ttk.Frame):
    """Canvas mapy fizycznej oraz tabela raportowanych powiązań UE--komórka."""

    CELL_COLORS = ("#4f9bd1", "#9474d1", "#cf8b52", "#52b985", "#c46b90", "#63afba")

    def __init__(self, parent: tk.Misc, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self._snapshot = TopologySnapshot()
        self._draw_pending = False
        # ``None`` oznacza automatyczne dopasowanie do aktualnych granic
        # danych.  Po ręcznym zoomie/panowaniu zapisujemy granice w metrach,
        # dzięki czemu następna próbka live nie "odskakuje" użytkownikowi.
        self._viewport_bounds: Optional[Tuple[float, float, float, float]] = None
        self._viewport_manual = False
        self._pan_anchor: Optional[Tuple[int, int]] = None
        self.source_var = tk.StringVar(value="Brak danych topologii")
        self.detail_var = tk.StringVar(value="")
        self.viewport_var = tk.StringVar(value="Widok: automatyczne dopasowanie")
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="TOPOLOGIA RADIOWA", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.source_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e")

        map_box = ttk.LabelFrame(self, text="UKŁAD FIZYCZNY", padding=7)
        map_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        map_box.columnconfigure(0, weight=1)
        map_box.rowconfigure(1, weight=1)
        map_tools = ttk.Frame(map_box)
        map_tools.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        map_tools.columnconfigure(0, weight=1)
        ttk.Label(map_tools, textvariable=self.viewport_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            map_tools,
            text="Kółko: przybliż · przeciągnij: przesuń",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="e", padx=(8, 8))
        ttk.Button(map_tools, text="−", width=3, command=lambda: self._zoom_center(1.0 / 1.25)).grid(
            row=0, column=2, sticky="e"
        )
        ttk.Button(map_tools, text="+", width=3, command=lambda: self._zoom_center(1.25)).grid(
            row=0, column=3, sticky="e", padx=(4, 0)
        )
        ttk.Button(map_tools, text="Dopasuj", command=self.fit_to_data).grid(row=0, column=4, sticky="e", padx=(4, 0))
        self.canvas = tk.Canvas(map_box, background="#171b20", highlightthickness=0, height=290)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._request_draw(), add=True)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start, add=True)
        self.canvas.bind("<B1-Motion>", self._on_pan_move, add=True)
        self.canvas.bind("<ButtonRelease-1>", self._on_pan_end, add=True)
        # Windows/macOS oraz X11 zgłaszają kółko w różnych formatach.
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel, add=True)
        self.canvas.bind("<Button-4>", lambda event: self._on_mouse_wheel(event, 1), add=True)
        self.canvas.bind("<Button-5>", lambda event: self._on_mouse_wheel(event, -1), add=True)

        associations = ttk.LabelFrame(self, text="UE → KOMÓRKA", padding=7)
        associations.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        associations.columnconfigure(0, weight=1)
        associations.rowconfigure(0, weight=1)
        holder = ttk.Frame(associations)
        holder.grid(row=0, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.association_tree = ttk.Treeview(
            holder,
            columns=("ue", "cell", "tx"),
            show="headings",
            height=11,
        )
        for column, title, width in (
            ("ue", "UE", 70),
            ("cell", "Komórka", 100),
            ("tx", "Stan TX", 110),
        ):
            self.association_tree.heading(column, text=title)
            self.association_tree.column(column, anchor="w", width=width, stretch=True)
        tree_y = ttk.Scrollbar(holder, orient="vertical", command=self.association_tree.yview)
        tree_x = ttk.Scrollbar(holder, orient="horizontal", command=self.association_tree.xview)
        self.association_tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.association_tree.grid(row=0, column=0, sticky="nsew")
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x.grid(row=1, column=0, sticky="ew")
        ttk.Label(associations, textvariable=self.detail_var, style="Muted.TLabel", justify="left", wraplength=340).grid(
            row=1, column=0, sticky="ew", pady=(7, 0)
        )

    def clear(self) -> None:
        self._reset_viewport()
        self.set_snapshot(TopologySnapshot())

    def set_configuration(self, configuration: Any) -> None:
        """Pokazuje potwierdzony plan przed pierwszą próbką ns-3."""

        self.set_snapshot(planned_topology(configuration))

    def load_artifacts(self, directory: Union[str, Path], at_sim_time_s: Optional[float] = None) -> TopologySnapshot:
        """Czyta lokalne artefakty runu; wywołuj poza wątkiem GUI przy dużych plikach."""

        snapshot = parse_topology_artifacts(directory, at_sim_time_s=at_sim_time_s)
        self.set_snapshot(snapshot)
        return snapshot

    def update_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Obsługuje zdarzenie live lub sam payload ``topology``.

        Gdy nie ma jeszcze danych topologii, konfiguracja służy tylko do
        narysowania znanego planu — nie nadpisuje odebranej próbki rzeczywistej.
        """

        incoming = _mapping(snapshot)
        topology = _mapping(incoming.get("topology"))
        runtime = _mapping(incoming.get("runtime"))
        if not topology:
            topology = _mapping(runtime.get("topology"))
        if topology:
            self.set_snapshot(topology_from_payload(topology))
            return
        if self._snapshot.mode == "reported":
            return
        simulation = _mapping(incoming.get("simulation"))
        configuration = _mapping(simulation.get("configuration")) or _mapping(incoming.get("configuration"))
        if configuration:
            self.set_configuration(configuration)

    def set_snapshot(self, snapshot: TopologySnapshot) -> None:
        # Pusty widok zawsze kasuje ręczne przesunięcie.  Nowy run nie
        # dziedziczy zatem przypadkowego zbliżenia mapy z poprzedniego.
        previous_time = self._snapshot.sample_time_s
        incoming_time = snapshot.sample_time_s
        timeline_restarted = (
            previous_time is not None
            and incoming_time is not None
            and incoming_time + 0.001 < previous_time
        )
        if not snapshot.cells and not snapshot.ues or timeline_restarted:
            self._reset_viewport()
        elif not self._viewport_manual:
            # Automatyczny widok ma zawsze obejmować aktualne granice danych.
            self._viewport_bounds = None
        self._snapshot = snapshot
        self._refresh_text()
        self._refresh_associations()
        self._request_draw()

    def fit_to_data(self) -> None:
        """Wraca do automatycznego, pełnego widoku danych backendu."""

        self._reset_viewport()
        self._request_draw()

    def _reset_viewport(self) -> None:
        self._viewport_bounds = None
        self._viewport_manual = False
        self._pan_anchor = None
        self.viewport_var.set("Widok: automatyczne dopasowanie")

    def _current_viewport(self) -> Tuple[float, float, float, float]:
        return self._viewport_bounds or self._bounds()

    def _set_manual_viewport(self, bounds: Sequence[float]) -> None:
        self._viewport_bounds = normalize_viewport(bounds)
        self._viewport_manual = True
        width = self._viewport_bounds[1] - self._viewport_bounds[0]
        height = self._viewport_bounds[3] - self._viewport_bounds[2]
        self.viewport_var.set("Widok ręczny: {:.0f} × {:.0f} m".format(width, height))

    def _zoom_center(self, factor: float) -> None:
        """Obsługuje przyciski +/- względem środka aktualnie oglądanego kadru."""

        if not self._snapshot.cells and not self._snapshot.ues:
            return
        x_min, x_max, y_min, y_max = self._current_viewport()
        self._set_manual_viewport(
            zoom_viewport(
                (x_min, x_max, y_min, y_max),
                (x_min + x_max) / 2.0,
                (y_min + y_max) / 2.0,
                factor,
            )
        )
        self._request_draw()

    def _on_mouse_wheel(self, event: tk.Event, direction: Optional[int] = None) -> str:
        """Przybliża mapę wokół kursora bez resetowania widoku live."""

        if not self._snapshot.cells and not self._snapshot.ues:
            return "break"
        if direction is None:
            delta = getattr(event, "delta", 0)
            direction = 1 if delta > 0 else -1 if delta < 0 else 0
        if not direction:
            return "break"
        world_x, world_y = self._canvas_to_world(event.x, event.y)
        factor = 1.18 if direction > 0 else 1.0 / 1.18
        self._set_manual_viewport(zoom_viewport(self._current_viewport(), world_x, world_y, factor))
        self._request_draw()
        return "break"

    def _on_pan_start(self, event: tk.Event) -> str:
        if self._snapshot.cells or self._snapshot.ues:
            self._pan_anchor = (int(event.x), int(event.y))
            self.canvas.configure(cursor="fleur")
        return "break"

    def _on_pan_move(self, event: tk.Event) -> str:
        if self._pan_anchor is None:
            return "break"
        previous_x, previous_y = self._pan_anchor
        delta_x_px, delta_y_px = int(event.x) - previous_x, int(event.y) - previous_y
        if not delta_x_px and not delta_y_px:
            return "break"
        scale = self._view_scale(self._current_viewport())
        # Przeciąganie przesuwa zawartość w stronę kursora.  Oś świata Y jest
        # odwrotna do osi ekranu, więc znak przesunięcia Y jest przeciwny.
        self._set_manual_viewport(
            pan_viewport(
                self._current_viewport(),
                -float(delta_x_px) / scale,
                float(delta_y_px) / scale,
            )
        )
        self._pan_anchor = (int(event.x), int(event.y))
        self._request_draw()
        return "break"

    def _on_pan_end(self, _event: tk.Event) -> str:
        self._pan_anchor = None
        self.canvas.configure(cursor="")
        return "break"

    def _refresh_text(self) -> None:
        pieces = [self._snapshot.source]
        if self._snapshot.sample_time_s is not None:
            pieces.append("próbka t = {:.3g} s".format(self._snapshot.sample_time_s))
        if self._snapshot.mode == "planned":
            pieces.append("układ planowany")
        self.source_var.set("  |  ".join(piece for piece in pieces if piece))
        self.detail_var.set(self._snapshot.message or "Brak dodatkowych informacji.")

    def _refresh_associations(self) -> None:
        for item in self.association_tree.get_children():
            self.association_tree.delete(item)
        cell_states = {cell.id: self._tx_text(cell.tx_sleeping) for cell in self._snapshot.cells}
        for ue in self._snapshot.ues:
            cell = ue.serving_cell or "brak raportu"
            state = cell_states.get(ue.serving_cell or "", "brak pozycji komórki") if ue.serving_cell else "—"
            self.association_tree.insert("", "end", values=("UE {}".format(ue.id), cell, state))
        if not self._snapshot.ues and self._snapshot.mode == "reported":
            self.association_tree.insert("", "end", values=("—", "brak pozycji UE", "—"))

    @staticmethod
    def _tx_text(tx_sleeping: Optional[bool]) -> str:
        if tx_sleeping is True:
            return "TX uśpiony"
        if tx_sleeping is False:
            return "TX aktywny"
        return "brak stanu"

    def _request_draw(self) -> None:
        if self._draw_pending:
            return
        self._draw_pending = True
        self.after_idle(self._draw)

    def _draw(self) -> None:
        self._draw_pending = False
        canvas = self.canvas
        canvas.delete("all")
        width = max(220, canvas.winfo_width())
        height = max(180, canvas.winfo_height())
        if not self._snapshot.cells and not self._snapshot.ues:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Brak pozycji radiowych w bieżącej próbce",
                fill="#aab4c0",
            )
            return
        x_min, x_max, y_min, y_max = self._current_viewport()
        scale, offset_x, offset_y = self._view_transform((x_min, x_max, y_min, y_max), width, height)

        def point(x: float, y: float) -> Tuple[float, float]:
            return (offset_x + (x - x_min) * scale, height - offset_y - (y - y_min) * scale)

        self._draw_grid(point, x_min, x_max, y_min, y_max)
        cells = {cell.id: cell for cell in self._snapshot.cells}
        # Linia jest rysowana tylko dla jawnie raportowanego serving_cell.
        for ue in self._snapshot.ues:
            cell = cells.get(ue.serving_cell or "")
            if cell is not None:
                ux, uy = point(ue.x, ue.y)
                cx, cy = point(cell.x, cell.y)
                canvas.create_line(ux, uy, cx, cy, fill=self._cell_color(cell.id), dash=(3, 3), width=1)

        # Cienki ślad pozwala zobaczyć faktyczny ruch UE nawet wtedy, gdy
        # różnica między pojedynczymi ramkami jest mała względem obszaru 4 km.
        # Punkty pochodzą tylko z kolejnych zapisów ns-3 w payloadzie live.
        for ue in self._snapshot.ues:
            if len(ue.trail) < 2:
                continue
            coordinates: List[float] = []
            for trail_x, trail_y in ue.trail:
                canvas_x, canvas_y = point(trail_x, trail_y)
                coordinates.extend((canvas_x, canvas_y))
            if len(coordinates) >= 4:
                canvas.create_line(
                    *coordinates,
                    fill=self._cell_color(ue.serving_cell),
                    width=1,
                    stipple="gray50",
                )

        for cell in self._snapshot.cells:
            x, y = point(cell.x, cell.y)
            color = "#697581" if cell.tx_sleeping else self._cell_color(cell.id)
            radius = 8 if cell.role.lower() == "gnb" else 6
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="#e5e9ef", width=1)
            suffix = " (TX off)" if cell.tx_sleeping else ""
            canvas.create_text(x + 10, y - 10, text="{} {}{}".format(cell.role, cell.id, suffix), anchor="sw", fill="#e5e9ef", font=("TkDefaultFont", 8))

        for index, ue in enumerate(self._snapshot.ues):
            x, y = point(ue.x, ue.y)
            color = self._cell_color(ue.serving_cell) if ue.serving_cell else "#55b87a"
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline="#101419")
            if len(self._snapshot.ues) <= 28 or index < 12:
                canvas.create_text(x + 6, y + 6, text="UE {}".format(ue.id), anchor="nw", fill="#dce3eb", font=("TkDefaultFont", 8))

        canvas.create_text(8, height - 8, text="gNB/eNB: komórka   •   UE: urządzenie   - - -: raportowane przypisanie   ···: ślad ruchu", anchor="sw", fill="#aab4c0", font=("TkDefaultFont", 8))

    def _view_transform(
        self,
        bounds: Sequence[float],
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> Tuple[float, float, float]:
        """Przelicza widok w metrach na skalę i przesunięcie Canvasu."""

        x_min, x_max, y_min, y_max = normalize_viewport(bounds)
        canvas_width = max(220.0, float(self.canvas.winfo_width()) if width is None else float(width))
        canvas_height = max(180.0, float(self.canvas.winfo_height()) if height is None else float(height))
        usable_width = max(1.0, canvas_width - 2.0 * MAP_PADDING_PX)
        usable_height = max(1.0, canvas_height - 2.0 * MAP_PADDING_PX)
        scale = min(usable_width / (x_max - x_min), usable_height / (y_max - y_min))
        offset_x = (canvas_width - scale * (x_max - x_min)) / 2.0
        offset_y = (canvas_height - scale * (y_max - y_min)) / 2.0
        return scale, offset_x, offset_y

    def _view_scale(self, bounds: Sequence[float]) -> float:
        return self._view_transform(bounds)[0]

    def _canvas_to_world(self, canvas_x: float, canvas_y: float) -> Tuple[float, float]:
        """Zamienia pozycję kursora na metry świata dla zoomu wokół kursora."""

        x_min, x_max, y_min, y_max = self._current_viewport()
        width = max(220.0, float(self.canvas.winfo_width()))
        height = max(180.0, float(self.canvas.winfo_height()))
        scale, offset_x, offset_y = self._view_transform((x_min, x_max, y_min, y_max), width, height)
        return (
            x_min + (float(canvas_x) - offset_x) / scale,
            y_min + (height - offset_y - float(canvas_y)) / scale,
        )

    def _draw_grid(
        self,
        point: Any,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> None:
        canvas = self.canvas
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = x_min + (x_max - x_min) * fraction
            y = y_min + (y_max - y_min) * fraction
            left_x, bottom_y = point(x, y_min)
            right_x, top_y = point(x_max, y)
            canvas.create_line(left_x, 0, left_x, canvas.winfo_height(), fill="#252c34")
            canvas.create_line(0, bottom_y, canvas.winfo_width(), bottom_y, fill="#252c34")
            if fraction in (0.0, 0.5, 1.0):
                canvas.create_text(left_x + 2, canvas.winfo_height() - 21, text="{:.0f} m".format(x), anchor="sw", fill="#7e8995", font=("TkDefaultFont", 7))
                canvas.create_text(3, bottom_y - 2, text="{:.0f}".format(y), anchor="sw", fill="#7e8995", font=("TkDefaultFont", 7))

    def _bounds(self) -> Tuple[float, float, float, float]:
        supplied = self._snapshot.bounds
        if supplied is not None:
            x_min, x_max, y_min, y_max = supplied
            if x_max > x_min and y_max > y_min:
                return supplied
        points = [(item.x, item.y) for item in self._snapshot.cells] + [(item.x, item.y) for item in self._snapshot.ues]
        x_values, y_values = [point[0] for point in points], [point[1] for point in points]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        span = max(x_max - x_min, y_max - y_min, 100.0)
        margin = span * 0.12
        return (x_min - margin, x_max + margin, y_min - margin, y_max + margin)

    def _cell_color(self, identifier: Optional[str]) -> str:
        text = str(identifier or "")
        # Stabilny indeks niezależny od losowego hashowania procesu Pythona.
        index = sum(ord(character) for character in text) % len(self.CELL_COLORS)
        return self.CELL_COLORS[index]


__all__ = [
    "TopologyCell",
    "TopologySnapshot",
    "TopologyUe",
    "TopologyView",
    "normalize_viewport",
    "pan_viewport",
    "parse_topology_artifacts",
    "planned_topology",
    "topology_from_payload",
    "zoom_viewport",
]
