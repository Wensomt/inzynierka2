#!/usr/bin/env bash
# Pierwsze uruchomienie po skopiowaniu pakietu na Linuxa.
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$APP_DIR/scripts/linux/setup.sh" --launch "$@"
