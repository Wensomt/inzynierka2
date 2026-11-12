# Propozycja pracy inżynierskiej

Proponowany tytuł: **Projekt i implementacja środowiska do oceny efektywności
energetycznej sieci O-RAN z wykorzystaniem symulatora ns-3**.

Tytuł opisuje wykonane narzędzie i nie zakłada z góry, że heurystyka przyniesie
oszczędności w każdym wariancie ruchu.

## Spis treści

1. **Wstęp**
   1. Motywacja: zużycie energii w sieciach dostępowych
   2. Cel i zakres pracy
   3. Organizacja pracy
2. **Podstawy sieci 5G i architektury O-RAN**
   1. UE, gNB i podział funkcji sieci radiowej
   2. nearRT-RIC, interfejs E2 i aplikacje xApp
   3. Raportowanie KPM i sterowanie komórkami
   4. Efektywność energetyczna a jakość transmisji
3. **Środowisko symulacyjne i model pomiaru**
   1. ns-3 i wykorzystany wariant mmWave-LENA-O-RAN
   2. Integracja z FlexRIC i uruchamianie w Dockerze
   3. Topologia, mobilność i model ruchu UDP
   4. Model energii radia i granice jego zastosowania
   5. Definicje metryk, okno pomiarowe i opróżnianie kolejek
4. **Projekt aplikacji laboratoryjnej**
   1. Wymagania funkcjonalne i przyjęte uproszczenia
   2. Architektura i podział odpowiedzialności modułów
   3. Model konfiguracji i wyniku eksperymentu
   4. Cykl życia symulacji i komunikacja z GUI
   5. Organizacja plików oraz odtwarzalność eksperymentów
5. **Implementacja**
   1. Instalacja i przygotowanie środowiska
   2. Uruchamianie RIC, ns-3 i xAppa
   3. Pomiar danych, energii, strat i opóźnienia
   4. Odczyt na żywo, wykresy i archiwizacja
   5. Heurystyka My Energy i warunki podejmowania decyzji
   6. Porównywanie wyników i eksport
6. **Weryfikacja poprawności rozwiązania**
   1. Testy konfiguracji, parsera i obliczeń
   2. Testy procesów, zatrzymywania i sprzątania kontenerów
   3. Spójność danych live z wynikiem końcowym
   4. Wykryte problemy pomiarowe i sposób ich usunięcia
   5. Sprawdzenie interfejsu i instalatora
7. **Eksperymenty i analiza wyników**
   1. Plan eksperymentów i wspólne parametry
   2. Wariant bez xAppa jako punkt odniesienia
   3. Wariant z My Energy dla różnych obciążeń
   4. Energia, EE i jakość transmisji
   5. Decyzje xAppa a zmiany stanu komórek
   6. Powtórzenia dla wielu RngRun i rozrzut wyników
   7. Wpływ parametrów heurystyki i ograniczenia interpretacji
8. **Podsumowanie**
   1. Zakres zrealizowanego celu
   2. Najważniejsze obserwacje i ograniczenia
   3. Możliwości rozwoju

Bibliografia. Załączniki: instrukcja uruchomienia, parametry eksperymentów,
format plików wynikowych, wybrane logi i dostęp do kodu.

## Co trzeba przygotować do rozdziału badawczego

Najpierw ustal zakres pracy z promotorem. Jako pierwszy plan techniczny można
przyjąć trzy poziomy obciążenia, wariant bez xAppa i z My Energy oraz kilka
sparowanych wartości RngRun. Dobierz długość przebiegu tak, aby xApp zdążył
otrzymać raporty i faktycznie podjąć decyzje. Pojedyncze kontrole 0,8 s i 1,5 s
służą sprawdzeniu działania, nie zastępują tego badania.

W każdej parze zachowaj topologię, mobilność, ruch, simTime i drain. Zmieniaj
tylko politykę sterowania. Zapisz wersję obrazu, hashe źródeł, parametry xAppa,
progi QoS i log decyzji. Pokaż także wariant, w którym nie ma oszczędności lub
polityka pogarsza transmisję — to też jest wartościowy wynik pomiarowy.

Nie dokładałbym rozdziału o ML, panelu webowym ani dawnych kampaniach,
jeżeli nie są częścią wybranego, działającego zakresu pracy.
