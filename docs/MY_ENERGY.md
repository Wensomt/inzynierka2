# My Energy: edycja i badanie

W zakładce **My Energy** są parametry oraz dwa edytory: kod xAppa C i kod
scenariusza C++. Można wyszukiwać tekst, przejść bezpośrednio do decyzji
algorytmu lub rozmieszczenia sieci, a kod zapisać przez **Ctrl+S**.
Przed nadpisaniem zmienionego pliku program zachowuje kopię w `backups`.
Następne uruchomienie kopiuje i kompiluje zapisane źródła w kontenerze.
Błąd kompilacji zatrzymuje przebieg i zostaje w logu.

Najczęstsze zmiany progów wykonuj w formularzu. Przy zmianie samej reguły
decyzyjnej przycisk **Decyzje algorytmu** prowadzi do `forEachCell`:

1. Odrzuć komórki wyłączone, bez świeżego KPM i komórkę kotwiczącą.
2. Poczekaj na kilka kolejnych ocen małego obciążenia.
3. Znajdź cel dla każdego UE i sprawdź cały plan przeniesienia.
4. Wyślij handovery i poczekaj na raporty z komórek docelowych.
5. Po potwierdzeniu wszystkich UE i odczekaniu zadanego czasu wyślij wyłączenie.

`getTargetCellID` wybiera cel, `sm_cb_kpm` odbiera KPM, a
`switchOffCurrentCell` buduje i wysyła sterowanie E2. Funkcje tworzące
struktury ASN.1 obsługują format protokołu; zmiana progu lub reguły wyboru
komórki nie wymaga ich przepisywania. W scenariuszu C++ przycisk
**Rozmieszczenie sieci** prowadzi do geometrii, a sekcja `GlobalValue`
zawiera deklaracje parametrów przekazywanych przez formularz.

W uruchomieniu z repozytorium pliki są w `data/my_energy`. W EXE trafiają do
`%LOCALAPPDATA%/O-RAN Energy Lab/my_energy`. Stara nazwa katalogu danych
pozostaje dla zgodności z istniejącymi wynikami i ustawieniami. Źródła w
`resources` są wzorcami do przywrócenia, nie miejscem edycji w zainstalowanym EXE.

## Sieć

Podstawowy wariant ma 12 UE i 3 komórki NR, dodatkowy 24 UE i 6 komórek.
`CellLayout=1` oznacza osobne gNB rozmieszczone w regularnym wielokącie;
rozstaw 200 m jest odległością sąsiednich gNB. UE poruszają się w obszarze
o początkowym promieniu 250 m. Anteny są dookólne, wysokości wynoszą 10 m
dla gNB i 1,5 m dla UE. Ruch to 200 B co 2000 µs na UE, czyli 0,8 Mbit/s/UE
podczas generowania ruchu. Wspólna kotwica LTE zapewnia architekturę EN-DC
używaną przez ten wariant mmWave-LENA-O-RAN.

