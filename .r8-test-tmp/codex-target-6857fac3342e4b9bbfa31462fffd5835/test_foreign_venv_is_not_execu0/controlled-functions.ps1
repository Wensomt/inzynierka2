
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
function Start-Process { throw 'UNMOCKED process' }
function Invoke-WebRequest { throw 'UNMOCKED network' }
function Invoke-NativeCommand { throw 'UNMOCKED native' }
function Invoke-Winget { throw 'UNMOCKED winget' }
function Get-CimInstance { throw 'UNMOCKED CIM' }
function Start-Sleep { throw 'UNMOCKED sleep' }
function New-DesktopShortcut { throw 'UNMOCKED shortcut' }
Write-Output '__CONTROLLED_ENTERED__'
function Assert-LocalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -notmatch '^[A-Za-z]:\\') { throw "Wymagana lokalna sciezka: $Path" }
    while ($full) {
        $item = $null
        try { $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { }
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Niedozwolony reparse point: $full" }
        $full = [IO.Path]::GetDirectoryName($full)
    }
}
function Assert-VenvOwnership {
    param($Python)
    Assert-LocalPath $script:VenvDirectory
    Assert-LocalPath (Split-Path -Parent $script:VenvPython)
    Assert-LocalPath $script:VenvPython
    $marker = Join-Path $script:VenvDirectory '.oran-lab-owner'
    $cfg = Join-Path $script:VenvDirectory 'pyvenv.cfg'
    Assert-LocalPath $marker
    Assert-LocalPath $cfg
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
        [IO.File]::ReadAllText($marker) -ne ($script:ProjectRoot + "`n" + $Python.Interpreter)) {
        throw 'Obcy lub niekompletny .venv: zachowano zawartosc. Uzyj czystego katalogu instalacji.'
    }
    $settings = [IO.File]::ReadAllText($cfg)
    if ($settings -notmatch '(?im)^include-system-site-packages\s*=\s*false\s*$') {
        throw 'Venv musi izolowac pakiety od bazowego CPythona.'
    }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($script:VenvDirectory)
    while ($pending.Count) {
        foreach ($item in (Get-ChildItem -LiteralPath ($pending.Pop()) -Force)) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Niedozwolony reparse point w venv: $($item.FullName)" }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}
function Ensure-PythonAndDependencies {
    Write-Step "Sprawdzanie Pythona i bibliotek aplikacji"
    $script:VenvDirectory = Join-Path $script:ProjectRoot '.venv'
    $script:VenvPython = Join-Path $script:VenvDirectory 'Scripts\python.exe'
    $marker = Join-Path $script:VenvDirectory '.oran-lab-owner'
    Assert-LocalPath $marker
    $existingVenv = Test-Path -LiteralPath $script:VenvDirectory
    if ($existingVenv -and -not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'Obcy .venv: zachowano zawartosc.' }
    $python = Get-ProjectPython
    if (-not $python) {
        if ($existingVenv) { throw 'Nie znaleziono Pythona istniejacego venv; brak automatycznej reinstalacji.' }
        if (-not $InstallPython) { throw 'Wymagany CPython 3.13 x64 z Tk. Zainstaluj recznie lub jawnie wybierz -InstallPython.' }
        Install-PythonFromOfficialSource
        $python = Get-ProjectPython
    }
    if (-not $python) {
        throw "Nie znaleziono zgodnego CPython 3.13 x64 z Tk."
    }

    Write-Host "Python $($python.Version): $($python.Interpreter)"
    if ($existingVenv) {
        Assert-VenvOwnership -Python $python
    }
    else {
        New-Item -ItemType Directory -Path $script:VenvDirectory | Out-Null
        [IO.File]::WriteAllText($marker, $script:ProjectRoot + "`n" + $python.Interpreter)
        & $python.Interpreter -I -m venv $script:VenvDirectory
        if ($LASTEXITCODE -ne 0) {
            throw "Nie udało się utworzyć środowiska aplikacji w $script:VenvDirectory."
        }
    }

    Assert-VenvOwnership -Python $python
    Assert-VenvIdentity -Python $python
    if (-not (Test-RuntimePins)) {
        & $script:VenvPython -I -m pip --isolated install --require-hashes --only-binary=:all: --no-deps --disable-pip-version-check -r (Join-Path $script:ProjectRoot 'requirements-windows.lock')
        if ($LASTEXITCODE -ne 0 -or -not (Test-RuntimePins)) { throw 'Nie mozna potwierdzic zamknietych pinow runtime.' }
    }
    $script:BasePython = $python
}

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\codex-target-6857fac3342e4b9bbfa31462fffd5835\test_foreign_venv_is_not_execu0'
function Write-Step { }
function Get-ProjectPython { [pscustomobject]@{Interpreter='UNMOCKED-python.exe';Version='3.13.16'} }
$rejected=$false
try { Ensure-PythonAndDependencies } catch { $rejected=$_.Exception.Message -like '*Obcy*' }
if (-not $rejected) { throw 'Foreign venv not rejected' }
