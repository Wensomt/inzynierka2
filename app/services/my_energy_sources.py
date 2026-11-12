"""Edytowalne źródła My Energy, wspólne dla GUI i uruchamiania w Dockerze."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Mapping, Any

from app.config import APP_DATA_DIR, RESOURCE_DIR

FILES = {"xapp": "xapp_my_energy_optimized.c", "scenario": "my_energy_scenario_optimized.cc"}
_lock = RLock()


def source_path(config: Mapping[str, Any], kind: str) -> Path:
    name = FILES[kind]
    configured = (config.get("lab") or {}).get("my_energy_source_dir")
    root = Path(configured) if configured else APP_DATA_DIR / "my_energy"
    with _lock:
        root.mkdir(parents=True, exist_ok=True)
        target = root / name
        bundled = RESOURCE_DIR / "resources" / name
        manifest = root / "bundled.json"
        previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
        digest = hashlib.sha256(bundled.read_bytes()).hexdigest()
        # Aktualizacja aplikacji odświeża wyłącznie niezmienioną kopię wzorca.
        # Katalog wskazany jawnie (np. zamrożony eksperyment) jest niezależny.
        if not target.exists() or (not configured and hashlib.sha256(target.read_bytes()).hexdigest() == previous.get(name) and digest != previous.get(name)):
            target.write_bytes(bundled.read_bytes())
        previous[name] = digest
        manifest.write_text(json.dumps(previous, indent=2), encoding="utf-8")
        return target


def save_source(config: Mapping[str, Any], kind: str, content: str) -> Path:
    if not content.strip():
        raise ValueError("Źródło nie może być puste")
    with _lock:
        path = source_path(config, kind)
        if path.read_text(encoding="utf-8") == content:
            return path
        backup = path.parent / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + path.name)
        backup.parent.mkdir(exist_ok=True)
        backup.write_bytes(path.read_bytes())
        path.write_text(content, encoding="utf-8")
        return path
