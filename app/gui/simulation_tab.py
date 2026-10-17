from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, Optional

from app.simulation.models import Simulation

from .scrollable import ScrollableFrame


class SimulationTab(ttk.Frame):
    """Jedna czytelna zakładka konfiguracji symulacji."""

    INTEGER_PARAMETERS = {
        "N_Ues",
        "N_MmWaveEnbNodes",
        "RngRun",
        "UdpPacketSizeBytes",
        "UdpIntervalUs",
    }

    FIELDS = (
        ("simTime", "Czas symulacji [s]", "scenario"),
        ("N_Ues", "Liczba UE", "scenario"),
        ("N_MmWaveEnbNodes", "Liczba gNB", "scenario"),
        ("RngRun", "RngRun", "scenario"),
        ("UdpPacketSizeBytes", "Rozmiar pakietu [B]", "traffic"),
        ("UdpIntervalUs", "Odstęp pakietów [µs]", "traffic"),
        ("Bandwidth", "Szerokość pasma [Hz]", "radio"),
        ("CenterFrequency", "Częstotliwość [Hz]", "radio"),
        ("IntersideDistanceUEs", "Odległość UE [m]", "radio"),
        ("IntersideDistanceCells", "Odległość gNB [m]", "radio"),
        ("indicationPeriodicity", "Okres KPM [s]", "technical"),
    )

    def __init__(
        self,
        parent: tk.Misc,
        simulation: Simulation,
        application_config: Dict[str, Any],
        on_duplicate: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.simulation = simulation
        self.application_config = application_config
        self.on_duplicate = on_duplicate
        self.parameter_vars: Dict[str, tk.StringVar] = {}
        self.parameter_entries: Dict[str, ttk.Entry] = {}
        self.xapp_environment_vars: Dict[str, tk.Variable] = {}
        self._scenario_labels: Dict[str, str] = {}
        self._xapp_labels: Dict[str, str] = {"Wyłączony": "none"}
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.scroller = ScrollableFrame(self, content_padding=14)
        self.scroller.grid(row=0, column=0, sticky="nsew")
        content = self.scroller.content
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)

        header = ttk.Frame(content)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Nazwa:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=self.simulation.name)
        ttk.Entry(header, textvariable=self.name_var, width=38).grid(row=0, column=1, sticky="ew", padx=(8, 12))
        ttk.Button(header, text="Duplikuj", command=lambda: self.on_duplicate(self.simulation.id)).grid(row=0, column=2, sticky="e")
        self.status_var = tk.StringVar(value="Stan: {}".format(self.simulation.status.value))
        ttk.Label(header, textvariable=self.status_var).grid(row=0, column=3, sticky="e", padx=(12, 0))

        scenario_box = ttk.LabelFrame(content, text="SCENARIUSZ", padding=10)
        scenario_box.grid(row=1, column=0, sticky="new", padx=(0, 7), pady=(0, 10))
        traffic_box = ttk.LabelFrame(content, text="RUCH", padding=10)
        traffic_box.grid(row=1, column=1, sticky="new", padx=(7, 0), pady=(0, 10))
        radio_box = ttk.LabelFrame(content, text="RADIO", padding=10)
        radio_box.grid(row=2, column=0, sticky="new", padx=(0, 7), pady=(0, 10))
        technical_box = ttk.LabelFrame(content, text="PARAMETRY TECHNICZNE", padding=10)
        technical_box.grid(row=2, column=1, sticky="new", padx=(7, 0), pady=(0, 10))
        xapp_box = ttk.LabelFrame(content, text="XAPP", padding=10)
        xapp_box.grid(row=3, column=0, columnspan=2, sticky="ew")

        self._add_scenario_selector(scenario_box)
        groups = {
            "scenario": scenario_box,
            "traffic": traffic_box,
            "radio": radio_box,
            "technical": technical_box,
        }
        row_counts = {key: 1 if key == "scenario" else 0 for key in groups}
        for key, label, group in self.FIELDS:
            row = row_counts[group]
            self._add_parameter(groups[group], row, key, label)
            row_counts[group] += 1
        self._add_xapp_selector(xapp_box)
        self._refresh_available_fields()

    def _add_scenario_selector(self, frame: ttk.LabelFrame) -> None:
        ttk.Label(frame, text="Scenariusz:").grid(row=0, column=0, sticky="w", pady=3)
        values = []
        for key, item in (self.application_config.get("scenarios") or {}).items():
            label = str((item or {}).get("label") or key)
            text = "{} — {}".format(key, label)
            self._scenario_labels[text] = str(key)
            values.append(text)
        current = next((text for text, key in self._scenario_labels.items() if key == self.simulation.configuration.scenario), values[0] if values else "")
        self.scenario_var = tk.StringVar(value=current)
        self.scenario_choice = ttk.Combobox(frame, state="readonly", values=values, textvariable=self.scenario_var, width=34)
        self.scenario_choice.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.scenario_choice.bind("<<ComboboxSelected>>", lambda _event: self._refresh_available_fields())
        frame.columnconfigure(1, weight=1)

    def _add_parameter(self, frame: ttk.LabelFrame, row: int, key: str, label: str) -> None:
        ttk.Label(frame, text=label + ":").grid(row=row, column=0, sticky="w", pady=3)
        value = self.simulation.configuration.parameters.get(key, "")
        variable = tk.StringVar(value=str(value))
        entry = ttk.Entry(frame, textvariable=variable, width=24)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        frame.columnconfigure(1, weight=1)
        self.parameter_vars[key] = variable
        self.parameter_entries[key] = entry

    def _add_xapp_selector(self, frame: ttk.LabelFrame) -> None:
        ttk.Label(frame, text="Tryb xApp:").grid(row=0, column=0, sticky="w", pady=3)
        values = ["Wyłączony"]
        for key, item in (self.application_config.get("xapps") or {}).items():
            if key == "none":
                continue
            text = "{} — {}".format(key, str((item or {}).get("label") or key))
            self._xapp_labels[text] = str(key)
            values.append(text)
        current = next((text for text, key in self._xapp_labels.items() if key == self.simulation.configuration.xapp), "Wyłączony")
        self.xapp_var = tk.StringVar(value=current)
        self.xapp_choice = ttk.Combobox(frame, state="readonly", values=values, textvariable=self.xapp_var, width=42)
        self.xapp_choice.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        self.xapp_choice.bind("<<ComboboxSelected>>", lambda _event: self._refresh_xapp_parameters())
        ttk.Label(
            frame,
            text="Parametry są dostępne wyłącznie wtedy, gdy wybrany xApp deklaruje je w konfiguracji.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.xapp_parameters_frame = ttk.Frame(frame)
        self.xapp_parameters_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.xapp_parameters_frame.columnconfigure(1, weight=1)
        frame.columnconfigure(1, weight=1)
        self._refresh_xapp_parameters()

    def _refresh_xapp_parameters(self) -> None:
        for child in self.xapp_parameters_frame.winfo_children():
            child.destroy()
        self.xapp_environment_vars = {}
        xapp = self._xapp_labels.get(self.xapp_var.get(), "none")
        specifications = dict(((self.application_config.get("xapps") or {}).get(xapp) or {}).get("parameters") or {})
        if not specifications:
            text = "xApp jest wyłączony." if xapp == "none" else "Ten xApp nie deklaruje parametrów konfigurowalnych."
            ttk.Label(self.xapp_parameters_frame, text=text, style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
            return
        for row, (key, specification) in enumerate(specifications.items()):
            unit = str(specification.get("unit") or "")
            label = "{}{}".format(specification.get("label") or key, " [{}]".format(unit) if unit else "")
            ttk.Label(self.xapp_parameters_frame, text=label + ":").grid(row=row * 2, column=0, sticky="w", pady=(4 if row else 0, 0))
            value = self.simulation.configuration.xapp_environment.get(key, specification.get("default", ""))
            kind = str(specification.get("type") or "text").lower()
            if kind in {"bool", "boolean"}:
                variable: tk.Variable = tk.BooleanVar(value=self._as_bool(value))
                widget = ttk.Checkbutton(self.xapp_parameters_frame, text="Włączone", variable=variable)
            else:
                variable = tk.StringVar(value=str(value))
                widget = ttk.Entry(self.xapp_parameters_frame, textvariable=variable, width=18)
            widget.grid(row=row * 2, column=1, sticky="ew", padx=(8, 0), pady=(4 if row else 0, 0))
            description = str(specification.get("description") or "")
            if description:
                ttk.Label(self.xapp_parameters_frame, text=description, style="Muted.TLabel", wraplength=600).grid(row=row * 2 + 1, column=0, columnspan=2, sticky="w")
            self.xapp_environment_vars[key] = variable

    def _refresh_available_fields(self) -> None:
        scenario = self._selected_scenario()
        item = (self.application_config.get("scenarios") or {}).get(scenario) or {}
        allowed = {str(value) for value in item.get("allowed_params") or []}
        for key, entry in self.parameter_entries.items():
            enabled = not allowed or key in allowed
            entry.configure(state="normal" if enabled else "disabled")

    def _selected_scenario(self) -> str:
        return self._scenario_labels.get(self.scenario_var.get(), self.simulation.configuration.scenario)

    def commit(self) -> Simulation:
        self.simulation.name = self.name_var.get().strip() or "Symulacja"
        scenario = self._selected_scenario()
        item = (self.application_config.get("scenarios") or {}).get(scenario) or {}
        allowed = {str(value) for value in item.get("allowed_params") or []}
        # Parametry edytowane w builderze, ale niewidoczne w tym skróconym
        # widoku, pozostają częścią konfiguracji zamiast znikać przy zapisie.
        parameters: Dict[str, Any] = {
            key: value
            for key, value in self.simulation.configuration.parameters.items()
            if not allowed or key in allowed
        }
        for key, variable in self.parameter_vars.items():
            if allowed and key not in allowed:
                continue
            text = variable.get().strip()
            if text == "":
                parameters.pop(key, None)
            else:
                parameters[key] = self._convert(key, text)
        self.simulation.configuration.scenario = scenario
        self.simulation.configuration.parameters = parameters
        xapp = self._xapp_labels.get(self.xapp_var.get(), "none")
        self.simulation.configuration.xapp = xapp
        specifications = dict(((self.application_config.get("xapps") or {}).get(xapp) or {}).get("parameters") or {})
        environment: Dict[str, Any] = {}
        for key, variable in self.xapp_environment_vars.items():
            specification = specifications.get(key) or {}
            environment[key] = self._convert_xapp(variable.get(), str(specification.get("type") or "text"))
        self.simulation.configuration.xapp_environment = environment
        return self.simulation

    def apply_configuration(self, configuration: SimulationConfiguration) -> None:
        """Nakłada zapisany scenariusz na tę zakładkę bez uruchamiania runu."""

        self.simulation.configuration = copy.deepcopy(configuration)
        selected_scenario = next(
            (label for label, key in self._scenario_labels.items() if key == configuration.scenario),
            self.scenario_var.get(),
        )
        self.scenario_var.set(selected_scenario)
        self._refresh_available_fields()
        for key, variable in self.parameter_vars.items():
            variable.set(str(configuration.parameters.get(key, "")))
        selected_xapp = next(
            (label for label, key in self._xapp_labels.items() if key == configuration.xapp),
            "Wyłączony",
        )
        self.xapp_var.set(selected_xapp)
        self._refresh_xapp_parameters()
        self.update_status()

    def update_status(self) -> None:
        detail = self.simulation.error or self.simulation.status.value
        self.status_var.set("Stan: {}".format(detail))

    @classmethod
    def _convert(cls, key: str, value: str) -> Any:
        try:
            return int(value) if key in cls.INTEGER_PARAMETERS else float(value)
        except ValueError:
            return value

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _convert_xapp(value: Any, kind: str) -> Any:
        if kind.lower() in {"bool", "boolean"}:
            return bool(value)
        text = str(value).strip()
        try:
            if kind.lower() in {"int", "integer"}:
                return int(float(text))
            if kind.lower() in {"float", "number"}:
                return float(text)
        except ValueError:
            return text
        return text
