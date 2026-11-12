#!/usr/bin/env bash
# Jednorazowo przygotowuje Ubuntu/Debian do pracy z aplikacją.
# Zmiany systemowe są widoczne w terminalu i wymagają świadomego sudo.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

LAUNCH_AFTER_SETUP=0
ASSUME_YES=0
GROUP_WAS_ADDED=0
ADMIN_APPROVED=0
RUN_USER="$(orlab_run_user)"

usage() {
    cat <<'EOF'
Użycie: setup.sh [--launch] [--yes]
  --launch  po udanym przygotowaniu uruchamia GUI
  --yes     nie pyta o potwierdzenie przed użyciem sudo

Obsługiwane są Ubuntu i Debian z Pythonem 3.8 lub nowszym.
EOF
}

pause_on_error() {
    local status=$?
    if [[ "$status" -ne 0 && -t 0 ]]; then
        printf '\nPrzygotowanie nie zostało ukończone. Naciśnij Enter, aby zamknąć terminal.\n' >&2
        read -r _ || true
    fi
    exit "$status"
}

trap pause_on_error EXIT

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --launch) LAUNCH_AFTER_SETUP=1 ;;
        --yes) ASSUME_YES=1 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

if [[ "$EUID" -eq 0 || "$RUN_USER" == "root" ]]; then
    orlab_die "Uruchom przygotowanie jako zwykły użytkownik pulpitu, bez sudo. Skrypt sam poprosi o sudo tylko do zmian systemowych."
    exit 1
fi

LOCK_DIR="${XDG_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/oran-energy-lab}"
mkdir -p -- "$LOCK_DIR"
LOCK_PATH="$LOCK_DIR/setup.lock"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_PATH"
    if ! flock -n 9; then
        orlab_die "Inne przygotowanie środowiska już trwa."
        exit 1
    fi
fi

