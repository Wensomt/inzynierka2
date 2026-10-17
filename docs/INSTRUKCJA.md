# O-RAN Energy Lab

## Uruchomienie

Wymagania:

- Python 3.8 lub nowszy z Tkinter;
- uruchomiona usługa Docker (Docker Desktop na Windowsie lub Docker Engine na Linuxie);
- obraz symulatora `wcnc_tap`, zawierający ns-3, FlexRIC oraz wymagany xApp.

Zainstaluj zależności i uruchom aplikację:

```powershell
python -m pip install -r requirements.txt
python -m app.main
```

Przed pierwszym uruchomieniem kolejki wybierz w zakładce **Ustawienia**
**Sprawdź środowisko**. Aplikacja wyświetla konkretny problem, np. brak Docker
albo obrazu symulatora.

## Linux: instalacja i skrót na pulpicie

Na Ubuntu/Debianie użyj jednorazowo `bash install_linux.sh`. Skrypt tworzy
skrót **O-RAN Energy Lab** na pulpicie i w menu, a kolejne uruchomienia odbywają
się jednym kliknięciem. Pełna procedura, w tym bezpieczne dostarczenie obrazu
symulatora, jest w [INSTALACJA_LINUX.md](INSTALACJA_LINUX.md).

## Praca w laboratorium

1. Wybierz liczbę symulacji i kliknij **Nowa sesja**.
2. Ustaw parametry w osobnej zakładce każdej symulacji.
3. Kliknij **URUCHOM WSZYSTKIE**. Symulacje są wykonywane kolejno.
4. Otwórz **Dashboard**, aby zobaczyć aktualny etap, postęp czasu ns-3,
   czas rzeczywisty, ostrożnie wyliczony ETA, metryki i wykres z danych
   dostępnych w bieżącym runie.
5. Otwórz **Docker**, aby sprawdzić jeden kontener sesji oraz procesy RIC,
   ns-3 i xApp. Każdy proces ma osobny, przyrostowy log z filtrem i limitem
   widocznej historii.
6. Otwórz **Scenariusze**, aby zapisać, powielić, importować, eksportować i
   zastosować nazwany zestaw parametrów do aktywnej zakładki symulacji.
7. Otwórz **xApps**, aby wybrać konfigurację, utworzyć kopię szablonu My
   Energy/KPM lub zbudować źródło do nowego lokalnego obrazu Docker.
8. Otwórz **Wyniki**, aby zobaczyć stan i zapisane metryki.
9. Otwórz **Porównanie**, wybierz dwie zakończone symulacje i zobacz tabelę
   metryk oraz różnice konfiguracji.

Przycisk **STOP** kończy bieżącą symulację, zapisuje jej stan jako przerwany i
sprząta jej procesy oraz kontener. Jeżeli przed przerwaniem powstały artefakty,
aplikacja zapisuje je jako wynik niepełny z odpowiednim ostrzeżeniem. Błąd
pojedynczej symulacji nie zatrzymuje kolejnych pozycji kolejki, o ile cleanup
jej kontenera został potwierdzony.

### Wspólny czas modelowany z budżetu rzeczywistego

Pole **Łączny budżet czasu rzeczywistego ns-3** jest opcjonalne. Nie ustawia
ono `simTime`. Dla przykładu: przy 10 minutach i dwóch symulacjach pierwsza,
już gotowa symulacja dostaje 5 minut aktywnej pracy w czasie rzeczywistym.
Po tej chwili aplikacja wysyła żądanie zatrzymania ns-3, odczytuje ostatni
faktycznie zapisany czas modelowany i wymusza tę samą wartość `simTime` oraz
ten sam `RngRun` w każdej następnej symulacji. Rozruch kontenera/RIC i zapis
wyników nie są częścią limitu. Następne runy zawsze są wykonywane, także gdy
rzeczywisty czas całej kolejki przekroczy pierwotny budżet; po pierwszym runie
dashboard pokazuje orientacyjną datę zakończenia opartą na jego faktycznym
czasie.

Gdy xApp zakończy się przed ns-3, aplikacja zapisuje kod i czas zdarzenia,
próbuje go uruchomić ponownie do ustawionego limitu, a po jego wyczerpaniu
pozwala ns-3 zakończyć symulację bez xAppa. Pliki decyzji kolejnych uruchomień
xAppa pozostają osobne.

## Dane live i ograniczenia

`optimized` zapisuje `ee_timeseries.csv` około co 0,1 s czasu symulowanego.
Zawiera czas, dostarczone bity, energię, moc interwałową, throughput interwałowy,
EE, packet loss oraz średnie opóźnienie. Packet loss i delay pochodzą z
FlowMonitor ograniczonego do rzeczywistych UDP downlinków UE; delay pozostaje
brakiem danych przed pierwszym odebranym pakietem. To jest źródło dashboardu;
aplikacja odczytuje tylko jego ostatnie kompletne wiersze.

SINR i PRB nadal są pokazywane tylko wtedy, gdy aktywny backend zapisuje
odpowiedni strumień KPM. Nie są zastępowane wymyślonymi wartościami.

Gdy ustawiono całkowity budżet czasu kolejki, jest on dzielony równo na aktywną
pracę każdego runu w **czasie rzeczywistym**. Dla 10 min / 2 runów każdy run
zostaje zatrzymany po ok. 5 min od pełnej gotowości (RIC → ns-3 → xApp).
`simTime` pozostaje wyłącznie odczytem ns-3 i nie steruje paskiem postępu ani
czasem kolejnej symulacji.

Kontener Docker uruchomiony przez aplikację jest jeden na run. nearRT-RIC,
ns-3 i xApp są procesami `docker exec` w tym kontenerze, co ma znaczenie przy
interpretacji widoku Docker i logów.

## Konfiguracje i wyniki

**Zapisz konfigurację** zapisuje zestaw zakładek bez wyników. Wynik każdej
symulacji zawiera jej konfigurację, stan, log i dane odczytane z artefaktów.

W szczegółach wyniku dostępne są eksporty JSON i CSV. Historia pokazuje
zakończone symulacje zapisane przez desktopową aplikację.

## Pakowanie na Windows

```powershell
./scripts/build-windows.ps1
```

Plik wykonywalny powstaje w `dist/O-RAN Energy Lab/`. Docker Desktop i obraz
symulatora pozostają wymagani także dla wersji `.exe`. W wersji spakowanej
konfiguracje i wyniki są zapisywane domyślnie w `%LOCALAPPDATA%\O-RAN Energy Lab`
(można wskazać inną lokalizację przez `ORAN_LAB_DATA_DIR`).
