# Symulator efektywności energetycznej sieci O-RAN

Desktopowe laboratorium w Pythonie do konfiguracji, uruchamiania i porównywania
niezależnych symulacji 5G/O-RAN.

Interfejs jest zbudowany w Tkinter/ttk i prowadzi przez prosty tok pracy:

```text
konfiguracja -> uruchomienie -> obserwacja live -> wyniki -> porównanie
```

Zakładka **My Energy** zawiera parametry oraz edytory kodu xAppa C i scenariusza
C++. Zapis tworzy kopię poprzedniej wersji; kolejny przebieg automatycznie
kompiluje zapisany kod. **Dodaj parę porównawczą** tworzy dwa przebiegi z tym
samym ruchem, topologią i RngRun, z xAppem oraz bez niego.

Domyślna topologia ma 12 UE i 3 osobne komórki NR z kotwicą LTE. Jest też
wariant 24 UE / 6 komórek. To uproszczona sieć wielu gNB z antenami dookólnymi,
nie odwzorowanie trzech sektorów jednego masztu. `CellLayout=1` rozmieszcza
gNB w regularnym wielokącie, a rozstaw oznacza odległość sąsiednich gNB.
Wariant `0` zachowuje dawny układ: środek i pierścień.

Szczegóły edycji, dwóch polityk i odtwarzania badań: [My Energy](docs/MY_ENERGY.md).
Pełna kontrola 12 s: [wyniki My Energy](docs/WYNIKI_MY_ENERGY_2026-10-20.md)
— 11,22% mniej energii modelu w pojedynczej sparowanej próbie.

Każda symulacja ma własną zakładkę. Nowe przebiegi mają domyślnie 5 s czasu
modelowanego; czas pracy komputera może być znacznie dłuższy. Mogą różnić się liczbą UE, gNB, ruchem,
parametrami radiowymi i xAppem; porównanie ich nie blokuje, tylko pokazuje
różnice konfiguracji. Zakładki Na żywo, Docker i logi, Scenariusze i xApps działają
bez przeglądarki oraz bez wymagania terminala w zwykłym workflow.

## Uruchomienie na Windows

