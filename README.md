# O-RAN Energy Lab

Desktopowe laboratorium w Pythonie do konfiguracji, uruchamiania i porównywania
niezależnych symulacji 5G/O-RAN.

Interfejs jest zbudowany w Tkinter/ttk i prowadzi przez prosty tok pracy:

```text
konfiguracja -> uruchomienie -> obserwacja live -> wyniki -> porównanie
```

Każda symulacja ma własną zakładkę. Mogą różnić się liczbą UE, gNB, ruchem,
parametrami radiowymi i xAppem; porównanie ich nie blokuje, tylko pokazuje
różnice konfiguracji. Zakładki Dashboard, Docker, Scenariusze i xApps działają
bez przeglądarki oraz bez wymagania terminala w zwykłym workflow.

## Uruchomienie na Windows

W katalogu projektu kliknij **`install_windows.bat`**. Instalator przygotowuje
Python 3.12/3.13 i Tkinter, biblioteki aplikacji, WSL2, Docker Desktop z backendem
WSL2, źródła i obraz `wcnc_tap` oraz skrót na pulpicie. Nie wymaga wcześniej
zainstalowanego Pythona, Gita, Dockera ani `winget`; w razie braku `winget`
korzysta z oficjalnych instalatorów producentów. Po instalacji uruchamia
aplikację. Przy pierwszej konfiguracji Windows może poprosić o zgodę
administratora i restart; po restarcie kliknij `install_windows.bat` ponownie.
Przy pierwszym uruchomieniu Docker Desktop pokaże własne warunki korzystania —
zaakceptuj je, żeby uruchomić silnik.

Wymagane są aktualne Windows 10/11 x64, połączenie z Internetem, wirtualizacja
sprzętowa w BIOS/UEFI i co najmniej 8 GB RAM. Instalator pobiera przypięte
źródła symulatora i buduje obraz lokalnie wewnątrz Dockera; nie wymaga
wcześniejszego checkoutu Gita na Windows. Pierwsza kompilacja może potrwać
kilkadziesiąt minut i wymaga wolnego miejsca na dysku.
Logi każdego uruchomienia zapisuje w `%LOCALAPPDATA%\O-RAN Energy Lab\install`.
Można też dołączyć `resources\wcnc_tap.tar` i opcjonalny plik SHA-256 albo
ustawić `ORAN_LAB_IMAGE_REF` na adres obrazu z pełnym digestem.

Kolejne uruchomienia: kliknij skrót **O-RAN Energy Lab** na pulpicie albo
`run_windows.bat` w katalogu projektu. Build samodzielnego EXE:

```powershell
& .\scripts\build-windows.ps1
```

Wersję spakowaną uruchamia się z całego katalogu
`dist/O-RAN Energy Lab/`, a nie z samego pliku `.exe` — podkatalog `_internal`
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

Aktualny [audyt poprawności](docs/AUDYT_2026-10-02.md) opisuje naprawy,
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
rzeczywistego FlowMonitor dla UDP downlink do UE. Wartość opóźnienia pozostaje
`N/A`, dopóki nie ma odebranego pakietu; SINR i PRB są nadal widoczne wyłącznie,
gdy backend naprawdę udostępnia odpowiedni strumień KPM.

Końcowe `ee_summary.csv` zapisuje pełne liczniki Tx/Rx i sumę opóźnień po
opróżnianiu kolejek (`DrainTime_s`). Końcowy loss oznacza pakiety niedostarczone
do końca okna; live loss oznacza straty już stwierdzone przez FlowMonitor.
`flow-monitor.xml` zachowuje statystyki per przepływ. Brak odebranych danych
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
