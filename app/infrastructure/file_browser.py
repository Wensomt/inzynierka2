"""Otwieranie lokalnego katalogu lub wskazanie pliku w menedżerze plików."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_in_file_manager(path: Path) -> None:
    """Otwiera folder albo zaznacza plik w Eksploratorze Windows."""

    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError("Nie znaleziono ścieżki: {}".format(target))
    if os.name == "nt":
        if target.is_dir():
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(
                ["explorer.exe", "/select,", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    elif sys.platform == "darwin":
        subprocess.Popen(
            ["open", "-R", str(target)] if target.is_file() else ["open", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        directory = target.parent if target.is_file() else target
        subprocess.Popen(
            ["xdg-open", str(directory)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
