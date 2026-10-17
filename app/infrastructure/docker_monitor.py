from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _as_number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cpu_percent(stats: Dict[str, Any]) -> Optional[float]:
    current = (stats.get("cpu_stats") or {}) if isinstance(stats, dict) else {}
    previous = (stats.get("precpu_stats") or {}) if isinstance(stats, dict) else {}
    current_usage = (current.get("cpu_usage") or {}) if isinstance(current, dict) else {}
    previous_usage = (previous.get("cpu_usage") or {}) if isinstance(previous, dict) else {}
    cpu_delta = _as_number(current_usage.get("total_usage"))
    previous_cpu = _as_number(previous_usage.get("total_usage"))
    system_delta = _as_number(current.get("system_cpu_usage"))
    previous_system = _as_number(previous.get("system_cpu_usage"))
    if (
        cpu_delta is None
        or previous_cpu is None
        or system_delta is None
        or previous_system is None
    ):
        return None
    cpu_delta -= previous_cpu
    system_delta -= previous_system
    if cpu_delta < 0 or system_delta <= 0:
        return None
    cpus = _as_number(current.get("online_cpus"))
    if cpus is None or cpus <= 0:
        per_cpu = current_usage.get("percpu_usage")
        cpus = float(len(per_cpu)) if isinstance(per_cpu, list) and per_cpu else 1.0
    return (cpu_delta / system_delta) * cpus * 100.0


def inspect_container(container: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Zwraca mały snapshot jednego kontenera bez utrzymywania streamu stats.

    Wywołujący musi uruchamiać tę funkcję poza wątkiem Tkintera. Brak healthchecka
    i brak danych statystycznych są raportowane jako ``None``, a nie jako sukces.
    """

    errors = []
    try:
        container.reload()
    except Exception as exc:
        errors.append("Nie można odświeżyć kontenera: {}".format(exc))

    attrs = getattr(container, "attrs", {}) or {}
    state = attrs.get("State", {}) if isinstance(attrs, dict) else {}
    state = state if isinstance(state, dict) else {}
    config = attrs.get("Config", {}) if isinstance(attrs, dict) else {}
    config = config if isinstance(config, dict) else {}
    health_data = state.get("Health") if isinstance(state.get("Health"), dict) else {}
    health = health_data.get("Status") if health_data else None

    stats: Dict[str, Any] = {}
    if str(state.get("Status") or getattr(container, "status", "")) == "running":
        try:
            received = container.stats(stream=False)
            stats = received if isinstance(received, dict) else {}
        except Exception as exc:
            errors.append("Nie można odczytać statystyk: {}".format(exc))

    memory = (stats.get("memory_stats") or {}) if isinstance(stats, dict) else {}
    memory = memory if isinstance(memory, dict) else {}
    memory_usage = _as_number(memory.get("usage"))
    memory_limit = _as_number(memory.get("limit"))
    memory_pct = (
        (memory_usage / memory_limit) * 100.0
        if memory_usage is not None and memory_limit is not None and memory_limit > 0
        else None
    )
    started = _timestamp(state.get("StartedAt"))
    ended = _timestamp(state.get("FinishedAt"))
    current = now or datetime.now(timezone.utc)
    until = ended or current
    uptime_s = max(0.0, (until - started).total_seconds()) if started is not None else None
    restart_count = _as_number(attrs.get("RestartCount") if isinstance(attrs, dict) else None)

    return {
        "id": str(getattr(container, "id", attrs.get("Id", ""))),
        "short_id": str(getattr(container, "short_id", "") or str(getattr(container, "id", ""))[:12]),
        "name": str(getattr(container, "name", attrs.get("Name", ""))).lstrip("/"),
        "image": str(config.get("Image") or ""),
        "state": str(state.get("Status") or getattr(container, "status", "") or "unknown"),
        "health": str(health) if health else None,
        "started_at": state.get("StartedAt") or None,
        "finished_at": state.get("FinishedAt") or None,
        "uptime_s": uptime_s,
        "restart_count": int(restart_count) if restart_count is not None else None,
        "exit_code": state.get("ExitCode") if state.get("ExitCode") is not None else None,
        "oom_killed": state.get("OOMKilled") if state.get("OOMKilled") is not None else None,
        "error": state.get("Error") or None,
        "cpu_pct": _cpu_percent(stats),
        "memory_bytes": memory_usage,
        "memory_limit_bytes": memory_limit,
        "memory_pct": memory_pct,
        "errors": errors,
    }
