from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.simulation.models import Simulation, SimulationStatus
from app.simulation.validation import parameter_label


@dataclass
class MetricComparison:
    key: str
    label: str
    unit: str
    left: Optional[float]
    right: Optional[float]
    difference: Optional[float]
    difference_kind: str = "percent"


@dataclass
class ConfigurationDifference:
    label: str
    left: Any
    right: Any


@dataclass
class Comparison:
    left_name: str
    right_name: str
    configuration_differences: List[ConfigurationDifference] = field(default_factory=list)
    energy: List[MetricComparison] = field(default_factory=list)
    quality: List[MetricComparison] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    assessment: str = ""
    assessment_kind: str = "inconclusive"


ENERGY_ROWS: Tuple[Tuple[str, str, str], ...] = (
    ("energy_efficiency_bit_per_j", "Średnia efektywność energetyczna", "Mbit/J"),
    ("energy_per_mbit_j", "Energia na 1 Mbit", "J/Mbit"),
    ("total_energy_j", "Energia całkowita", "J"),
    ("average_power_w", "Średnia moc", "W"),
)

QUALITY_ROWS: Tuple[Tuple[str, str, str], ...] = (
    ("throughput_mbit_per_s", "Średnia przepływność użyteczna", "Mbit/s"),
    ("delay_ms", "Średnie opóźnienie odebranych pakietów", "ms"),
    ("packet_loss_pct", "Packet loss", "%"),
    ("delivered_mbit", "Dostarczone dane (estymata PDCP)", "Mbit"),
)

OPTIONAL_QUALITY_ROWS: Tuple[Tuple[str, str, str], ...] = (
    ("mean_sinr_db", "Średni SINR", "dB"),
    ("p05_sinr_db", "SINR p05", "dB"),
    ("mean_prb_pct", "Średnie wykorzystanie PRB", "%"),
    ("active_ues", "Aktywne UE", ""),
)


def compare_simulations(
    left: Simulation, right: Simulation,
    qos_limits: Optional[Dict[str, float]] = None,
) -> Comparison:
    if left.result is None or right.result is None:
        raise ValueError("Obie symulacje muszą mieć zapisany wynik")
    left_metrics, left_quality = _display_metrics(left)
    right_metrics, right_quality = _display_metrics(right)
    energy = _metric_rows(left_metrics, right_metrics, ENERGY_ROWS)
    quality = _metric_rows(left_metrics, right_metrics, QUALITY_ROWS)
    quality.extend(
        row
        for row in _metric_rows(left_quality, right_quality, OPTIONAL_QUALITY_ROWS)
        if row.left is not None or row.right is not None
    )
    _label_live_fallbacks(energy, left, right)
    _label_live_fallbacks(quality, left, right)
    warnings = list(dict.fromkeys(left.result.warnings + right.result.warnings))
    assessment_kind, assessment, audit = _assess(left, right, qos_limits or {})
    warnings.extend(audit)
    return Comparison(
        left_name=left.name,
        right_name=right.name,
        configuration_differences=_configuration_differences(left, right),
        energy=energy,
        quality=quality,
        warnings=warnings,
        assessment=assessment,
        assessment_kind=assessment_kind,
    )


def _display_metrics(simulation: Simulation) -> Tuple[Dict[str, Optional[float]], Dict[str, Optional[float]]]:
    """Dodaje ostatnie KPI live do tabeli, ale nie do oceny ważności eksperymentu."""

    assert simulation.result is not None
    metrics = dict(simulation.result.metrics)
    quality = dict(simulation.result.quality)
    runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
    live = runtime.get("live")
    live = live if isinstance(live, dict) else {}
    current = live.get("current_metrics")
    current = current if isinstance(current, dict) else {}
    for key, value in current.items():
        if value is None:
            continue
        if metrics.get(key) is None and key in metrics:
            metrics[key] = _number(value)
        if quality.get(key) is None and key in quality:
            quality[key] = _number(value)
    return metrics, quality


