from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable, List, Optional, Set

from app.data.storage import SavedConfiguration, SimulationStorage
from app.simulation.models import Simulation


def suggested_configuration_name(
    existing_names: Iterable[str],
    now: Optional[datetime] = None,
) -> str:
    """Tworzy czytelną, bezkolizyjną nazwę dla zapisu jednym kliknięciem.

    Ręczne nadanie nazwy pozostaje możliwe, ale zwykłe zapisanie zestawu kart
    nie wymaga wpisywania ani zapamiętywania identyfikatora. Nazwa zawiera
    lokalną datę i godzinę, więc od razu wiadomo, kiedy powstał dany zestaw.
    """

    timestamp = now or datetime.now()
    base = "Zapis {}".format(timestamp.strftime("%Y-%m-%d %H-%M"))
    used: Set[str] = {str(item).strip().casefold() for item in existing_names}
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = "{} {}".format(base, suffix)
        suffix += 1
    return candidate


class ConfigurationLibraryDialog(tk.Toplevel):
    """Lista zapisanych zestawów kart symulacji z importem i bezpiecznym usuwaniem."""

    def __init__(
        self,
        parent: tk.Misc,
        storage: SimulationStorage,
        current_simulations: Callable[[], Iterable[Simulation]],
        on_load: Callable[[List[Simulation]], None],
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.current_simulations = current_simulations
        self.on_load = on_load
        self._records = {}
        self.title("Biblioteka konfiguracji")
        self.minsize(720, 420)
        self.geometry("820x500")
        self.transient(parent.winfo_toplevel())
        self.name_var = tk.StringVar(value=suggested_configuration_name(()))
        self.status_var = tk.StringVar(value="Wybierz zapis, aby go wczytać, wyeksportować lub usunąć.")
        self._build()
        self._refresh()
        self.grab_set()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(14, 14, 14, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="ZAPISANE KONFIGURACJE", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            header,
            text="Zapis zawiera układ kart i parametry. Wyniki runów pozostają w Historii.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 12))
        ttk.Label(header, text="Nazwa nowego zapisu (opcjonalna):").grid(row=2, column=0, sticky="w")
        ttk.Entry(header, textvariable=self.name_var).grid(row=2, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(header, text="Zapisz bieżące", style="Accent.TButton", command=self._save_new).grid(
            row=2, column=2, sticky="e"
        )

        content = ttk.Frame(self, padding=(14, 0, 14, 8))
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            content,
            columns=("name", "count", "saved"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        for key, title, width, stretch in (
            ("name", "Nazwa", 390, True),
            ("count", "Symulacje", 105, False),
            ("saved", "Ostatni zapis", 190, False),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=stretch, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self._load_selected())

        controls = ttk.Frame(self, padding=(14, 0, 14, 14))
        controls.grid(row=2, column=0, sticky="ew")
        ttk.Button(controls, text="Wczytaj wybrane", command=self._load_selected).pack(side="left")
        ttk.Button(controls, text="Aktualizuj wybrane", command=self._update_selected).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Importuj…", command=self._import).pack(side="left", padx=(18, 0))
        ttk.Button(controls, text="Eksportuj…", command=self._export).pack(side="left", padx=(7, 0))
        ttk.Button(controls, text="Usuń", command=self._delete).pack(side="left", padx=(7, 0))
        ttk.Button(controls, text="Odśwież", command=self._refresh).pack(side="left", padx=(18, 0))
        ttk.Button(controls, text="Zamknij", command=self.destroy).pack(side="right")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", padding=(14, 0, 14, 10)).grid(
            row=3, column=0, sticky="w"
        )

    def _refresh(self, select_name: Optional[str] = None) -> None:
        selected = select_name or self._selected_name()
        self._records = {item.name: item for item in self.storage.list_configuration_records()}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for item in self._records.values():
            self.tree.insert(
                "",
                "end",
                iid=item.name,
                values=(item.name, item.simulation_count, self._display_time(item.saved_at)),
            )
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.focus(selected)
            self.tree.see(selected)
        elif self._records:
            first = next(iter(self._records))
            self.tree.selection_set(first)
            self.tree.focus(first)

    def _selected_name(self) -> Optional[str]:
        selected = self.tree.selection()
        name = selected[0] if selected else ""
        return name if name in self._records else None

    def _on_select(self, _event: object = None) -> None:
        name = self._selected_name()
        if name:
            record = self._records[name]
            self.status_var.set(
                "Wybrano „{}” ({} sym.).".format(name, record.simulation_count)
            )

    def _current(self) -> List[Simulation]:
        simulations = list(self.current_simulations())
        if not simulations:
            raise ValueError("Najpierw utwórz co najmniej jedną kartę symulacji.")
        return simulations

    def _save_new(self) -> None:
        requested_name = self.name_var.get().strip() or suggested_configuration_name(
            self._records
        )
        try:
            path = self.storage.save_configuration(requested_name, self._current())
        except FileExistsError:
            messagebox.showwarning(
                "Zapis już istnieje",
                "Wybierz inną nazwę albo zaznacz zapis i użyj „Aktualizuj wybrane”.",
                parent=self,
            )
            return
        except Exception as exc:
            messagebox.showerror("Nie można zapisać", str(exc), parent=self)
            return
        self._refresh(path.stem)
        self.name_var.set(suggested_configuration_name(self._records))
        self.status_var.set("Zapisano bieżącą konfigurację: {}".format(path.name))

    def _update_selected(self) -> None:
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Konfiguracje", "Wybierz zapis do aktualizacji.", parent=self)
            return
        if not messagebox.askyesno(
            "Aktualizuj konfigurację",
            "Zastąpić parametry w zapisie „{}” bieżącymi kartami symulacji?".format(name),
            parent=self,
        ):
            return
        try:
            self.storage.save_configuration(name, self._current(), overwrite=True)
        except Exception as exc:
            messagebox.showerror("Nie można zaktualizować", str(exc), parent=self)
            return
        self._refresh(name)
        self.status_var.set("Zaktualizowano zapis „{}”.".format(name))

    def _load_selected(self) -> None:
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Konfiguracje", "Wybierz zapis do wczytania.", parent=self)
            return
        record = self._records[name]
        if not messagebox.askyesno(
            "Wczytaj konfigurację",
            "Wczytać „{}” ({} sym.) i zastąpić bieżące karty symulacji?".format(
                name, record.simulation_count
            ),
            parent=self,
        ):
            return
        try:
            self.on_load(self.storage.load_configuration(name))
        except Exception as exc:
            messagebox.showerror("Nie można wczytać", str(exc), parent=self)
            return
        self.destroy()

    def _import(self) -> None:
        source = filedialog.askopenfilename(
            parent=self,
            title="Importuj konfigurację",
            filetypes=(("Konfiguracja JSON", "*.json"), ("Wszystkie pliki", "*.*")),
        )
        if not source:
            return
        try:
            record = self.storage.import_configuration(Path(source))
        except Exception as exc:
            messagebox.showerror("Nie można zaimportować", str(exc), parent=self)
            return
        self._refresh(record.name)
        self.status_var.set("Zaimportowano „{}”.".format(record.name))

    def _export(self) -> None:
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Konfiguracje", "Wybierz zapis do eksportu.", parent=self)
            return
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="Eksportuj konfigurację",
            initialfile="{}.json".format(name),
            defaultextension=".json",
            filetypes=(("Konfiguracja JSON", "*.json"),),
        )
        if not destination:
            return
        try:
            self.storage.export_configuration(name, Path(destination))
        except Exception as exc:
            messagebox.showerror("Nie można wyeksportować", str(exc), parent=self)
            return
        self.status_var.set("Wyeksportowano „{}”.".format(name))

    def _delete(self) -> None:
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Konfiguracje", "Wybierz zapis do usunięcia.", parent=self)
            return
        record = self._records[name]
        prompt = (
            "Usunąć zapis konfiguracji „{}” ({} sym.)?\n\n"
            "Usunięty zostanie tylko ten plik konfiguracji. Wyniki runów i scenariusze pozostaną bez zmian."
        ).format(name, record.simulation_count)
        if not messagebox.askyesno("Usuń konfigurację", prompt, parent=self):
            return
        try:
            self.storage.delete_configuration(name)
        except Exception as exc:
            messagebox.showerror("Nie można usunąć", str(exc), parent=self)
            return
        self._refresh()
        self.status_var.set("Usunięto zapis „{}”.".format(name))

    @staticmethod
    def _display_time(value: str) -> str:
        text = str(value or "").replace("T", " ")
        if text.endswith("+00:00"):
            text = text[:-6] + " UTC"
        return text[:24] if text else "—"
