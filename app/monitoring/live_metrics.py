"""Przyrostowe przetwarzanie rzeczywistych próbek energii i danych.

Moduł nie zna Dockera ani Tkintera.  Warstwa wykonawcza przekazuje mu tekst
nowych wierszy (albo ponownie odczytaną zawartość pliku), a GUI otrzymuje
zwykły, niezmienny słownik ze snapshotem.  Dzięki temu te same punkty mogą
zostać zapisane w katalogu przebiegu i pokazane na żywo bez osobnego modelu
metryk.

Głównymi źródłami są ``metrics.csv`` i ``ee_timeseries.csv``. Pierwszy z nich
jest używany, gdy backend już go zapisuje podczas działania, ponieważ zawiera
również rzeczywiście raportowane KPI jakości. Drugi pozostaje pełnym
fallbackiem dla wariantu scenariusza, który zapisuje bezpośrednio serię EE.
Gdy starszy scenariusz zapisuje wyłącznie ``energyfilecell*.csv``, dostępny
jest ograniczony fallback energii i mocy; bez danych o ruchu nie uzupełnia on
pozostałych metryk.
"""

from __future__ import annotations

import csv
import math
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


Point = Dict[str, float]


def _finite_number(value: Any) -> Optional[float]:
    """Zwraca skończoną liczbę albo ``None`` dla braku/błędnej wartości."""

    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _column_key(value: Any) -> str:
    """Normalizuje zwykłe warianty nazw kolumn z ns-3."""

    return (
        str(value)
        .lstrip("\ufeff")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("-", "_")
    )


def estimate_remaining_s(
    sim_duration_s: Any,
    sim_time_s: Any,
    elapsed_real_s: Any,
) -> Optional[float]:
    """Szacuje pozostały czas rzeczywisty na podstawie dotychczasowego tempa.

    Wynik jest celowo ``None``, gdy nie ma choćby jednego kompletnego punktu
    odniesienia.  Nie zgadujemy ETA przed otrzymaniem czasu symulowanego.
    """

    duration = _finite_number(sim_duration_s)
    completed = _finite_number(sim_time_s)
    elapsed = _finite_number(elapsed_real_s)
    if duration is None or completed is None or elapsed is None:
        return None
    if duration <= 0.0 or completed <= 0.0 or elapsed < 0.0:
        return None
    if completed >= duration:
        return 0.0
    return max(0.0, (duration - completed) * (elapsed / completed))


