# Kontrola My Energy — 20 października 2026

W pełnej parze 12 s ns-3 My Energy podjęło decyzję o konsolidacji ruchu,
wykonało cztery potwierdzone handovery i wyłączyło jedną z trzech komórek NR.
Energia modelu spadła o **11,2248%**, a końcowe agregaty transmisji nie uległy
pogorszeniu. To potwierdzenie działania w jednym wariancie, nie dowód
skuteczności przy dowolnym ruchu lub w rzeczywistej sieci.

## Warunki

Oba przebiegi zakończyły się statusem `COMPLETED`; kontrola zwróciła `OK`.
Parametry, źródło scenariusza, poprawka zegara KPM i okno pomiaru są zgodne.
Ukończony wcześniej przebieg bez xAppa został ponownie użyty po kontroli
zgodności i skopiowany wraz z surowymi artefaktami. Nie jest to nowe powtórzenie
statystyczne. Przerwana próba 19 października nie wchodzi do poniższych wyników.

| Parametr | Wartość |
|---|---|
| Czas modelowany / okno energii i danych | 0–12 s |
| Generowanie UDP | 0,1–10 s |
| Czas na opróżnienie kolejek | 2 s |
| Użytkownicy / komórki NR | 12 / 3, dodatkowo kotwica LTE |
| Rozmieszczenie gNB | regularny trójkąt, rozstaw sąsiadów 200 m |
| Początkowy promień rozmieszczenia UE | 250 m |
| Częstotliwość / pasmo | 3,5 GHz / 20 MHz |
| Ruch na UE | 200 B co 2000 µs, czyli 0,8 Mbit/s podczas nadawania |
| RngRun / okres KPM | 1 / 0,1 s ns-3 |
| Polityka My Energy | profil `energy`, opisany w [instrukcji](MY_ENERGY.md) |

Każdy wariant wysłał **59 400 pakietów**. Pierwsze 39 wspólnych próbek energii,
do 4 s i przed sterowaniem, było identycznych: maksymalna różnica 0 J.

## Wynik końcowy

| Metryka | Bez xAppa | My Energy |
|---|---:|---:|
| Energia radia NR [J] | 36 302,1631 | 32 227,3002 |
| Średnia moc w oknie 12 s [W] | 3025,1803 | 2685,6084 |
| Odebrane dane aplikacji [Mbit] | 93,8496 | 93,8896 |
| Odebrane pakiety | 58 656 | 58 681 |
| Niedostarczone pakiety | 744 | 719 |
| Końcowy udział niedostarczonych [%] | 1,252525 | 1,210438 |
| Średnie opóźnienie [ms] | 14,206130 | 14,016153 |
| Średnia przepływność w oknie 12 s [Mbit/s] | 7,820800 | 7,824133 |
| Efektywność energetyczna [bit/J] | 2585,2344 | 2913,3560 |
| Energia na odebrane dane [J/Mbit] | 386,8121 | 343,2468 |

Różnica energii wynosi **4074,8628 J**. Oszczędność liczona jest jako
`100 × (E_bez_xApp − E_MyEnergy) / E_bez_xApp`. Nie wynika z obcięcia czasu
symulacji ani zmniejszenia zadanej liczby pakietów.

Wzrost EE wynosi około 12,69%. Straty zmalały o 0,04209 punktu procentowego,
a średnie opóźnienie o 0,18998 ms. Przy kryterium braku pogorszenia względem
wariantu bez xAppa porównywarka zwraca `improved`. Nie dobierano progów usługi
po obejrzeniu wyników.

Pomocnicza telemetria KPM pokazuje jednocześnie pogorszenie warunków
radiowych: średnia raportowanych SINR spadła z 27,0773 do 25,6213 dB,
a średnia raportowanych wartości p05 z 11,1404 do 6,5486 dB. Ta ostatnia
wartość nie jest percentylem całego zbioru próbek UE. Parser nie potwierdza
pełnej zgodności okna agregatów KPM z oknem energii/UDP; traktujemy je jako
informację pomocniczą, a nie potwierdzenie jakości dla każdego użytkownika.
Konsolidacja nie poprawiła wszystkich wskaźników sieci.

