# Brief dla botów Hermes i tablicy Kanban

Stan rozpoznania na 6 listopada 2026. Katalog roboczy to `C:\Users\Dawid\Documents\inzynierka 2`. Ścieżki względne poniżej odnoszą się do tego katalogu. Brief służy do organizacji pracy w Hermesie. Format źródłowy samej pracy jest do decyzji autora.

Używaj oznaczeń **potwierdzone**, **robocze** i **do potwierdzenia**. Daty z raportów oznaczają wykonane prace techniczne. Daty z kalendarza uczelni są warunkowe, dopóki autor nie wskaże właściwego semestru i terminu w APD.

## 1. Temat i dane studiów

- **Roboczy tytuł PL** „Projekt i implementacja środowiska do oceny efektywności energetycznej sieci O-RAN z wykorzystaniem symulatora ns-3”. Źródło `docs/SPIS_TRESCI.md`.
- **Typ** indywidualna praca dyplomowa inżynierska, studia I stopnia. Lokalna formatka zawiera pola wspólne dla prac inżynierskich i magisterskich.
- **Uczelnia i wydział według formatki** Politechnika Bydgoska im. Jana i Jędrzeja Śniadeckich, Wydział Telekomunikacji, Informatyki i Elektrotechniki (WTIiE). Formalny temat PL/EN, opis z APD, kierunek, tryb, semestr oraz ewentualna specjalność są do potwierdzenia. Źródło `praca_inzynierska/front.tex` i [regulamin WTIiE](https://wtie.pbs.edu.pl/pl/media/62831).

## 2. Problem, cel i zakres

Problem inżynierski polega na uzyskaniu porównywalnego pomiaru energii i jakości transmisji przy zmianie polityki sterowania komórkami NR. Celem jest zaprojektowanie i wykonanie lokalnego środowiska do konfiguracji, uruchamiania, obserwacji, archiwizacji i porównywania eksperymentów 5G/O-RAN w ns-3 oraz demonstracja oceny xAppa My Energy.

- **Pytanie robocze** W jakich warunkach My Energy zmniejsza modelowaną energię radia NR i poprawia EE, zachowując ustalone kryteria QoS dla agregatów UDP? Jak zmienia się efekt przy różnych obciążeniach i `RngRun`?
- **Hipoteza robocza** Przy obciążeniu pozwalającym skonsolidować ruch My Energy może zmniejszyć energię NR i zwiększyć EE przy spełnieniu uprzednio ustalonych kryteriów przepływności, opóźnienia i strat. Do decyzji promotora, czy formalizować hipotezę i jakie kryteria przyjąć.
- **Zakres** Python/Tkinter jako aplikacja desktopowa, Docker `wcnc_tap`, ns-3 z mmWave-LENA-O-RAN, nearRT-RIC/FlexRIC, E2/KPM, scenariusz C++ i xApp C. Model EN-DC ze wspólną kotwicą LTE. Podstawowa topologia to 12 symulowanych UE i 3 osobne gNB z antenami dookólnymi. GUI udostępnia też wariant 24 UE i 6 komórek NR.
- **Mechanizm My Energy** Ocena obciążenia i świeżości KPM, dobór celów według SINR i dostępnej pojemności, handovery, potwierdzenie w raportach serving, a następnie polecenie wyłączenia. Profil `energy` w pełnej parze miał próg SINR celu 5 dB, do 4 UE w wyłączanej komórce oraz okres oceny i potwierdzania po 1 s ns-3. Istnieje też profil zachowawczy.
- **Granice zakresu** Pomiary dotyczą modelu radia NR z zerowym prądem w stanie wyłączenia. Zakres nie obejmuje pomiarów fizycznej stacji, LTE, RIC ani hosta, kalibracji u operatora, modelu sektorów jednego masztu, gwarancji QoS per UE, automatycznego ponownego włączania komórek, panelu webowego ani historycznego demonstratora ML. SINR/PRB z KPM mają charakter pomocniczy. Sterowanie E2 jest asynchroniczne i może zależeć od obciążenia hosta.

Źródła lokalne `README.md`, `docs/JAK_DZIALA.md`, `docs/MY_ENERGY.md`, `docs/METRYKI.md`.

## 3. Metody i stan badań

- **Metoda** Dla każdego poziomu obciążenia i `RngRun` porównać wariant bez xAppa z My Energy. W parze utrzymać te same topologię, mobilność, ruch, radio, `RngRun`, `simTime`, okno pomiaru, drain i wersję backendu. Zmieniać politykę sterowania. Zachować obraz lub jego identyfikator, konfigurację, SHA-256 zamrożonych źródeł, CSV i log decyzji. Porównywać ukończone przebiegi o wspólnym oknie bez limitu czasu rzeczywistego. Wariantów przerwanych i nieporównywalnych nie wliczać do efektu.
- **Metryki** Energia modelowanego NR w J, średnia moc w W, dostarczone bity podzielone przez energię w bit/J, J/Mbit, goodput, średnie opóźnienie i odsetek pakietów niedostarczonych do końca okna. Ruch downlink UDP mierzą `UdpClient`, `PacketSink` i `SeqTsHeader`. Energia i dane obejmują `0..simTime`, łącznie z drain. Wskaźnik live obejmuje pakiety w drodze. Historyczne wyniki z błędnym QoS FlowMonitor wymagają powtórzenia.
- **Wykonano** Kontrole 0,8 s i 1,5 s z 18.10.2026 sprawdziły pomiar, przepływ danych i integrację. Pełną kontrolę sterowania z 20.10.2026 wykonano dla 12 s, 12 UE, 3 NR, `RngRun=1`, ruchu 0,8 Mbit/s na UE i 2 s drain. Oba przebiegi ukończono. Użyto ponownie sprawdzonego przebiegu odniesienia. Udokumentowano cztery potwierdzone handovery, polecenie wyłączenia komórki 3 i pierwszy odczyt wyłączenia w 6,2 s.

| Metryka w parze 12 s | Bez xAppa | My Energy |
| --- | ---: | ---: |
| Energia NR [J] | 36 302,1631 | 32 227,3002 |
| EE [bit/J] | 2585,2344 | 2913,3560 |
| Odebrane dane [Mbit] | 93,8496 | 93,8896 |
| Średnie opóźnienie [ms] | 14,206130 | 14,016153 |
| Niedostarczone pakiety [%] | 1,252525 | 1,210438 |
| Goodput [Mbit/s] | 7,820800 | 7,824133 |

W tej jednej parze energia spadła o **11,2248%**, a EE wzrosła o ok. **12,69%**. Końcowe agregaty UDP nie pogorszyły się, zaś pomocnicze agregaty SINR spadły. Oba warianty wysłały po 59 400 pakietów. Weryfikacja przed sterowaniem wykazała 39 wspólnych próbek energii z różnicą 0 J. Pojedyncza para dokumentuje działanie w określonych warunkach i nie daje podstaw do uogólnienia oszczędności. Raport dokumentuje również testy aplikacji 219 zaliczonych i 5 pominiętych, osobne kontrole polityki C oraz otwarcie zbudowanego EXE. Bieżący audyt porównywał zapisy na dysku, bez ponownego uruchomienia testów lub symulacji.

- **Plan do uzgodnienia** Trzy poziomy ruchu, wiele sparowanych `RngRun`, wspólne okno pozwalające na decyzje xAppa, kryteria QoS ustalone przed pomiarem, raport różnic w każdej parze, rozrzutu i niepewności. Uwzględnić wyniki zerowe i niekorzystne. Ustalić liczbę powtórzeń, progi i ewentualny wariant 24 UE / 6 NR.
- **Dostęp do danych** Materiał empiryczny to lokalne dane wygenerowane przez symulator, z modelowanymi UE i ruchem UDP. W przejrzanych materiałach badawczych brak wskazania danych klientów operatora, uczestników badań lub płatnego zbioru. Dostęp techniczny do plików lokalnych i prawo do udostępnienia ich zewnętrznym usługom to odrębne kwestie. Zasady przekazywania są w sekcji 7.

Źródła `docs/WYNIKI_MY_ENERGY_2026-10-20.md`, `docs/PRZEGLAD_2026-10-18.md`, `docs/METRYKI.md` oraz `data/experiments/backend-check/20261020-085534/{report.json,summary.csv,verification.json}`.

## 4. Wytyczne uczelni, promotora i AI

- [Regulamin dyplomowania WTIiE od roku akademickiego 2026/2027](https://wtie.pbs.edu.pl/pl/media/62831). § 2 dotyczy zakresu pracy inżynierskiej i wkładu własnego, a § 5 języka, metryki, APD, dokumentacji technicznej i kontroli JSA. [Procedura i załączniki WTIiE](https://wtie.pbs.edu.pl/pl/student/dyplomowanie/zasady-i-procedury-postepowania) zawierają wzory strony tytułowej i metryki oraz harmonogram wydziałowy.
- [Regulamin studiów PBŚ obowiązujący od 1.10.2026](https://pbs.edu.pl/pl/media/57011), zwłaszcza § 34–36. [Materiały edytorskie WTIiE](https://wtie.pbs.edu.pl/pl/student/dyplomowanie/druki-do-pobrania) zawierają formatkę i przykładowy PDF. Lokalny ZIP formatki ma datę modyfikacji 23.10.2026. To data lokalnego pliku, a zgodność zawartości z aktualnymi załącznikami wydziału wymaga porównania.
- **Promotor i AI** W sprawdzonych materiałach nie ustalono indywidualnych wytycznych promotora ani osobnej reguły określającej dozwolony udział AI i formę ujawnienia. Wymagania samodzielności, oryginalności i widocznego wkładu własnego wynikają z regulaminów. Roboczo boty mają wspierać organizację zadań, indeksowanie materiałów i zgłaszanie uwag. Redakcja, generowanie treści do pracy, pomoc w analizie i zakres zmian kodu do pracy wymagają ustalenia granic z autorem i sprawdzenia zasad promotora. Prowadzić wewnętrzny rejestr wsparcia AI z datą, narzędziem, celem, zakresem, wykorzystanym rezultatem i weryfikacją przez autora. Docelową formę i miejsce ujawnienia ustalić z promotorem.

## 5. Język, objętość, struktura i cytowania

- **Język** Polski zgodnie z zasadą ogólną WTIiE i językiem lokalnego konspektu. Tytuły PL/EN według formatki. APD wymaga streszczeń i słów kluczowych po polsku i angielsku. Metryka w pracy następuje bezpośrednio po stronie tytułowej i opiera się na informacjach z APD. Źródło [§ 5 regulaminu WTIiE](https://wtie.pbs.edu.pl/pl/media/62831).
- **Objętość** Przykładowy szablon WTIiE wskazuje ok. 50 stron jako wielkość typową. W druku wartość maksimum ma uszkodzony znak, więc limit i ewentualny cel dla tej pracy są do potwierdzenia. Źródło [szablon WTIiE, s. 6](https://wtie.pbs.edu.pl/pl/media/25949#page=6).
- **Struktura robocza** Po stronie tytułowej, metryce i spisie treści następują Wstęp, Podstawy i przegląd rozwiązań, Projekt i implementacja symulatora, Testy i badania symulacyjne, Podsumowanie i wnioski, następnie bibliografia, spisy rysunków i tabel oraz załączniki. To układ rzeczywiście zapisany w `praca_inzynierska/`. Osobny `docs/SPIS_TRESCI.md` zawiera ośmiorozdziałową propozycję przydatną do rozpisania podzadań. Akceptacja konspektu do potwierdzenia.
- **Cytowania i styl** Formalny język techniczny, numeryczne odwołania `[n]`, numeracja i odwołania w tekście do rysunków, tabel i wzorów. Formatka ma BibTeX `plain` z `citeref`; `MSc.bib` jest do uzupełnienia. Szablon pokazuje datę dostępu dla WWW. Źródła dobierać i weryfikować na podstawie dokumentów pierwotnych. [Wskazówki edytorskie i przykład bibliografii WTIiE](https://wtie.pbs.edu.pl/pl/media/25949).

## 6. Terminy i etapy

- **Daty osobiste** Brak potwierdzonej daty i godziny oddania, wcześniejszych etapów u promotora i daty obrony w lokalnych materiałach. Dla harmonogramu używać `Europe/Warsaw`. Godzinę wpisywać po potwierdzeniu.
- **Termin warunkowy** § 35 regulaminu studiów wiąże złożenie z akceptacją finalnej wersji w APD przez promotora, najpóźniej do końca właściwej sesji poprawkowej. Jeśli dotyczy Cię VII, ostatni semestr I stopnia zimą 2026/2027, ogólna sesja poprawkowa kończy się **21.01.2027**. Jeśli studia kończą się latem 2026/2027, ogólna letnia sesja poprawkowa kończy się **07.09.2027**. To daty z ogólnego kalendarza, które trzeba przypisać do właściwego semestru i skonfrontować z APD, trybem i wcześniejszym terminem promotora. [Regulamin studiów PBŚ § 35](https://pbs.edu.pl/pl/media/57011) · [Zarządzenie Z.106.2025.2026 z 30.06.2026, s. 1–3](https://pbs.edu.pl/pl/media/60277).
- **Kolejność zależności** Potwierdzenie APD, wytycznych i zasad AI → akceptacja konspektu i protokołu badań → wykonanie i zamrożenie serii → analiza → tekst autora → korekta → akceptacja promotora i finalizacja APD/JSA → obrona. Na tablicy oddzielić termin przekazania wersji promotorowi od terminu akceptacji w APD.

## 7. Mapa materiałów i zasady dostępu

| Materiał | Gdzie szukać |
| --- | --- |
| Formatka, konspekt i szkielety rozdziałów | `praca_inzynierska/`, `praca_inzynierska_WTIiE_spis_tresci.zip` |
| Opis techniczny i źródła do rozdziałów | `README.md`, `docs/JAK_DZIALA.md`, `docs/INSTRUKCJA.md`, `docs/METRYKI.md`, `docs/MY_ENERGY.md` |
| Kod desktopowy | `app/`, w szczególności `gui/`, `simulation/`, `monitoring/`, `data/`, `comparison/` |
| Wzorcowy scenariusz i xApp | `resources/my_energy_scenario_optimized.cc`, `resources/xapp_my_energy_optimized.c`, `resources/patch_kpm_clock.py` |
| Edytowalne źródła z GUI | `data/my_energy/`, w wersji EXE `%LOCALAPPDATA%\O-RAN Energy Lab\my_energy` |
| Pełna para i jej zamrożone źródła | `data/experiments/backend-check/20261020-085534/`, w tym `sources/`, `simulations/`, `exports/`, `summary.csv`, `comparison.png`, `comparison.pdf` |
| Kontrole i testy | `scripts/check_simulator.py`, `scripts/check_xapp_policy.py`, `scripts/summarize_experiment.py`, `tests/`, `docs/PRZEGLAD_2026-10-18.md` |
| Źródła zależności symulatora | `.simulator-src/`, w tym `mmwave-LENA-oran/` i `e2sim-kpmv3/` |
| Dawny prototyp webowy | `C:\Users\Dawid\Documents\Inżynierka\oran_energy_lab_v03`, w tym archiwalna `docs/thesis-methodology.md` |

Lokalny kod backendu mmWave-LENA-O-RAN i `contrib/oran-interface` zawiera tekst GPL v2. Przed przekazaniem kodu lub obrazu zweryfikować nagłówki i warunki dla konkretnych składników. W korzeniu własnego repozytorium nie znaleziono pliku licencji udzielającego zgody na redystrybucję projektu. Ustalić zasady publikacji z autorem i uczelnią. Regulamin opisuje też pierwszeństwo publikacji pracy przez uczelnię. [Regulamin WTIiE, § 2](https://wtie.pbs.edu.pl/pl/media/62831).

Do Hermes wystarczy brief, katalog źródeł i zweryfikowane agregaty. Jeśli pojawią się korespondencja z promotorem, dane z APD, nieopublikowany tekst, logi z identyfikatorami środowiska lub materiały objęte ograniczeniem dostępu, karta ma podać najpierw rodzaj materiału, właściciela, miejsce i ograniczenie, bez treści. Surowe pliki, konfiguracje, archiwa i kod przekazywać po sprawdzeniu zakresu dostępu, licencji i zawartości. Jeśli bot działa poza tym komputerem, lokalna ścieżka służy jako wskazówka dla autora.

## 8. Stan tekstu, kodu i zakres redakcji

W `praca_inzynierska/` są nagłówki pięciu rozdziałów, formatka, pola strony tytułowej i metryki oraz pusty szkielet bibliografii. Rozdziały i pełna bibliografia pozostają do napisania. Dokumentacja techniczna i wyniki istnieją osobno. Kod roboczy jest na `codex/review-simplify-lab`, z punktem odniesienia `d6b207f` z 17.10.2026 i licznymi lokalnymi zmianami poza commitem. Przy opisie pomiaru pierwszeństwo mają źródła zamrożone w konkretnym runie. Dawny prototyp webowy stanowi materiał historyczny, a jego metodyki nie należy kopiować do obecnego projektu bez uzgodnienia.

Zakres redakcji do ustalenia przez autora. Roboczo prowadzić przegląd i uwagi na kartach. Każda propozycja zmiany tez, danych, kodu, wniosków lub sekcji pracy ma wskazywać podstawę i trafiać do decyzji autora. Wyniki, jednostki i źródła weryfikować przed przeniesieniem do pracy.

## 9. Formaty i narzędzia

- **Źródło pracy** Decyzja otwarta między LaTeX, DOCX i Markdown. Lokalny szkielet LaTeX startuje z `praca_inzynierska/main.tex`, wczytuje `MSc.tex`, klasę `memoir` 12 pt A4, `style.tex`, `front.tex`, pliki rozdziałów, `MSc.bib`, `back.tex` i załącznik. Markdown tego briefu dotyczy wyłącznie przekazania informacji botom.
- **Oddanie** Po akceptacji promotora do APD trafia PDF bez blokady edycji, maks. 15 MB. Załączniki, jeśli występują, trafiają w osobnym archiwum ZIP/RAR/7Z/TGZ do 60 MB. Dla projektu lub programu regulamin wymaga też dokumentacji technicznej w APD. Zawartość i sposób pakowania dokumentacji ustalić z promotorem. [Regulamin WTIiE, § 5 ust. 8–10](https://wtie.pbs.edu.pl/pl/media/62831).
- **Narzędzia wykryte** Python 3.13, repozytoryjna `.venv`, Git, WSL, klient Docker i Pandoc. Komendy `pdflatex`, `latexmk` i `bibtex` nie były widoczne w `PATH`. Działanie kompilacji pracy i aktualny stan silnika Docker nie były sprawdzane. W projekcie są skrypty Windows/Linux, a pakiet Windows znajduje się w `dist/Symulator O-RAN/`. Wydział udostępnia także formatkę na Overleaf. Wybór usługi i przekazanie do niej materiałów są do decyzji autora. Każda instalacja wymaga odrębnej zgody autora. [Wymogi edytorskie i formatki WTIiE](https://wtie.pbs.edu.pl/pl/student/dyplomowanie/wymogi-edytorskie-prac-dyplomowych).

## 10. Współpraca i początkowe karty

Proponowane kolumny `Do potwierdzenia → Do zrobienia → W toku → Do akceptacji → Gotowe`. W karcie umieszczaj jeden rezultat, zależności, ścieżkę do dowodu, kryterium akceptacji i status ustaleń. W sprawach wymagających decyzji właścicielem jest autor. Preferencje dotyczące trybu korekt, akceptowania zmian i zakresu pracy poszczególnych botów są do ustalenia.

| Karta | Stan początkowy | Kryterium zamknięcia |
| --- | --- | --- |
| Ustalenia APD i promotora | Do potwierdzenia | Potwierdzone temat PL/EN, kierunek, tryb, semestr i terminy, w tym godzina, jeśli podana |
| Zasady AI i zakres redakcji | Do potwierdzenia | Zapisany dozwolony udział AI, forma ujawnienia, zakres korekt i dostęp do materiałów |
| Formatka, konspekt i oddanie | Do potwierdzenia | Uzgodniony układ, objętość, źródłowy format edycji i pakiet APD |
| Inwentaryzacja pilota | Gotowe | Para 12 s wskazana przez datę, ścieżki, konfigurację, metryki i ograniczenia |
| Protokół serii porównawczej | Do zrobienia | Uzgodnione poziomy obciążenia, `RngRun`, liczba par, progi QoS i sposób raportowania |
| Eksperymenty i analiza | Do zrobienia | Ukończone porównywalne pary, zachowane pliki wejściowe i wyniki oraz opis niepewności |
| Źródła, tekst i finalizacja | Do zrobienia | Zweryfikowana bibliografia i dane, tekst zaakceptowany przez autora i promotora, pakiet zgodny z APD |

Na etapie organizacji wystarczy rola „autor”. Imię, nazwisko, numer albumu i dane promotora uzupełnia autor lokalnie dopiero w stronie tytułowej, metryce i odpowiednich polach APD. W Kanbanie nie przechowywać haseł, tokenów, PESEL ani innych zbędnych danych.
