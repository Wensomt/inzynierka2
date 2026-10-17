# Instalacja na Linuxie

Ten sposób jest przeznaczony dla Ubuntu 20.04+ i Debiana 11+ z graficznym
pulpitem oraz systemd. Wymagany jest Python 3.8 lub nowszy. Architektura
obrazu symulatora musi odpowiadać architekturze komputera docelowego.

Python 3.8 jest obsługiwany dla zgodności z Ubuntu 20.04; przy nowej instalacji
warto jednak użyć nowszego wydania Pythona i systemu, które nadal otrzymuje
aktualizacje bezpieczeństwa.

Po jednorazowym przygotowaniu użytkownik uruchamia aplikację ikoną **O-RAN
Energy Lab** z pulpitu lub menu.

Najpierw przenieś katalog do jego docelowej lokalizacji. Jeżeli później go
przeniesiesz, uruchom ponownie `bash install_linux.sh`, aby odświeżyć ścieżkę
zapisaną w skrócie.

## Co przenieść

Przenieś cały katalog projektu, nie pojedynczy plik. Najwygodniejsze miejsce to
na przykład `~/Applications/O-RAN Energy Lab`.

Katalog `data` warto przenieść, gdy potrzebna jest dotychczasowa historia.
Środowiska `.venv`, cache i katalog `.git` nie są potrzebne na komputerze
docelowym.

## Dostarczenie obrazu symulatora

Projekt nie zgaduje publicznego adresu obrazu `wcnc_tap`. Kompletny obraz
możesz dostarczyć jako archiwum, wskazać niezmiennym refem rejestru albo
zbudować z dołączonego katalogu `.simulator-src`. Wybierz jeden z wariantów
poniżej przed pierwszym uruchomieniem na komputerze docelowym.

### Wariant offline — zalecany

Na komputerze, na którym obraz `wcnc_tap` już działa, wejdź do katalogu
projektu i wykonaj:

```bash
bash scripts/linux/export-image.sh
```

Powstaną pliki:

```text
resources/wcnc_tap.tar
resources/wcnc_tap.tar.sha256
```

Następnie przenieś cały katalog projektu wraz z tymi plikami. Instalator
sam sprawdzi sumę SHA-256 i wykona `docker load`.

### Wariant z rejestrem

Skopiuj plik przykładowy i wpisz rzeczywisty, niezmienny adres obrazu:

```bash
cp config/linux.env.example config/linux.env
```

W `config/linux.env` ustaw jedną wartość w takim formacie:

```text
ORAN_LAB_IMAGE_REF=registry.example.org/oran/wcnc_tap@sha256:TU_JEST_PEŁNY_DIGEST
```

W przypadku prywatnego rejestru najpierw wykonaj `docker login` dla jego hosta.
Instalator nie akceptuje samych tagów, takich jak `latest`, ponieważ nie
oznaczają stałej wersji obrazu.

Logowanie wykonaj jako ten sam zwykły użytkownik, który uruchomi instalator.
Podczas pobierania instalator korzysta z jego katalogu `~/.docker`, nawet gdy
do komunikacji z Docker Engine potrzebuje chwilowo `sudo`.

### Wariant z lokalnych źródeł

Jeżeli w przenoszonym katalogu są oba katalogi:

```text
.simulator-src/e2sim-kpmv3
.simulator-src/mmwave-LENA-oran
```

oraz plik `mmwave-LENA-oran/ns3_docker/Dockerfile`, instalator użyje ich
automatycznie, gdy nie znajdzie archiwum ani refu obrazu. Dockerfile pobiera
swoje jawnie zdefiniowane zależności, więc ten wariant wymaga Internetu i może
trwać długo. Jeżeli w snapshotcie brakuje zainicjalizowanego modułu `src/nr`,
instalator najpierw pobierze dokładny commit wskazany przez jego metadane Git;
jeżeli nie może tego zrobić, zatrzyma się przed Docker buildem z czytelnym
błędem. Przed buildem instalator przekazuje do kontrolowanego katalogu build
kontekstu aktualne źródła scenariusza oraz My Energy xAppa z `resources`.
Dockerfile kopiuje je odpowiednio do `scratch` i istniejącego katalogu xAppów
FlexRIC, a potem buduje obraz. Przed każdym runem aplikacja dodatkowo
synchronizuje scenariusz do świeżej sesji i wymusza build jego dokładnego
targetu, więc stare binarium nie może ukryć brakujących metryk live.

Jeśli nie chcesz używać lokalnego buildu mimo obecności źródeł, ustaw w
`config/linux.env`:

