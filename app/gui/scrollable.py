"""Małe, natywne przewijane kontenery dla widoków ttk.

Tkinter nie daje ``ttk.Frame`` wbudowanego scrollbara. Ten moduł skupia
obsługę Canvas + Scrollbar w jednym miejscu, aby długie formularze nie były
obcinane przy minimalnym rozmiarze okna aplikacji.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Set


class ScrollableFrame(ttk.Frame):
    """Kontener z pionowym scrollbarem i dostępnym ``content`` do layoutu.

    Szerokość treści dopasowuje się do obszaru widoku, więc formularze nadal
    korzystają z ``sticky='ew'``. Opcjonalny poziomy scrollbar jest przydatny
    dla szerokich tabel lub tekstu bez zawijania.
    """

    def __init__(
        self,
        parent: tk.Misc,
        content_padding: Any = 0,
        horizontal: bool = False,
        **kwargs: Any
    ) -> None:
        super().__init__(parent, **kwargs)
        self._horizontal = bool(horizontal)
        self._wheel_bound: Set[int] = set()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            background="#f3f3f3",
        )
        self.vertical_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vertical_scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical_scrollbar.grid(row=0, column=1, sticky="ns")

        self.horizontal_scrollbar = None
        if self._horizontal:
            self.horizontal_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
            self.canvas.configure(xscrollcommand=self.horizontal_scrollbar.set)
            self.horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.content = ttk.Frame(self.canvas, padding=content_padding)
        self._content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._on_content_configure, add=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure, add=True)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel, add=True)
        self.canvas.bind("<Button-4>", self._on_mousewheel, add=True)
        self.canvas.bind("<Button-5>", self._on_mousewheel, add=True)

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def scroll_to_widget(self, widget: tk.Misc, padding: int = 8) -> bool:
        """Przewija tak, aby wskazany element był widoczny przy górnej krawędzi.

        Widoki laboratoryjne mogą być celowo gęste.  Metoda pozwala prowadzić
        użytkownika do dalszej części jednego widoku bez tworzenia drugiego
        drzewa zakładek i bez ręcznego liczenia pozycji elementu przez każdy
        panel.  Zwraca ``False``, jeżeli element nie należy do ``content`` lub
        nie ma jeszcze mierzalnego obszaru.
        """

        try:
            self.update_idletasks()
            content_root_y = self.content.winfo_rooty()
            widget_root_y = widget.winfo_rooty()
        except tk.TclError:
            return False

        bbox = self.canvas.bbox(self._content_window)
        if bbox is None:
            return False
        total_height = max(0, int(bbox[3]) - int(bbox[1]))
        viewport_height = max(0, int(self.canvas.winfo_height()))
        if total_height <= 0 or viewport_height <= 0:
            return False

        # Canvas interpretuje argument yview_moveto jako część całego
        # scrollregionu, a nie tylko przewijalnego odcinka.
        requested_offset = max(0, int(widget_root_y - content_root_y) - max(0, int(padding)))
        maximum_fraction = max(0.0, 1.0 - (float(viewport_height) / float(total_height)))
        fraction = min(float(requested_offset) / float(total_height), maximum_fraction)
        self.canvas.yview_moveto(fraction)
        return True

    def _on_content_configure(self, _event: Any = None) -> None:
        self._bind_wheel_to_children()
        self._refresh_scroll_region()

    def _on_canvas_configure(self, event: Any) -> None:
        if not self._horizontal:
            self.canvas.itemconfigure(self._content_window, width=max(1, int(event.width)))
        self._refresh_scroll_region()

    def _refresh_scroll_region(self) -> None:
        bbox = self.canvas.bbox(self._content_window)
        if bbox is not None:
            self.canvas.configure(scrollregion=bbox)

    def _bind_wheel_to_children(self) -> None:
        """Podpina koło myszy także do pól utworzonych dynamicznie."""

        def visit(widget: tk.Misc) -> None:
            try:
                identifier = int(widget.winfo_id())
            except tk.TclError:
                return
            if identifier not in self._wheel_bound:
                self._wheel_bound.add(identifier)
                widget.bind("<MouseWheel>", self._on_mousewheel, add=True)
                widget.bind("<Button-4>", self._on_mousewheel, add=True)
                widget.bind("<Button-5>", self._on_mousewheel, add=True)
            for child in widget.winfo_children():
                visit(child)

        visit(self.content)

    def _on_mousewheel(self, event: Any) -> str:
        if getattr(event, "num", None) == 4:
            amount = -3
        elif getattr(event, "num", None) == 5:
            amount = 3
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return "break"
            amount = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
        self.canvas.yview_scroll(amount, "units")
        return "break"