as_root() {
    if [[ "$EUID" -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

approve_admin_changes() {
    [[ "$ADMIN_APPROVED" -eq 1 ]] && return 0
    printf '\nDo przygotowania systemu potrzebne będą uprawnienia administratora.\n'
    printf 'Może zostać zainstalowany Python/Tk lub Docker Engine oraz dodany użytkownik %s do grupy docker.\n' "$RUN_USER"
    printf 'Jeżeli nie ma gotowego obrazu, instalator może też zbudować go z dołączonego .simulator-src; może wtedy pobrać brakujące moduły źródłowe i wymaga Internetu.\n'
    printf 'Grupa docker daje szeroki dostęp do systemu poprzez Docker.\n'
    if [[ "$ASSUME_YES" -ne 1 ]]; then
        local answer
        read -r -p 'Kontynuować? [t/N] ' answer
        [[ "$answer" =~ ^([tT][aA][kK]|[tTyY])$ ]] || {
            printf 'Przerwano bez zmian systemowych.\n'
            exit 0
        }
    fi
    if [[ "$EUID" -ne 0 ]]; then
        sudo -v
    fi
    ADMIN_APPROVED=1
}

require_debian_family() {
    [[ -r /etc/os-release ]] || {
        orlab_die "Nie można rozpoznać dystrybucji Linuxa. Obsługiwane są Ubuntu i Debian."
        exit 1
    }
    # shellcheck disable=SC1091
    source /etc/os-release
    case "${ID:-}" in
        ubuntu|debian) ;;
        *)
            orlab_die "Automatyczne przygotowanie obsługuje Ubuntu i Debian; wykryto: ${PRETTY_NAME:-nieznany system}."
            exit 1
            ;;
    esac
}

system_python_ready() {
    local python_bin
    python_bin="$(orlab_python)" || return 1
    orlab_python_supported "$python_bin" || return 1
    "$python_bin" -c 'import tkinter, venv' >/dev/null 2>&1
}

ensure_system_python() {
    local python_bin=""
    if python_bin="$(orlab_python)"; then
        if ! orlab_python_supported "$python_bin"; then
            orlab_die "Wykryto Python $($python_bin --version 2>&1), ale wymagany jest Python 3.8+. Ustaw ORAN_LAB_PYTHON na nowszy interpreter albo zaktualizuj system."
            exit 1
        fi
    fi
    if system_python_ready; then
        return 0
    fi
    require_debian_family
    approve_admin_changes
    as_root apt-get update
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-tk
    if ! system_python_ready; then
        orlab_die "Nie udało się przygotować Pythona 3.8+ z Tkinter i venv."
        exit 1
    fi
}

install_docker_from_official_repository() {
    local distro codename architecture temporary_source
    # ID i VERSION_CODENAME zostały już wczytane przez require_debian_family.
    distro="$ID"
    codename="${VERSION_CODENAME:-}"
    if [[ "$distro" == "ubuntu" ]]; then
        codename="${UBUNTU_CODENAME:-$codename}"
    fi
    [[ -n "$codename" ]] || {
        orlab_die "Brak nazwy wydania systemu potrzebnej do konfiguracji repozytorium Docker."
        exit 1
    }
    architecture="$(dpkg --print-architecture)"
    as_root apt-get update
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
    as_root install -m 0755 -d /etc/apt/keyrings
    as_root curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
    as_root chmod a+r /etc/apt/keyrings/docker.asc
    temporary_source="$(mktemp)"
    cat > "$temporary_source" <<EOF
Types: deb
URIs: https://download.docker.com/linux/$distro
Suites: $codename
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    as_root install -m 0644 "$temporary_source" /etc/apt/sources.list.d/docker.sources
    rm -f -- "$temporary_source"
    as_root apt-get update
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_from_system_repository() {
    as_root apt-get update
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
}

ensure_no_conflicting_docker_packages() {
    command -v dpkg-query >/dev/null 2>&1 || return 0
    local package installed=()
    for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
        if [[ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" == "installed" ]]; then
            installed+=("$package")
        fi
    done
    if [[ "${#installed[@]}" -gt 0 ]]; then
        orlab_die "Wykryto pakiety mogące kolidować z Docker Engine: ${installed[*]}. Nie usuwam ich automatycznie, aby nie naruszyć istniejącego środowiska Docker."
        exit 1
    fi
}

ensure_docker_engine() {
    if ! command -v docker >/dev/null 2>&1; then
        require_debian_family
        approve_admin_changes
        if [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "20.04" ]]; then
            # Ubuntu 20.04 ma wspierany przez dystrybucję pakiet docker.io;
            # nie dodajemy zewnętrznego repozytorium wyłącznie dla tej wersji.
            install_docker_from_system_repository
        else
            ensure_no_conflicting_docker_packages
            install_docker_from_official_repository
        fi
    fi
    if ! as_root docker info >/dev/null 2>&1; then
        approve_admin_changes
        if command -v systemctl >/dev/null 2>&1; then
            as_root systemctl enable --now docker
        elif command -v service >/dev/null 2>&1; then
            as_root service docker start
        fi
    fi
    if ! as_root docker info >/dev/null 2>&1; then
        orlab_die "Docker jest zainstalowany, lecz usługa nie odpowiada. Sprawdź logi systemowe usługi docker."
        exit 1
    fi
}

ensure_docker_group() {
    if orlab_user_in_docker_group "$RUN_USER"; then
        return 0
    fi
    approve_admin_changes
    as_root usermod -aG docker "$RUN_USER"
    GROUP_WAS_ADDED=1
}

ensure_python_environment() {
    local python_bin venv_dir
    python_bin="$(orlab_python)"
    venv_dir="$(orlab_venv_dir "$python_bin")"
    mkdir -p -- "$(dirname -- "$venv_dir")"
    if [[ ! -x "$venv_dir/bin/python" ]]; then
        "$python_bin" -m venv "$venv_dir"
    fi
    "$venv_dir/bin/python" -m pip install --upgrade pip
    "$venv_dir/bin/python" -m pip install -r "$ORAN_LAB_ROOT/requirements.txt"
    # Ikona pulpitu uruchamia nowy proces, więc musi poznać dokładnie ten sam
    # interpreter, z którego utworzono środowisko wirtualne.
    orlab_save_python_selection "$python_bin"
}

configured_setting() {
    local environment_name="$1" value=""
    value="${!environment_name:-}"
    if [[ -z "$value" ]]; then
        value="$(orlab_linux_setting "$environment_name" 2>/dev/null || true)"
    fi
    printf '%s\n' "$value"
}

verify_archive_checksum() {
    local archive="$1" expected="$(configured_setting ORAN_LAB_IMAGE_SHA256)" actual checksum_file
    checksum_file="${archive}.sha256"
    if [[ -z "$expected" && -f "$checksum_file" ]]; then
        expected="$(awk 'NF { print $1; exit }' "$checksum_file")"
    fi
    if [[ -z "$expected" ]]; then
        printf 'Uwaga: brak sumy SHA-256 dla %s; obraz zostanie załadowany bez dodatkowej weryfikacji.\n' "$archive" >&2
        return 0
    fi
    actual="$(sha256sum "$archive" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] || {
        orlab_die "Suma SHA-256 obrazu nie jest zgodna z oczekiwaną wartością."
        exit 1
    }
}

ensure_host_git() {
    if command -v git >/dev/null 2>&1; then
        return 0
    fi
    require_debian_family
    approve_admin_changes
    as_root apt-get update
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y git
    if ! command -v git >/dev/null 2>&1; then
        orlab_die "Nie udało się zainstalować git potrzebnego do uzupełnienia lokalnych modułów źródłowych."
        exit 1
    fi
}

prepare_local_source_submodules() {
    # Snapshot przekazany z projektem może mieć puste wpisy submodule.  W
    # szczególności src/nr jest wymagany przez CMake obrazu mmWave-LENA-O-RAN;
    # bez niego Docker build kończy się błędem dopiero po długim kopiowaniu
    # kontekstu.  Nie odświeżamy gałęzi ani nie zmieniamy plików głównego
    # repozytorium: pobieramy wyłącznie dokładne commity wskazane przez
    # .gitmodules i gitlinki bieżącego snapshotu.
    local source_root="$1" nr_cmake status
    nr_cmake="$source_root/src/nr/CMakeLists.txt"
    if [[ -f "$nr_cmake" ]]; then
        return 0
    fi
    if [[ ! -d "$source_root/.git" || ! -f "$source_root/.gitmodules" ]]; then
        orlab_die "Lokalny snapshot symulatora nie zawiera src/nr ani metadanych Git potrzebnych do jego pobrania. Dołącz pełny src/nr lub użyj archiwum/refu obrazu."
        exit 1
    fi
    ensure_host_git
    printf 'Uzupełnianie brakujących modułów źródłowych ns-3 (w tym src/nr)...\n'
    if ! GIT_TERMINAL_PROMPT=0 git -C "$source_root" submodule sync --recursive; then
        orlab_die "Nie można zsynchronizować adresów modułów Git lokalnego snapshotu."
        exit 1
    fi
    if ! GIT_TERMINAL_PROMPT=0 git -C "$source_root" submodule update --init --recursive; then
        orlab_die "Nie można pobrać wymaganego modułu src/nr. Sprawdź Internet/DNS albo dołącz pełne źródła submodule."
        exit 1
    fi
    if [[ ! -f "$nr_cmake" ]]; then
        status="$(git -C "$source_root" submodule status --recursive 2>/dev/null || true)"
        orlab_die "Po pobraniu nadal brakuje src/nr/CMakeLists.txt. Stan modułów: ${status:-brak danych}."
        exit 1
    fi
}

prepare_local_lab_resources() {
    # Dockerfile dostaje build context wyłącznie z ``.simulator-src``. Zanim
    # zacznie się budowa, wkładamy do kontrolowanego podkatalogu dokładnie te
    # dwa źródła, które są dostarczane z aplikacją. Dzięki temu pierwszy obraz
    # ma aktualny scenariusz i xApp, a nie tylko bazowy ns-3/FlexRIC z sieci.
    local source_context="$1" staged_dir scenario_source xapp_source
    scenario_source="$ORAN_LAB_ROOT/resources/my_energy_scenario_optimized.cc"
    xapp_source="$ORAN_LAB_ROOT/resources/xapp_my_energy_optimized.c"
    [[ -f "$scenario_source" ]] || {
        orlab_die "Brak dostarczonego źródła scenariusza: $scenario_source"
        exit 1
    }
    [[ -f "$xapp_source" ]] || {
        orlab_die "Brak dostarczonego źródła xAppa: $xapp_source"
        exit 1
    }
    staged_dir="$source_context/oran-lab-resources"
    mkdir -p -- "$staged_dir"
    cp -f -- "$scenario_source" "$staged_dir/my_energy_scenario_optimized.cc"
    cp -f -- "$xapp_source" "$staged_dir/xapp_my_energy_optimized.c"
}

ensure_simulator_image() {
    local python_bin image_name archive image_ref user_home docker_config source_context source_dockerfile build_local_sources
    python_bin="$(orlab_python)"
    image_name="$(orlab_image_name "$python_bin")"
    if as_root docker image inspect "$image_name" >/dev/null 2>&1; then
        printf 'Obraz symulatora jest gotowy: %s\n' "$image_name"
        verify_image_architecture "$image_name"
        return 0
    fi

    archive="$(configured_setting ORAN_LAB_IMAGE_ARCHIVE)"
    if [[ -z "$archive" && -f "$ORAN_LAB_ROOT/resources/wcnc_tap.tar" ]]; then
        archive="resources/wcnc_tap.tar"
    fi
    image_ref="$(configured_setting ORAN_LAB_IMAGE_REF)"
    build_local_sources="$(configured_setting ORAN_LAB_BUILD_LOCAL_SOURCES)"
    source_context="$ORAN_LAB_ROOT/.simulator-src"
    source_dockerfile="$source_context/mmwave-LENA-oran/ns3_docker/Dockerfile"

    if [[ -n "$archive" && -n "$image_ref" ]]; then
        orlab_die "Wskaż tylko jedno źródło obrazu: archiwum ORAN_LAB_IMAGE_ARCHIVE albo ORAN_LAB_IMAGE_REF."
        exit 1
    fi

    if [[ -n "$archive" ]]; then
        archive="$(orlab_resolve_project_path "$archive")"
        [[ -f "$archive" ]] || {
            orlab_die "Wskazany plik obrazu nie istnieje: $archive"
            exit 1
        }
        verify_archive_checksum "$archive"
        approve_admin_changes
        printf 'Ładowanie obrazu symulatora z: %s\n' "$archive"
        as_root docker load --input "$archive"
    elif [[ -n "$image_ref" ]]; then
        if [[ "$image_ref" != *@sha256:* ]]; then
            orlab_die "ORAN_LAB_IMAGE_REF musi zawierać niezmienny digest @sha256:..."
            exit 1
        fi
        approve_admin_changes
        printf 'Pobieranie obrazu symulatora: %s\n' "$image_ref"
        if docker info >/dev/null 2>&1; then
            # Po ponownym zalogowaniu użytkownik ma już dostęp do daemonu;
            # używamy wtedy jego zwykłego `docker login` i credential helpera.
            docker pull "$image_ref"
        else
            user_home="$(orlab_user_home "$RUN_USER" || true)"
            docker_config="${user_home:+$user_home/.docker}"
            if [[ -n "$docker_config" && -d "$docker_config" ]]; then
            # `docker login` wykonany przez użytkownika zapisuje poświadczenia
            # w jego katalogu. Docker daemon wymaga roota podczas setupu, ale
            # klient może bezpiecznie użyć właśnie tego magazynu poświadczeń.
                as_root env DOCKER_CONFIG="$docker_config" docker pull "$image_ref"
            else
                as_root docker pull "$image_ref"
            fi
        fi
        as_root docker tag "$image_ref" "$image_name"
    elif [[ "$build_local_sources" != "0" && -d "$source_context/e2sim-kpmv3" && -d "$source_context/mmwave-LENA-oran" && -f "$source_dockerfile" ]]; then
        # Dockerfile dostarczony razem z lokalnym snapshotem oczekuje obu
        # katalogów dokładnie w korzeniu build context. Nie zgadujemy obrazu
        # z rejestru: budujemy jawnie ze źródeł przekazanych z aplikacją.
        approve_admin_changes
        prepare_local_source_submodules "$source_context/mmwave-LENA-oran"
        prepare_local_lab_resources "$source_context"
        printf 'Budowanie obrazu symulatora z lokalnych źródeł: %s\n' "$source_context"
        printf 'To może potrwać długo i Dockerfile może pobrać swoje jawnie zdefiniowane zależności.\n'
        as_root docker build --tag "$image_name" --file "$source_dockerfile" "$source_context"
    else
        cat >&2 <<EOF
Brakuje obrazu symulatora $image_name.
Nie znaleziono ani archiwum/ref obrazu, ani kompletnego .simulator-src do builda. Wybierz jeden wariant:
  1. dołącz resources/wcnc_tap.tar oraz resources/wcnc_tap.tar.sha256,
  2. skopiuj config/linux.env.example do config/linux.env i wpisz
     ORAN_LAB_IMAGE_REF z pełnym digestem @sha256:...,
  3. dołącz .simulator-src/e2sim-kpmv3 oraz .simulator-src/mmwave-LENA-oran
     z ns3_docker/Dockerfile (albo usuń ORAN_LAB_BUILD_LOCAL_SOURCES=0).
EOF
        exit 1
    fi

    if ! as_root docker image inspect "$image_name" >/dev/null 2>&1; then
        orlab_die "Po załadowaniu/pobraniu nadal brakuje obrazu o nazwie: $image_name"
        exit 1
    fi
    verify_image_architecture "$image_name"
}

verify_image_architecture() {
    local image_name="$1" host_arch image_arch
    host_arch="$(uname -m)"
    image_arch="$(as_root docker image inspect "$image_name" --format '{{.Architecture}}' 2>/dev/null || true)"
    case "$host_arch" in
        x86_64|amd64) host_arch="amd64" ;;
        aarch64|arm64) host_arch="arm64" ;;
    esac
    case "$image_arch" in
        x86_64) image_arch="amd64" ;;
        aarch64) image_arch="arm64" ;;
    esac
    if [[ -z "$image_arch" || "$host_arch" != "$image_arch" ]]; then
        orlab_die "Architektura obrazu Docker ($image_arch) nie odpowiada komputerowi ($host_arch)."
        exit 1
    fi
}

