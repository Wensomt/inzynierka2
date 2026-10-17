#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
NS3_ROOT="${NS3_ROOT:-$HOME/ns-O-RAN-flexric/mmwave-LENA-oran}"
FLEXRIC_ROOT="${FLEXRIC_ROOT:-$HOME/flexric}"
SCENARIO_DST="$NS3_ROOT/scratch/my_energy_scenario_optimized.cc"
TARGET_NAME="xapp_my_energy_optimized"
ORANGE_DIR="$FLEXRIC_ROOT/examples/xApp/c/orange"
MYXAPP_DIR="$FLEXRIC_ROOT/examples/xApp/c/myxapp"

[[ -d "$NS3_ROOT/scratch" ]] || { echo "Brak katalogu: $NS3_ROOT/scratch"; exit 1; }
[[ -d "$ORANGE_DIR" ]] || { echo "Brak katalogu: $ORANGE_DIR"; exit 1; }

cp -v "$APP_DIR/resources/my_energy_scenario_optimized.cc" "$SCENARIO_DST"

# Preferuj istniejący target w myxapp. To zapobiega deklarowaniu tej samej
# nazwy targetu jednocześnie w katalogach myxapp i orange.
if [[ -f "$MYXAPP_DIR/CMakeLists.txt" ]] && grep -Fq "add_executable($TARGET_NAME" "$MYXAPP_DIR/CMakeLists.txt"; then
    TARGET_DIR="$MYXAPP_DIR"
elif grep -Fq "add_executable($TARGET_NAME" "$ORANGE_DIR/CMakeLists.txt"; then
    TARGET_DIR="$ORANGE_DIR"
elif [[ -d "$MYXAPP_DIR" ]]; then
    TARGET_DIR="$MYXAPP_DIR"
else
    TARGET_DIR="$ORANGE_DIR"
fi

TARGET_CMAKE="$TARGET_DIR/CMakeLists.txt"
TARGET_SOURCE="$TARGET_DIR/xapp_my_energy_optimized.c"
cp -v "$APP_DIR/resources/xapp_my_energy_optimized.c" "$TARGET_SOURCE"

# Usuń definicje tego samego targetu z pozostałych katalogów.
python3 - "$TARGET_CMAKE" "$ORANGE_DIR/CMakeLists.txt" "$MYXAPP_DIR/CMakeLists.txt" <<'PY'
from pathlib import Path
import re
import sys

preferred = Path(sys.argv[1]).resolve()
target = "xapp_my_energy_optimized"


def remove_function_block(text, function):
    lines = text.splitlines(keepends=True)
    output = []
    removed = 0
    i = 0
    pattern = re.compile(
        rf"^\s*{re.escape(function)}\s*\(\s*{re.escape(target)}(?:\s|\))"
    )
    while i < len(lines):
        if pattern.search(lines[i]):
            removed += 1
            depth = lines[i].count("(") - lines[i].count(")")
            i += 1
            while i < len(lines) and depth > 0:
                depth += lines[i].count("(") - lines[i].count(")")
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            continue
        output.append(lines[i])
        i += 1
    return "".join(output), removed

for raw in sys.argv[2:]:
    path = Path(raw)
    if not path.is_file() or path.resolve() == preferred:
        continue
    text = path.read_text(encoding="utf-8")
    total = 0
    for function in ("add_executable", "target_link_libraries"):
        text, count = remove_function_block(text, function)
        total += count
    if total:
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        print(f"Usunięto duplikat targetu z: {path}")
PY

# Dodaj target tylko wtedy, gdy nie istnieje w wybranym CMakeLists.txt.
if ! grep -Fq "add_executable($TARGET_NAME" "$TARGET_CMAKE"; then
cat >> "$TARGET_CMAKE" <<'CMAKE'

add_executable(xapp_my_energy_optimized
    xapp_my_energy_optimized.c
    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
)

target_link_libraries(xapp_my_energy_optimized
    PUBLIC
    e42_xapp
    pthread
    sctp
    dl
)
CMAKE
    echo "Dodano target $TARGET_NAME do $TARGET_CMAKE"
else
    echo "Target $TARGET_NAME już istnieje w $TARGET_CMAKE"
fi

TARGET_SUBDIR="$(basename "$TARGET_DIR")"
python3 - "$APP_DIR/config.json" "$FLEXRIC_ROOT/build/examples/xApp/c/$TARGET_SUBDIR" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
build_dir = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
data["xapps"]["optimized"]["cwd"] = build_dir
data["xapps"]["optimized"]["executable"] = "./xapp_my_energy_optimized"
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Zaktualizowano config.json: zoptymalizowany xApp będzie szukany w {build_dir}")
PY

echo
echo "Źródła przygotowane poprawnie."
echo "Wybrany katalog targetu: $TARGET_DIR"
echo "Scenariusz: $SCENARIO_DST"
echo "Źródło xAppa: $TARGET_SOURCE"
echo
echo "Teraz wykonaj:"
echo "  cmake --build $FLEXRIC_ROOT/build -j\$(nproc) --target $TARGET_NAME"