```text
ORAN_LAB_BUILD_LOCAL_SOURCES=0
```

## Pierwsze uruchomienie

W terminalu wykonaj:

```bash
cd "$HOME/Applications/O-RAN Energy Lab"
bash install_linux.sh
```

Skrypt pokazuje plan zmian i przed użyciem `sudo` prosi o potwierdzenie. W razie
braku przygotowuje:

- Python, Tkinter i środowisko aplikacji;
- Docker Engine z oficjalnego repozytorium Docker;
- uruchomienie usługi Docker;
- obraz symulatora z archiwum albo wskazanego rejestru;
- skrót w menu oraz na pulpicie.

Nie uruchamiaj instalatora przez `sudo`, np. nie używaj `sudo bash
install_linux.sh`. Musi on działać jako użytkownik pulpitu, aby skrót,
środowisko Python i dane trafiły do właściwego profilu; skrypt sam wywoła
`sudo` wyłącznie do instalacji pakietów i obsługi Docker Engine.

Nie usuwa istniejących obrazów, kontenerów, wolumenów ani konfiguracji Docker.
Nie wykonuje też czyszczenia systemu Docker.

Jeśli instalator doda użytkownika do grupy `docker`, trzeba **wylogować się i
zalogować ponownie**. Jest to wymóg systemu, aby aplikacja miała dostęp do
gniazda Docker bez uruchamiania jej jako root. Grupa `docker` daje szeroki
dostęp przez Docker, dlatego używaj jej tylko na zaufanym komputerze.

Po ponownym zalogowaniu kliknij ikonę **O-RAN Energy Lab**. Zwykłe uruchomienie
nie otwiera terminala. Gdy później zabraknie wymaganego składnika, launcher
otworzy widoczny terminal przygotowania zamiast ukrytego pytania o hasło.

Jeżeli używasz interpretera spoza systemowego `python3`, ustaw przed pierwszą
instalacją `ORAN_LAB_PYTHON` na pełną ścieżkę do działającego interpretera.
Instalator zapamięta ten wybór dla ikony pulpitu. Nie instaluje jednak
samodzielnie dowolnego, wskazanego interpretera — gdy ścieżka przestanie
istnieć, ustaw zmienną ponownie albo usuń plik
`~/.config/oran-energy-lab/launcher.env` i uruchom instalator.

## Gdzie trafiają dane

Launcher przechowuje dane i konfigurację użytkownika poza katalogiem kodu:

```text
~/.local/share/O-RAN Energy Lab
~/.config/oran-energy-lab/config.json
~/.local/state/oran-energy-lab/launcher.log
~/.local/state/oran-energy-lab/gui.log
~/.cache/oran-energy-lab/venv-python3.X
```

Logi konkretnej symulacji są w katalogu danych aplikacji:

```text
~/.local/share/O-RAN Energy Lab/simulations/<ID_RUNU>/logs/runner.log
~/.local/share/O-RAN Energy Lab/simulations/<ID_RUNU>/logs/ric.log
~/.local/share/O-RAN Energy Lab/simulations/<ID_RUNU>/logs/ns3.log
~/.local/share/O-RAN Energy Lab/simulations/<ID_RUNU>/logs/xapp.log
~/.local/share/O-RAN Energy Lab/simulations/<ID_RUNU>/logs/xapp-build.log
~/.local/share/O-RAN Energy Lab/simulations/<ID_RUNU>/attempts/attempt-1/logs/
```

`<ID_RUNU>` jest identyfikatorem zapisanym w `simulation.json`. Powód błędu,
rodzaj awarii i stan retry znajdują się w tym samym pliku. Przy uruchomieniu
developerskim bez instalatora katalogiem bazowym jest `data/simulations` w
repozytorium; zmienne `ORAN_LAB_DATA_DIR` i `XDG_DATA_HOME` mogą tę lokalizację
zmienić.

Możesz zmienić lokalizacje przez `ORAN_LAB_DATA_DIR`, `ORAN_LAB_CONFIG_PATH`,
`XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME` i `XDG_CACHE_HOME`.

## Szybka diagnostyka

```bash
docker info
docker image inspect wcnc_tap
bash run.sh
```

Jeśli problem wystąpił tuż po kliknięciu ikony, sprawdź
`~/.local/state/oran-energy-lab/launcher.log` oraz
`~/.local/state/oran-energy-lab/gui.log`. W samej aplikacji użyj też
**Ustawienia → Sprawdź środowisko**.
