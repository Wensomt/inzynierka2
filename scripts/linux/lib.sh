#!/usr/bin/env bash
# Wspólne funkcje dla uruchamiania O-RAN Energy Lab na Linuxie.
# Ten plik jest ładowany przez skrypty; nie uruchamiaj go bezpośrednio.

ORAN_LAB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"

orlab_die() {
    printf 'Błąd: %s\n' "$*" >&2
    return 1
}

orlab_launcher_settings_path() {
    printf '%s/launcher.env\n' "$(dirname -- "$(orlab_config_path)")"
}

orlab_saved_python() {
    local settings
    settings="$(orlab_launcher_settings_path)"
    [[ -r "$settings" ]] || return 1
    awk '
        /^ORAN_LAB_PYTHON=/ {
            sub(/^ORAN_LAB_PYTHON=/, "")
            sub(/\r$/, "")
            print
            exit
        }
    ' "$settings"
}

orlab_python() {
    local requested="${ORAN_LAB_PYTHON:-}"
    if [[ -z "$requested" ]]; then
        requested="$(orlab_saved_python 2>/dev/null || true)"
    fi
    requested="${requested:-python3}"
    if [[ "$requested" == */* ]]; then
        [[ -x "$requested" ]] || return 1
        printf '%s\n' "$requested"
        return 0
    fi
    command -v "$requested" 2>/dev/null
}

orlab_save_python_selection() {
    local python_bin="$1" settings temporary
    settings="$(orlab_launcher_settings_path)"
    mkdir -p -- "$(dirname -- "$settings")"
    temporary="$(mktemp "${settings}.XXXXXX")"
    chmod 600 "$temporary"
    printf 'ORAN_LAB_PYTHON=%s\n' "$python_bin" > "$temporary"
    mv -f -- "$temporary" "$settings"
}

orlab_user_home() {
    local target_user="$1" value=""
    if command -v getent >/dev/null 2>&1; then
        value="$(getent passwd "$target_user" | awk -F: 'NR == 1 { print $6 }')"
    fi
    if [[ -z "$value" && "$target_user" == "${USER:-}" ]]; then
        value="$HOME"
    fi
    [[ -n "$value" ]] || return 1
    printf '%s\n' "$value"
}

orlab_python_supported() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

orlab_python_minor() {
    "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

orlab_venv_dir() {
    local python_bin="$1"
    if [[ -n "${ORAN_LAB_VENV_DIR:-}" ]]; then
        printf '%s\n' "$ORAN_LAB_VENV_DIR"
        return 0
    fi
    local cache_root version
    cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/oran-energy-lab"
    version="$(orlab_python_minor "$python_bin")"
    printf '%s/venv-python%s\n' "$cache_root" "$version"
}

orlab_data_dir() {
    printf '%s/O-RAN Energy Lab\n' "${XDG_DATA_HOME:-$HOME/.local/share}"
}

orlab_config_path() {
    printf '%s/oran-energy-lab/config.json\n' "${XDG_CONFIG_HOME:-$HOME/.config}"
}

orlab_state_dir() {
    printf '%s/oran-energy-lab\n' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

orlab_export_user_paths() {
    export ORAN_LAB_DATA_DIR="${ORAN_LAB_DATA_DIR:-$(orlab_data_dir)}"
    export ORAN_LAB_CONFIG_PATH="${ORAN_LAB_CONFIG_PATH:-$(orlab_config_path)}"
    mkdir -p -- "$ORAN_LAB_DATA_DIR" "$(dirname -- "$ORAN_LAB_CONFIG_PATH")" "$(orlab_state_dir)"
}

orlab_image_name() {
    local python_bin="${1:-}" result=""
    if [[ -n "$python_bin" && -r "$ORAN_LAB_ROOT/config.json" ]]; then
        result="$("$python_bin" -c 'import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        data = json.load(stream)
    print(str(data.get("lab", {}).get("simulator_image") or "wcnc_tap"))
except Exception:
    print("wcnc_tap")' "$ORAN_LAB_ROOT/config.json" 2>/dev/null || true)"
    fi
    printf '%s\n' "${ORAN_LAB_IMAGE_NAME:-${result:-wcnc_tap}}"
}

orlab_linux_setting() {
    local setting="$1" settings_file="$ORAN_LAB_ROOT/config/linux.env"
    [[ -f "$settings_file" ]] || return 1
    awk -v name="$setting" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        $0 ~ "^[[:space:]]*" name "=" {
            sub("^[[:space:]]*" name "=", "")
            sub(/\r$/, "")
            print
            exit
        }
    ' "$settings_file"
}

orlab_resolve_project_path() {
    local value="$1"
    if [[ "$value" == /* ]]; then
        printf '%s\n' "$value"
    else
        printf '%s/%s\n' "$ORAN_LAB_ROOT" "$value"
    fi
}

orlab_run_user() {
    local fallback_user="${USER:-}"
    if [[ -z "$fallback_user" ]]; then
        fallback_user="$(id -un)"
    fi
    printf '%s\n' "${ORAN_LAB_USER:-${SUDO_USER:-$fallback_user}}"
}

orlab_user_in_docker_group() {
    local target_user="$1" groups
    groups="$(id -nG "$target_user" 2>/dev/null || true)"
    [[ " $groups " == *" docker "* ]]
}
