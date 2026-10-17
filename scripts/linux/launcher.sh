#!/usr/bin/env bash
# Launcher używany przez wpis .desktop. Przy brakach otwiera widoczne
# przygotowanie środowiska zamiast próbować wykonywać sudo w tle.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SETUP="$SCRIPT_DIR/setup.sh"
MODE="${1:-}"

usage() {
    cat <<'EOF'
Użycie: launcher.sh [--desktop|--setup|--start-ready]
  --desktop      tryb wpisu .desktop; przy brakach otwiera terminal przygotowania
  --setup        wykonuje przygotowanie w bieżącym terminalu
  --start-ready  uruchamia GUI po zakończonym sprawdzeniu przygotowania
EOF
}

selected_python() {
    local candidate
    candidate="$(orlab_python)" || return 1
    orlab_python_supported "$candidate" || return 1
    printf '%s\n' "$candidate"
}

runtime_ready() {
    local python_bin venv_dir image_name
    python_bin="$(selected_python)" || return 1
    venv_dir="$(orlab_venv_dir "$python_bin")"
    [[ -x "$venv_dir/bin/python" ]] || return 1
    "$venv_dir/bin/python" -c 'import docker, tkinter' >/dev/null 2>&1 || return 1
    command -v docker >/dev/null 2>&1 || return 1
    docker info >/dev/null 2>&1 || return 1
    image_name="$(orlab_image_name "$python_bin")"
    docker image inspect "$image_name" >/dev/null 2>&1
}

start_ready() {
    local python_bin venv_dir state_dir log_file status
    python_bin="$(selected_python)" || {
        orlab_die "Brak Pythona 3.8 lub nowszego. Uruchom przygotowanie." >&2
        exit 1
    }
    venv_dir="$(orlab_venv_dir "$python_bin")"
    [[ -x "$venv_dir/bin/python" ]] || {
        orlab_die "Brak przygotowanego środowiska Pythona. Uruchom przygotowanie." >&2
        exit 1
    }
    orlab_export_user_paths
    state_dir="$(orlab_state_dir)"
    mkdir -p -- "$state_dir"
    log_file="$state_dir/gui.log"
    # Skrót .desktop uruchamia skrypt przez pełną ścieżkę, ale użytkownik może
    # również wywołać go z dowolnego katalogu. Moduł ``app`` musi zawsze być
    # importowany z katalogu pakietu, a nie z bieżącego katalogu terminala.
    if (cd "$ORAN_LAB_ROOT" && "$venv_dir/bin/python" -m app.main) >>"$log_file" 2>&1; then
        status=0
    else
        status=$?
    fi
    if [[ "$status" -ne 0 ]]; then
        if command -v zenity >/dev/null 2>&1; then
            zenity --error --title="O-RAN Energy Lab" --text="Aplikacja zakończyła się błędem. Szczegóły: $log_file" || true
        elif command -v notify-send >/dev/null 2>&1; then
            notify-send "O-RAN Energy Lab" "Aplikacja zakończyła się błędem. Szczegóły: $log_file" || true
        fi
    fi
    return "$status"
}

show_no_terminal_error() {
    local message="Brakuje składnika O-RAN Energy Lab. Uruchom ręcznie: bash '$SETUP' --launch"
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="O-RAN Energy Lab" --text="$message" || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send "O-RAN Energy Lab" "$message" || true
    fi
    printf '%s\n' "$message" >&2
}

open_setup_terminal() {
    if command -v x-terminal-emulator >/dev/null 2>&1; then
        nohup x-terminal-emulator -e bash "$SETUP" --launch >/dev/null 2>&1 &
        return 0
    fi
    if command -v gnome-terminal >/dev/null 2>&1; then
        nohup gnome-terminal -- bash "$SETUP" --launch >/dev/null 2>&1 &
        return 0
    fi
    if command -v konsole >/dev/null 2>&1; then
        nohup konsole -e bash "$SETUP" --launch >/dev/null 2>&1 &
        return 0
    fi
    if command -v xfce4-terminal >/dev/null 2>&1; then
        nohup xfce4-terminal --command="bash $(printf '%q' "$SETUP") --launch" >/dev/null 2>&1 &
        return 0
    fi
    show_no_terminal_error
    return 1
}

case "$MODE" in
    --help|-h)
        usage
        ;;
    --start-ready)
        start_ready
        ;;
    --setup)
        exec bash "$SETUP" --launch
        ;;
    --desktop)
        if runtime_ready; then
            if start_ready; then
                exit 0
            else
                exit $?
            fi
        fi
        open_setup_terminal
        ;;
    "")
        if runtime_ready; then
            if start_ready; then
                exit 0
            else
                exit $?
            fi
        fi
        exec bash "$SETUP" --launch
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
