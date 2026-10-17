"""Widok kontenera sesji, procesów laboratoryjnych i ich logów.

Klasa jest wyłącznie odbiorcą danych z warstwy usługowej: nie wywołuje Docker
SDK ani nie uruchamia poleceń. Dzięki temu wszystkie operacje blokujące mogą
pozostać w workerach, a Tkinter otrzymuje tylko snapshoty i nowe rekordy logu.
"""

from __future__ import annotations

import json
import re
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MAX_VISIBLE_LOG_LINES = 4000


def as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def event_payload(event: Any) -> Dict[str, Any]:
    """Normalizuje słownik lub obiekt zdarzenia z opcjonalnym payloadem."""

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


def log_record_text(value: Any) -> Optional[str]:
    """Zwraca tekst rekordu bez tworzenia komunikatu zastępczego."""

    if isinstance(value, Mapping):
        for key in ("text", "message", "line", "log"):
            if value.get(key) is not None:
                return str(value.get(key))
        return None
    if value is None:
        return None
    return str(value)


def filter_log_lines(lines: Iterable[str], query: str = "", level: str = "Wszystkie") -> List[str]:
    """Czysty helper używany przez renderowanie i testy bez tworzenia widgetów."""

    query_lower = query.strip().lower()
    level_upper = level.strip().upper()
    result: List[str] = []
    for line in lines:
        text = str(line)
        upper = text.upper()
        if level_upper == "ERROR" and "ERROR" not in upper:
            continue
        if level_upper == "WARNING" and "WARNING" not in upper and "WARN" not in upper:
            continue
        if query_lower and query_lower not in text.lower():
            continue
        result.append(text)
    return result


