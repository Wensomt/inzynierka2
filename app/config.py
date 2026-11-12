from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
APP_TITLE = "Symulator efektywności energetycznej sieci O-RAN"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) if getattr(sys, "frozen", False) else BASE_DIR


def _frozen_data_dir() -> Path:
    if os.name == "nt":
        parent = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    else:
        parent = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return parent / "O-RAN Energy Lab"


if getattr(sys, "frozen", False):
    APP_DATA_DIR = Path(
        os.environ.get(
            "ORAN_LAB_DATA_DIR",
            str(_frozen_data_dir()),
        )
    ).expanduser()
else:
    APP_DATA_DIR = Path(os.environ.get("ORAN_LAB_DATA_DIR", str(BASE_DIR / "data"))).expanduser()

CONFIG_PATH = Path(
    os.environ.get(
        "ORAN_LAB_CONFIG_PATH",
        str(APP_DATA_DIR / "config.json") if getattr(sys, "frozen", False) else str(BASE_DIR / "config.json"),
    )
).expanduser()


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = RLock()
        self._data: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_config_file()
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            self._upgrade_my_energy_metadata()
            return self.as_dict()

    def _upgrade_my_energy_metadata(self) -> None:
        """Dodaje nowe pole do starej konfiguracji EXE, zachowując ustawienia."""
        scenario = (self._data.get("scenarios") or {}).get("optimized") or {}
        if Path(str(scenario.get("path", "")).replace("\\", "/")).name != "my_energy_scenario_optimized.cc":
            return
        allowed = scenario.get("allowed_params")
        if not isinstance(allowed, list):
            return
        changed = False
        if "CellLayout" not in allowed:
            allowed.append("CellLayout")
            changed = True
        parameters = ((self._data.get("xapps") or {}).get("optimized") or {}).get("parameters") or {}
        for key in ("EE_DECISION_PERIOD_SEC", "EE_SHUTDOWN_CONFIRMATION_SEC", "EE_SHUTDOWN_TIMEOUT_SEC", "EE_CELL_SWITCH_COOLDOWN_SEC", "EE_KPM_MAX_AGE_SEC"):
            if key in parameters and parameters[key].get("unit") != "s ns-3":
                parameters[key]["unit"] = "s ns-3"
                changed = True
        if changed:
            backup = self.path.with_name(self.path.stem + ".before-my-energy-editor.json")
            if not backup.exists():
                shutil.copy2(self.path, backup)
            self._atomic_write(self._data)

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if not isinstance(data, dict):
                raise ValueError("Konfiguracja musi być obiektem JSON")
            if int(data.get("readiness", {}).get("ric_port", 36421)) != 36421:
                raise ValueError("nearRT-RIC musi używać portu 36421")
            self._atomic_write(data)
            self._data = data
            return self.as_dict()

    def save_discovery_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            data = self.as_dict()
            lab = data.setdefault("lab", {})
            lab["last_discovery"] = snapshot
            paths = lab.setdefault("discovered_paths", {})
            components = snapshot.get("components", {})
            mapping = {
                "near_rt_ric": "near_rt_ric",
                "ns3_root": "ns3_root",
                "ns3_wrapper": "ns3_wrapper",
                "flexric_root": "flexric_root",
            }
            for source, target in mapping.items():
                path = components.get(source, {}).get("path")
                if path:
                    paths[target] = path
            lab["discovered_paths"] = paths
            return self.save(data)

    @property
    def bundled_default_path(self) -> Path:
        """Ścieżka do konfiguracji dostarczonej z bieżącą wersją aplikacji."""

        return RESOURCE_DIR / "config.json"

    def restore_bundled_default(self) -> Optional[Path]:
        """Zastępuje lokalną konfigurację aktualnym domyślnym plikiem.

        W wersji PyInstaller dane użytkownika znajdują się poza katalogiem
        aplikacji i celowo przetrwają aktualizację EXE. To jest pożądane dla
        własnych ustawień, ale może także zatrzymać dawny opis scenariusza po
        zainstalowaniu nowej wersji. Przed zastąpieniem zawsze tworzymy kopię
        dotychczasowego pliku; zwrócona ścieżka jest ``None`` tylko w trybie
        deweloperskim, gdy plik docelowy jest już plikiem dostarczonym.
        """

        with self._lock:
            bundled = self.bundled_default_path
            if not bundled.is_file():
                raise FileNotFoundError(
                    "Brak dostarczonego pliku konfiguracji: {}".format(bundled)
                )
            try:
                data = json.loads(bundled.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Nie można odczytać dostarczonej konfiguracji: {}".format(exc)
                )
            if not isinstance(data, dict):
                raise ValueError("Dostarczona konfiguracja musi być obiektem JSON")

            try:
                same_file = self.path.resolve() == bundled.resolve()
            except OSError:
                same_file = self.path == bundled
            if same_file:
                self._data = data
                return None

            backup: Optional[Path] = None
            if self.path.is_file():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                for suffix in range(1, 1000):
                    candidate = self.path.with_name(
                        "{}.before-update-{}-{:03d}{}".format(
                            self.path.stem,
                            stamp,
                            suffix,
                            self.path.suffix or ".json",
                        )
                    )
                    if not candidate.exists():
                        backup = candidate
                        break
                if backup is None:
                    raise RuntimeError("Nie można utworzyć unikalnej kopii konfiguracji")
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.path, backup)

            # Używamy tego samego atomowego zapisu co dla zwykłej edycji
            # ustawień — brak ryzyka pozostawienia połowy pliku po awarii.
            self._atomic_write(data)
            self._data = data
            return backup

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            Path(temporary_name).replace(self.path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    def _ensure_config_file(self) -> None:
        if self.path.is_file():
            return
        default_path = RESOURCE_DIR / "config.json"
        if self.path != default_path and default_path.is_file():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(default_path.read_text(encoding="utf-8"), encoding="utf-8")
            return
        raise FileNotFoundError("Brak pliku konfiguracji: {}".format(self.path))

    @staticmethod
    def expand(value: str) -> Path:
        return Path(value).expanduser().resolve()
