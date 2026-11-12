param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) {
  $installedPython = Join-Path $env:LOCALAPPDATA "O-RAN Energy Lab\venv-py3.13\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $installedPython)) {
    $installedPython = Join-Path $env:LOCALAPPDATA "O-RAN Energy Lab\venv-py3.12\Scripts\python.exe"
  }
  if (Test-Path -LiteralPath $installedPython) {
    $Python = $installedPython
  } else {
    $Python = "python"
  }
}

Push-Location $ProjectRoot
try {
  & $Python -m pip install -r requirements.txt pyinstaller
  if ($LASTEXITCODE -ne 0) { throw "Nie udało się zainstalować zależności do builda Windows." }
  & $Python -m PyInstaller --noconfirm --clean --windowed --name "Symulator O-RAN" --collect-all docker --add-data "config.json;." --add-data "resources;resources" --paths . app/main.py
  if ($LASTEXITCODE -ne 0) { throw "Build PyInstaller zakończył się błędem." }
}
finally {
  Pop-Location
}

Write-Host "Gotowe: dist/Symulator O-RAN/Symulator O-RAN.exe"