def _label_live_fallbacks(rows: List[MetricComparison], left: Simulation, right: Simulation) -> None:
    """Oznacza wartości z końcowego snapshotu, aby nie udawały KPI finalnych."""

    for row in rows:
        fallback_used = False
        for simulation in (left, right):
            values = _simulation_metric_values(simulation, row.key)
            if values.get("final") is None and values.get("live") is not None:
                fallback_used = True
        if fallback_used:
            row.label += " (telemetria live)"


def _simulation_metric_values(simulation: Simulation, key: str) -> Dict[str, Optional[float]]:
    """Zwraca końcową oraz ostatnią live wartość dla jednego KPI."""

    if simulation.result is None:
        return {"final": None, "live": None}
    final = simulation.result.metrics.get(key)
    if final is None:
        final = simulation.result.quality.get(key)
    runtime = simulation.runtime if isinstance(simulation.runtime, dict) else {}
    live = runtime.get("live")
    live = live if isinstance(live, dict) else {}
    current = live.get("current_metrics")
    current = current if isinstance(current, dict) else {}
    return {"final": _number(final), "live": _number(current.get(key))}


def _metric_rows(
    left: Dict[str, Optional[float]],
    right: Dict[str, Optional[float]],
    definitions: Iterable[Tuple[str, str, str]],
) -> List[MetricComparison]:
    result = []
    for key, label, unit in definitions:
        left_value = _number(left.get(key))
        right_value = _number(right.get(key))
        if key == "packet_loss_pct":
            difference = right_value - left_value if left_value is not None and right_value is not None else None
            kind = "percentage_points"
        elif key in {"delay_ms", "mean_sinr_db", "p05_sinr_db"}:
            difference = right_value - left_value if left_value is not None and right_value is not None else None
            kind = "absolute"
        else:
            difference = _percent_change(left_value, right_value)
            kind = "percent"
        result.append(MetricComparison(key, label, unit, left_value, right_value, difference, kind))
    return result


