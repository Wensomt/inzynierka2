"""Odwracalne usuwanie zapisanych runów."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict

from app.data.storage import DeletedRun, SimulationStorage
from app.simulation.models import Simulation


class RunTrashDialog(tk.Toplevel):
    """Mały kosz wyników: przywracanie bez ryzyka nadpisania innego runu."""

    def __init__(
        self,
        parent: tk.Misc,
        storage: SimulationStorage,
        on_restored: Callable[[Simulation], None],
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.on_restored = on_restored
        self._items: Dict[str, DeletedRun] = {}
        self.status_var = tk.StringVar(value="Wybierz run do przywrócenia.")
        self.title("Kosz wyników")
        self.geometry("840x470")
        self.minsize(660, 350)
        self.transient(parent.winfo_toplevel())
        self._build()
        self._refresh()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        heading = ttk.Frame(self, padding=(14, 14, 14, 8))
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="KOSZ WYNIKÓW", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            heading,
            text="Usunięte runy są tu przenoszone, więc można je przywrócić bez ponownego uruchamiania.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        content = ttk.Frame(self, padding=(14, 0, 14, 8))
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            content,
            columns=("name", "status", "deleted", "id"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width in (
            ("name", "Nazwa", 300),
            ("status", "Stan", 110),
            ("deleted", "Przeniesiono", 175),
            ("id", "ID", 150),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w", stretch=key == "name")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _event: self._restore())

        controls = ttk.Frame(self, padding=(14, 0, 14, 12))
        controls.grid(row=2, column=0, sticky="ew")
        ttk.Label(controls, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(controls, text="Odśwież", command=self._refresh).pack(side="right")
        ttk.Button(controls, text="Zamknij", command=self.destroy).pack(side="right", padx=(0, 7))
        ttk.Button(controls, text="Przywróć wybrane", style="Accent.TButton", command=self._restore).pack(
            side="right", padx=(0, 14)
        )

    def _refresh(self) -> None:
        selected = self._selected_id()
        self._items = {item.trash_id: item for item in self.storage.deleted_runs()}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for item in self._items.values():
            self.tree.insert(
                "",
                "end",
                iid=item.trash_id,
                values=(item.name, item.status, self._display_time(item.deleted_at), item.simulation_id[:8]),
            )
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.focus(selected)
        elif self._items:
            first = next(iter(self._items))
            self.tree.selection_set(first)
            self.tree.focus(first)
        self.status_var.set(
            "{} runów dostępnych do przywrócenia.".format(len(self._items))
            if self._items
            else "Kosz wyników jest pusty."
        )

    def _selected_id(self) -> str:
        selected = self.tree.selection()
        return selected[0] if selected else ""

    def _restore(self) -> None:
        trash_id = self._selected_id()
        item = self._items.get(trash_id)
        if item is None:
            messagebox.showinfo("Kosz wyników", "Wybierz run do przywrócenia.", parent=self)
            return
        if not messagebox.askyesno(
            "Przywróć wynik",
            "Przywrócić run „{}”?\n\nJeżeli istnieje run o tym samym ID, przywrócony zapis dostanie nowe ID; żaden wynik nie zostanie nadpisany.".format(
                item.name
            ),
            parent=self,
        ):
            return
        try:
            simulation = self.storage.restore_deleted_run(trash_id)
        except Exception as exc:
            messagebox.showerror("Nie można przywrócić", str(exc), parent=self)
            return
        self.on_restored(simulation)
        self._refresh()
        self.status_var.set("Przywrócono „{}”.".format(simulation.name))

    @staticmethod
    def _display_time(value: str) -> str:
        text = str(value or "").replace("T", " ")
        return text[:19] + " UTC" if text else "—"
