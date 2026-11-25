"""Build the single-file Python installer for Windows."""

from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_FILES = (
    "config.json",
    "install_windows.ps1",
    "install_windows.bat",
    "run_windows.ps1",
    "run_windows.bat",
    "requirements-windows.lock",
    "resources/Energy_saving_with_cell_utilization_scenario.cc",
    "resources/my_energy_scenario_optimized.cc",
    "resources/xapp_my_energy_optimized.c",
    "resources/patch_kpm_clock.py",
    "resources/simulator/Dockerfile",
    "resources/simulator/sources.json",
)
LICENSE_FILES = ("GPL-2.0-mmwave.txt", "OAI-PL-1.1-historical.txt")


def write_payload(path: Path, licenses: Path) -> None:
    files = {name: ROOT / name for name in INSTALLER_FILES}
    files.update({
        source.relative_to(ROOT).as_posix(): source
        for source in (ROOT / "app").rglob("*.py")
    })
    files.update({f"LICENSES/{name}": licenses / name for name in LICENSE_FILES})
    files["INSTRUKCJA-INSTALACJI.md"] = ROOT / "scripts/windows/INSTRUKCJA-INSTALACJI.md"
    with ZipFile(path, "x") as archive:
        archive.writestr(ZipInfo("__main__.py"), (ROOT / "scripts/windows/setup.py").read_bytes(),
                         compress_type=ZIP_DEFLATED)
        for name, source in sorted(files.items()):
            if not source.is_file() or source.is_symlink():
                raise FileNotFoundError(f"Brak zwykłego pliku potrzebnego w instalatorze: {source}")
            archive.writestr(ZipInfo(f"payload/{name}"), source.read_bytes(), compress_type=ZIP_DEFLATED)


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--licenses", required=True, type=Path,
                        help="Katalog z oryginalnymi tekstami GPL-2.0-mmwave.txt i OAI-PL-1.1-historical.txt.")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/O-RAN-Energy-Lab-Setup.pyz")
    args = parser.parse_args()

    output = args.output.absolute()
    if output.exists():
        parser.error(f"Plik docelowy już istnieje: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=output.parent) as staging:
        archive = Path(staging) / "Setup.pyz"
        write_payload(archive, args.licenses)
        archive.rename(output)
    print(f"{sha256(output.read_bytes()).hexdigest()}  {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
