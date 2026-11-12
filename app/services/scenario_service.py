from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from uuid import uuid4

from app.simulation.models import DEFAULT_SIM_TIME_S, SimulationConfiguration
from app.simulation.validation import validate_configuration


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SavedScenario:
    """Trwały, nazwany zestaw realnych parametrów uruchomienia."""

    id: str
    name: str
    configuration: SimulationConfiguration
    created_at: str
    updated_at: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "configuration": self.configuration.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SavedScenario":
        return cls(
            id=str(value.get("id") or uuid4()),
            name=str(value.get("name") or "Scenariusz"),
            description=str(value.get("description") or ""),
            configuration=SimulationConfiguration.from_dict(
                dict(value.get("configuration") or value.get("config") or {})
            ),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
        )


# Dane opisowe są oparte na GlobalValue aktywnego, dostarczonego scenariusza.
# Gdy scenariusz nie deklaruje danego klucza w config.json, widok go nie pokaże.
PARAMETER_META: Dict[str, Dict[str, Any]] = {
    "CellLayout": {
        "group": "RADIO", "label": "Układ: 0 środek, 1 wielokąt", "unit": "",
        "description": "Osobne gNB: 0 środek i pierścień, 1 regularny wielokąt. To nie są sektory wspólnego masztu.",
        "default": 1, "minimum": 0, "maximum": 1, "kind": "integer",
    },
    "simTime": {
        "group": "GENERAL",
        "label": "Czas symulacji",
        "unit": "s",
        "description": "Czas modelowany przez ns-3, niezależny od zegara komputera. Zacznij od krótkiego przebiegu.",
        "default": DEFAULT_SIM_TIME_S,
        "minimum": 0.2,
        "maximum": 100000.0,
        "kind": "float",
    },
    "RngRun": {
        "group": "GENERAL",
        "label": "RngRun",
        "unit": "",
        "description": "Numer uruchomienia generatora losowego ns-3.",
        "default": 1,
        "minimum": 1,
        "kind": "integer",
    },
    "TrafficStart_s": {
        "group": "TRAFFIC", "label": "Początek generowania ruchu", "unit": "s",
        "description": "Czas uruchomienia klientów UDP. Energia i dane są mierzone w całym oknie 0..simTime.",
        "default": 0.1, "minimum": 0, "kind": "float",
    },
    "DrainTime_s": {
        "group": "TRAFFIC", "label": "Czas opróżniania kolejek", "unit": "s",
        "description": "Klienci kończą przed końcem symulacji. 0 wybiera min(2 s, 20% simTime); dodatnia wartość jest używana dokładnie.",
        "default": 0, "minimum": 0, "kind": "float",
    },
    "N_Ues": {
        "group": "UE",
        "label": "Liczba UE",
        "unit": "",
        "description": "Liczba urządzeń użytkownika w symulacji.",
        "default": 3,
        "minimum": 1,
        "maximum": 512,
        "kind": "integer",
    },
    "N_MmWaveEnbNodes": {
        "group": "RADIO",
        "label": "Liczba gNB",
        "unit": "",
        "description": "Liczba węzłów mmWave/gNB.",
        "default": 4,
        "minimum": 1,
        "maximum": 255,
        "kind": "integer",
    },
    "Bandwidth": {
        "group": "RADIO",
        "label": "Szerokość pasma",
        "unit": "Hz",
        "description": "Szerokość pasma modelu radiowego.",
        "default": 20000000,
        "minimum": 0.000001,
        "kind": "float",
    },
    "CenterFrequency": {
        "group": "RADIO",
        "label": "Częstotliwość środkowa",
        "unit": "Hz",
        "description": "Częstotliwość modelu propagacji.",
        "default": 3500000000,
        "minimum": 0.000001,
        "kind": "float",
    },
    "IntersideDistanceUEs": {
        "group": "UE",
        "label": "Promień obszaru UE",
        "unit": "m",
        "description": "Wartość odległości używana przy rozmieszczeniu UE.",
        "default": 500,
        "kind": "float",
    },
    "IntersideDistanceCells": {
        "group": "RADIO",
        "label": "Rozstaw komórek NR",
        "unit": "m",
        "description": "Wartość odległości używana przy rozmieszczeniu komórek.",
        "default": 600,
        "kind": "float",
    },
    "UdpPacketSizeBytes": {
        "group": "TRAFFIC",
        "label": "Rozmiar pakietu UDP",
        "unit": "B",
        "description": "Rozmiar pakietu downlink generowanego dla każdego UE.",
        "default": 200,
        "minimum": 12,
        "maximum": 65507,
        "kind": "integer",
    },
    "UdpIntervalUs": {
        "group": "TRAFFIC",
        "label": "Odstęp pakietów UDP",
        "unit": "µs",
        "description": "Interwał między pakietami downlink dla każdego UE.",
        "default": 500,
        "minimum": 1,
        "maximum": 4294967295,
        "kind": "integer",
    },
    "indicationPeriodicity": {
        "group": "GENERAL",
        "label": "Okres raportu KPM",
        "unit": "s",
        "description": "Okres raportowania E2/KPM.",
        "default": 0.1,
        "minimum": 0.01,
        "maximum": 2.0,
        "kind": "float",
    },
    "e2TermIp": {
        "group": "GENERAL",
        "label": "Adres E2 termination",
        "unit": "",
        "description": "Adres endpointu E2 używany przez scenariusz.",
        "default": "127.0.0.1",
        "kind": "text",
    },
    "KPM_E2functionID": {
        "group": "GENERAL",
        "label": "KPM E2 function ID",
        "unit": "",
        "description": "Identyfikator funkcji KPM przekazywany do scenariusza.",
        "default": 2,
        "kind": "float",
    },
    "RC_E2functionID": {
        "group": "GENERAL",
        "label": "RC E2 function ID",
        "unit": "",
        "description": "Identyfikator funkcji RC przekazywany do scenariusza.",
        "default": 3,
        "kind": "float",
    },
    "e2nrEnabled": {"group": "GENERAL", "label": "Raporty NR E2", "unit": "", "description": "Włącza raporty E2 dla NR.", "default": True, "kind": "bool"},
    "e2lteEnabled": {"group": "GENERAL", "label": "Raporty LTE E2", "unit": "", "description": "Włącza raporty E2 dla LTE.", "default": True, "kind": "bool"},
    "enableE2FileLogging": {"group": "GENERAL", "label": "E2 file logging", "unit": "", "description": "Zapisuje log E2 do pliku zamiast łączyć z RIC.", "default": False, "kind": "bool"},
    "bufferSize": {"group": "TRAFFIC", "label": "Bufor RLC", "unit": "MiB", "description": "Rozmiar bufora RLC tx (1 MiB = 1048576 B).", "default": 10, "minimum": 0, "maximum": 4095, "kind": "integer"},
    "enableTraces": {"group": "GENERAL", "label": "Traces ns-3", "unit": "", "description": "Włącza ślady ns-3.", "default": True, "kind": "bool"},
    "outageThreshold": {"group": "RADIO", "label": "Próg outage", "unit": "dB", "description": "Próg SNR dla zdarzeń outage.", "default": -5.0, "kind": "float"},
    "numberOfRaPreambles": {"group": "RADIO", "label": "Preambuły RA", "unit": "", "description": "Liczba preambuł random access obsługiwana przez MAC.", "default": 40, "minimum": 4, "maximum": 64, "kind": "integer"},
    "e2du": {"group": "GENERAL", "label": "Raporty DU", "unit": "", "description": "Włącza raporty E2 DU.", "default": True, "kind": "bool"},
    "e2cuUp": {"group": "GENERAL", "label": "Raporty CU-UP", "unit": "", "description": "Włącza raporty E2 CU-UP.", "default": True, "kind": "bool"},
    "e2cuCp": {"group": "GENERAL", "label": "Raporty CU-CP", "unit": "", "description": "Włącza raporty E2 CU-CP.", "default": True, "kind": "bool"},
    "reducedPmValues": {"group": "GENERAL", "label": "Ograniczone PM", "unit": "", "description": "Używa podzbioru kontenerów PM.", "default": False, "kind": "bool"},
    "controlFileName": {"group": "GENERAL", "label": "Plik sterowania", "unit": "", "description": "Ścieżka do pliku sterowania scenariusza.", "default": "", "kind": "text"},
}