Instalator dopuszcza Windows 11 x64 25H2 (build 26200) w edycji Pro,
Enterprise lub Education oraz 24H2 (build 26100) w edycji Enterprise lub Education,
zgodnie z macierzą tej wersji. Potrzebne są połączenie z Internetem, aktywna
wirtualizacja z SLAT, co najmniej 8 GB RAM i miejsce na obraz oraz cache
kompilacji. Wymagania systemowe Dockera opisuje
[instrukcja Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/#system-requirements).

W terminalu otwartym w katalogu projektu uruchom:

```powershell
.\install_windows.bat -ImageMode Source -InstallWsl -InstallDocker -InstallPython
```

Przełączniki pozwalają instalatorowi uzupełnić brakujące WSL 2, Docker Desktop
i CPython 3.13 x64 z Tk. Po komunikacie o restarcie uruchom ponownie Windows
i wykonaj to samo polecenie. Jeśli instalator właśnie doinstalował Docker
Desktop, otwórz go, zaakceptuj jego warunki i poczekaj na uruchomienie silnika
Linux z WSL 2 przed ponownym wykonaniem polecenia. Git na hoście nie jest
wymagany. Pierwsza budowa obrazu może potrwać kilkadziesiąt minut.

Tryb `-ImageMode Local -LocalImageId sha256:…` używa istniejącego lokalnego
obrazu o wskazanym ID i zgodnym manifeście źródeł. Zależności aplikacji trafiają
do `.venv` w katalogu projektu, a logi instalatora do `.install\logs`.
Po ukończonej instalacji uruchamiaj aplikację przez `run_windows.bat`.

Na Windows 11 Home x64 26H2 (SKU 101, build 26300) możesz jawnie wybrać tryb
`-AllowWindowsHome`. Dodaj go do polecenia instalacji i do `run_windows.bat`.
Tryb nadal wymaga działającego lokalnego silnika Linux/amd64 w Docker Desktop
z WSL 2.

### Jednoplikowy instalator

Instalator `O-RAN-Energy-Lab-Setup.pyz` zawiera pliki aplikacji i uruchamia
ten sam instalator Windows. U odbiorcy wystarczy uruchomić
`py O-RAN-Energy-Lab-Setup.pyz` z zainstalowanym Pythonem. Paczka pobiera
zależności i buduje obraz Docker na komputerze odbiorcy.

Budowa paczki z tekstami licencji scenariusza i xApp:

```powershell
python scripts/build_windows_installer.py --licenses "C:\ścieżka\do\LICENSES"
```

Osobny build deweloperski PyInstaller:

```powershell
& .\scripts\build-windows.ps1
```

Wersję spakowaną uruchamia się z całego katalogu
`dist/Symulator O-RAN/`, a nie z samego pliku `.exe` — podkatalog `_internal`
zawiera Python, Tk i dostarczone źródła scenariusza/xAppa. Po podmianie wersji
można użyć **Ustawienia → Odśwież konfigurację dostarczoną z aplikacją**; program
najpierw zapisze kopię poprzedniej lokalnej konfiguracji.

## Uruchomienie na Linuxie

Dla Ubuntu/Debiana przygotowano instalator oraz skrót w menu i na pulpicie.
Po skopiowaniu całego katalogu projektu wykonaj jednorazowo:

```bash
bash install_linux.sh
```

Instalator przygotowuje Python/Tk, środowisko aplikacji i Docker Engine, gdy
brakuje ich w systemie. Wymaga świadomego podania hasła administratora tylko
do zmian systemowych. Obraz `wcnc_tap` może być dostarczony jako
`resources/wcnc_tap.tar` z plikiem `.sha256`, wskazany w `config/linux.env`
pełną referencją z digestem albo zbudowany z dołączonego
  `.simulator-src/e2sim-kpmv3` i `.simulator-src/mmwave-LENA-oran`. Przy budowie
  lokalnego obrazu instalator automatycznie dołącza aktualny scenariusz i My
  Energy xApp z katalogu `resources`, a następnie buduje je razem z obrazem.
  Instalator nie zgaduje obrazu z rejestru.

Pełny opis przeniesienia i pierwszego uruchomienia: [instalacja Linux](docs/INSTALACJA_LINUX.md).

Szczegóły: [instrukcja](docs/INSTRUKCJA.md) i [metryki](docs/METRYKI.md).

Do przygotowania opisu projektu: [jak działa całość](docs/JAK_DZIALA.md)
oraz [propozycja spisu treści pracy](docs/SPIS_TRESCI.md).

Aktualny [przegląd i weryfikacja](docs/PRZEGLAD_2026-10-18.md) opisuje naprawy,
ograniczenia modelu i zakres wykonanej weryfikacji. Porównanie ocenia teraz EE
razem z QoS: można podać progi opóźnienia, strat i przepływności; bez progów
wymagany jest brak pogorszenia względem pierwszego przebiegu. Wyniki częściowe
i nieporównywalne pozostają dostępne, ale nie otrzymują korzystnej oceny.

## Metryki

- średnia efektywność energetyczna: `bit/J`;
- energia na 1 Mbit: `J/Mbit`;
- energia całkowita: `J`;
- średnia moc: `W`;
- throughput, opóźnienie, packet loss i dostarczone dane — gdy występują w
  zapisanych wynikach.

Energia dotyczy modelowanej radiowej części RAN/gNB, nie fizycznego poboru
energii komputera.

## Obserwacja podczas działania

Scenariusz `optimized` zapisuje `ee_timeseries.csv` w trakcie pracy ns-3.
Aplikacja odczytuje jego ostatnie kompletne wiersze okresowo w wątku roboczym,
bez pobierania całego pliku i bez blokowania Tkintera. Dashboard pokazuje tylko
wartości obecne w tym pliku: czas symulowany, postęp, orientacyjny ETA, energię,
moc, EE, energię na Mbit, dostarczone dane i throughput. Po zakończeniu ten sam
artefakt jest kopiowany do wyniku runu i analizowany ponownie jako wynik końcowy.

Ten sam scenariusz zapisuje również `PacketLoss_pct` i `MeanDelay_ms` z
liczników aplikacji UDP: UdpClient i PacketSink. Opóźnienie wyznacza znacznik
SeqTsHeader, który przechodzi przez radio. FlowMonitor nie nadaje się do tego
wariantu MC PDCP, który usuwa jego byte tags. Wartość opóźnienia pozostaje
`N/A`, dopóki nie ma odebranego pakietu; SINR i PRB są nadal widoczne wyłącznie,
gdy backend naprawdę udostępnia odpowiedni strumień KPM.

Końcowe `ee_summary.csv` zapisuje pełne liczniki Tx/Rx i sumę opóźnień po
opróżnianiu kolejek (`DrainTime_s`). Końcowy loss oznacza pakiety niedostarczone
do końca okna; wskaźnik live obejmuje również pakiety będące jeszcze w drodze.
Obecny wariant zapisuje agregaty sieciowe, bez rozkładów opóźnień per UE. Brak odebranych danych
daje EE=0 i J/Mbit=N/A, bez sztucznego zgłaszania awarii poprawnego przebiegu.

Opcjonalny budżet kolejki jest limitem **czasu rzeczywistego** aktywnej pracy
każdego gotowego runu. Przykład: 10 min i 2 runy daje po ok. 5 min dla każdego,
po starcie xAppa. `simTime` jest tylko odczytem ns-3 i nie zmienia czasu kolejnej
symulacji. Przed pierwszym ukończonym runem data końca jest oznaczona jako plan;
potem prognoza uwzględnia zaobserwowany narzut przygotowania Docker/RIC.

Widok Docker rozróżnia jeden kontener sesji od procesów w jego wnętrzu:
nearRT-RIC, ns-3 i xApp. Procesy nie są osobnymi kontenerami. Logi procesów są
strumieniowane przyrostowo, pełna historia zostaje na dysku, a widok GUI ma limit
historii, wyszukiwanie, filtr ERROR/WARNING, pauzę auto-scroll, kopiowanie i
zapis widocznych wpisów.

## Scenariusze i xAppy

Zakładka **Scenariusze** zapisuje osobne, importowalne pliki JSON z parametrami
dopuszczonymi przez aktywny backend. Builder pokazuje jednostki, opisy, wartości
domyślne oraz zweryfikowane ograniczenia. Nie dodaje pól, których runner nie
przekaże do ns-3.

Zakładka **xApps** pokazuje wpisy z `config.json`. Dla `My Energy` dostępne jest
rzeczywiste źródło i tworzenie lokalnej kopii szablonu My Energy/KPM. Build działa
w tymczasowym kontenerze aktualnego obrazu; po sukcesie tworzy nowy lokalny obraz
Docker i przełącza na niego następne symulacje. Obraz bazowy nie jest nadpisywany
ani automatycznie usuwany. Wpis Orange ma w tym repozytorium wyłącznie binarkę
obrazu, dlatego interfejs uczciwie nie oferuje dla niego builda bez źródła.

Dla dostarczonego My Energy runner synchronizuje i buduje również xApp
w każdej sesji, aby stary obraz nie uruchomił starej polityki. Weryfikuje SHA256
źródła, a wynik kompilacji zapisuje w `logs/xapp-build.log`.

## Docker lifecycle

Każda symulacja uruchamia własny kontener oznaczony identyfikatorem sesji.
Przy zwykłym zamknięciu, zatrzymaniu lub błędzie aplikacja usuwa wyłącznie
kontenery bieżącej sesji. Niezależny proces ochronny sprząta taki kontener po
twardym zakończeniu aplikacji; kolejne uruchomienie usuwa także osierocone
kontenery tej aplikacji. Przed przejściem do kolejnej symulacji aplikacja
potwierdza usunięcie kontenera poprzedniej. Gdy potwierdzenie nie powiedzie
się, zapisuje ostrzeżenie i mimo to próbuje uruchomić następną pozycję w nowym,
osobnym kontenerze sesji; tylko wyraźny STOP użytkownika zatrzymuje kolejkę.
