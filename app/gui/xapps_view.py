from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable, Dict, Optional, Tuple

from app.services.xapp_service import XAppBuildResult, XAppRecord, XAppService


class XAppsView(ttk.Frame):
    """Zarządzanie skonfigurowanymi i własnymi xAppami bez wbudowanego IDE."""

    def __init__(
        self,
        parent: tk.Misc,
        service: XAppService,
        on_config_changed: Optional[Callable[[], None]] = None,
        can_build: Optional[Callable[[], bool]] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(parent, padding=12, **kwargs)
        self.service = service
        self.on_config_changed = on_config_changed
        self.can_build = can_build
        self._items: Dict[str, XAppRecord] = {}
        self._results: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._building = False
        self._build()
        self.refresh()
        self.after(100, self._poll_results)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.panes = ttk.Panedwindow(self, orient="vertical")
        self.panes.grid(row=0, column=0, sticky="nsew")
        left = ttk.LabelFrame(self.panes, text="XAPPS", padding=8)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=("name", "kind", "source", "build"), show="headings", height=4)
        for key, text, width in (
            ("name", "Nazwa", 180),
            ("kind", "Typ", 90),
            ("source", "Źródło", 115),
            ("build", "Build", 105),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w", stretch=key == "name")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal_scrollbar.set)
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        buttons = ttk.Frame(left)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Utwórz xApp", command=self._create).grid(row=0, column=0, sticky="ew")
        ttk.Button(buttons, text="Odśwież", command=self.refresh).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        right = ttk.Frame(self.panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(left)
        self.panes.add(right)
        self._sash_positioned = False
        self.panes.bind("<Configure>", self._position_sash, add=True)
        header = ttk.Frame(right)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        self.title_var = tk.StringVar(value="Wybierz xApp")
        self.status_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.title_var, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="e")
        detail_box = ttk.LabelFrame(right, text="KONFIGURACJA I STATUS", padding=7)
        detail_box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        detail_box.columnconfigure(0, weight=1)
        self.details = tk.Text(
            detail_box,
            height=9,
            wrap="word",
            state="disabled",
            font=("TkFixedFont", 9),
            background="#ffffff",
            foreground="#202020",
            insertbackground="#202020",
            relief="flat",
        )
        self.details.grid(row=0, column=0, sticky="ew")
        detail_scroll = ttk.Scrollbar(detail_box, orient="vertical", command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        log_box = ttk.LabelFrame(right, text="BUILD LOG", padding=7)
        log_box.grid(row=2, column=0, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_box,
            wrap="none",
            state="disabled",
            font=("TkFixedFont", 9),
            background="#ffffff",
            foreground="#202020",
            insertbackground="#202020",
            relief="flat",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        xscroll = ttk.Scrollbar(log_box, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        controls = ttk.Frame(right)
        controls.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.open_folder_button = ttk.Button(controls, text="Otwórz folder", command=self._open_folder)
        self.open_folder_button.pack(side="left")
        self.open_source_button = ttk.Button(controls, text="Otwórz źródło", command=self._open_source)
        self.open_source_button.pack(side="left", padx=(6, 0))
        self.validate_button = ttk.Button(controls, text="Waliduj", command=self._validate)
        self.validate_button.pack(side="left", padx=(6, 0))
        self.build_button = ttk.Button(controls, text="Build", style="Accent.TButton", command=self._build_xapp)
        self.build_button.pack(side="right")
        ttk.Button(controls, text="Pokaż log", command=self._show_log).pack(side="right", padx=(0, 6))
        ttk.Button(controls, text="Test", command=self._test_info).pack(side="right", padx=(0, 6))

    def _position_sash(self, _event: Any = None) -> None:
        if self._sash_positioned:
            return
        self._sash_positioned = True

        def position() -> None:
            try:
                if self.panes.winfo_height() > 100:
                    self.panes.sashpos(0, 175)
            except tk.TclError:
                pass

        self.after_idle(position)

    def refresh(self) -> None:
        current = self._selected_key()
        self._items = {item.key: item for item in self.service.list()}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for item in self._items.values():
            source = "lokalne" if item.source_path and item.source_path.is_file() else "tylko obraz"
            build = item.last_build_status or ("gotowy" if item.buildable else "niedostępny")
            self.tree.insert(
                "",
                "end",
                iid=item.key,
                values=(item.name, "własny" if item.custom else "wpis config", source, build),
            )
        if current and self.tree.exists(current):
            self.tree.selection_set(current)
            self._show(current)
        elif self._items:
            first = next(iter(self._items))
            self.tree.selection_set(first)
            self._show(first)
        else:
            self._show(None)

    def _on_select(self, _event: Any = None) -> None:
        self._show(self._selected_key())

    def _show(self, key: Optional[str]) -> None:
        item = self._items.get(key or "")
        if item is None:
            self.title_var.set("Wybierz xApp")
            self.status_var.set("")
            self._set_text(self.details, "Brak skonfigurowanych xAppów.")
            self._set_text(self.log, "")
            self.build_button.configure(state="disabled")
            return
        self.title_var.set(item.name)
        status = item.last_build_status or ("BUILD READY" if item.buildable else "SOURCE NOT AVAILABLE")
        self.status_var.set(status)
        lines = [
            "Klucz: {}".format(item.key),
            "Target: {}".format(item.target or "N/A"),
            "Binarka: {}".format(item.executable or "N/A"),
            "Źródło: {}".format(item.source_path or "brak lokalnego źródła"),
            "Ostatni build: {}".format(item.last_build or "brak"),
            "Obraz builda: {}".format(item.image or "brak"),
            "",
            item.description or "",
        ]
        if item.parameters:
            lines.append("\nParametry deklarowane przez xApp:")
            for parameter, specification in item.parameters.items():
                spec = specification if isinstance(specification, dict) else {}
                lines.append("- {}: {}".format(spec.get("label") or parameter, spec.get("default", "")))
        else:
            lines.append("\nTen xApp nie deklaruje parametrów konfigurowalnych.")
        self._set_text(self.details, "\n".join(lines))
        self.build_button.configure(state="normal" if item.buildable and not self._building else "disabled")
        self._show_log()

    def _create(self) -> None:
        name = simpledialog.askstring(
            "Utwórz xApp",
            "Nazwa nowego xAppa (szablon: My Energy / KPM):",
            parent=self.winfo_toplevel(),
        )
        if not name:
            return
        try:
            item = self.service.create_from_my_energy_template(name)
            self.refresh()
            self.tree.selection_set(item.key)
            self._show(item.key)
        except Exception as exc:
            messagebox.showerror("Nie można utworzyć xAppa", str(exc), parent=self.winfo_toplevel())

    def _validate(self) -> None:
        item = self._items.get(self._selected_key() or "")
        if item is None:
            return
        errors = self.service.validate(item)
        if errors:
            messagebox.showerror("Walidacja xAppa", "\n".join(errors), parent=self.winfo_toplevel())
        else:
            messagebox.showinfo("Walidacja xAppa", "Lokalna definicja xAppa jest poprawna.", parent=self.winfo_toplevel())

    def _build_xapp(self) -> None:
        item = self._items.get(self._selected_key() or "")
        if item is None or not item.buildable or self._building:
            return
        if self.can_build is not None and not self.can_build():
            messagebox.showwarning(
                "Build xAppa",
                "Zaczekaj na zakończenie bieżącej kolejki przed zmianą obrazu Docker.",
                parent=self.winfo_toplevel(),
            )
            return
        accepted = messagebox.askyesno(
            "Build xAppa",
            "Build uruchomi się w tymczasowym kontenerze z aktualnego obrazu. "
            "Po sukcesie aplikacja utworzy nowy lokalny obraz Docker i przełączy na niego kolejne symulacje. Kontynuować?",
            parent=self.winfo_toplevel(),
        )
        if not accepted:
            return
        self._building = True
        self.status_var.set("BUILDING")
        self.build_button.configure(state="disabled")

        def work() -> None:
            try:
                self._results.put(("build", self.service.build(item.key)))
            except Exception as exc:
                self._results.put(("build_error", exc))

        threading.Thread(target=work, name="oran-lab-xapp-build", daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                kind, value = self._results.get_nowait()
                if kind == "build":
                    self._on_build_result(value)
                elif kind == "build_error":
                    self._building = False
                    messagebox.showerror("Build xAppa", str(value), parent=self.winfo_toplevel())
                    self.refresh()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll_results)

    def _on_build_result(self, result: XAppBuildResult) -> None:
        self._building = False
        if result.ok:
            if self.on_config_changed:
                self.on_config_changed()
            messagebox.showinfo("Build xAppa", result.message, parent=self.winfo_toplevel())
        else:
            messagebox.showerror("Build xAppa", result.message, parent=self.winfo_toplevel())
        self.refresh()
        self._show_log_path(result.log_path)

    def _open_folder(self) -> None:
        item = self._items.get(self._selected_key() or "")
        if item is None or item.source_path is None:
            messagebox.showinfo("xApp", "Ten wpis nie ma lokalnego folderu źródłowego.", parent=self.winfo_toplevel())
            return
        self._open_path(item.source_path.parent)

    def _open_source(self) -> None:
        item = self._items.get(self._selected_key() or "")
        if item is None or item.source_path is None or not item.source_path.is_file():
            messagebox.showinfo("xApp", "Ten wpis nie ma lokalnego pliku źródłowego.", parent=self.winfo_toplevel())
            return
        self._open_path(item.source_path)

    def _open_path(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror("Nie można otworzyć", str(exc), parent=self.winfo_toplevel())

    def _show_log(self) -> None:
        item = self._items.get(self._selected_key() or "")
        if item is None or item.last_build_log is None:
            self._set_text(self.log, "Brak zapisanego logu builda.")
            return
        self._show_log_path(item.last_build_log)

    def _show_log_path(self, path: Path) -> None:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=5000)
            self._set_text(self.log, "".join(lines))
            self.log.configure(state="normal")
            self.log.see("end")
            self.log.configure(state="disabled")
        except OSError:
            self._set_text(self.log, "Brak zapisanego logu builda.")

    def _test_info(self) -> None:
        messagebox.showinfo(
            "Test xAppa",
            "W aktywnej architekturze xApp wymaga uruchomionych nearRT-RIC i węzła E2, "
            "więc nie ma uczciwego testu samodzielnego. Użyj krótkiej symulacji z wybranym xAppem.",
            parent=self.winfo_toplevel(),
        )

    def _selected_key(self) -> Optional[str]:
        selected = self.tree.selection()
        return selected[0] if selected else None

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if text:
            widget.insert("1.0", text)
        widget.configure(state="disabled")
