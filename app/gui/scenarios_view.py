from __future__ import annotations

import copy
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from app.services.scenario_service import SavedScenario, ScenarioService
from app.simulation.models import SimulationConfiguration
from app.simulation.session_controller import default_configuration

from .scrollable import ScrollableFrame


class ToolTip:
    """Mały tooltip Tkinter bez zewnętrznej biblioteki."""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show, add=True)
        widget.bind("<Leave>", self._hide, add=True)

    def _show(self, _event: Any = None) -> None:
        if self.window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            self.window = tk.Toplevel(self.widget)
            self.window.wm_overrideredirect(True)
            self.window.wm_geometry("+{}+{}".format(x, y))
            tk.Label(
                self.window,
                text=self.text,
                justify="left",
                background="#fffbdc",
                foreground="#202020",
                relief="solid",
                borderwidth=1,
                padx=7,
                pady=5,
                wraplength=340,
            ).pack()
        except tk.TclError:
            self.window = None

    def _hide(self, _event: Any = None) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
        self.window = None


class ScenariosView(ttk.Frame):
    """Lista trwałych scenariuszy i prosty builder realnych parametrów."""

    GROUPS = ("GENERAL", "UE", "TRAFFIC", "RADIO", "ENERGY", "XAPP")
    GROUP_LABELS = {
        "GENERAL": "GENERAL",
        "UE": "UE",
        "TRAFFIC": "TRAFFIC",
        "RADIO": "RADIO",
        "ENERGY": "ENERGY",
        "XAPP": "XAPP",
    }

    def __init__(
        self,
        parent: tk.Misc,
        service: ScenarioService,
        application_config: Dict[str, Any],
        on_apply: Optional[Callable[[SavedScenario], None]] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(parent, padding=12, **kwargs)
        self.service = service
        self.application_config = application_config
        self.on_apply = on_apply
        self._items: Dict[str, SavedScenario] = {}
        self._current_id: Optional[str] = None
        self._parameter_vars: Dict[str, tk.Variable] = {}
        self._parameter_meta: Dict[str, Dict[str, Any]] = {}
        self._xapp_vars: Dict[str, tk.Variable] = {}
        self._scenario_labels: Dict[str, str] = {}
        self._xapp_labels: Dict[str, str] = {"Wyłączony": "none"}
        self._build()
        self.refresh()
        self._new()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.panes = ttk.Panedwindow(self, orient="vertical")
        self.panes.grid(row=0, column=0, sticky="nsew")
        left = ttk.LabelFrame(self.panes, text="ZAPISANE SCENARIUSZE", padding=8)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        columns = ("name", "backend", "xapp")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=4)
        for key, label, width in (("name", "Nazwa", 180), ("backend", "Backend", 115), ("xapp", "xApp", 105)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key == "name")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal_scroll.set)
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        buttons = ttk.Frame(left)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for column in range(3):
            buttons.columnconfigure(column, weight=1)
        ttk.Button(buttons, text="Nowy", command=self._new).grid(row=0, column=0, sticky="ew")
        ttk.Button(buttons, text="Kopiuj", command=self._duplicate).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(buttons, text="Usuń", command=self._delete).grid(row=0, column=2, sticky="ew")
        ttk.Button(buttons, text="Importuj…", command=self._import).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(buttons, text="Eksportuj…", command=self._export).grid(row=1, column=1, sticky="ew", padx=5, pady=(5, 0))
        ttk.Button(buttons, text="Odśwież", command=self.refresh).grid(row=1, column=2, sticky="ew", pady=(5, 0))

        right = ttk.Frame(self.panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(left)
        self.panes.add(right)
        self._sash_positioned = False
        self.panes.bind("<Configure>", self._position_sash, add=True)
        title = ttk.Frame(right)
        title.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        title.columnconfigure(1, weight=1)
        ttk.Label(title, text="SCENARIO BUILDER", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(title, text="Nazwa:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.name_var = tk.StringVar(value="")
        ttk.Entry(title, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", padx=(7, 10), pady=(8, 0))
        ttk.Button(title, text="Zapisz", style="Accent.TButton", command=self._save).grid(row=1, column=2, sticky="e", pady=(8, 0))
        ttk.Label(title, text="Opis:").grid(row=2, column=0, sticky="nw", pady=(6, 0))
        self.description_text = tk.Text(
            title,
            height=2,
            wrap="word",
            background="#ffffff",
            foreground="#202020",
            insertbackground="#202020",
            relief="flat",
        )
        self.description_text.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        description_scroll = ttk.Scrollbar(title, orient="vertical", command=self.description_text.yview)
        self.description_text.configure(yscrollcommand=description_scroll.set)
        description_scroll.grid(row=2, column=3, sticky="ns", pady=(6, 0))

        selector = ttk.LabelFrame(right, text="BACKEND", padding=8)
        selector.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        selector.columnconfigure(1, weight=1)
        ttk.Label(selector, text="Scenariusz backendu:").grid(row=0, column=0, sticky="w")
        self.backend_var = tk.StringVar(value="")
        self.backend_choice = ttk.Combobox(selector, state="readonly", textvariable=self.backend_var)
        self.backend_choice.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.backend_choice.bind("<<ComboboxSelected>>", lambda _event: self._rebuild_fields())

        self.group_notebook = ttk.Notebook(right)
        self.group_notebook.grid(row=2, column=0, sticky="nsew")
        bottom = ttk.Frame(right)
        bottom.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(bottom, text="Zastosuj do aktywnej symulacji", command=self._apply).pack(side="left")
        self.form_status = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.form_status, style="Muted.TLabel").pack(side="right")
        self._build_selector_values()

    def _position_sash(self, _event: Any = None) -> None:
        if self._sash_positioned:
            return
        self._sash_positioned = True

        def position() -> None:
            try:
                if self.panes.winfo_height() > 100:
                    self.panes.sashpos(0, 205)
            except tk.TclError:
                pass

        self.after_idle(position)

    def _build_selector_values(self) -> None:
        labels: List[str] = []
        for key, item in (self.application_config.get("scenarios") or {}).items():
            label = "{} — {}".format(key, (item or {}).get("label") or key)
            self._scenario_labels[label] = str(key)
            labels.append(label)
        self.backend_choice.configure(values=labels)
        xapp_labels = ["Wyłączony"]
        for key, item in (self.application_config.get("xapps") or {}).items():
            if key == "none":
                continue
            label = "{} — {}".format(key, (item or {}).get("label") or key)
            self._xapp_labels[label] = str(key)
            xapp_labels.append(label)
        self._xapp_values = xapp_labels

    def refresh(self) -> None:
        self._items = {item.id: item for item in self.service.list()}
        current = self._current_id
        for item in self.tree.get_children():
            self.tree.delete(item)
        for item in self._items.values():
            self.tree.insert(
                "",
                "end",
                iid=item.id,
                values=(item.name, item.configuration.scenario, item.configuration.xapp),
            )
        if current and self.tree.exists(current):
            self.tree.selection_set(current)

    def reload_application_config(self, application_config: Dict[str, Any]) -> None:
        """Odświeża listę realnych backendów/xAppów po udanym buildzie."""

        self.application_config = application_config
        self.service.application_config = dict(application_config)
        self._scenario_labels = {}
        self._xapp_labels = {"Wyłączony": "none"}
        self._build_selector_values()
        selected = self._selected_backend()
        if selected:
            self._rebuild_fields()

    def _new(self) -> None:
        self._current_id = None
        self.name_var.set("Nowy scenariusz")
        self.description_text.delete("1.0", "end")
        default = default_configuration(self.application_config)
        self._set_configuration(default)
        self.form_status.set("Nowy scenariusz — parametry pochodzą z config.json.")

    def _on_select(self, _event: Any = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        item = self._items.get(selected[0])
        if item is None:
            return
        self._current_id = item.id
        self.name_var.set(item.name)
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", item.description)
        self._set_configuration(copy.deepcopy(item.configuration))
        self.form_status.set("Edytujesz zapisany scenariusz.")

    def _set_configuration(self, configuration: SimulationConfiguration) -> None:
        selected = next(
            (label for label, key in self._scenario_labels.items() if key == configuration.scenario),
            next(iter(self._scenario_labels), ""),
        )
        self.backend_var.set(selected)
        self._pending_configuration = copy.deepcopy(configuration)
        self._rebuild_fields()

    def _rebuild_fields(self) -> None:
        configuration = getattr(self, "_pending_configuration", None)
        if not isinstance(configuration, SimulationConfiguration):
            configuration = default_configuration(self.application_config)
        scenario_key = self._selected_backend()
        if scenario_key != configuration.scenario:
            configuration = SimulationConfiguration(
                scenario=scenario_key,
                parameters=dict(configuration.parameters),
                xapp=configuration.xapp,
                xapp_environment=dict(configuration.xapp_environment),
            )
        for page in self.group_notebook.winfo_children():
            page.destroy()
        self._parameter_vars = {}
        self._parameter_meta = {}
        self._xapp_vars = {}
        allowed = self.service.allowed_parameters(scenario_key)
        grouped: Dict[str, List[str]] = {group: [] for group in self.GROUPS}
        for key in allowed:
            group = str(self.service.parameter_meta(key).get("group") or "GENERAL").upper()
            grouped.setdefault(group if group in grouped else "GENERAL", []).append(key)
        for group in self.GROUPS:
            scroll_page = ScrollableFrame(self.group_notebook, content_padding=10)
            self.group_notebook.add(scroll_page, text=self.GROUP_LABELS[group])
            page = scroll_page.content
            if group == "ENERGY" and not grouped[group]:
                ttk.Label(
                    page,
                    text="Aktywny backend nie deklaruje parametrów modelu energii dostępnych z wiersza poleceń.",
                    style="Muted.TLabel",
                    wraplength=500,
                    justify="left",
                ).grid(row=0, column=0, sticky="w")
            else:
                page.columnconfigure(1, weight=1)
                for row, key in enumerate(grouped[group]):
                    self._add_parameter(page, row, key, configuration.parameters.get(key))
            if group == "XAPP":
                self._add_xapp_fields(page, len(grouped[group]) + (1 if grouped[group] else 0), configuration)
            scroll_page.scroll_to_top()

    def _add_parameter(self, parent: ttk.Frame, row: int, key: str, value: Any) -> None:
        meta = self.service.parameter_meta(key)
        self._parameter_meta[key] = meta
        label_text = "{}{}".format(meta.get("label") or key, " [{}]".format(meta["unit"]) if meta.get("unit") else "")
        label = ttk.Label(parent, text=label_text + ":")
        label.grid(row=row * 2, column=0, sticky="w", pady=(0 if row == 0 else 7, 0))
        ToolTip(label, str(meta.get("description") or ""))
        kind = str(meta.get("kind") or "text")
        current = value if value not in (None, "") else meta.get("default", "")
        if kind == "bool":
            variable: tk.Variable = tk.BooleanVar(value=self._as_bool(current))
            widget = ttk.Checkbutton(parent, text="Włączone", variable=variable)
        else:
            variable = tk.StringVar(value=str(current))
            widget = ttk.Entry(parent, textvariable=variable)
        widget.grid(row=row * 2, column=1, sticky="ew", padx=(10, 0), pady=(0 if row == 0 else 7, 0))
        hint = self._hint(meta)
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=480).grid(row=row * 2 + 1, column=0, columnspan=2, sticky="w")
        self._parameter_vars[key] = variable

    def _add_xapp_fields(self, parent: ttk.Frame, row: int, configuration: SimulationConfiguration) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Separator(parent).grid(row=row * 2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(parent, text="xApp:").grid(row=row * 2 + 1, column=0, sticky="w")
        current = next(
            (label for label, key in self._xapp_labels.items() if key == configuration.xapp),
            "Wyłączony",
        )
        self.xapp_var = tk.StringVar(value=current)
        choice = ttk.Combobox(parent, state="readonly", values=self._xapp_values, textvariable=self.xapp_var)
        choice.grid(row=row * 2 + 1, column=1, sticky="ew", padx=(10, 0))
        choice.bind("<<ComboboxSelected>>", lambda _event: self._rebuild_xapp_parameters(parent, row * 2 + 2, configuration.xapp_environment))
        self._rebuild_xapp_parameters(parent, row * 2 + 2, configuration.xapp_environment)

    def _rebuild_xapp_parameters(self, parent: ttk.Frame, start_row: int, saved: Dict[str, Any]) -> None:
        for child in list(parent.grid_slaves()):
            info = child.grid_info()
            try:
                row = int(info.get("row", -1))
            except (TypeError, ValueError):
                row = -1
            if row >= start_row:
                child.destroy()
        self._xapp_vars = {}
        xapp_key = self._selected_xapp()
        specifications = dict(((self.application_config.get("xapps") or {}).get(xapp_key) or {}).get("parameters") or {})
        if not specifications:
            text = "Wybrany xApp nie deklaruje parametrów konfigurowalnych." if xapp_key != "none" else "xApp jest wyłączony."
            ttk.Label(parent, text=text, style="Muted.TLabel").grid(row=start_row, column=0, columnspan=2, sticky="w", pady=(7, 0))
            return
        for index, (key, spec) in enumerate(specifications.items()):
            row = start_row + index * 2
            label = "{}{}".format(spec.get("label") or key, " [{}]".format(spec["unit"]) if spec.get("unit") else "")
            label_widget = ttk.Label(parent, text=label + ":")
            label_widget.grid(row=row, column=0, sticky="w", pady=(7, 0))
            ToolTip(label_widget, str(spec.get("description") or ""))
            value = saved.get(key, spec.get("default", ""))
            kind = str(spec.get("type") or "text").lower()
            if kind in {"boolean", "bool"}:
                variable: tk.Variable = tk.BooleanVar(value=self._as_bool(value))
                widget = ttk.Checkbutton(parent, text="Włączone", variable=variable)
            else:
                variable = tk.StringVar(value=str(value))
                widget = ttk.Entry(parent, textvariable=variable)
            widget.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(7, 0))
            if spec.get("description"):
                ttk.Label(parent, text="Domyślnie: {}".format(spec.get("default", "")), style="Muted.TLabel").grid(row=row + 1, column=0, columnspan=2, sticky="w")
            self._xapp_vars[key] = variable

    def _save(self) -> None:
        try:
            configuration = self._read_configuration()
            description = self.description_text.get("1.0", "end-1c")
            if self._current_id:
                item = self.service.update(self._current_id, self.name_var.get(), configuration, description)
            else:
                item = self.service.create(self.name_var.get(), configuration, description)
            self._current_id = item.id
            self.refresh()
            self.tree.selection_set(item.id)
            self.form_status.set("Zapisano scenariusz.")
        except Exception as exc:
            messagebox.showerror("Nie można zapisać scenariusza", str(exc), parent=self.winfo_toplevel())

    def _duplicate(self) -> None:
        if not self._current_id:
            messagebox.showinfo("Scenariusze", "Wybierz scenariusz do skopiowania.", parent=self.winfo_toplevel())
            return
        try:
            item = self.service.duplicate(self._current_id)
            self._current_id = item.id
            self.refresh()
            self.tree.selection_set(item.id)
            self._on_select()
        except Exception as exc:
            messagebox.showerror("Nie można skopiować", str(exc), parent=self.winfo_toplevel())

    def _delete(self) -> None:
        if not self._current_id:
            return
        item = self._items.get(self._current_id)
        if item is None:
            return
        prompt = (
            "Usunąć zapisany scenariusz „{}”?\n\n"
            "Usunięty zostanie tylko ten zapis parametrów. Runy i wyniki pozostaną bez zmian."
        ).format(item.name)
        if not messagebox.askyesno("Usuń scenariusz", prompt, parent=self.winfo_toplevel()):
            return
        try:
            self.service.delete(item.id)
            self._current_id = None
            self.refresh()
            self._new()
            self.form_status.set("Usunięto zapis scenariusza „{}”.".format(item.name))
        except Exception as exc:
            messagebox.showerror("Nie można usunąć", str(exc), parent=self.winfo_toplevel())

    def _export(self) -> None:
        if not self._current_id:
            messagebox.showinfo("Scenariusze", "Wybierz scenariusz do eksportu.", parent=self.winfo_toplevel())
            return
        destination = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="Eksportuj scenariusz", defaultextension=".json", filetypes=(("JSON", "*.json"),)
        )
        if not destination:
            return
        try:
            self.service.export(self._current_id, Path(destination))
            self.form_status.set("Wyeksportowano scenariusz do {}.".format(Path(destination).name))
        except Exception as exc:
            messagebox.showerror("Nie można wyeksportować", str(exc), parent=self.winfo_toplevel())

    def _import(self) -> None:
        source = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="Importuj scenariusz", filetypes=(("JSON", "*.json"), ("Wszystkie pliki", "*.*")))
        if not source:
            return
        try:
            item = self.service.import_file(Path(source))
            self._current_id = item.id
            self.refresh()
            self.tree.selection_set(item.id)
            self._on_select()
            self.form_status.set("Zaimportowano scenariusz „{}”.".format(item.name))
        except Exception as exc:
            messagebox.showerror("Nie można zaimportować", str(exc), parent=self.winfo_toplevel())

    def _apply(self) -> None:
        try:
            configuration = self._read_configuration()
            item = SavedScenario(
                id=self._current_id or "",
                name=self.name_var.get().strip() or "Scenariusz",
                description=self.description_text.get("1.0", "end-1c"),
                configuration=configuration,
                created_at="",
                updated_at="",
            )
            if self.on_apply:
                self.on_apply(item)
                self.form_status.set("Zastosowano do aktywnej symulacji.")
        except Exception as exc:
            messagebox.showerror("Nie można zastosować", str(exc), parent=self.winfo_toplevel())

    def _read_configuration(self) -> SimulationConfiguration:
        parameters: Dict[str, Any] = {}
        for key, variable in self._parameter_vars.items():
            meta = self._parameter_meta[key]
            value = variable.get()
            parameters[key] = self._convert(value, str(meta.get("kind") or "text"))
        environment: Dict[str, Any] = {}
        specifications = dict(((self.application_config.get("xapps") or {}).get(self._selected_xapp()) or {}).get("parameters") or {})
        for key, variable in self._xapp_vars.items():
            spec = specifications.get(key) or {}
            environment[key] = self._convert(variable.get(), str(spec.get("type") or "text"))
        raw = SimulationConfiguration(
            scenario=self._selected_backend(),
            parameters=parameters,
            xapp=self._selected_xapp(),
            xapp_environment=environment,
        )
        return self.service.normalise_configuration(raw)

    def _selected_backend(self) -> str:
        return self._scenario_labels.get(self.backend_var.get(), next(iter(self._scenario_labels.values()), ""))

    def _selected_xapp(self) -> str:
        variable = getattr(self, "xapp_var", None)
        return self._xapp_labels.get(variable.get() if variable is not None else "Wyłączony", "none")

    @staticmethod
    def _convert(value: Any, kind: str) -> Any:
        if kind.lower() in {"bool", "boolean"}:
            return bool(value)
        text = str(value).strip()
        if kind.lower() in {"int", "integer"}:
            return int(float(text))
        if kind.lower() in {"float", "number"}:
            return float(text)
        return text

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _hint(meta: Dict[str, Any]) -> str:
        chunks = []
        if meta.get("description"):
            chunks.append(str(meta["description"]))
        if "default" in meta:
            chunks.append("domyślnie: {}".format(meta["default"]))
        lower, upper = meta.get("minimum"), meta.get("maximum")
        if lower is not None and upper is not None:
            chunks.append("zakres: {}–{}".format(lower, upper))
        elif lower is not None:
            chunks.append("min.: {}".format(lower))
        elif upper is not None:
            chunks.append("max.: {}".format(upper))
        return " | ".join(chunks)