install_desktop_entry() {
    bash "$ORAN_LAB_ROOT/install_desktop.sh"
}

start_gui() {
    local state_dir log_file
    if ! docker info >/dev/null 2>&1; then
        printf '\nDodano użytkownika do grupy docker. Wyloguj się i zaloguj ponownie, a następnie kliknij ikonę Symulator O-RAN.\n'
        return 0
    fi
    state_dir="$(orlab_state_dir)"
    mkdir -p -- "$state_dir"
    log_file="$state_dir/launcher.log"
    nohup bash "$SCRIPT_DIR/launcher.sh" --start-ready >"$log_file" 2>&1 &
    printf 'Symulator O-RAN został uruchomiony. Log startu: %s\n' "$log_file"
}

printf 'Przygotowanie Symulator O-RAN dla użytkownika %s\n' "$RUN_USER"
ensure_system_python
ensure_docker_engine
ensure_docker_group
ensure_python_environment
install_desktop_entry
ensure_simulator_image

if [[ "$GROUP_WAS_ADDED" -eq 1 ]]; then
    printf '\nDodano użytkownika %s do grupy docker. Wyloguj się i zaloguj ponownie; jest to wymagane przez system.\n' "$RUN_USER"
    printf 'Skrót Symulator O-RAN został już utworzony.\n'
    exit 0
fi

printf '\nŚrodowisko jest gotowe.\n'
if [[ "$LAUNCH_AFTER_SETUP" -eq 1 ]]; then
    start_gui
fi
