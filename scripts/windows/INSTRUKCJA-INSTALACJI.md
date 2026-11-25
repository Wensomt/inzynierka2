# Symulator efektywności energetycznej O-RAN

Uruchom plik **O-RAN-Energy-Lab-Setup.pyz** w terminalu poleceniem **py O-RAN-Energy-Lab-Setup.pyz**. Jeśli polecenie **py** jest niedostępne, użyj polecenia **python O-RAN-Energy-Lab-Setup.pyz** albo zainstaluj [Python 3.13 x64 z Tk](https://www.python.org/downloads/windows/).

Przy pierwszym uruchomieniu potrzebne jest połączenie z Internetem. Jeśli Windows poprosi o zmianę uprawnień w celu przygotowania WSL 2, przeczytaj komunikat. Po wymaganym restarcie uruchom ponownie ten sam plik.

Instalator może otworzyć kreator Docker Desktop. Wybierz instalację dla własnego konta i backend WSL 2. Po instalacji uruchom Docker Desktop, zapoznaj się z jego warunkami i poczekaj na uruchomienie silnika Linux. Następnie ponownie otwórz instalator symulatora. Pierwsza kompilacja obrazu może potrwać kilkadziesiąt minut.

Po uruchomieniu aplikacji w zakładce **Start** utwórz sesję z jedną symulacją. W zakładce symulacji wybierz scenariusz **My Energy** i uruchom przebieg. Wyniki pojawią się w zakładce **Wyniki**. Ten sam plik instalatora otwiera aplikację również po zakończeniu instalacji. W razie problemu ścieżka logu pojawi się w oknie instalatora.

Scenariusze C++ zawierają nagłówki GPL 2.0 z oznaczeniem autorstwa. Źródło xApp zawiera nagłówek OAI Public License 1.1. Teksty licencji znajdują się w katalogu **LICENSES**. Przypięte wersje pobieranych źródeł są wymienione w **resources/simulator/sources.json**.