def _assess(left: Simulation, right: Simulation, limits: Dict[str, float]) -> Tuple[str, str, List[str]]:
    """Descriptive paired comparison; never a statistical significance test.

    Unspecified service limits use strict non-degradation relative to the first
    run. Only numerical roundoff is tolerated; no hidden QoS budget is assumed.
    """
    assert left.result is not None and right.result is not None
    warnings = ["Pierwsza symulacja jest punktem odniesienia. Jedna para nie potwierdza istotności statystycznej; potrzebne są powtórzenia dla wielu RngRun."]
    blockers: List[str] = []
    left_config, right_config = _flatten_configuration(left), _flatten_configuration(right)
    confounders = [key for key in sorted(set(left_config) | set(right_config))
                   if key != "xapp" and not key.startswith("xapp.")
                   and left_config.get(key) != right_config.get(key)]
    if confounders:
        blockers.append("Różne warunki eksperymentu: " + ", ".join(_configuration_label(key) for key in confounders) + ".")
    for item in (left, right):
        if item.status != SimulationStatus.COMPLETED:
            blockers.append("{}: przebieg nie został ukończony.".format(item.name))
        termination = item.runtime.get("termination") or {}
        if item.runtime.get("experiment_valid") is False or (isinstance(termination, dict) and termination.get("partial")):
            blockers.append("{}: przebieg częściowy lub zaburzone działanie xAppa.".format(item.name))
        if item.result.metadata.get("measurement_consistent") is False:
            blockers.append("{}: metryki pochodzą z niespójnych lub nieokreślonych okien pomiarowych.".format(item.name))
    first_meta, second_meta = left.result.metadata, right.result.metadata
    for key, label in (("measurement_duration_s", "czas pomiaru"), ("energy_scope", "zakres modelu energii")):
        if first_meta.get(key) is None or second_meta.get(key) is None:
            blockers.append("Brak informacji o {} w co najmniej jednym wyniku.".format(label))
        elif first_meta[key] != second_meta[key]:
            blockers.append("Wyniki mają różny {}.".format(label))
    for key in ("simulator_source", "data_layer", "packet_loss_definition", "measurement_start_s"):
        if first_meta.get(key) != second_meta.get(key):
            blockers.append("Różna definicja lub pochodzenie pomiaru: {}.".format(key))
    if any(meta.get("simulator_source") == "SOURCE_LOCAL_DISCRETE_SIMULATOR" for meta in (first_meta, second_meta)):
        blockers.append("Uproszczony model lokalny nie stanowi walidacji wyników ns-3/O-RAN.")

    a, b = left.result.metrics, right.result.metrics
    required = ("energy_efficiency_bit_per_j", "total_energy_j", "delivered_mbit", "throughput_mbit_per_s", "packet_loss_pct", "delay_ms")
    missing = [key for key in required if _number(a.get(key)) is None or _number(b.get(key)) is None]
    if missing:
        blockers.append("Niepełne metryki EE/QoS: " + ", ".join(missing) + ".")
    if any((_number(item.get("delivered_mbit")) or 0) <= 0 for item in (a, b)):
        blockers.append("Brak dostarczonych danych w co najmniej jednym przebiegu.")

    allowed = {"max_delay_ms", "max_packet_loss_pct", "min_throughput_mbit_per_s"}
    for key, value in limits.items():
        parsed = _number(value)
        if key not in allowed or parsed is None or parsed < 0 or (key == "max_packet_loss_pct" and parsed > 100):
            raise ValueError("Niepoprawny próg QoS: {}".format(key))
    if blockers:
        return "inconclusive", "Nie można rozstrzygnąć korzyści EE z zachowaniem QoS.", warnings + blockers

    regressions = []
    for key, limit_key, label, increasing_is_bad in (
        ("delay_ms", "max_delay_ms", "opóźnienie", True),
        ("packet_loss_pct", "max_packet_loss_pct", "packet loss", True),
        ("throughput_mbit_per_s", "min_throughput_mbit_per_s", "przepływność", False),
    ):
        baseline = float(limits.get(limit_key, a[key]))
        observed = float(b[key])
        tolerance = 1e-9 * max(1.0, abs(baseline))
        bad = observed > baseline + tolerance if increasing_is_bad else observed < baseline - tolerance
        if bad:
            regressions.append(label)
    warnings.append("QoS: " + ("podane progi usługi; pozostałe KPI względem pierwszej symulacji." if limits else "brak zadanych progów usługi; wymagany brak pogorszenia względem pierwszej symulacji."))
    ee_improved = float(b["energy_efficiency_bit_per_j"]) > float(a["energy_efficiency_bit_per_j"])
    if regressions:
        return "tradeoff", "Pogorszenie QoS lub przekroczenie progu: {}. Sam spadek energii nie potwierdza korzyści.".format(", ".join(regressions)), warnings
    if ee_improved:
        saving = float(b["total_energy_j"]) < float(a["total_energy_j"])
        return "improved", ("W tej parze: wyższa EE i mniejsza energia przy spełnionych kryteriach QoS." if saving else "W tej parze: wyższa EE przy spełnionych kryteriach QoS; brak zmniejszenia energii całkowitej."), warnings
    return "no_improvement", "Kryteria QoS spełnione, ale nie zaobserwowano wzrostu EE.", warnings


def _configuration_differences(left: Simulation, right: Simulation) -> List[ConfigurationDifference]:
    left_values = _flatten_configuration(left)
    right_values = _flatten_configuration(right)
    result = []
    for key in sorted(set(left_values) | set(right_values)):
        first, second = left_values.get(key), right_values.get(key)
        if first != second:
            result.append(ConfigurationDifference(_configuration_label(key), first, second))
    return result


def _flatten_configuration(simulation: Simulation) -> Dict[str, Any]:
    configuration = simulation.configuration
    values: Dict[str, Any] = {
        "scenario": configuration.scenario,
        "xapp": "OFF" if configuration.xapp == "none" else configuration.xapp,
    }
    values.update(configuration.parameters)
    for key, value in configuration.xapp_environment.items():
        values["xapp." + str(key)] = value
    return values


def _configuration_label(key: str) -> str:
    if key == "scenario":
        return "Scenariusz"
    if key == "xapp":
        return "xApp"
    if key.startswith("xapp."):
        return "xApp: " + key.partition(".")[2]
    return parameter_label(key)


def _percent_change(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None or right is None or left == 0:
        return None
    return (right - left) / abs(left) * 100.0


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
