#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ENTRY_FILE="$APPLICATIONS_DIR/oran-energy-lab.desktop"

mkdir -p -- "$APPLICATIONS_DIR"
chmod +x "$APP_DIR/run.sh" "$APP_DIR/scripts/linux/launcher.sh" "$APP_DIR/scripts/linux/setup.sh" "$APP_DIR/scripts/linux/export-image.sh"

cat > "$ENTRY_FILE" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Symulator O-RAN
Comment=Lokalne laboratorium symulacji ns-3/FlexRIC/xApp
Exec=/usr/bin/env bash "$APP_DIR/run.sh" --desktop
TryExec=/usr/bin/env
Path=$APP_DIR
Terminal=false
StartupNotify=true
Icon=utilities-terminal
Categories=Development;Science;Education;
DESKTOP
chmod +x "$ENTRY_FILE"

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$ENTRY_FILE"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi

DESKTOP_PATH=""
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_PATH="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [[ -z "$DESKTOP_PATH" || "$DESKTOP_PATH" == "$HOME" ]]; then
  DESKTOP_PATH="$HOME/Desktop"
fi
mkdir -p -- "$DESKTOP_PATH"
cp -- "$ENTRY_FILE" "$DESKTOP_PATH/Symulator O-RAN.desktop"
chmod +x "$DESKTOP_PATH/Symulator O-RAN.desktop"
if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP_PATH/Symulator O-RAN.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "Zainstalowano skrót w menu: $ENTRY_FILE"
echo "Zainstalowano skrót na pulpicie: $DESKTOP_PATH/Symulator O-RAN.desktop"
