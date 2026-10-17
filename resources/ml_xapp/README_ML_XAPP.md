# Minimalny ML xApp: `xapp_ml_prb`

Ten xApp jest najprostszym wariantem badawczym: subskrybuje KPI KPM v3 przez FlexRIC, uczy lekki model regresji logistycznej online i wypisuje decyzje typu `CANDIDATE_CELL_SLEEP`, `KEEP_ACTIVE_NORMAL_LOAD` albo `KEEP_ACTIVE_HIGH_LOAD`.

Domyślnie działa w trybie advisory/monitoring. Nie wysyła wiadomości RIC Control. To jest celowe: najpierw można porównać decyzje ML z działaniem Orange `xapp_es_with_cell_util`, a dopiero później przenieść decyzję do kontrolera RC.

## Instalacja

```bash
cd ~/oran_ric_taap_repro_20260509/ml_xapp   # albo katalog po rozpakowaniu paczki
./install_ml_xapp.sh
```

## Uruchomienie

W osobnych terminalach muszą działać:

1. `nearRT-RIC`,
2. `python3 gui_trigger.py`,
3. scenariusz ns-3 z włączonym FlexRIC/E2,
4. dopiero potem ML xApp:

```bash
./run_ml_xapp.sh
```

Wyniki są zapisywane do:

```text
~/flexric/build/examples/xApp/c/monitor/ml_xapp_decisions.csv
```

## Parametry

```bash
ML_XAPP_DURATION=180 \
ML_XAPP_PERIOD_MS=1000 \
ML_XAPP_PRB_LOW=20 \
ML_XAPP_PRB_HIGH=70 \
ML_XAPP_P_LOW=0.30 \
ML_XAPP_P_HIGH=0.70 \
ML_XAPP_LR=0.05 \
./run_ml_xapp.sh
```

## Interpretacja naukowa

Model używa słabego etykietowania regułowego: etykieta jest tworzona z progów
DL volume, TB DL i serving SINR, czyli z tych samych pomiarów co cechy.
Nie są to niezależne etykiety prawidłowej decyzji energetycznej. Aktualny kod
nie mierzy zwalidowanego procentowego wykorzystania PRB; nazwa programu
i historyczne aliasy zmiennych `ML_XAPP_PRB_*` nie zmieniają jednostek KPI.
Należy używać nazw `ML_XAPP_LOAD_LOW/HIGH` i ustalić skalę danych dla backendu.

Jest to demonstrator uczenia online imitującego regułę, działający advisory,
bez RIC Control. Nie wykazano przewagi predykcyjnej nad tą regułą ani oszczędności
energii z zachowaniem QoS. Do takiej oceny potrzebne są niezależne dane testowe,
rozdzielenie uczenia i oceny oraz eksperyment z rzeczywistym sterowaniem.
