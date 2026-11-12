from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


DEFAULT_SIM_TIME_S = 5


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimulationStatus(str, Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass
class SimulationConfiguration:
    """Konfiguracja faktycznie przekazywana do scenariusza i xAppa."""

    scenario: str
    parameters: Dict[str, Any]
    xapp: str = "none"
    xapp_environment: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario,
            "parameters": dict(self.parameters),
            "xapp": self.xapp,
            "xapp_environment": dict(self.xapp_environment),
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "SimulationConfiguration":
        return cls(
            scenario=str(value.get("scenario") or ""),
            parameters=dict(value.get("parameters") or value.get("scenario_params") or {}),
            xapp=str(value.get("xapp") or "none"),
            xapp_environment=dict(value.get("xapp_environment") or value.get("xapp_env") or {}),
        )


@dataclass
class SimulationResult:
    """Wynik wyprowadzony z artefaktów zapisanych przez symulator."""

    metrics: Dict[str, Optional[float]] = field(default_factory=dict)
    quality: Dict[str, Optional[float]] = field(default_factory=dict)
    series: Dict[str, List[Dict[str, float]]] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "SimulationResult":
        return cls(
            metrics=dict(value.get("metrics") or {}),
            quality=dict(value.get("quality") or {}),
            series=dict(value.get("series") or {}),
            sources=dict(value.get("sources") or {}),
            warnings=list(value.get("warnings") or []),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class Simulation:
    """Jedna niezależna symulacja widoczna w sesji laboratorium."""

    name: str
    configuration: SimulationConfiguration
    id: str = field(default_factory=lambda: str(uuid4()))
    status: SimulationStatus = SimulationStatus.DRAFT
    created_at: str = field(default_factory=now_utc)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    runtime: Dict[str, Any] = field(default_factory=dict)
    result: Optional[SimulationResult] = None
    error: Optional[str] = None
    log_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "id": self.id,
            "name": self.name,
            "configuration": self.configuration.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "runtime": dict(self.runtime),
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Simulation":
        raw_status = str(value.get("status") or SimulationStatus.DRAFT.value)
        try:
            status = SimulationStatus(raw_status)
        except ValueError:
            status = SimulationStatus.DRAFT
        raw_result = value.get("result")
        return cls(
            id=str(value.get("id") or value.get("uuid") or uuid4()),
            name=str(value.get("name") or "Symulacja"),
            configuration=SimulationConfiguration.from_dict(
                dict(value.get("configuration") or {})
            ),
            status=status,
            created_at=str(value.get("created_at") or now_utc()),
            started_at=value.get("started_at"),
            ended_at=value.get("ended_at"),
            runtime=dict(value.get("runtime") or {}),
            result=SimulationResult.from_dict(raw_result) if isinstance(raw_result, dict) else None,
            error=value.get("error"),
            log_path=value.get("log_path"),
        )