Nie należy opisywać tego jako trzech sektorów jednego masztu. Typowy podział
na trzy sektory po 120° wymaga anten kierunkowych i jawnego modelu sektorów;
przykład takiej topologii opisuje [Ericsson Technology Review](https://www.ericsson.com/494bc0/assets/local/reports-papers/ericsson-technology-review/docs/2023/etr-magazine-2023-1.pdf).
Liczba komórek, sektorów i fizycznych lokalizacji nie jest tym samym.
To model badawczy, bez kalibracji do konkretnej stacji i operatora.

`CellLayout=0` zachowuje dawną geometrię: jedna komórka centralna i pozostałe
na pierścieniu. Wszystkie parametry trafiają do zapisanej konfiguracji runu.

## Dwie polityki do porównania

Domyślna polityka zachowawcza wymaga SINR celu co najmniej 0 dB i spadku SINR
nie większego niż 3 dB. Może przez to odmawiać konsolidacji nawet wtedy,
gdy komórka docelowa zapewnia dobrą transmisję.

Przycisk **Polityka energetyczna** ustawia jawny wariant badawczy:

| Parametr | Wartość |
|---|---:|
| Minimalny SINR celu | 5 dB |
| Minimalna różnica SINR cel − źródło | −100 dB, czyli praktycznie bez preferowania lepszego sygnału |
| Maksymalna liczba UE w wyłączanej komórce | 4 |
| Okres oceny | 1 s ns-3 |
| Potwierdzenie po ostatnim handoverze | 1 s ns-3 |

Pozostałe pola formularza nadal obowiązują. Próg SINR i liczba UE są
heurystyką, a nie gwarancją QoS. Dlatego po przebiegu trzeba sprawdzić
rzeczywiście odebrane dane, straty i opóźnienie.

Algorytm zachowuje jedną komórkę kotwiczącą NR, sprawdza małe obciążenie
przez kolejne oceny i szuka celu dla każdego UE. Najpierw sprawdza cały plan
i rezerwacje pojemności; dopiero potem wysyła handovery. Samo przyjęcie
polecenia nie oznacza wykonania: xApp czeka na raport serving z celu.
Polecenie wyłączenia wysyła po potwierdzeniu wszystkich przeniesień i czasie
potwierdzania. Brak potwierdzenia albo przekroczenie timeoutu kończy próbę
odmową. Nie zaimplementowano automatycznego ponownego włączania komórek.

Wiek KPM, okres decyzji, potwierdzenia i cooldown korzystają z czasu ns-3.
Runner ustawia w tym celu timestamp nagłówków KPM w kontenerze sesji.
Czas oczekiwania na start E2 nadal jest czasem komputera. Zapis `time` w CSV
decyzji nowego My Energy oznacza sekundy ns-3; w historycznych runach był
timestampem systemowym. Nie należy mieszać tych osi.

W tym backendzie stan energetyczny komórki jest umowny: polecenie ustawia
TxPower na 0 i wysoki współczynnik szumów, a `MmWaveSpectrumPhy` mapuje ten
znacznik na stan 4 modelu energii. Model przypisuje mu zerowy prąd.
Nie jest to pomiar fizycznego wyłączenia całej stacji. W raporcie trzeba
oddzielić wysłane polecenia, potwierdzone handovery, stan modelu oraz energię.

## Uruchomienie eksperymentu

**Dodaj parę porównawczą** dodaje do sesji dwa warianty z identyczną topologią,
ruchem i RngRun. Pierwszy nie ma xAppa, drugi używa My Energy. Pełne wyniki
powinny kończyć się przy tym samym `simTime`, bez limitu czasu rzeczywistego.
W My Energy proponowany czas wynosi 12 s ns-3; liczenie może zająć wiele minut.

Kontrola bez GUI:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_simulator --sim-time 12 --ues 12 --cells 3 --seed 1 --policy energy
```

Skrypt zamraża źródła, zapisuje ich SHA-256, konfigurację, próbki live i wyniki
w `data/experiments/backend-check/<data>`. Każdy przebieg działa kolejno.
Powtórzenie z `--seed 2` zachowuje parametry i zmienia RngRun.
`--policy conservative` wybiera wariant zachowawczy.

Po edycji samego xAppa można użyć `--baseline <katalog-przebiegu-bez-xApp>`.
Skrypt przyjmie wyłącznie ukończony, ważny pomiar z identycznymi parametrami,
źródłem scenariusza i poprawką zegara KPM. Kopiuje jego pełne artefakty do
nowego eksperymentu i zapisuje informację o ponownym użyciu. Zmiana topologii,
ruchu, RngRun lub kodu scenariusza wymaga nowego odniesienia.
Skrypt zapisuje indeks przebiegów także przed rozpoczęciem kolejnego pomiaru.
Przerwane próby nie otrzymują procentowej oszczędności w podsumowaniu.

Zestawienie CSV/JSON oraz opcjonalne wykresy do pracy:

```powershell
.\.venv\Scripts\python.exe -m scripts.summarize_experiment data/experiments/backend-check/<data> --plots
```

Wykresy wymagają osobno `matplotlib`; zwykła aplikacja go nie wymaga.
Pojedyncza para służy do weryfikacji działania. Do wniosków ogólnych potrzebne
są sparowane RngRun, różne obciążenia, określone wymagania QoS i analiza rozrzutu.
