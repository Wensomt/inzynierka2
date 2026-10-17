#!/bin/bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/flexric/examples/xApp/c/monitor/xapp_ml_prb.c"
BUILD_DIR="$HOME/flexric/build"

if [ ! -f "$SRC_DIR/xapp_ml_prb.c" ]; then
  echo "[ERROR] Missing source file: $SRC_DIR/xapp_ml_prb.c"
  exit 1
fi

if [ ! -d "$HOME/flexric" ]; then
  echo "[ERROR] Missing FlexRIC directory: $HOME/flexric"
  exit 1
fi

mkdir -p "$(dirname "$TARGET")"
if [ -f "$TARGET" ]; then
  cp "$TARGET" "$TARGET.bak_$(date +%Y%m%d_%H%M%S)"
fi

cp "$SRC_DIR/xapp_ml_prb.c" "$TARGET"

cd "$BUILD_DIR"
cmake .. -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00
make -j"$(nproc)" xapp_ml_prb

echo "[OK] Orange KPM-compatible ML-xApp rebuilt."
echo "[OK] Binary: $BUILD_DIR/examples/xApp/c/monitor/xapp_ml_prb"