def docker_records(snapshot: Mapping[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Wyciąga kontenery i procesy z kilku wspieranych kształtów snapshotu.

    Zwraca ``(kind, name, record)``. Nie uzupełnia brakujących pól; nazwa z
    klucza słownika jest jedynie identyfikatorem rekordu dostarczonym przez
    backend.
    """

    records: List[Tuple[str, str, Dict[str, Any]]] = []
    seen: set = set()

    def add(kind: str, name: Any, value: Any) -> None:
        record = as_mapping(value)
        if not record:
            return
        record_name = str(record.get("name") or record.get("short_id") or record.get("id") or name or "")
        if not record_name:
            return
        marker = (kind, record_name)
        if marker in seen:
            return
        seen.add(marker)
        records.append((kind, record_name, record))

    def add_many(kind: str, value: Any) -> None:
        if isinstance(value, Mapping):
            # Pojedynczy rekord kontenera/procesu ma zwykle własną nazwę.
            if any(key in value for key in ("name", "id", "short_id", "status", "state")):
                add(kind, "", value)
            else:
                for key, item in value.items():
                    add(kind, key, item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for index, item in enumerate(value):
                add(kind, str(index), item)

    add_many("container", snapshot.get("containers"))
    add_many("container", snapshot.get("container"))
    docker = as_mapping(snapshot.get("docker"))
    add_many("container", docker.get("containers"))
    add_many("container", docker.get("container"))
    simulator = as_mapping(snapshot.get("simulator"))
    add_many("container", simulator.get("container"))

    for key in ("processes", "components"):
        add_many("process", snapshot.get(key))
    runtime = as_mapping(snapshot.get("runtime"))
    for key in ("processes", "components"):
        add_many("process", runtime.get(key))
    return records


def _deep_merge(base: Mapping[str, Any], incoming: Mapping[str, Any]) -> Dict[str, Any]:
    result = as_mapping(base)
    for key, value in incoming.items():
        previous = result.get(key)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            result[key] = _deep_merge(as_mapping(previous), as_mapping(value))
        else:
            result[key] = value
    return result


class DockerView(ttk.Frame):
    """Kompaktowy inspektor kontenera sesji, procesów i logów.

    Publiczne punkty wejścia:

    * ``update_snapshot(snapshot)`` — stan kontenerów/procesów i opcjonalne
      przyrostowe ``logs``;
    * ``handle_event(event)`` — obecny ``QueueEvent`` albo przyszły payload;
    * ``append_log`` / ``append_logs`` — wyłącznie nowe rekordy logu.

    Pole ``logs`` powinno zawierać nowe wpisy, najlepiej ze ``seq``. Pełna
    historia logów pozostaje w plikach runu; pamięć widoku jest ograniczona.
    """

    def __init__(self, parent: tk.Misc, max_visible_log_lines: int = MAX_VISIBLE_LOG_LINES, **kwargs: Any) -> None:
        super().__init__(parent, padding=12, **kwargs)
        self.max_visible_log_lines = max(100, int(max_visible_log_lines))
        self._snapshot: Dict[str, Any] = {}
        self._records: Dict[str, Dict[str, Any]] = {}
        self._logs: Dict[str, Deque[str]] = {}
        self._last_sequences: Dict[str, int] = {}
        self._last_generations: Dict[str, int] = {}
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.panes = ttk.Panedwindow(self, orient="vertical")
        self.panes.grid(row=0, column=0, sticky="nsew")
        left = ttk.LabelFrame(self.panes, text="KONTENERY I PROCESY", padding=7)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        columns = ("kind", "name", "role", "state", "health", "uptime", "restarts", "cpu", "ram", "exit")
        headings = ("Typ", "Nazwa", "Rola", "Stan", "Health", "Uptime", "Restarty", "CPU", "RAM", "Exit")
        self.records_tree = ttk.Treeview(left, columns=columns, show="headings", height=7)
        for column, heading in zip(columns, headings):
            self.records_tree.heading(column, text=heading)
            self.records_tree.column(column, width=92 if column not in {"name", "role"} else 135, anchor="w", stretch=True)
        tree_y = ttk.Scrollbar(left, orient="vertical", command=self.records_tree.yview)
        tree_x = ttk.Scrollbar(left, orient="horizontal", command=self.records_tree.xview)
        self.records_tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.records_tree.grid(row=0, column=0, sticky="nsew")
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x.grid(row=1, column=0, sticky="ew")
        self.records_tree.bind("<<TreeviewSelect>>", self._on_record_selected)
        self.records_tree.tag_configure("ok", foreground="#1b6e35")
        self.records_tree.tag_configure("warning", foreground="#8a5b00")
        self.records_tree.tag_configure("error", foreground="#9d1d1d")

        details_box = ttk.LabelFrame(left, text="SZCZEGÓŁY", padding=5)
        details_box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        details_box.columnconfigure(0, weight=1)
        details_box.rowconfigure(0, weight=1)
        self.details = tk.Text(
            details_box,
            height=5,
            wrap="word",
            state="disabled",
            font=("TkFixedFont", 9),
            background="#171b20",
            foreground="#e5e9ef",
            insertbackground="#e5e9ef",
            relief="flat",
        )
        details_scroll = ttk.Scrollbar(details_box, orient="vertical", command=self.details.yview)
        self.details.configure(yscrollcommand=details_scroll.set)
        self.details.grid(row=0, column=0, sticky="nsew")
        details_scroll.grid(row=0, column=1, sticky="ns")

        right = ttk.LabelFrame(self.panes, text="LIVE LOG", padding=7)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(left)
        self.panes.add(right)
        self._sash_positioned = False
        self.panes.bind("<Configure>", self._position_sash, add=True)
        ttk.Label(right, text="Źródło:").grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar(value="")
        self.source_choice = ttk.Combobox(right, textvariable=self.source_var, state="readonly")
        self.source_choice.grid(row=0, column=1, sticky="ew", padx=(6, 8))
        self.source_choice.bind("<<ComboboxSelected>>", lambda _event: self._render_log())
        ttk.Label(right, text="Szukaj:").grid(row=1, column=0, sticky="w", pady=(7, 0))
        controls = ttk.Frame(right)
        controls.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(7, 0))
        controls.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar(value="")
        self.search_entry = ttk.Entry(controls, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda _event: self._render_log())
        self.level_var = tk.StringVar(value="Wszystkie")
        self.level_choice = ttk.Combobox(
            controls,
            textvariable=self.level_var,
            state="readonly",
            values=("Wszystkie", "ERROR", "WARNING"),
            width=11,
        )
        self.level_choice.grid(row=0, column=1, padx=(6, 0))
        self.level_choice.bind("<<ComboboxSelected>>", lambda _event: self._render_log())
        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Auto-scroll", variable=self.autoscroll_var).grid(row=0, column=2, padx=(8, 0))

        log_frame = ttk.Frame(right)
        log_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            wrap="none",
            state="disabled",
            font=("TkFixedFont", 9),
            background="#171b20",
            foreground="#e5e9ef",
            insertbackground="#e5e9ef",
            relief="flat",
        )
        log_y = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_x = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=log_y.set, xscrollcommand=log_x.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_y.grid(row=0, column=1, sticky="ns")
        log_x.grid(row=1, column=0, sticky="ew")

        buttons = ttk.Frame(right)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Wyczyść widok", command=self.clear_current_log).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Kopiuj", command=self.copy_visible_log).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Zapisz…", command=self.save_visible_log).pack(side="left")

    def _position_sash(self, _event: Any = None) -> None:
        if self._sash_positioned:
            return
        self._sash_positioned = True

        def position() -> None:
            try:
                height = self.panes.winfo_height()
                if height > 100:
                    self.panes.sashpos(0, max(210, int(height * 0.43)))
            except tk.TclError:
                pass

        self.after_idle(position)

    def clear(self) -> None:
        """Czyści wyłącznie dane prezentowane przez widok."""

        self._snapshot = {}
        self._records = {}
        self._logs = {}
        self._last_sequences = {}
        self._last_generations = {}
        self.source_var.set("")
        self.source_choice.configure(values=())
        for item in self.records_tree.get_children():
            self.records_tree.delete(item)
        self._set_text(self.details, "")
        self._set_text(self.log_text, "")

    def handle_event(self, event: Any) -> None:
        """Przyjmuje obecne zdarzenie kolejki albo słownik z payloadem."""

        data = event_payload(event)
        self.update_snapshot(data)
        if (
            str(data.get("phase") or "").upper() == "LOG"
            and data.get("message") is not None
            and data.get("log") is None
            and data.get("logs") is None
        ):
            source, text = self._source_and_text(data)
            self.append_log(source, text)

    def update_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Scala snapshot, odświeża rekordy oraz odbiera opcjonalne nowe logi."""

        incoming = as_mapping(snapshot)
        if not incoming:
            return
        self._snapshot = _deep_merge(self._snapshot, incoming)
        self._refresh_records()
        self._consume_log_payload(incoming)

    def append_log(
        self,
        source: str,
        text: Any,
        sequence: Optional[int] = None,
        generation: Optional[int] = None,
        refresh: bool = True,
    ) -> bool:
        """Dopisuje jeden nowy wpis logu do ograniczonego bufora źródła."""

        text_value = log_record_text(text)
        if text_value is None:
            return False
        source_name = str(source or "log")
        if generation is not None:
            previous_generation = self._last_generations.get(source_name)
            if previous_generation != generation:
                self._last_generations[source_name] = generation
                self._last_sequences.pop(source_name, None)
        if sequence is not None:
            previous = self._last_sequences.get(source_name)
            if previous is not None and sequence <= previous:
                # A restarted docker-exec stream starts its local sequence at
                # one.  A lower number is a new stream even if an older
                # backend did not provide the explicit generation field.
                if sequence < previous:
                    self._last_sequences.pop(source_name, None)
                else:
                    return False
            self._last_sequences[source_name] = sequence
        lines = self._logs.setdefault(source_name, deque(maxlen=self.max_visible_log_lines))
        lines.extend(text_value.rstrip("\n").splitlines() or [""])
        if refresh:
            self._refresh_sources(source_name)
            if self.source_var.get() == source_name:
                self._render_log()
        return True

    def append_logs(self, source: str, records: Any) -> None:
        """Dopisuje listę nowych rekordów, używając ``seq`` gdy jest dostępne."""

        raw_records = records
        if isinstance(raw_records, Mapping):
            raw_records = raw_records.get("lines", raw_records.get("records", [raw_records]))
        if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
            raw_records = [raw_records]
        appended = False
        for record in raw_records:
            sequence = None
            generation = None
            if isinstance(record, Mapping):
                try:
                    sequence_value = record.get("seq")
                    sequence = int(sequence_value) if sequence_value is not None else None
                except (TypeError, ValueError):
                    sequence = None
                try:
                    generation_value = record.get("generation")
                    generation = int(generation_value) if generation_value is not None else None
                except (TypeError, ValueError):
                    generation = None
            appended = self.append_log(source, record, sequence, generation, refresh=False) or appended
        if appended:
            self._refresh_sources(str(source or "log"))
            if self.source_var.get() == str(source or "log"):
                self._render_log()

    def clear_current_log(self) -> None:
        """Usuwa tylko ograniczony bufor GUI aktualnie wybranego źródła."""

        source = self.source_var.get()
        if source:
            self._logs[source] = deque(maxlen=self.max_visible_log_lines)
            self._last_sequences.pop(source, None)
            self._last_generations.pop(source, None)
        self._render_log()

    def select_component(self, component: str) -> None:
        """Wybiera rekord/log dla elementu stałego panelu SYSTEM."""

        key = str(component or "").lower()
        aliases = {
            "docker": ("docker", "kontener", "sesji"),
            "ric": ("ric", "nearrt"),
            "ns3": ("ns3", "ns-3"),
            "xapp": ("xapp", "xapp"),
        }
        for record_id, record in self._records.items():
            searchable = "{} {}".format(record.get("name") or "", record.get("role") or "").lower()
            if any(token in searchable for token in aliases.get(key, (key,))):
                self.records_tree.selection_set(record_id)
                self.records_tree.focus(record_id)
                self.records_tree.see(record_id)
                self._show_record_details(record_id)
                break
        if key in self._logs:
            self.source_var.set(key)
            self._render_log()

    def copy_visible_log(self) -> None:
        text = self._visible_log_text()
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            return

    def save_visible_log(self) -> None:
        text = self._visible_log_text()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="Zapisz widoczne logi",
            defaultextension=".log",
            filetypes=(("Plik logu", "*.log"), ("Plik tekstowy", "*.txt"), ("Wszystkie pliki", "*.*")),
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError:
            return

    def _refresh_records(self) -> None:
        selected = self.records_tree.selection()
        selected_id = selected[0] if selected else ""
        self._records = {}
        for item in self.records_tree.get_children():
            self.records_tree.delete(item)
        normal_shutdown = bool(self._snapshot.get("queue_terminal")) or str(
            self._snapshot.get("phase") or ""
        ).upper() == "COMPLETED"
        for index, (kind, name, record) in enumerate(docker_records(self._snapshot)):
            # Po poprawnym końcu kolejki rekord kontenera może pochodzić z
            # ostatniego pomiaru sprzed cleanupu. Procesy są wtedy celowo
            # zatrzymane, a sesja Docker usunięta; oba fakty prezentujemy jako
            # zakończenie, nie jako aktywny kontener lub czerwony błąd.
            display_record = dict(record)
            state_before = self._value(display_record, "status", "state")
            if normal_shutdown:
                if kind == "container":
                    display_record["status"] = "removed"
                    display_record["state"] = "removed"
                elif str(state_before or "").lower() in {"stopped", "exited", "dead"}:
                    display_record["status"] = "completed"
                    display_record["state"] = "completed"
            record_id = "{}:{}:{}".format(kind, index, name)
            self._records[record_id] = display_record
            role = self._value(display_record, "role")
            state = self._value(display_record, "status", "state")
            health = self._value(display_record, "health", "health_status")
            uptime = self._duration_value(display_record.get("uptime_s", display_record.get("uptime")))
            restarts = self._value(display_record, "restart_count", "restarts")
            cpu = self._percent_value(display_record.get("cpu_pct", display_record.get("cpu_percent", display_record.get("cpu"))))
            ram = self._memory_value(display_record)
            exit_code = self._value(display_record, "exit_code", "returncode")
            tag = self._state_tag("{} {} {}".format(state or "", health or "", self._value(display_record, "error") or ""))
            self.records_tree.insert(
                "",
                "end",
                iid=record_id,
                values=(
                    "Docker" if kind == "container" else "Proces",
                    name,
                    role or "",
                    state or "",
                    health or "",
                    uptime or "",
                    restarts or "",
                    cpu or "",
                    ram or "",
                    exit_code if exit_code is not None else "",
                ),
                tags=(tag,) if tag else (),
            )
        if selected_id and selected_id in self._records:
            self.records_tree.selection_set(selected_id)
            self._show_record_details(selected_id)
        else:
            self._set_text(self.details, "")

    def _consume_log_payload(self, snapshot: Mapping[str, Any]) -> None:
        logs = snapshot.get("logs")
        if isinstance(logs, Mapping):
            for source, records in logs.items():
                self.append_logs(str(source), records)
        elif logs is not None:
            source = str(snapshot.get("log_source") or snapshot.get("source") or "log")
            self.append_logs(source, logs)
        record = snapshot.get("log")
        if record is not None:
            if isinstance(record, Mapping):
                source = str(record.get("source") or record.get("name") or snapshot.get("log_source") or "log")
                self.append_logs(source, [record])
            else:
                source = str(snapshot.get("log_source") or snapshot.get("source") or "log")
                self.append_log(source, record)

    def _source_and_text(self, payload: Mapping[str, Any]) -> Tuple[str, str]:
        source = str(payload.get("log_source") or payload.get("source") or "")
        text = str(payload.get("message") or "")
        match = re.match(r"^\[([^\]]+)\]\s*(.*)$", text)
        if match:
            source = source or match.group(1)
            text = match.group(2)
        return source or "log", text

    def _refresh_sources(self, preferred: str = "") -> None:
        sources = sorted(self._logs)
        self.source_choice.configure(values=sources)
        current = self.source_var.get()
        if current not in sources:
            self.source_var.set(preferred if preferred in sources else (sources[0] if sources else ""))

    def _on_record_selected(self, _event: Any) -> None:
        selected = self.records_tree.selection()
        if not selected:
            return
        record_id = selected[0]
        self._show_record_details(record_id)
        values = self.records_tree.item(record_id, "values")
        if len(values) > 1 and values[1] in self._logs:
            self.source_var.set(values[1])
            self._render_log()

    def _show_record_details(self, record_id: str) -> None:
        record = self._records.get(record_id)
        if record is None:
            self._set_text(self.details, "")
            return
        self._set_text(self.details, json.dumps(record, ensure_ascii=False, indent=2, default=str))

    def _render_log(self) -> None:
        source = self.source_var.get()
        lines = list(self._logs.get(source, ()))
        visible = filter_log_lines(lines, self.search_var.get(), self.level_var.get())
        self._set_text(self.log_text, "\n".join(visible))
        if self.autoscroll_var.get() and visible:
            self.log_text.configure(state="normal")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def _visible_log_text(self) -> str:
        return self.log_text.get("1.0", "end-1c")

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if value:
            widget.insert("1.0", value)
        widget.configure(state="disabled")

    @staticmethod
    def _value(record: Mapping[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            if key in record and record.get(key) is not None:
                return str(record.get(key))
        return None

    @staticmethod
    def _duration_value(value: Any) -> str:
        try:
            seconds = max(0, int(float(value)))
        except (TypeError, ValueError):
            return ""
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

    @staticmethod
    def _percent_value(value: Any) -> str:
        try:
            return "{:.1f}%".format(float(value))
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _memory_value(record: Mapping[str, Any]) -> str:
        value = record.get("memory_bytes", record.get("memory_usage", record.get("memory", record.get("ram"))))
        limit = record.get("memory_limit_bytes")
        try:
            used_mib = float(value) / (1024.0 * 1024.0)
        except (TypeError, ValueError):
            return ""
        try:
            limit_mib = float(limit) / (1024.0 * 1024.0)
        except (TypeError, ValueError):
            limit_mib = None
        return "{:.1f} MiB{}".format(used_mib, " / {:.1f}".format(limit_mib) if limit_mib else "")

    @staticmethod
    def _state_tag(value: str) -> str:
        lowered = value.lower()
        if any(token in lowered for token in ("error", "fail", "exited", "stopped", "dead")):
            return "error"
        if any(token in lowered for token in ("warn", "degrad", "start", "restart", "stop")):
            return "warning"
        if any(token in lowered for token in ("running", "ready", "healthy", "ok")):
            return "ok"
        return ""
