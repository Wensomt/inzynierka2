"""Przegląd rzeczywiście zapisanych plików pojedynczego runu."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, Iterable

from app.data.storage import RunArtifact, SimulationStorage
from app.infrastructure.file_browser import open_in_file_manager
from app.simulation.models import Simulation


def display_file_size(value: int) -> str:
    """Krótki, czytelny rozmiar pliku bez ukrywania rzeczywistej liczby bajtów."""

    size = max(0, int(value))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return "{} {}".format(size if unit == "B" else "{:.1f}".format(size), unit)
        size /= 1024.0
    return "{} B".format(value)


class RunDataDialog(tk.Toplevel):
    """Lista plików i ograniczony podgląd CSV/JSON/logów bez pełnego IDE."""

    FILTERS = {
        "Wszystkie": lambda _path: True,
        "CSV": lambda path: path.lower().endswith(".csv"),
        "JSON": lambda path: path.lower().endswith(".json"),
        "Logi i tekst": lambda path: path.lower().endswith((".log", ".txt")),
    }

    def __init__(self, parent: tk.Misc, storage: SimulationStorage, simulation: Simulation) -> None:
        super().__init__(parent)
        self.storage = storage
        self.simulation = simulation
        self._artifacts: Dict[str, RunArtifact] = {}
        self.filter_var = tk.StringVar(value="Wszystkie")
        self.status_var = tk.StringVar(value="Wybierz plik, aby zobaczyć ograniczony podgląd.")
        self.title("Dane runu — {}".format(simulation.name))
        self.geometry("1120x720")
        self.minsize(820, 520)
        self.transient(parent.winfo_toplevel())
        self._build()
        self._refresh()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        heading = ttk.Frame(self, padding=(14, 14, 14, 8))
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(1, weight=1)
        ttk.Label(heading, text="PLIKI ZAPISANE DLA RUNU", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            heading,
            text="{} [{}]".format(self.simulation.name, self.simulation.id[:8]),
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="e")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=14)
        files = ttk.LabelFrame(panes, text="ARTEFAKTY", padding=8)
        preview = ttk.LabelFrame(panes, text="PODGLĄD", padding=8)
        panes.add(files, weight=1)
        panes.add(preview, weight=2)

        files.columnconfigure(0, weight=1)
        files.rowconfigure(1, weight=1)
        filters = ttk.Frame(files)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(filters, text="Pokaż:").pack(side="left")
        filter_box = ttk.Combobox(
            filters,
            values=tuple(self.FILTERS),
            textvariable=self.filter_var,
            state="readonly",
            width=17,
        )
        filter_box.pack(side="left", padx=(6, 0))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._populate())
        self.tree = ttk.Treeview(
            files,
            columns=("path", "size", "modified"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width in (
            ("path", "Plik", 270),
            ("size", "Rozmiar", 80),
            ("modified", "Modyfikacja", 160),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w", stretch=key == "path")
        self.tree.grid(row=1, column=0, sticky="nsew")
        tree_y = ttk.Scrollbar(files, orient="vertical", command=self.tree.yview)
        tree_y.grid(row=1, column=1, sticky="ns")
        tree_x = ttk.Scrollbar(files, orient="horizontal", command=self.tree.xview)
        tree_x.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.tree.bind("<<TreeviewSelect>>", self._preview_selected)
        self.tree.bind("<Double-1>", self._open_selected_file)

        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.text = tk.Text(
            preview,
            wrap="none",
            state="disabled",
            relief="flat",
            background="#171c22",
            foreground="#e6e9ed",
            insertbackground="#e6e9ed",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        text_y = ttk.Scrollbar(preview, orient="vertical", command=self.text.yview)
        text_y.grid(row=0, column=1, sticky="ns")
        text_x = ttk.Scrollbar(preview, orient="horizontal", command=self.text.xview)
        text_x.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=text_y.set, xscrollcommand=text_x.set)

        controls = ttk.Frame(self, padding=(14, 8, 14, 12))
        controls.grid(row=2, column=0, sticky="ew")
        ttk.Label(controls, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(controls, text="Odśwież", command=self._refresh).pack(side="right")
        ttk.Button(controls, text="Zamknij", command=self.destroy).pack(side="right", padx=(0, 7))
        ttk.Button(controls, text="Folder runu", command=self._open_run_folder).pack(side="right", padx=(0, 7))
        ttk.Button(controls, text="Pokaż wybrany plik", command=self._open_selected_file).pack(
            side="right", padx=(0, 7)
        )

    def _refresh(self) -> None:
        try:
            artifacts = self.storage.list_run_artifacts(self.simulation.id)
        except Exception as exc:
            messagebox.showerror("Nie można odczytać danych", str(exc), parent=self)
            return
        self._artifacts = {item.relative_path: item for item in artifacts}
        self._populate()
        self.status_var.set("{} plików zapisanych dla runu.".format(len(artifacts)))

    def _populate(self) -> None:
        selected = self._selected_path()
        predicate = self.FILTERS.get(self.filter_var.get(), self.FILTERS["Wszystkie"])
        for item in self.tree.get_children():
            self.tree.delete(item)
        for artifact in self._filtered(self._artifacts.values(), predicate):
            self.tree.insert(
                "",
                "end",
                iid=artifact.relative_path,
                values=(
                    artifact.relative_path,
                    display_file_size(artifact.size_bytes),
                    self._display_time(artifact.modified_at),
                ),
            )
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.focus(selected)
            self.tree.see(selected)

    @staticmethod
    def _filtered(artifacts: Iterable[RunArtifact], predicate: object) -> Iterable[RunArtifact]:
        for artifact in artifacts:
            if callable(predicate) and predicate(artifact.relative_path):
                yield artifact

    def _selected_path(self) -> str:
        selected = self.tree.selection()
        return selected[0] if selected else ""

    def _preview_selected(self, _event: object = None) -> None:
        path = self._selected_path()
        if not path:
            return
        try:
            preview = self.storage.read_run_artifact(self.simulation.id, path)
        except Exception as exc:
            self._set_text("Nie można odczytać podglądu:\n{}".format(exc))
            self.status_var.set("Nie można odczytać {}.".format(path))
            return
        self._set_text(preview.text)
        message = "{} · {}".format(path, display_file_size(self._artifacts[path].size_bytes))
        if preview.binary:
            message += " · plik binarny"
        elif preview.truncated:
            message += " · widoczny jest ograniczony podgląd"
        self.status_var.set(message)

    def _set_text(self, value: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.configure(state="disabled")

    def _open_run_folder(self) -> None:
        try:
            open_in_file_manager(self.storage.directory(self.simulation.id))
        except Exception as exc:
            messagebox.showerror("Nie można otworzyć folderu", str(exc), parent=self)

    def _open_selected_file(self, _event: object = None) -> None:
        relative_path = self._selected_path()
        if not relative_path:
            return
        try:
            directory = self.storage.directory(self.simulation.id)
            open_in_file_manager(directory.joinpath(*relative_path.split("/")))
        except Exception as exc:
            messagebox.showerror("Nie można pokazać pliku", str(exc), parent=self)

    @staticmethod
    def _display_time(value: str) -> str:
        text = str(value or "").replace("T", " ")
        return text[:19] + " UTC" if text else "—"
