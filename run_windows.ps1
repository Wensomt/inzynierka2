$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$localAppData = $env:LOCALAPPDATA
if (-not $localAppData) {
    Write-Error "Nie znaleziono katalogu profilu LocalAppData."
    exit 1
}

$venvCandidates = @(
    (Join-Path $localAppData "O-RAN Energy Lab\venv-py3.13\Scripts\python.exe"),
    (Join-Path $localAppData "O-RAN Energy Lab\venv-py3.12\Scripts\python.exe")
)
$venvPython = $venvCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $venvPython) {
    Write-Error "Środowisko aplikacji nie jest zainstalowane. Kliknij najpierw install_windows.bat."
    exit 1
}

Push-Location $projectRoot
try {
    & $venvPython -m app.main
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
