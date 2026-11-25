"""Static dependency-entry policy checks, distinct from executed installer RED.
Expected lock names follow the approved Windows/CPython 3.13 contract. Wheels
were independently qualified in R4; these tests never resolve or install them.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('entry,lock', [
    ('requirements.txt', 'requirements-windows.lock'),
    ('requirements-dev.txt', 'requirements-dev-windows.lock'),
])
def test_requirements_entry_enforces_hashed_windows_lock(entry, lock):
    lines = [line.strip() for line in (ROOT / entry).read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    assert '--require-hashes' in lines, 'Dependency entry does not enforce artifact hashes'
    assert '-r ' + lock in lines, 'Dependency entry does not select qualified Windows lock'


def test_launcher_does_not_select_shared_appdata_environment():
    # Static policy check only: do not execute launcher top-level/GUI.
    launcher = (ROOT / 'run_windows.ps1').read_text(encoding='utf-8-sig')
    assert 'venv-py3.13' not in launcher, 'Launcher still selects shared profile venv'
    assert 'runtime.json' in launcher, 'Launcher does not consult root-scoped runtime receipt'
