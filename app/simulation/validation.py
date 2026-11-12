from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping

from .models import SimulationConfiguration


REQUIRED_PARAMETERS = (
    "simTime",
    "N_MmWaveEnbNodes",
    "N_Ues",
    "RngRun",
)

INTEGER_PARAMETERS = frozenset({"CellLayout", "N_MmWaveEnbNodes", "N_Ues", "RngRun", "UdpPacketSizeBytes", "UdpIntervalUs", "bufferSize", "numberOfRaPreambles", "KPM_E2functionID", "RC_E2functionID"})

# Bounds verified against the bundled ns-3 classes (UdpClient, MmWaveEnbMac)
# and the scenario's indexed UE/cell arrays, not just its looser GlobalValues.
BACKEND_BOUNDS = {
    "CellLayout": (0, 1),
    "N_MmWaveEnbNodes": (1, 255), "N_Ues": (1, 512),
    "UdpPacketSizeBytes": (12, 65507), "UdpIntervalUs": (1, 4294967295),
    "numberOfRaPreambles": (4, 64), "indicationPeriodicity": (0.01, 2),
    "bufferSize": (0, 4095),  # MiB * 1024^2 must fit the uint32 RLC buffer.
    "TrafficStart_s": (0, 100000), "DrainTime_s": (0, 100000),
    "IntersideDistanceUEs": (0, None), "IntersideDistanceCells": (0, None),
    "KPM_E2functionID": (0, 65535), "RC_E2functionID": (0, 65535),
}