class LiveMetricsCollector:
    """Utrzymuje ograniczony, przyrostowy widok pełnego CSV metryk.

    ``ingest_csv_text`` toleruje częściowe dostawy tekstu.  Wiersz bez znaku
    końca linii zostaje zachowany do następnego wywołania, aby nie policzyć
    przypadkowo wartości zapisanej tylko częściowo przez proces symulacji.
    Powtórne odczytanie całego pliku jest również bezpieczne: wiersze o czasie
    nie większym niż ostatni zaakceptowany punkt są traktowane jako nieaktualne.
    """

    DEFAULT_MAX_POINTS = 3000

    # Klucze są już w formie zwracanej przez _column_key().
    _ALIASES: Dict[str, Tuple[str, ...]] = {
        "sim_time_s": ("time_s", "time", "simtime_s", "sim_time_s"),
        "measurement_duration_s": ("measurement_duration_s", "measurementduration_s", "analysis_duration_s"),
        "measurement_start_s": ("measurement_start_s", "measurementstart_s"),
        "tx_packets": ("tx_packets", "txpackets"),
        "rx_packets": ("rx_packets", "rxpackets"),
        "delay_sum_s": ("delay_sum_s", "delaysum_s"),
        "delivered_bits": (
            "deliveredbits",
            "delivered_bits",
            "delivereddata_bits",
            "delivereddatabits",
        ),
        "total_energy_j": ("totalenergy_j", "total_energy_j", "netenergy"),
        "interval_power_w": (
            "intervalpower_w",
            "interval_power_w",
            "total_power_w",
            "power_w",
        ),
        "interval_throughput_bps": (
            "intervalthroughput_bps",
            "interval_throughput_bps",
            "throughput_bps",
        ),
        "cumulative_ee_bit_per_j": (
            "cumulativeee_bit_per_j",
            "cumulative_ee_bit_per_j",
            "ee_bit_per_j",
        ),
        "energy_per_mbit_j": (
            "j_per_mbit",
            "energy_per_mbit_j",
            "energycost_j_per_mbit",
        ),
        # Poniższe pola nie są wymagane. Trafiają do widoku wyłącznie wtedy,
        # gdy obecny backend rzeczywiście zapisuje je w metrics.csv/KPI.
        "packet_loss_pct": ("packet_loss_pct", "packetloss_pct"),
        "mean_prb_pct": (
            "mean_prb_utilization_pct",
            "meanprbutilization_pct",
            "prb_utilization_pct",
            "dlprbusage",
        ),
        "p05_sinr_db": ("p05_sinr_db", "p05sinr_db"),
        "delay_ms": (
            "mean_delay_ms",
            "meandelay_ms",
            "delay_ms",
        ),
    }

    _SERIES_KEYS = (
        "energy_j",
        "power_w",
        "energy_efficiency_bit_per_j",
        "energy_per_mbit_j",
        "throughput_mbit_per_s",
        "delivered_mbit",
        "packet_loss_pct",
        "mean_prb_pct",
        "p05_sinr_db",
        "delay_ms",
    )

    # Starsze warianty scenariusza nie tworzą ee_timeseries.csv, lecz nadal
    # zapisują przyrostowo osobny plik energii dla każdej komórki.  Z tych
    # plików można uczciwie pokazać energię i moc wyprowadzoną z różnicy
    # skumulowanej energii. Nie zawierają one jednak danych o ruchu, więc nie
    # są źródłem EE, kosztu J/Mbit ani przepływności.
    _ENERGY_FILE_ALIASES: Dict[str, Tuple[str, ...]] = {
        "sim_time_s": ("time", "time_s", "simtime_s", "sim_time_s"),
        "total_energy_j": ("netenergy", "totalenergy_j", "total_energy_j"),
    }

    def __init__(self, max_points: int = DEFAULT_MAX_POINTS) -> None:
        try:
            capacity = int(max_points)
        except (TypeError, ValueError):
            raise ValueError("max_points musi być dodatnią liczbą całkowitą")
        if capacity < 1:
            raise ValueError("max_points musi być dodatnią liczbą całkowitą")

        self.max_points = capacity
        self._header_signature: Tuple[str, ...] = ()
        self._columns: Dict[str, int] = {}
        self._pending_text = ""
        self._last_time_s: Optional[float] = None
        self._last_values: Dict[str, float] = {}
        self._primary_source: Optional[str] = None
        self._series: Dict[str, Deque[Point]] = {
            key: deque(maxlen=capacity) for key in self._SERIES_KEYS
        }
        self._accepted_rows = 0
        self._stale_rows = 0
        self._invalid_rows = 0
        self._header_changes = 0

        # Stan zapasowego źródła energyfilecell*.csv.  Jest trzymany osobno,
        # aby nigdy nie mieszać go z pełniejszym ee_timeseries.csv.
        self._energy_file_states: Dict[str, Dict[str, Any]] = {}
        self._energy_file_series: Dict[str, Deque[Point]] = {
            key: deque(maxlen=capacity) for key in self._SERIES_KEYS
        }
        self._energy_file_last_signature: Optional[Tuple[Tuple[str, float, float], ...]] = None
        self._energy_file_last_time_s: Optional[float] = None
        self._energy_file_last_energy_j: Optional[float] = None
        self._energy_file_max_skew_s: Optional[float] = None
        self._energy_file_accepted_rows = 0
        self._energy_file_stale_rows = 0
        self._energy_file_invalid_rows = 0
        self._energy_file_invalid_files = 0

    @property
    def last_time_s(self) -> Optional[float]:
        """Ostatni zaakceptowany czas symulowany."""

        return self._last_time_s

    @property
    def primary_source(self) -> Optional[str]:
        """Nazwa pliku, z którego pochodzi bieżący pełny strumień."""

        return self._primary_source

    @property
    def has_full_metric_schema(self) -> bool:
        """Czy ostatni nagłówek pozwala obliczyć komplet głównych metryk.

        Nie wystarcza tu sama energia: wymagamy również danych o ruchu oraz
        mocy/przepływności. EE i J/Mbit mogą być w takim pliku wyprowadzone
        bez zgadywania z energii i dostarczonych bitów.
        """

        return {
            "sim_time_s",
            "delivered_bits",
            "total_energy_j",
            "interval_power_w",
            "interval_throughput_bps",
        }.issubset(self._columns)

    @property
    def has_complete_primary_sample(self) -> bool:
        """Czy ostatnia próbka ma faktyczne wartości dla pełnego widoku.

        Sam nagłówek ``metrics.csv`` nie jest wystarczającym dowodem. Część
        backendów tworzy od razu kompletny zestaw kolumn, a w pierwszych
        wierszach pozostawia pola ruchu puste. Gdyby taki wiersz zablokował
        ``ee_timeseries.csv``, dashboard pozostałby bez EE, kosztu i danych
        mimo że drugi, realny plik już je raportuje.
        """

        return self._accepted_rows > 0 and {
            "sim_time_s",
            "delivered_bits",
            "total_energy_j",
            "interval_power_w",
            "interval_throughput_bps",
        }.issubset(self._last_values)

    def reset(self) -> None:
        """Czyści dane bieżącego przebiegu, zachowując skonfigurowaną retencję."""

        self._header_signature = ()
        self._columns = {}
        self._pending_text = ""
        self._last_time_s = None
        self._last_values = {}
        self._primary_source = None
        for values in self._series.values():
            values.clear()
        self._accepted_rows = 0
        self._stale_rows = 0
        self._invalid_rows = 0
        self._header_changes = 0
        self._energy_file_states = {}
        for values in self._energy_file_series.values():
            values.clear()
        self._energy_file_last_signature = None
        self._energy_file_last_time_s = None
        self._energy_file_last_energy_j = None
        self._energy_file_max_skew_s = None
        self._energy_file_accepted_rows = 0
        self._energy_file_stale_rows = 0
        self._energy_file_invalid_rows = 0
        self._energy_file_invalid_files = 0

    def ingest_csv_text(self, text: str, source: str = "ee_timeseries.csv") -> int:
        """Przyjmuje fragment tekstu CSV i zwraca liczbę nowych punktów.

        ``text`` może zawierać nagłówek przy każdym wywołaniu.  Nagłówek o
        zmienionej kolejności/nazwach kolumn jest rozpoznawany ponownie bez
        przerywania zbierania danych.
        """

        if not isinstance(text, str):
            raise TypeError("tekst CSV musi być napisem")
        if not text:
            return 0
        source_name = str(source).strip() or "nieznany plik CSV"

        # The caller can resend a complete file with its header after an
        # earlier poll ended mid-record. Do not prepend that partial record
        # to the new header and accidentally corrupt both.
        first_line = text.splitlines()[0] if text.splitlines() else ""
        first_cell = _column_key(first_line.split(",", 1)[0])
        restarts_file = first_cell in self._ALIASES["sim_time_s"]
        payload = ("" if restarts_file else self._pending_text) + text
        self._pending_text = ""
        accepted = 0
        for raw_line, complete in self._complete_lines(payload):
            if not complete:
                self._pending_text = raw_line
                continue
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            try:
                row = next(csv.reader([line]))
            except (csv.Error, StopIteration):
                self._invalid_rows += 1
                continue
            if self._set_header_if_present(row):
                continue
            if self._ingest_row(row, source_name):
                accepted += 1
        return accepted

    def ingest_energy_files(self, files: Mapping[str, str]) -> int:
        """Przyjmuje aktualne ogony prawdziwych ``energyfilecell*.csv``.

        Ten fallback jest używany tylko, kiedy główna seria
        ``ee_timeseries.csv`` nie dostarczyła jeszcze żadnego wiersza.  Każdy
        plik zawiera skumulowaną energię jednej komórki; po odebraniu danych
        z dostępnych poprawnych plików sumujemy je w jeden punkt energii.

        Wejściem jest mapa ``nazwa pliku -> tekst CSV``. Warstwa wykonawcza
        może bezpiecznie przekazywać przy każdym odpytywaniu cały ograniczony
        ogon: kolektor odrzuca powtórzone rekordy według czasu i energii.
        """

        if not isinstance(files, Mapping):
            raise TypeError("pliki energii muszą być mapą nazwa -> tekst CSV")

        accepted = 0
        changed = False
        for raw_name, text in sorted(files.items(), key=lambda item: str(item[0])):
            filename = str(raw_name).strip()
            if not filename:
                self._energy_file_invalid_files += 1
                continue
            if not isinstance(text, str):
                self._energy_file_invalid_files += 1
                continue
            state = self._energy_file_states.setdefault(
                filename,
                {
                    "header_valid": False,
                    "last_time_s": None,
                    "last_energy_j": None,
                },
            )
            new_rows = self._ingest_one_energy_file(text, state)
            accepted += new_rows
            changed = changed or bool(new_rows)

        if changed:
            self._append_energy_file_aggregate()
        return accepted

    def snapshot(
        self,
        sim_duration_s: Any = None,
        elapsed_real_s: Any = None,
    ) -> Dict[str, Any]:
        """Zwraca JSON-serializowalny stan danych zebranych do tej chwili."""

        primary_available = self._accepted_rows > 0
        sim_time_s = self._last_time_s if primary_available else self._energy_file_last_time_s
        duration = _finite_number(sim_duration_s)
        elapsed = _finite_number(elapsed_real_s)
        progress_pct: Optional[float] = None
        if duration is not None and duration > 0.0 and sim_time_s is not None:
            progress_pct = min(100.0, max(0.0, 100.0 * sim_time_s / duration))

        current_metrics = (
            self._current_metrics(sim_time_s)
            if primary_available
            else self._energy_file_current_metrics(sim_time_s)
        )
        series = self._series if primary_available else self._energy_file_series
        data_source = (
            self._primary_source
            if primary_available
            else "energyfilecell*.csv"
            if self._energy_file_last_energy_j is not None
            else None
        )
        available_series = [key for key, values in series.items() if values]
        series_sources: Dict[str, str] = {}
        if primary_available:
            series_sources = {
                key: str(self._primary_source or "nieznany plik CSV")
                for key in available_series
            }
        elif self._energy_file_last_energy_j is not None:
            if series.get("energy_j"):
                skew = self._energy_file_max_skew_s
                series_sources["energy_j"] = (
                    "energyfilecell*.csv (NetEnergy; sample-and-hold, max skew {:.6g} s)".format(skew)
                    if skew is not None
                    else "energyfilecell*.csv (NetEnergy; sample-and-hold)"
                )
            if series.get("power_w"):
                series_sources["power_w"] = (
                    "energyfilecell*.csv (różnica sumy NetEnergy / czas)"
                )
        return {
            "sim_time_s": sim_time_s,
            "sim_duration_s": duration if duration is not None and duration > 0.0 else None,
            "elapsed_real_s": elapsed if elapsed is not None and elapsed >= 0.0 else None,
            "progress_pct": progress_pct,
            "eta_s": estimate_remaining_s(duration, sim_time_s, elapsed),
            "current_metrics": current_metrics,
            "series": {
                key: [dict(point) for point in values]
                for key, values in series.items()
            },
            "series_sources": series_sources,
            "data_status": {
                "accepted_rows": self._accepted_rows,
                "stale_rows": self._stale_rows,
                "invalid_rows": self._invalid_rows,
                "header_changes": self._header_changes,
                "data_source": data_source,
                "primary_available": primary_available,
                "has_live_data": bool(available_series),
                "available_series": available_series,
                "unavailable_series": [
                    key for key in self._SERIES_KEYS if key not in available_series
                ],
                "energy_file_accepted_rows": self._energy_file_accepted_rows,
                "energy_file_stale_rows": self._energy_file_stale_rows,
                "energy_file_invalid_rows": self._energy_file_invalid_rows,
                "energy_file_invalid_files": self._energy_file_invalid_files,
                "energy_file_max_timestamp_skew_s": self._energy_file_max_skew_s,
                "energy_file_count": sum(
                    1
                    for state in self._energy_file_states.values()
                    if state.get("header_valid")
                ),
                "retention_limit": self.max_points,
            },
        }

    @staticmethod
    def estimate_remaining_s(
        sim_duration_s: Any,
        sim_time_s: Any,
        elapsed_real_s: Any,
    ) -> Optional[float]:
        """Wygodny odpowiednik funkcji modułowej dla wywołującego."""

        return estimate_remaining_s(sim_duration_s, sim_time_s, elapsed_real_s)

    @staticmethod
    def _complete_lines(payload: str) -> Iterable[Tuple[str, bool]]:
        """Dzieli tekst bez utraty niepełnego końcowego wiersza."""

        for item in payload.splitlines(True):
            yield item, item.endswith("\n") or item.endswith("\r")

    def _set_header_if_present(self, row: Sequence[str]) -> bool:
        normalized = tuple(_column_key(value) for value in row)
        columns: Dict[str, int] = {}
        for field, aliases in self._ALIASES.items():
            for index, value in enumerate(normalized):
                if value in aliases:
                    columns[field] = index
                    break

        # Prawdziwy nagłówek zawiera czas i przynajmniej jedną inną znaną kolumnę.
        if "sim_time_s" not in columns or len(columns) < 2:
            return False
        if self._header_signature and normalized != self._header_signature:
            self._header_changes += 1
        self._header_signature = normalized
        self._columns = columns
        return True

    def _ingest_row(self, row: Sequence[str], source: str) -> bool:
        if not self._header_signature:
            self._invalid_rows += 1
            return False
        if len(row) != len(self._header_signature):
            self._invalid_rows += 1
            return False

        values: Dict[str, float] = {}
        for field, index in self._columns.items():
            value = _finite_number(row[index])
            if value is not None and (field == "p05_sinr_db" or value >= 0):
                if field in ("packet_loss_pct", "mean_prb_pct") and value > 100:
                    continue
                values[field] = value

        sim_time_s = values.get("sim_time_s")
        if sim_time_s is None or sim_time_s < 0.0:
            self._invalid_rows += 1
            return False
        if self._last_time_s is not None and sim_time_s <= self._last_time_s:
            self._stale_rows += 1
            return False

        self._normalize_counters(values)
        self._last_time_s = sim_time_s
        # Każda próbka jest samodzielnym pomiarem. Zachowanie starej wartości
        # dla pustej komórki CSV mieszałoby np. energię z czasu t=2 z ruchem
        # z t=1 i mogłoby udawać kompletny wynik. Seria nadal zachowuje
        # wszystkie faktycznie otrzymane punkty, ale karta bieżących metryk
        # pokazuje wyłącznie stan ostatniej pełnej/niepełnej próbki.
        self._last_values = values
        if self._primary_source is None:
            self._primary_source = source
        self._accepted_rows += 1
        self._append_series(sim_time_s, values)
        return True

    @staticmethod
    def _normalize_counters(values: Dict[str, float]) -> None:
        energy = values.get("total_energy_j")
        delivered = values.get("delivered_bits")
        if energy is not None and delivered is not None:
            ratios = {
                "cumulative_ee_bit_per_j": delivered / energy if energy > 0 else None,
                "energy_per_mbit_j": energy / (delivered / 1e6) if delivered > 0 else None,
            }
            for key, value in ratios.items():
                if value is None:
                    values.pop(key, None)
                elif key not in values or not math.isclose(values[key], value, rel_tol=1e-4, abs_tol=1e-9):
                    values[key] = value
        rx = values.get("rx_packets")
        delay_sum = values.get("delay_sum_s")
        if rx is not None and delay_sum is not None:
            if rx > 0:
                values["delay_ms"] = delay_sum * 1000.0 / rx
            else:
                values.pop("delay_ms", None)

    def _append_series(self, sim_time_s: float, values: Dict[str, float]) -> None:
        energy = values.get("total_energy_j")
        delivered_bits = values.get("delivered_bits")
        power = values.get("interval_power_w")
        throughput = values.get("interval_throughput_bps")
        ee = values.get("cumulative_ee_bit_per_j")
        energy_per_mbit = values.get("energy_per_mbit_j")

        if energy is not None:
            self._append_point("energy_j", sim_time_s, energy)
        if power is not None:
            self._append_point("power_w", sim_time_s, power)
        if delivered_bits is not None:
            self._append_point("delivered_mbit", sim_time_s, delivered_bits / 1_000_000.0)
        if throughput is not None:
            self._append_point("throughput_mbit_per_s", sim_time_s, throughput / 1_000_000.0)
        if ee is None and energy is not None and energy > 0.0 and delivered_bits is not None:
            ee = delivered_bits / energy
        if ee is not None:
            self._append_point("energy_efficiency_bit_per_j", sim_time_s, ee)
        if (
            energy_per_mbit is None
            and energy is not None
            and delivered_bits is not None
            and delivered_bits > 0.0
        ):
            energy_per_mbit = energy / (delivered_bits / 1_000_000.0)
        if energy_per_mbit is not None:
            self._append_point(
                "energy_per_mbit_j",
                sim_time_s,
                energy_per_mbit,
            )
        for series_key, value_key in (
            ("packet_loss_pct", "packet_loss_pct"),
            ("mean_prb_pct", "mean_prb_pct"),
            ("p05_sinr_db", "p05_sinr_db"),
            ("delay_ms", "delay_ms"),
        ):
            value = values.get(value_key)
            if value is not None:
                self._append_point(series_key, sim_time_s, value)

    def _ingest_one_energy_file(self, text: str, state: Dict[str, Any]) -> int:
        """Przetwarza jeden ogon pliku ``Time,NetEnergy,DiffEnergy``."""

        if not text:
            return 0
        try:
            # Podobnie jak główna seria nie wolno przyjąć ostatniej, właśnie
            # zapisywanej linijki jako pełnego pomiaru.
            complete_lines = [
                raw_line.rstrip("\r\n")
                for raw_line, complete in self._complete_lines(text)
                if complete
            ]
            rows = list(csv.reader(complete_lines))
        except csv.Error:
            self._energy_file_invalid_files += 1
            return 0
        if not rows:
            return 0

        header = tuple(_column_key(value) for value in rows[0])
        columns: Dict[str, int] = {}
        for field, aliases in self._ENERGY_FILE_ALIASES.items():
            for index, value in enumerate(header):
                if value in aliases:
                    columns[field] = index
                    break
        if len(columns) != len(self._ENERGY_FILE_ALIASES):
            self._energy_file_invalid_files += 1
            return 0

        state["header_valid"] = True
        accepted = 0
        for row in rows[1:]:
            if len(row) != len(header):
                if any(str(value).strip() for value in row):
                    self._energy_file_invalid_rows += 1
                continue
            sim_time_s = _finite_number(row[columns["sim_time_s"]])
            energy_j = _finite_number(row[columns["total_energy_j"]])
            if (
                sim_time_s is None
                or energy_j is None
                or sim_time_s < 0.0
                or energy_j < 0.0
            ):
                self._energy_file_invalid_rows += 1
                continue

            previous_time = state.get("last_time_s")
            previous_energy = state.get("last_energy_j")
            if previous_time is not None:
                if sim_time_s < previous_time:
                    self._energy_file_stale_rows += 1
                    continue
                if sim_time_s == previous_time and previous_energy is not None:
                    if energy_j <= previous_energy:
                        self._energy_file_stale_rows += 1
                        continue

            state["last_time_s"] = sim_time_s
            state["last_energy_j"] = energy_j
            self._energy_file_accepted_rows += 1
            accepted += 1
        return accepted

    def _append_energy_file_aggregate(self) -> None:
        """Dodaje jeden aktualny punkt sumy energii z plików komórek."""

        valid_states = [
            state
            for state in self._energy_file_states.values()
            if state.get("header_valid")
        ]
        # Nie traktujemy braku próbki jednej komórki jako zera. Dopóki nie ma
        # aktualnej wartości ze wszystkich odkrytych poprawnych plików, suma
        # byłaby niepełna i nie może trafić do widoku live.
        if not valid_states or any(
            state.get("last_time_s") is None or state.get("last_energy_j") is None
            for state in valid_states
        ):
            return

        records = []
        for filename, state in sorted(self._energy_file_states.items()):
            if not state.get("header_valid"):
                continue
            time_s = state.get("last_time_s")
            energy_j = state.get("last_energy_j")
            if time_s is None or energy_j is None:
                continue
            records.append((filename, float(time_s), float(energy_j)))
        if not records:
            return
        signature = tuple(records)
        if signature == self._energy_file_last_signature:
            return
        self._energy_file_last_signature = signature
        sim_time_s = max(item[1] for item in records)
        total_energy_j = sum(item[2] for item in records)
        max_skew_s = sim_time_s - min(item[1] for item in records)
        self._energy_file_max_skew_s = max(
            self._energy_file_max_skew_s or 0.0, max_skew_s
        )

        previous_time = self._energy_file_last_time_s
        previous_energy = self._energy_file_last_energy_j
        energy_series = self._energy_file_series["energy_j"]
        if previous_time is None or sim_time_s > previous_time:
            energy_series.append({"x": sim_time_s, "y": total_energy_j})
            if (
                previous_time is not None
                and previous_energy is not None
                and total_energy_j >= previous_energy
            ):
                elapsed_s = sim_time_s - previous_time
                if elapsed_s > 0.0:
                    self._energy_file_series["power_w"].append(
                        {
                            "x": sim_time_s,
                            "y": (total_energy_j - previous_energy) / elapsed_s,
                        }
                    )
        elif energy_series:
            # Kilka komórek może zgłosić zmianę w tym samym punkcie czasu.
            # Zastępujemy ostatnią sumę zamiast tworzyć sztuczny odstęp czasu.
            energy_series[-1] = {"x": previous_time, "y": total_energy_j}

        self._energy_file_last_time_s = sim_time_s
        self._energy_file_last_energy_j = total_energy_j

    def _append_point(self, key: str, sim_time_s: float, value: float) -> None:
        if not math.isfinite(value):
            return
        self._series[key].append({"x": sim_time_s, "y": value})

    def _current_metrics(self, sim_time_s: Optional[float]) -> Dict[str, Optional[float]]:
        energy = self._last_values.get("total_energy_j")
        delivered_bits = self._last_values.get("delivered_bits")
        ee = self._last_values.get("cumulative_ee_bit_per_j")
        delivered_mbit = (
            delivered_bits / 1_000_000.0 if delivered_bits is not None else None
        )

        energy_per_mbit = self._last_values.get("energy_per_mbit_j")
        if energy is not None and delivered_mbit is not None and delivered_mbit > 0.0:
            if energy_per_mbit is None:
                energy_per_mbit = energy / delivered_mbit
            if ee is None and energy > 0.0:
                ee = delivered_bits / energy

        average_power: Optional[float] = None
        average_throughput: Optional[float] = None
        measurement_duration = self._last_values.get("measurement_duration_s")
        if measurement_duration is None and sim_time_s is not None:
            measurement_duration = sim_time_s - self._last_values.get("measurement_start_s", 0.0)
        if measurement_duration is not None and measurement_duration > 0.0:
            if energy is not None:
                average_power = energy / measurement_duration
            if delivered_mbit is not None:
                average_throughput = delivered_mbit / measurement_duration

        result: Dict[str, Optional[float]] = {
            "energy_efficiency_bit_per_j": ee,
            "energy_per_mbit_j": energy_per_mbit,
            "total_energy_j": energy,
            "average_power_w": average_power,
            "throughput_mbit_per_s": average_throughput,
            "delivered_mbit": delivered_mbit,
        }
        for key in ("packet_loss_pct", "mean_prb_pct", "p05_sinr_db", "delay_ms"):
            if key in self._last_values:
                result[key] = self._last_values[key]
        return result

    def _energy_file_current_metrics(
        self,
        sim_time_s: Optional[float],
    ) -> Dict[str, Optional[float]]:
        """Metryki, które naprawdę da się policzyć z plików energii komórek."""

        energy = self._energy_file_last_energy_j
        average_power: Optional[float] = None
        if energy is not None and sim_time_s is not None and sim_time_s > 0.0:
            average_power = energy / sim_time_s
        return {
            "energy_efficiency_bit_per_j": None,
            "energy_per_mbit_j": None,
            "total_energy_j": energy,
            "average_power_w": average_power,
            "throughput_mbit_per_s": None,
            "delivered_mbit": None,
        }
