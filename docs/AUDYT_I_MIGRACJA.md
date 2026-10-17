# Audyt i wykonana migracja

> Dokument historyczny opisujący migrację interfejsu. Aktualną ocenę naukową
> i naprawy zawiera [audyt 2026-10-02](AUDYT_2026-10-02.md). Usunięcie statystyk
> z dawnego GUI nie oznacza, że powtórzenia i analiza niepewności są zbędne
> do wnioskowania badawczego.

## Stan zastany

Projekt był aplikacją FastAPI z panelem uruchamianym w przeglądarce. Warstwa
wykonawcza uruchamiała kolejno `nearRT-RIC`, ns-3 i opcjonalny xApp wewnątrz
kontenera symulatora przez Docker SDK oraz `docker exec`. Wyniki były kopiowane
do katalogu `data/simulations` i analizowane z plików CSV.

Potwierdzone źródła danych:

- `summary.json`, `ee_summary.csv`, `summary.csv` lub `analysis.json`:
  podsumowane dane energii, EE oraz jakości, gdy są dostępne;
- `ee_timeseries.csv`: dane do szeregu czasowego oraz bezpieczny fallback;
- `energyfilecell*.csv`: energia poszczególnych komórek, z których można
  wyprowadzić energię i moc sieci;
- `du-cell-*.txt` oraz `cu-*-cell-*.txt`: PRB, SINR i aktywne UE, gdy pliki są
  dostępne;
- lokalny format `summary.json`: zawiera również packet loss.

EE jest obliczana jako `delivered_bits / total_energy_j` w `bit/J`, a koszt
energii transmisji jako `total_energy_j / delivered_mbit` w `J/Mbit`. Energia
jest modelem energii radiowej części RAN/gNB, a nie pomiarem poboru komputera
lub fizycznej stacji bazowej.

`optimized` dopisuje `PacketLoss_pct` i `MeanDelay_ms` do tej samej serii live
na podstawie FlowMonitor filtrowanego do prawdziwych UDP downlinków UE. Delay
pozostaje `N/A`, gdy nie ma jeszcze odebranego pakietu; aplikacja nie tworzy
zastępczych wartości.

## Problemy wymagające zmiany

- interfejs był webowy i zawierał rozbudowany workflow serii wariantów;
- porównanie wymagało zgodnych parametrów i identyfikatora serii, choć celem
  laboratorium jest także porównanie różnych konfiguracji;
- aktywny kontener symulatora miał stałą nazwę i nie był jednoznacznie własnością
  pojedynczego uruchomienia aplikacji;
- kończenie procesów wewnątrz kontenera było obsługiwane, lecz brakowało
  niezależnego zabezpieczenia po twardej awarii procesu GUI;
- w warstwie analizy znajdowały się rozbudowane mechanizmy statystyczne i
  budżetowanie, które nie są potrzebne do prostego porównania dwóch symulacji.

## Docelowa architektura

```text
Tkinter / ttk
    |
    +-- SessionController: zakładki, kolejka, stan i logi
    |
    +-- SimulationRunner
    |     +-- DockerSessionManager: kontener oznaczony sesją
    |     +-- RIC -> ns-3 -> xApp
    |     +-- archiwizacja wyników
    |
    +-- SimulationStorage: konfiguracja, wynik, historia, eksport
    +-- ResultsParser: metryki wyłącznie z zapisanych danych
    +-- Comparator: różnice konfiguracji i prosta tabela dwóch wyników
    +-- Cleanup guardian: osobny proces oczekujący na proces GUI
```

Każdy kontener uruchamiany przez aplikację dostaje co najmniej etykiety:

```text
com.oranenergylab.managed=true
com.oranenergylab.session=<uuid>
com.oranenergylab.owner-pid=<pid>
```

Sprzątanie filtruje równocześnie po `managed=true` i dokładnym identyfikatorze
sesji. Nigdy nie wybiera wszystkich kontenerów Docker.

## Wykonana migracja

1. Zastąpiono wejście FastAPI wejściem desktopowym Tkinter/ttk.
2. Utworzono niezależne modele symulacji, zapis sesji, prostą kolejkę i porównanie.
3. Zachowano formaty artefaktów ns-3 oraz sekwencję RIC, ns-3 i xApp, lecz każda
   symulacja uruchamia własny kontener.
4. Dodano sprzątanie przy zamknięciu okna, sygnale, wyjątku oraz przez niezależny
   proces ochronny po twardym zakończeniu programu.
5. Usunięto z aktywnej aplikacji webowy workflow serii, blokadę porównań różnych
   konfiguracji i niepotrzebne statystyki.
6. Dodano testy kolejki, porównania, parsera, magazynu i Docker lifecycle z
   atrapami; uruchomienia rzeczywistego Docker wymagają hosta z Docker Desktop.

## Aktualny stan warstwy operatorskiej

### Działające źródła i procesy

- Jeden run tworzy dokładnie jeden kontener sesji z obrazu `wcnc_tap`.
  nearRT-RIC, ns-3 i xApp są procesami `docker exec` wewnątrz niego, nie
  trzema kontenerami.
- `ManagedDockerProcess` przekazuje stdout/stderr tych procesów przyrostowo do
  osobnych plików i ograniczonego bufora pamięci.
- `optimized` tworzy `ee_timeseries.csv` z czasem symulowanym, dostarczonymi
  bitami, energią, mocą interwałową, throughputem interwałowym, EE, packet loss
  oraz średnim opóźnieniem. Dashboard
  pobiera tylko ostatnie kompletne wiersze tego pliku w workerze.
- Po zakończeniu runu parser czyta pełny, zarchiwizowany artefakt tego samego
  formatu. Live i końcowy wynik nie mają odrębnych źródeł danych.

### Braki danych, których interfejs nie maskuje

`optimized` potwierdza szeregi live dla delay i packet loss przez FlowMonitor.
SINR i PRB nadal są opcjonalne: historyczne katalogi nie są dowodem, że bieżący
backend je zapisze, więc interfejs pokazuje je wyłącznie po wykryciu realnego
źródła, a w przeciwnym razie pokazuje N/A.

### Rozbudowane widoki

- **Dashboard**: postęp aktywnego limitu czasu rzeczywistego, odczyt czasu
  ns-3, data końca kolejki, bieżące metryki, serie energii/mocy/EE/kosztu
  energii/danych/throughputu oraz
  statusy komponentów.
- **Docker**: stan kontenera sesji (gdy Docker go udostępnia: health, uptime,
  restart count, CPU, RAM, exit code), osobne procesy oraz bounded live log.
- **Scenariusze**: CRUD, import/eksport i builder oparty na `allowed_params`.
- **xApps**: konfiguracja prawdziwych zmiennych środowiskowych My Energy,
  kopia szablonu My Energy/KPM i build do nowego lokalnego obrazu Docker.

Build xAppa używa istniejącego układu FlexRIC: target CMake z `e42_xapp`,
`pthread`, `sctp` i `dl`. Wymaga obrazu, w którym discovery wykryje FlexRIC i
katalog `examples/xApp/c/myxapp` albo `orange`; brak tych elementów kończy
operację czytelnym błędem bez zmiany obrazu bazowego.