def validate_configuration(
    configuration: SimulationConfiguration,
    application_config: Mapping[str, Any],
) -> List[str]:
    """Zwraca komunikaty gotowe do pokazania użytkownikowi."""

    errors: List[str] = []
    scenarios = dict(application_config.get("scenarios") or {})
    xapps = dict(application_config.get("xapps") or {})
    if configuration.scenario not in scenarios:
        errors.append("Nieznany scenariusz: {}".format(configuration.scenario))
    if configuration.xapp != "none" and configuration.xapp not in xapps:
        errors.append("Nieznany xApp: {}".format(configuration.xapp))
    if configuration.xapp == "optimized" and configuration.scenario != "optimized":
        errors.append("My Energy wymaga scenariusza My Energy z zegarem KPM czasu ns-3.")

    parameters = configuration.parameters
    scenario_item = scenarios.get(configuration.scenario) or {}
    allowed = {str(item) for item in scenario_item.get("allowed_params") or []}
    required = tuple(
        key for key in REQUIRED_PARAMETERS
        if not allowed or key in allowed or key in {"simTime", "RngRun"}
    )
    for key in required:
        value = parameters.get(key)
        try:
            number = float(value)
            if not math.isfinite(number):
                errors.append("{} ma niepoprawną wartość".format(parameter_label(key)))
            elif number <= 0:
                errors.append("{} musi być większe od zera".format(parameter_label(key)))
            elif key in INTEGER_PARAMETERS and not number.is_integer():
                errors.append("{} musi być liczbą całkowitą".format(parameter_label(key)))
        except (TypeError, ValueError):
            errors.append("{} ma niepoprawną wartość".format(parameter_label(key)))

    for key in ("Bandwidth", "CenterFrequency", "UdpPacketSizeBytes", "UdpIntervalUs"):
        if key not in parameters or parameters.get(key) in (None, ""):
            continue
        try:
            number = float(parameters[key])
            if not math.isfinite(number):
                errors.append("{} ma niepoprawną wartość".format(parameter_label(key)))
            elif number <= 0:
                errors.append("{} musi być większe od zera".format(parameter_label(key)))
            elif key in INTEGER_PARAMETERS and not number.is_integer():
                errors.append("{} musi być liczbą całkowitą".format(parameter_label(key)))
        except (TypeError, ValueError):
            errors.append("{} ma niepoprawną wartość".format(parameter_label(key)))

    if configuration.scenario == "optimized":
        for key, (minimum, maximum) in BACKEND_BOUNDS.items():
            if parameters.get(key) in (None, ""):
                continue
            try:
                number = float(parameters[key])
                if not math.isfinite(number) or (key in INTEGER_PARAMETERS and not number.is_integer()):
                    raise ValueError
                if number < minimum or (maximum is not None and number > maximum):
                    errors.append("{}: dopuszczalny zakres {}..{}".format(parameter_label(key), minimum, maximum if maximum is not None else "∞"))
            except (TypeError, ValueError):
                errors.append("{} ma niepoprawną wartość".format(parameter_label(key)))
        try:
            duration = float(parameters.get("simTime", 0))
            start = 0.1 if parameters.get("TrafficStart_s") in (None, "") else float(parameters["TrafficStart_s"])
            drain = float(parameters.get("DrainTime_s") or 0)
            effective_drain = min(2.0, duration * 0.2) if drain == 0 else drain
            if start + effective_drain >= duration:
                errors.append("Początek ruchu + czas opróżniania kolejek musi być mniejszy od simTime")
        except (TypeError, ValueError):
            pass
        if configuration.xapp != "none":
            enabled = lambda key, default: str(parameters.get(key, default)).lower() in {"true", "1", "yes"}
            if enabled("enableE2FileLogging", False):
                errors.append("xApp wymaga połączenia E2: wyłącz enableE2FileLogging")
            if not enabled("e2nrEnabled", True):
                errors.append("xApp sterujący komórkami NR wymaga e2nrEnabled")
            if configuration.xapp == "optimized":
                try:
                    if float(parameters.get("N_MmWaveEnbNodes", 4)) > 8:
                        errors.append("My Energy xApp obsługuje do 8 gNB: obecny backend koduje identyfikator komórki RC pojedynczą cyfrą (LTE=1, NR=2..9)")
                except (TypeError, ValueError):
                    pass

    minimum_time = scenario_item.get("minimum_sim_time_s")
    if minimum_time is not None:
        try:
            value = float(parameters.get("simTime"))
            if math.isfinite(value) and value < float(minimum_time):
                errors.append(
                    "Czas symulacji [s] musi być nie mniejszy niż {} dla wybranego scenariusza".format(
                        minimum_time
                    )
                )
        except (TypeError, ValueError):
            pass

    if configuration.scenario in scenarios:
        allowed_items = scenarios[configuration.scenario].get("allowed_params") or []
        if allowed_items:
            invalid = sorted(
                key for key, value in parameters.items()
                if value not in (None, "") and key not in set(str(item) for item in allowed_items)
            )
            if invalid:
                errors.append(
                    "Scenariusz nie obsługuje parametrów: {}".format(
                        ", ".join(parameter_label(key) for key in invalid)
                )
            )
    if configuration.xapp in xapps:
        xapp_item = xapps[configuration.xapp] or {}
        specifications = dict(xapp_item.get("parameters") or {})
        invalid_environment = sorted(
            key
            for key, value in configuration.xapp_environment.items()
            if value not in (None, "") and key not in specifications
        )
        if invalid_environment:
            errors.append(
                "xApp nie obsługuje parametrów: {}".format(
                    ", ".join(str(key) for key in invalid_environment)
                )
            )
        for key, value in configuration.xapp_environment.items():
            if value in (None, "") or key not in specifications:
                continue
            kind = str((specifications.get(key) or {}).get("type") or "").lower()
            if kind in {"integer", "int"}:
                try:
                    number = float(value)
                    if not math.isfinite(number) or not number.is_integer():
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append("Parametr xAppa {} musi być liczbą całkowitą".format(key))
            elif kind in {"float", "number"}:
                try:
                    number = float(value)
                    if not math.isfinite(number):
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append("Parametr xAppa {} ma niepoprawną wartość".format(key))
            elif kind in {"boolean", "bool"}:
                if isinstance(value, bool):
                    continue
                if str(value).strip().lower() not in {"0", "1", "true", "false", "yes", "no"}:
                    errors.append("Parametr xAppa {} musi być wartością logiczną".format(key))
            if kind in {"integer", "int", "float", "number"}:
                specification = specifications[key]
                try:
                    number = float(value)
                    minimum, maximum = specification.get("minimum"), specification.get("maximum")
                    if (minimum is not None and number < float(minimum)) or (maximum is not None and number > float(maximum)):
                        errors.append("Parametr xAppa {} jest poza dopuszczalnym zakresem".format(key))
                except (TypeError, ValueError):
                    pass
    return errors


def parameter_label(key: str) -> str:
    labels = {
        "simTime": "Czas symulacji [s]",
        "TrafficStart_s": "Początek ruchu [s]",
        "DrainTime_s": "Czas opróżniania kolejek [s]",
        "N_MmWaveEnbNodes": "Liczba gNB",
        "N_Ues": "Liczba UE",
        "RngRun": "RngRun",
        "Bandwidth": "Szerokość pasma [Hz]",
        "CenterFrequency": "Częstotliwość [Hz]",
        "UdpPacketSizeBytes": "Rozmiar pakietu [B]",
        "UdpIntervalUs": "Odstęp pakietów [µs]",
        "IntersideDistanceUEs": "Odległość UE [m]",
        "IntersideDistanceCells": "Odległość gNB [m]",
        "indicationPeriodicity": "Okres KPM [s]",
    }
    return labels.get(key, key)
