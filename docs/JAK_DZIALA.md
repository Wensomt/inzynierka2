# Jak działa projekt — wersja do wyjaśnienia na obronie

## Opis w kilku zdaniach

Program w Pythonie jest panelem do uruchamiania eksperymentów sieciowych.
Właściwa symulacja odbywa się w ns-3, w kontenerze Docker. W tym samym
kontenerze działają kontroler nearRT-RIC i opcjonalny xApp. Scenariusz C++
tworzy sieć, generuje ruch UDP i zapisuje pomiary do CSV. Python odczytuje
te pliki, pokazuje przebiegi i zapisuje wyniki do późniejszego porównania.

## Przebieg jednej symulacji

1. Formularz tworzy `SimulationConfiguration`: scenariusz, parametry i xApp.
2. `SessionController` sprawdza konfiguracje i uruchamia kolejkę w osobnym
   wątku. Dzięki temu okno Tkintera może nadal reagować.
3. `SimulationRunner` tworzy kontener sesji, sprawdza ścieżki, kopiuje
   aktualne źródło scenariusza i kompiluje je w kontenerze. Dla My Energy
   kopiuje i kompiluje także jego źródło C.
4. Runner uruchamia RIC, potem ns-3, czeka na połączenie E2 i dopiero wtedy
   uruchamia xApp. Bez xAppa nadal działa ten sam scenariusz radiowy.
5. ns-3 zapisuje `ee_timeseries.csv` co około 0,1 s czasu modelowanego.
   Python okresowo pobiera końcowe, kompletne wiersze pliku. `LiveMetricsCollector`
   odrzuca już widziane próbki. GUI dostaje słowniki przez kolejkę zdarzeń.
6. Po zakończeniu powstaje `ee_summary.csv`. Runner kopiuje artefakty,
   `ResultsParser` liczy wynik końcowy, a `SimulationStorage` zapisuje JSON.
7. Procesy i kontener tej sesji są sprzątane. Kolejny przebieg ma nowy kontener.

## Najważniejsze pliki

| Plik/moduł | Odpowiedzialność |
|---|---|
| `app/main.py` | uruchomienie okna |
| `app/gui/` | formularze, tabele, wykresy; bez wywoływania symulatora w wątku GUI |
| `app/simulation/models.py` | zwykłe struktury konfiguracji i wyniku |
| `app/simulation/session_controller.py` | kolejka symulacji i zdarzenia do GUI |
| `app/simulation/runner.py` | uruchamianie, gotowość, odczyt live, archiwizacja i zatrzymanie |
| `app/simulation/docker_session.py` | kontener jednej sesji i jego sprzątanie |
| `app/infrastructure/docker_process_manager.py` | procesy wewnątrz kontenera i logi |
| `resources/my_energy_scenario_optimized.cc` | sieć, ruch UDP i pomiar energii/QoS w ns-3 |
| `resources/xapp_my_energy_optimized.c` | heurystyka sterowania komórkami na podstawie KPM |
| `app/monitoring/live_metrics.py` | próbki CSV do wykresów |
| `app/data/results_parser.py` | wynik końcowy z artefaktów |
| `app/data/storage.py` | pliki wyników, konfiguracje, eksport i kosz |
| `app/comparison/comparator.py` | różnice konfiguracji, metryk i ocena EE z QoS |

## Skąd są liczby

- Energia: liczniki modeli energii radia NR, sumowane po gNB.
- Dostarczone dane: rzeczywiście odebrane bajty aplikacji PacketSink.
- Wysłane pakiety: bajty wysłane przez UdpClient / stały rozmiar pakietu.
- Opóźnienie: czas odbioru minus timestamp w SeqTsHeader, średnia po pakietach.
- Niedostarczone: Tx − Rx. Na żywo obejmuje też pakiety w drodze; końcowy
  wynik jest liczony po czasie pozostawionym na opróżnienie kolejek.
- EE: dostarczone bity / energia. Koszt danych: energia / dostarczone Mbit.
- Karty mocy i przepływności: średnie z całego dotychczasowego okna.
  Wykresy mocy i przepływności: wartości w poszczególnych przedziałach próbkowania.
- SINR, PRB i aktywne UE: osobne raporty KPM. Nie są tym samym pomiarem co
  odebrane dane aplikacji i nie zastępują ich liczników.

Szczegółowe wzory i jednostki: [METRYKI.md](METRYKI.md).

## Co należy powiedzieć uczciwie

Energia jest wynikiem modelu radia NR. Program nie mierzy poboru prądu
komputera ani kompletnej komercyjnej stacji bazowej. Nie wykazano kalibracji
modelu dla konkretnego urządzenia.

My Energy to heurystyka oparta na obciążeniu i SINR. Nie jest algorytmem ML
ani pełnym regulatorem z powrotnym włączaniem komórek. Włączenie xAppa samo
w sobie nie oznacza oszczędności. Trzeba pokazać jego decyzje oraz porównać
EE i QoS przy tych samych ustawieniach i wielu sparowanych RngRun.

Czas ns-3 i czas pracy komputera są różne. Dla My Energy runner ustawia
timestamp nagłówków KPM na mikrosekundy ns-3. Z tego czasu xApp liczy wiek
pomiarów, odstępy decyzji, potwierdzenia i cooldown. Oczekiwanie na uruchomienie
E2 pozostaje czasem rzeczywistym. Dostarczenie poleceń E2 jest asynchroniczne,
więc obciążenie hosta może nadal wpływać na dokładną chwilę obsługi sterowania.
Opcjonalny limit czasu rzeczywistego może dać częściowy wynik i nie powinien
zastępować pełnych przebiegów w badaniu porównawczym.

`resources/ml_xapp` jest osobnym, historycznym demonstratorem. Nie bierze
udziału w domyślnym przebiegu aplikacji. Nie trzeba go omawiać jako wykonanego
elementu głównego systemu. Również dawne `data/campaigns`, `comparisons`,
`experiments` i `presets` nie sterują aktualną kolejką desktopową.
