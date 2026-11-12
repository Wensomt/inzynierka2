# Metryki, energia i QoS

Wynik opisuje **modelowaną energię radiowej części NR**. Nie jest pomiarem
poboru komputera, całej stacji bazowej ani całej sieci 5G. Zakres zapisuje
`result.metadata.energy_scope`. Brak danych oznacza `N/A`, a nie zero.

## Wspólne okno i jednostki

W scenariuszu `optimized` energia E i dostarczone dane D są liczone
w przedziale **0..simTime**, łącznie z rozruchem i opróżnianiem kolejek.
Nie wycinamy automatycznie okresu rozgrzewania. D to suma
`PacketSink::GetTotalRx()` po UE pomnożona przez 8: ładunek UDP, włącznie
z aplikacyjnym SeqTsHeader klienta ns-3, bez nagłówków IP/UDP/MAC.
Mbit oznacza 10^6 bitów.

| Metryka | Jednostka | Definicja |
|---|---:|---|
| EE | bit/J | D / E; nieokreślona przy E=0 |
| Energia na dane | J/Mbit | E / (D / 10^6); nieokreślona przy D=0 |
| Energia całkowita | J | suma aktualnych liczników modeli energii NR |
| Średnia moc | W | E / T |
| Średnia przepływność użyteczna | Mbit/s | D / (10^6 × T) |
| Dostarczone dane | Mbit | D / 10^6 |
| Średnie opóźnienie | ms | 1000 × suma opóźnień / liczba odebranych pakietów |
| Końcowy packet loss | % | 100 × (TxPackets − RxPackets) / TxPackets po drain |

Opóźnienie jest średnią ważoną pakietami, wyłącznie dla odebranych pakietów.
Bez odbioru pozostaje nieokreślone. Bez wysłanych pakietów nieokreślony jest
packet loss. D=0 przy E>0 daje EE=0 i nieokreślone J/Mbit — jest to wynik złej
transmisji, nie awaria parsera. Wykres przepływności pokazuje próbki interwałowe;
karta średniej pokazuje D/T, a nie ostatnią próbkę.

## QoS i zakończenie ruchu

`TrafficStart_s` domyślnie wynosi 0,1 s. Klienci kończą w `simTime − drain`,
a odbiorniki pozostają czynne do końca. `DrainTime_s=0` wybiera
`min(2 s, 0,2 × simTime)`; dodatnia wartość jest używana dokładnie. Początek
ruchu i drain muszą pozostawić dodatni czas generowania. Przy przeciążeniu
należy sprawdzić wrażliwość wyniku na wydłużenie drain — wartość domyślna nie
dowodzi opróżnienia wszystkich kolejek.

Nadajnik UdpClient dostarcza liczbę wysłanych bajtów, dzieloną przez stały
rozmiar pakietu. Odbiornik PacketSink zlicza otrzymane pakiety, a z SeqTsHeader
odczytujemy czas wysłania. Sumujemy różnice `Simulator::Now() − timestamp`.
Dane i QoS dotyczą więc tych samych downlinków UDP do UE.
`qos_source=udp_application_seq_ts` identyfikuje tę metodę.

Live pokazuje `100 × (Tx − Rx) / Tx`: udział pakietów **jeszcze niedostarczonych**,
w tym pakietów w drodze. Ten wskaźnik może maleć podczas drain. Dopiero na końcu
jest to odsetek niedostarczonych do zadeklarowanego końca pomiaru
(`packet_loss_definition=not_received_by_measurement_end`). Nie jest to
wyłącznie radiowy packet loss.

W dostarczonym backendzie `mc-ue-pdcp.cc` wywołuje `RemoveAllByteTags()`.
Dlatego poprzedni FlowMonitor nie rejestrował odbioru, choć PacketSink odbierał
dane. Aktualny scenariusz nie używa FlowMonitor i nie tworzy `flow-monitor.xml`.
Historyczne wyniki z dodatnimi danymi UDP i `rx_packets=0` mają odrzucony pomiar
strat i opóźnienia. Nie należy używać ich w badaniu QoS.

## Ocena EE razem z QoS

Pierwsza symulacja jest odniesieniem. W Porównaniu można podać maksymalne
średnie opóźnienie, maksymalny packet loss i minimalną przepływność. Puste pole
wymaga braku pogorszenia względem odniesienia. Nie ma ukrytych uniwersalnych
progów QoS. Progi wpisane w widoku dotyczą bieżącego porównania; w pracy trzeba
zapisać je razem z wynikami.

Korzystna ocena wymaga wzrostu EE i spełnienia kryteriów QoS. Osobno podajemy,
czy spadła energia całkowita. Przebiegi częściowe, z restartami xAppa, różnymi
warunkami, oknami lub zakresem energii nie otrzymują takiej oceny. Tabele nadal
pozwalają oglądać różne konfiguracje; różnica nie jest wtedy izolowanym efektem
algorytmu. Zmianę SINR podajemy w dB, a packet loss w punktach procentowych.

Agregaty nie potwierdzają QoS każdego UE ani opóźnienia p95/p99. Do takich tez
potrzebne są rozkłady i wyniki per UE/przepływ oraz wymagania konkretnej usługi.
Bieżący scenariusz zapisuje agregaty sieciowe, nie wyniki per UE/przepływ.
Wskaźnik UE w raportach KPM nie oznacza liczby unikalnych użytkowników.
Po handoverze raporty komórek mogą jeszcze obejmować wcześniejsze konteksty;
w kontroli 12 UE suma raportów osiągała 16. Liczbę urządzeń określa `N_Ues`,
a ich zapisane przypisania pokazuje `ue_position.txt`. Potwierdzenia handoverów
i wyłączenie komórki sprawdzamy osobno w CSV decyzji i `gnbs.txt`.
Liczba UE i SINR w xAppie są warunkami ostrożnego sterowania, nie dowodem
gwarancji QoS.

## Pochodzenie i porównywalność

Parser zachowuje źródła metryk, definicje i metadane okna. E, D i ilorazy wybiera
jako spójny zestaw z jednego artefaktu. Nie łączy energii po rozgrzewaniu
z danymi całego przebiegu. Niezgodność źródeł QoS blokuje pozytywną wspólną
ocenę. Historyczne `SOURCE_LOCAL_DISCRETE_SIMULATOR` są oznaczane jako wyniki
uproszczonego modelu lokalnego, nie aktualnego ns-3.

Badanie powinno używać tego samego obrazu/źródeł, topologii, ruchu, okna,
drain i sparowanych RngRun. Powtórzenia dla kolejnych RngRun pozwalają oszacować
rozrzut: należy podać liczebność, wyniki par i przedziały ufności różnic.
Pojedyncza para nie jest testem istotności. Organizację generatorów opisuje
[podręcznik ns-3](https://www.nsnam.org/docs/manual/html/random-variables.html).

Definicja EE jako ruchu na energię odpowiada kierunkowi
[ETSI ES 203 228, v1.4.1](https://www.etsi.org/deliver/etsi_es/203200_203299/203228/01.04.01_60/es_203228v010401p.pdf).
Aplikacja nie realizuje pełnej procedury pomiarowej ani oceny zgodności z tą
normą. Zakres weryfikacji opisuje [przegląd](PRZEGLAD_2026-10-18.md).