## Dowód działania sterowania

1. **4,118482 s:** kandydat do wyłączenia — komórka 3, cztery UE.
2. **4,118482 s:** polecenia przeniesienia UE 3 i 7 do komórki 4 oraz UE 5 i 8
   do komórki 2.
3. **4,218482 s:** cztery potwierdzenia raportami serving z komórek docelowych.
4. **6,118482 s:** polecenie wyłączenia komórki 3, po potwierdzeniach i czasie
   oczekiwania; ocena wykonywana jest okresowo.
5. **6,2 s:** pierwszy zapis `ESstate=1` w `gnbs.txt`. Energia komórki 3
   w następnych przedziałach wynosi 0 J.
6. Na końcu zapisano wszystkie 12 UE: siedem w komórce 2, pięć w komórce 4,
   zero w wyłączonej komórce 3.

![Energia, moc i transmisja w pełnej parze](../data/experiments/backend-check/20261020-085534/comparison.png)

Linia przerywana oznacza pierwszy odczyt wyłączenia ze stanu gNB. Spadek mocy
około 10 s odpowiada zatrzymaniu generowania UDP w obu wariantach. Wykres
nieodebranych pakietów na początku obejmuje pakiety w drodze; tabela podaje
wynik po opróżnieniu kolejek. Niemal pokrywające się krzywe danych są oczekiwane
przy tak małej różnicy końcowej liczby odebranych pakietów.

## Artefakty i otwarcie w aplikacji

Pełny katalog: `data/experiments/backend-check/20261020-085534`.
Zawiera `report.json`, `summary.json`, `summary.csv`, `verification.json`,
wykresy PNG/PDF, zamrożone źródła z SHA-256, konfigurację i surowe pliki obu
przebiegów. W `exports` są przenośne archiwa ZIP.

- Bez xAppa: `1bbc68d8-6807-444c-83ec-6d2fec847842`.
- My Energy: `3cd24fc5-75c1-4c31-89dc-ef846d5e7257`.

Oba wyniki zaimportowano do historii aplikacji uruchamianej z repozytorium
i z EXE. W **Porównaniu** wybierz po lewej **Bez xApp — 12 s, 12 UE, 3 NR
(RngRun 1)**, po prawej **My Energy — 12 s, 12 UE, 3 NR (RngRun 1)**.

## Granice wniosku i dalsza część pracy

To energia modelu radia NR. Stan wyłączenia ma w tym backendzie zerowy prąd;
wynik nie jest pomiarem kompletnej stacji, LTE, RIC ani komputera. Topologia
obejmuje osobne gNB z antenami dookólnymi, nie sektory jednego masztu.
Nie wykazano kalibracji do urządzenia operatora.

Suma UE raportowanych w KPM po handoverze osiągała 16 przy 12 urządzeniach.
Nie traktujemy jej jako liczby unikalnych UE. Również dodatni wynik agregatów
nie dowodzi zachowania QoS każdego UE. Dla jednego przeniesienia raportowany
SINR po handoverze był niższy niż przewidywany SINR celu; sama heurystyka
progowa nie daje gwarancji jakości. Nie ma powrotnego włączania komórek.

Sterowanie E2 jest asynchroniczne. Przy tym samym RngRun obciążenie hosta może
zmienić dokładną chwilę obsługi polecenia. Do rozdziału badawczego potrzebne są
kolejne sparowane RngRun, różne obciążenia oraz rozrzut różnic energii i QoS.
Wariant 6 komórek / 24 UE jest dostępny w GUI, ale powyższy pomiar dotyczy
wyłącznie wariantu 3 komórki / 12 UE.

Sprawdzenia kodu: pełny pytest — 219 zaliczonych, 5 pominiętych, w tym
przejściowo niedostępny ekran jednego testu Tk. Osobne ponowienie ośmiu testów
widgetów i edycji źródeł zakończyło się sukcesem. Cztery pomijane testy procesów
wymagają POSIX. Pięć kontroli rzeczywistej polityki C w backendzie przeszło.
Przebudowano i otwarto Windows EXE; `git diff --check` nie wykazał błędów.
