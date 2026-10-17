#!/usr/bin/env bash
set -Eeuo pipefail
FLEXRIC_DIR="${FLEXRIC_DIR:-$HOME/flexric}"
cd "$FLEXRIC_DIR/build/examples/xApp/c/monitor"

export ML_XAPP_DURATION="${ML_XAPP_DURATION:-180}"
export ML_XAPP_PERIOD_MS="${ML_XAPP_PERIOD_MS:-1000}"
export ML_XAPP_PRB_LOW="${ML_XAPP_PRB_LOW:-20}"
export ML_XAPP_PRB_HIGH="${ML_XAPP_PRB_HIGH:-70}"
export ML_XAPP_P_LOW="${ML_XAPP_P_LOW:-0.30}"
export ML_XAPP_P_HIGH="${ML_XAPP_P_HIGH:-0.70}"
export ML_XAPP_LR="${ML_XAPP_LR:-0.05}"
export ML_XAPP_PRINT_KPI_NAMES="${ML_XAPP_PRINT_KPI_NAMES:-1}"

cat <<'WARN'
[WARN] Do not start this xApp before ns-3 E2 nodes are registered in nearRT-RIC.
[WARN] In the RIC terminal you should already see E2 SETUP-REQUEST lines and Registered E2 Nodes > 0.
[WARN] Starting xApp too early may crash this FlexRIC branch with len_e2_nodes_conn > 0 assertion.
WARN

if [ "${ML_XAPP_ASSUME_READY:-0}" != "1" ]; then
  read -r -p "Type YES to confirm E2 nodes are already connected: " ans
  if [ "$ans" != "YES" ]; then
    echo "[ABORT] xApp not started. Start nearRT-RIC and ns-3 scenario first."
    exit 2
  fi
fi

./xapp_ml_prb
