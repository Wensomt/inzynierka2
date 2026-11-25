"""Entry point embedded in the single-file Windows installer."""

import argparse
from hashlib import sha256
from pathlib import Path, PurePosixPath
import os
import re
import shutil
import stat
import subprocess
import sys
from uuid import uuid4
from zipfile import ZipFile


def assert_plain_path(path):
    for item in (path, *path.parents):
        try:
            attributes = item.lstat().st_file_attributes
        except FileNotFoundError:
            continue
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"Ścieżka jest przekierowana: {item}")


def extract(archive, destination):
    assert_plain_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for entry in archive.infolist():
        if not entry.filename.startswith("payload/"):
            continue
        name = entry.filename[len("payload/"):]
        relative = PurePosixPath(name)
        if (entry.is_dir() or relative.is_absolute() or ".." in relative.parts
                or "\\" in name or ":" in name or not relative.parts):
            raise ValueError(f"Nieprawidłowa ścieżka w paczce: {entry.filename}")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry) as source, target.open("xb") as output:
            shutil.copyfileobj(source, output)


def prepare_files(archive, root, digest):
    assert_plain_path(root)
    receipt = root / ".bundle-sha256"
    if root.exists():
        assert_plain_path(receipt)
        assert_plain_path(root / "install_windows.ps1")
        if not receipt.is_file() or receipt.read_text(encoding="ascii") != digest:
            raise FileExistsError(f"Katalog instalacji ma niezgodną wersję: {root}")
        return
    staging = root.with_name(f"{root.name}-staging-{uuid4().hex}")
    extract(archive, staging)
    (staging / receipt.name).write_text(digest, encoding="ascii")
    staging.rename(root)


def main():
    parser = argparse.ArgumentParser(description="Instalator symulatora O-RAN na Windows x64.")
    parser.add_argument("--extract-only", type=Path)
    parser.add_argument("--local-image", metavar="sha256:…")
    parser.add_argument("--skip-launch", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Ten instalator wymaga Windows.")
    if args.local_image and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.local_image):
        parser.error("--local-image wymaga pełnego identyfikatora obrazu SHA-256.")

    bundle = Path(sys.argv[0])
    with ZipFile(bundle) as archive:
        if args.extract_only:
            extract(archive, args.extract_only.absolute())
            return 0
        digest = sha256(bundle.read_bytes()).hexdigest()
        root = Path(os.environ["LOCALAPPDATA"]) / "O-RAN Energy Lab" / "versions" / digest[:16]
        prepare_files(archive, root, digest)

    print(f"Symulator O-RAN | {root}", flush=True)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [
        str(powershell), "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File",
        str(root / "install_windows.ps1"), "-AllowWindowsHome",
        "-InstallWsl", "-InstallDocker", "-InstallPython",
    ]
    if args.local_image:
        command.extend(("-ImageMode", "Local", "-LocalImageId", args.local_image))
    else:
        command.extend(("-ImageMode", "Source"))
    if args.skip_launch:
        command.append("-SkipLaunch")
    environment = dict(os.environ)
    environment.pop("PSModulePath", None)
    code = subprocess.run(command, cwd=root, env=environment).returncode
    if code == 2:
        print("Po restarcie Windows uruchom ponownie ten sam plik instalatora.")
    return code


if __name__ == "__main__":
    try:
        result = main()
    except (OSError, ValueError) as error:
        print(f"Błąd instalatora: {error}", file=sys.stderr)
        result = 1
    if result and len(sys.argv) == 1 and sys.stdin.isatty():
        input("Naciśnij Enter, aby zamknąć okno.")
    raise SystemExit(result)
