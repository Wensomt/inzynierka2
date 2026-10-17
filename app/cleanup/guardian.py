from __future__ import annotations

import argparse
import os
import time
from typing import Any, Iterable, List, Optional


MANAGED_LABEL = "com.oranenergylab.managed"
SESSION_LABEL = "com.oranenergylab.session"
OWNER_PID_LABEL = "com.oranenergylab.owner-pid"


def process_is_alive(pid: int) -> Optional[bool]:
    """Zwraca True/False lub None, gdy uprawnień nie da się potwierdzić."""

    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
            if not handle:
                return None if ctypes.get_last_error() == 5 else False
            try:
                wait_timeout = 0x00000102
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except PermissionError:
            return None
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return None
    except OSError:
        return False
    return True


def cleanup_session_containers(session_id: str, client: Optional[Any] = None) -> List[str]:
    """Zatrzymuje wyłącznie kontenery z obiema etykietami naszej sesji."""

    if not session_id:
        return []
    if client is None:
        try:
            import docker  # type: ignore[import-not-found]

            client = docker.from_env()
        except Exception:
            return []
    removed: List[str] = []
    filters = {
        "label": [
            "{}=true".format(MANAGED_LABEL),
            "{}={}".format(SESSION_LABEL, session_id),
        ]
    }
    for _attempt in range(2):
        try:
            containers = client.containers.list(all=True, filters=filters)
        except Exception:
            return removed
        if not containers:
            break
        for container in containers:
            labels = getattr(container, "labels", {}) or {}
            if labels.get(MANAGED_LABEL) != "true" or labels.get(SESSION_LABEL) != session_id:
                continue
            name = str(getattr(container, "name", "") or getattr(container, "id", ""))
            try:
                container.reload()
                if getattr(container, "status", "") == "running":
                    try:
                        container.stop(timeout=5)
                    except Exception:
                        # force=True poniżej jest celowym drugim mechanizmem cleanupu.
                        pass
                container.remove(force=True)
                if name not in removed:
                    removed.append(name)
            except Exception:
                continue
    return removed


def wait_for_parent_then_cleanup(parent_pid: int, session_id: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(parent_pid))
            if handle:
                try:
                    while ctypes.windll.kernel32.WaitForSingleObject(handle, 500) == 0x00000102:
                        pass
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            else:
                while process_is_alive(parent_pid) is not False:
                    time.sleep(0.5)
        except Exception:
            while process_is_alive(parent_pid) is not False:
                time.sleep(0.5)
    else:
        while process_is_alive(parent_pid) is not False:
            time.sleep(0.5)
    # Daj systemowi chwilę na domknięcie normalnej ścieżki aplikacji.
    time.sleep(0.5)
    cleanup_session_containers(session_id)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--session", required=True)
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    wait_for_parent_then_cleanup(arguments.parent_pid, arguments.session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
