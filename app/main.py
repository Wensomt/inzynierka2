from __future__ import annotations

import signal
import sys
import tkinter as tk
from typing import Iterable, Optional

from app.cleanup.guardian import main as guardian_main
from app.config import ConfigStore


def main(argv: Optional[Iterable[str]] = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "--cleanup-guardian":
        return guardian_main(arguments[1:])

    from app.gui.main_window import MainWindow

    root = tk.Tk()
    window = MainWindow(root, ConfigStore())

    def request_close(_signum: int, _frame: object) -> None:
        root.after(0, window._on_close)

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        try:
            signal.signal(signum, request_close)
        except (ValueError, OSError):
            pass
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
