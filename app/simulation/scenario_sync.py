"""Synchronizacja dostarczonego źródła scenariusza do świeżej sesji Docker.

Obraz symulatora może zawierać starszy wariant źródła niż katalog aplikacji.
Ponieważ każda symulacja i tak dostaje osobny kontener sesji, aktualizacja
dotyczy wyłącznie tego kontenera i nie modyfikuje obrazu ani cudzych zasobów.
To pozwala uruchamiać dokładnie scenariusz dostarczony z aplikacją, w tym
zapis ``ee_timeseries.csv`` używany przez widok live.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import shlex
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from app.config import RESOURCE_DIR
from app.services.my_energy_sources import source_path


@dataclass(frozen=True)
class ScenarioSyncResult:
    """Wynik synchronizacji jednego pliku źródłowego."""

    state: str
    local_path: str
    remote_path: str
    digest: str


def packaged_scenario_path(config: Mapping[str, Any], scenario_key: str) -> Optional[Path]:
    """Zwraca oczekiwaną ścieżkę dostarczonego źródła scenariusza.

    Wynik może nie istnieć.  Rozróżnienie ścieżki oczekiwanej od istniejącego
    pliku pozwala runnerowi wyjaśnić błąd paczki zamiast uruchamiać po cichu
    starsze źródło obecne tylko w obrazie Docker.
    """

    if scenario_key == "optimized":
        return source_path(config, "scenario")
    scenarios = config.get("scenarios")
    item = scenarios.get(scenario_key) if isinstance(scenarios, Mapping) else None
    configured_path = item.get("path") if isinstance(item, Mapping) else None
    base_name = posixpath.basename(str(configured_path or "").replace("\\", "/"))
    if not base_name:
        return None
    return RESOURCE_DIR / "resources" / base_name


def packaged_scenario_source(config: Mapping[str, Any], scenario_key: str) -> Optional[Path]:
    """Zwraca istniejący lokalny odpowiednik scenariusza, jeśli go dostarczono."""

    candidate = packaged_scenario_path(config, scenario_key)
    if candidate is None:
        return None
    return candidate if candidate.is_file() else None


def source_declares_ee_timeseries(path: Optional[Path]) -> bool:
    """Czy dostarczone źródło deklaruje pełną serię metryk live.

    To nie jest zgadywanie na podstawie nazwy scenariusza.  Sprawdzamy markery
    obecne w implementacji C++, która tworzy sześć metryk na potrzeby GUI.
    """

    if path is None or not path.is_file():
        return False
    try:
        content = path.read_bytes()
    except OSError:
        return False
    required_markers = (
        b"WriteEeTimeseries",
        b"ee_timeseries.csv",
        b"DeliveredBits",
        b"CumulativeEE_bit_per_J",
    )
    return all(marker in content for marker in required_markers)


def source_digest(path: Path) -> str:
    """Liczy stabilny skrót pliku bez ładowania całego źródła do pamięci."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sync_packaged_scenario(
    processes: Any,
    local_source: Path,
    remote_path: str,
) -> ScenarioSyncResult:
    """Kopiuje źródło tylko wtedy, gdy suma kontrolna różni się w kontenerze."""

    if not local_source.is_file():
        raise FileNotFoundError("Nie znaleziono źródła scenariusza: {}".format(local_source))
    remote = str(remote_path or "")
    if not remote.startswith("/") or posixpath.basename(remote) in {"", ".", ".."}:
        raise ValueError("Niepoprawna ścieżka scenariusza w kontenerze: {}".format(remote_path))

    local_digest = source_digest(local_source)
    current = _remote_digest(processes, remote)
    if current == local_digest:
        # Obraz może zawierać zgodny plik .cc, ale binarny target ns-3 z
        # wcześniejszej kompilacji. Zawsze oznaczamy źródło jako świeże, aby
        # `ns3 run` nie uruchomił takiego starego artefaktu.
        _touch_remote_source(processes, remote)
        return ScenarioSyncResult("unchanged", str(local_source), remote, local_digest)

    container = getattr(getattr(processes, "session", None), "container", None)
    put_archive = getattr(container, "put_archive", None)
    if not callable(put_archive):
        raise RuntimeError("Kontener sesji nie obsługuje kopiowania źródła scenariusza")

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        data = local_source.read_bytes()
        entry = tarfile.TarInfo(name=posixpath.basename(remote))
        entry.size = len(data)
        # Znacznik czasu wymusza przebudowę targetu przez istniejący wrapper
        # ns-3 przy kolejnym `ns3 run`, jeżeli treść źródła się zmieniła.
        entry.mtime = int(time.time())
        archive.addfile(entry, io.BytesIO(data))
    try:
        accepted = put_archive(posixpath.dirname(remote), payload.getvalue())
    except Exception as exc:
        raise RuntimeError("Nie można skopiować źródła scenariusza do kontenera: {}".format(exc)) from exc
    if accepted is False:
        raise RuntimeError("Kontener odrzucił kopiowanie źródła scenariusza")

    _touch_remote_source(processes, remote)
    verified = _remote_digest(processes, remote)
    if verified != local_digest:
        raise RuntimeError("Nie potwierdzono poprawnego kopiowania źródła scenariusza")
    return ScenarioSyncResult("updated", str(local_source), remote, local_digest)


def _touch_remote_source(processes: Any, remote: str) -> None:
    """Wymusza sprawdzenie przebudowy targetu ns-3 dla źródła scenariusza."""

    code, output = processes.exec_text("touch {}".format(shlex.quote(remote)))
    if code != 0:
        raise RuntimeError(
            "Nie można oznaczyć źródła scenariusza do przebudowy: {}".format(
                output.strip()
            )
        )


def _remote_digest(processes: Any, remote_path: str) -> Optional[str]:
    script_source = (
        "import hashlib, pathlib, sys; path=pathlib.Path(sys.argv[1]); "
        "sys.exit(3) if not path.is_file() else None; "
        "print(hashlib.sha256(path.read_bytes()).hexdigest())"
    )
    command = "python3 -c {} {}".format(shlex.quote(script_source), shlex.quote(remote_path))
    code, output = processes.exec_text(command)
    if code != 0:
        return None
    value = str(output or "").strip().splitlines()
    digest = value[-1].strip().lower() if value else ""
    return digest if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) else None


__all__ = [
    "ScenarioSyncResult",
    "packaged_scenario_path",
    "packaged_scenario_source",
    "source_declares_ee_timeseries",
    "source_digest",
    "sync_packaged_scenario",
]