class ScenarioService:
    """CRUD scenariuszy, bez modyfikowania globalnego config.json."""

    def __init__(self, root: Path, application_config: Mapping[str, Any]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.application_config = dict(application_config)

    def list(self) -> List[SavedScenario]:
        records: List[SavedScenario] = []
        for path in self.root.glob("*.json"):
            value = self._read_json(path)
            if not value:
                continue
            try:
                item = SavedScenario.from_dict(value)
                item.configuration = self.normalise_configuration(item.configuration)
                records.append(item)
            except (TypeError, ValueError):
                continue
        return sorted(records, key=lambda item: (item.name.casefold(), item.id))

    def get(self, scenario_id: str) -> SavedScenario:
        value = self._read_json(self._path(scenario_id))
        if not value:
            raise FileNotFoundError("Nie znaleziono scenariusza: {}".format(scenario_id))
        item = SavedScenario.from_dict(value)
        item.configuration = self.normalise_configuration(item.configuration)
        return item

    def create(
        self,
        name: str,
        configuration: SimulationConfiguration,
        description: str = "",
    ) -> SavedScenario:
        now = utc_now()
        item = SavedScenario(
            id=uuid4().hex,
            name=self._clean_name(name),
            description=str(description or "").strip(),
            configuration=self.normalise_configuration(configuration),
            created_at=now,
            updated_at=now,
        )
        self._validate(item.configuration)
        self._write(item)
        return item

    def update(
        self,
        scenario_id: str,
        name: str,
        configuration: SimulationConfiguration,
        description: str = "",
    ) -> SavedScenario:
        existing = self.get(scenario_id)
        existing.name = self._clean_name(name)
        existing.description = str(description or "").strip()
        existing.configuration = self.normalise_configuration(configuration)
        existing.updated_at = utc_now()
        self._validate(existing.configuration)
        self._write(existing)
        return existing

    def duplicate(self, scenario_id: str, name: Optional[str] = None) -> SavedScenario:
        source = self.get(scenario_id)
        return self.create(
            name or "{} — kopia".format(source.name),
            copy.deepcopy(source.configuration),
            source.description,
        )

    def delete(self, scenario_id: str) -> None:
        path = self._path(scenario_id)
        if not path.is_file():
            raise FileNotFoundError("Nie znaleziono scenariusza: {}".format(scenario_id))
        path.unlink()

    def export(self, scenario_id: str, destination: Path) -> Path:
        item = self.get(scenario_id)
        path = Path(destination)
        self._write_json(path, item.to_dict())
        return path

    def import_file(self, source: Path, name: Optional[str] = None) -> SavedScenario:
        value = self._read_json(Path(source))
        if not value:
            raise ValueError("Plik scenariusza nie zawiera poprawnego JSON.")
        item = SavedScenario.from_dict(value)
        return self.create(name or item.name, item.configuration, item.description)

    def normalise_configuration(self, configuration: SimulationConfiguration) -> SimulationConfiguration:
        """Zachowuje wyłącznie realne pola dozwolone przez aktywny config."""

        scenarios = dict(self.application_config.get("scenarios") or {})
        xapps = dict(self.application_config.get("xapps") or {})
        selected = configuration.scenario
        if selected not in scenarios:
            raise ValueError("Nieznany scenariusz: {}".format(selected))
        allowed = {str(key) for key in (scenarios[selected].get("allowed_params") or [])}
        parameters = {
            str(key): value
            for key, value in dict(configuration.parameters or {}).items()
            if not allowed or str(key) in allowed
        }
        xapp = str(configuration.xapp or "none")
        if xapp not in xapps:
            raise ValueError("Nieznany xApp: {}".format(xapp))
        xapp_item = dict(xapps.get(xapp) or {})
        supported_env = {str(key) for key in dict(xapp_item.get("parameters") or {})}
        xapp_environment = {
            str(key): value
            for key, value in dict(configuration.xapp_environment or {}).items()
            if str(key) in supported_env
        }
        return SimulationConfiguration(
            scenario=selected,
            parameters=parameters,
            xapp=xapp,
            xapp_environment=xapp_environment,
        )

    def allowed_parameters(self, scenario_key: str) -> List[str]:
        item = (self.application_config.get("scenarios") or {}).get(scenario_key) or {}
        return [str(key) for key in item.get("allowed_params") or []]

    @staticmethod
    def parameter_meta(key: str) -> Dict[str, Any]:
        return dict(PARAMETER_META.get(key) or {
            "group": "GENERAL",
            "label": key,
            "unit": "",
            "description": "Parametr przekazywany bezpośrednio do scenariusza.",
            "kind": "text",
        })

    def _validate(self, configuration: SimulationConfiguration) -> None:
        errors = validate_configuration(configuration, self.application_config)
        if errors:
            raise ValueError("; ".join(errors))

    def _path(self, scenario_id: str) -> Path:
        safe = str(scenario_id or "")
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", safe):
            raise ValueError("Niepoprawny identyfikator scenariusza")
        return self.root / (safe + ".json")

    @staticmethod
    def _clean_name(value: str) -> str:
        name = " ".join(str(value or "").strip().split())
        if not name:
            raise ValueError("Podaj nazwę scenariusza")
        if len(name) > 120:
            raise ValueError("Nazwa scenariusza może mieć najwyżej 120 znaków")
        return name

    def _write(self, item: SavedScenario) -> None:
        self._write_json(self._path(item.id), item.to_dict())

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            Path(temporary_name).replace(path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
