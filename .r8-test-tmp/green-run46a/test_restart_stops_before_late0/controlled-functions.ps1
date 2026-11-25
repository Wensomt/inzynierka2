
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
function Invoke-Setup {
    if (-not $env:LOCALAPPDATA) { throw "Nie znaleziono katalogu profilu LocalAppData." }
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    $caption = [string]$os.Caption
    if ($caption -match "Windows 11") {
        if ($build -lt 22631) { throw "Docker Desktop wymaga aktualnego Windows 11 (co najmniej build 22631). Zaktualizuj Windows i uruchom instalator ponownie." }
    }
    elseif ($caption -match "Windows 10") {
        if ($build -lt 19045) { throw "Docker Desktop wymaga Windows 10 22H2 (build 19045) lub Windows 11." }
    }
    else {
        throw "Nieobsługiwany system: $caption. Instalator jest przeznaczony dla Windows 10/11 64-bit."
    }

    if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "Obraz symulatora wymaga Windows x64 (AMD64). Wykryta architektura: $env:PROCESSOR_ARCHITECTURE"
    }
    $processors = @(Get-CimInstance Win32_Processor)
    if ($processors | Where-Object { $_.VirtualizationFirmwareEnabled -eq $false }) {
        throw "Włącz Intel VT-x/AMD-V (wirtualizację sprzętową) w BIOS/UEFI i uruchom instalator ponownie."
    }
    $ramGb = [math]::Round(([double]$os.TotalVisibleMemorySize * 1KB) / 1GB, 1)
    if ($ramGb -lt 8) { throw "Docker Desktop wymaga co najmniej 8 GB RAM; wykryto $ramGb GB." }

    $script:WingetExe = Get-ExecutablePath -Name "winget.exe"
    if ($script:WingetExe) {
        Write-Host "Dostępny winget: $script:WingetExe"
    }
    else {
        Write-Host "winget nie jest zainstalowany. Użyję oficjalnych instalatorów pobieranych bezpośrednio."
    }

    Ensure-Wsl2
    if ($script:RebootRequired) {
        $receiptDirectory = Join-Path $script:ProjectRoot ".install"
        New-Item -ItemType Directory -Path $receiptDirectory -Force | Out-Null
        $receiptPath = Join-Path $receiptDirectory "runtime.json"
        $temporaryReceipt = "$receiptPath.$([Guid]::NewGuid().ToString('N')).tmp"
        @{ schema_version = 1; root = $script:ProjectRoot; stage = "wsl"; needs_restart = $true } |
            ConvertTo-Json | Set-Content -LiteralPath $temporaryReceipt -Encoding UTF8
        Move-Item -LiteralPath $temporaryReceipt -Destination $receiptPath -Force
        Write-Host "`nWindows musi zostać uruchomiony ponownie, aby WSL2 zadziałał. Zapisano log; po restarcie kliknij install_windows.bat ponownie, aby kontynuować instalację." -ForegroundColor Yellow
        return 2
    }

    Ensure-PythonAndDependencies
    Ensure-DockerDesktop

    if ($script:RebootRequired) {
        Write-Host "`nPython, biblioteki i Docker Desktop są zainstalowane. Uruchom ponownie Windows, a potem ponownie kliknij install_windows.bat, aby przygotować obraz i uruchomić aplikację." -ForegroundColor Yellow
        return 2
    }

    Wait-ForDockerEngine
    Ensure-SimulatorImage
    New-DesktopShortcut

    $receiptDirectory = Join-Path $script:ProjectRoot ".install"
    New-Item -ItemType Directory -Path $receiptDirectory -Force | Out-Null
    $receiptPath = Join-Path $receiptDirectory "runtime.json"
    $temporaryReceipt = "$receiptPath.$([Guid]::NewGuid().ToString('N')).tmp"
    @{ schema_version = 1; root = $script:ProjectRoot; stage = "ready"; needs_restart = $false;
       python = (Join-Path $script:ProjectRoot ".venv\Scripts\python.exe") } |
        ConvertTo-Json | Set-Content -LiteralPath $temporaryReceipt -Encoding UTF8
    Move-Item -LiteralPath $temporaryReceipt -Destination $receiptPath -Force

    if (-not $SkipLaunch) {
        Write-Step "Uruchamianie symulatora O-RAN"
        Push-Location $script:ProjectRoot
        try {
            & $script:VenvPython -m app.main
            if ($LASTEXITCODE -ne 0) { throw "Aplikacja zakończyła się kodem $LASTEXITCODE." }
        }
        finally {
            Pop-Location
        }
    }
    return 0
}

$env:PROCESSOR_ARCHITECTURE='AMD64'
$script:RebootRequired=$false
$script:later=0
function Get-CimInstance { param($ClassName)
    if ($ClassName -eq 'Win32_OperatingSystem') {
        return [pscustomobject]@{Caption='Windows 11 Pro'; BuildNumber=22631; TotalVisibleMemorySize=16777216}
    }
    return [pscustomobject]@{VirtualizationFirmwareEnabled=$true}
}
function Get-ExecutablePath { return $null }
function Write-Step { }
function Ensure-Wsl2 { }
function Ensure-PythonAndDependencies { $script:later++ }
function Ensure-DockerDesktop { $script:later++ }
function Wait-ForDockerEngine { $script:later++ }
function Ensure-SimulatorImage { $script:later++ }
function New-DesktopShortcut { }
function Controlled-Gui { throw 'GUI must not start' }
$script:VenvPython='Controlled-Gui'

$SkipLaunch=$true
function Ensure-Wsl2 { $script:RebootRequired=$true }
$code=Invoke-Setup
if ($code -ne 2 -or $script:later -ne 0) { throw 'Restart continued to later stages' }
