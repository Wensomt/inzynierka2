#!/usr/bin/env bash
set -Eeuo pipefail
FLEXRIC_DIR="${FLEXRIC_DIR:-$HOME/flexric}"
TARGET="$FLEXRIC_DIR/examples/xApp/c/monitor/xapp_ml_prb.c"
[ -f "$TARGET" ] || { echo "[ERROR] Missing: $TARGET" >&2; exit 1; }
sed -i 's/\be2_node_arr_t\b/e2_node_arr_xapp_t/g; s/\bfree_e2_node_arr\b/free_e2_node_arr_xapp/g' "$TARGET"
echo "[OK] Patched FlexRIC API type names in $TARGET"
cd "$FLEXRIC_DIR/build"
cmake .. -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00
make -j"$(nproc)" xapp_ml_prb
