#!/usr/bin/env bash
# Tworzy przenośne archiwum lokalnego obrazu symulatora i jego sumę SHA-256.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

FORCE=0
OUTPUT=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1 ;;
        --output)
            shift
            [[ "$#" -gt 0 ]] || { printf '%s\n' 'Brak ścieżki po --output.' >&2; exit 2; }
            OUTPUT="$1"
            ;;
        --help|-h)
            cat <<'EOF'
Użycie: export-image.sh [--output ścieżka] [--force]
Domyślnie zapisuje resources/wcnc_tap.tar oraz .sha256.
Nie nadpisuje istniejącego pliku bez --force.
EOF
            exit 0
            ;;
        *) printf 'Nieznana opcja: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

python_bin="$(orlab_python)" || { printf '%s\n' 'Brak interpretera Python do odczytu config.json.' >&2; exit 1; }
image_name="$(orlab_image_name "$python_bin")"
if [[ -z "$OUTPUT" ]]; then
    OUTPUT="$ORAN_LAB_ROOT/resources/wcnc_tap.tar"
fi
OUTPUT="$(orlab_resolve_project_path "$OUTPUT")"
CHECKSUM_FILE="${OUTPUT}.sha256"

command -v docker >/dev/null 2>&1 || { printf '%s\n' 'Brak polecenia docker.' >&2; exit 1; }
docker image inspect "$image_name" >/dev/null 2>&1 || {
    printf 'Brak lokalnego obrazu: %s\n' "$image_name" >&2
    exit 1
}
if [[ -e "$OUTPUT" && "$FORCE" -ne 1 ]]; then
    printf 'Plik już istnieje: %s\nUżyj --force tylko gdy świadomie chcesz go zastąpić.\n' "$OUTPUT" >&2
    exit 1
fi

mkdir -p -- "$(dirname -- "$OUTPUT")"
TEMPORARY_OUTPUT="${OUTPUT}.tmp.$$"
trap 'rm -f -- "$TEMPORARY_OUTPUT" "${TEMPORARY_OUTPUT}.sha256"' EXIT

printf 'Zapisywanie obrazu %s do %s\n' "$image_name" "$OUTPUT"
docker save --output "$TEMPORARY_OUTPUT" "$image_name"
sha256sum "$TEMPORARY_OUTPUT" > "${TEMPORARY_OUTPUT}.sha256"
mv -f -- "$TEMPORARY_OUTPUT" "$OUTPUT"
mv -f -- "${TEMPORARY_OUTPUT}.sha256" "$CHECKSUM_FILE"
trap - EXIT

printf 'Gotowe:\n  %s\n  %s\n' "$OUTPUT" "$CHECKSUM_FILE"
