#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLEXRIC_DIR="${FLEXRIC_DIR:-$HOME/flexric}"
TARGET_DIR="$FLEXRIC_DIR/examples/xApp/c/monitor"
CMAKE_FILE="$TARGET_DIR/CMakeLists.txt"

[ -d "$TARGET_DIR" ] || { echo "[ERROR] FlexRIC monitor xApp directory not found: $TARGET_DIR" >&2; exit 1; }
[ -f "$CMAKE_FILE" ] || { echo "[ERROR] CMakeLists.txt not found: $CMAKE_FILE" >&2; exit 1; }

cp "$SCRIPT_DIR/xapp_ml_prb.c" "$TARGET_DIR/xapp_ml_prb.c"

if ! grep -q "xapp_ml_prb" "$CMAKE_FILE"; then
  cat >> "$CMAKE_FILE" <<'CMAKE'

add_executable(xapp_ml_prb xapp_ml_prb.c ../../../../src/util/alg_ds/alg/defer.c )
target_link_libraries(xapp_ml_prb PUBLIC e42_xapp -pthread -lsctp -ldl -lm )
CMAKE
fi

mkdir -p "$FLEXRIC_DIR/build"
cd "$FLEXRIC_DIR/build"
cmake .. -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00
make -j"$(nproc)" xapp_ml_prb

echo "[OK] ML xApp built: $FLEXRIC_DIR/build/examples/xApp/c/monitor/xapp_ml_prb"
