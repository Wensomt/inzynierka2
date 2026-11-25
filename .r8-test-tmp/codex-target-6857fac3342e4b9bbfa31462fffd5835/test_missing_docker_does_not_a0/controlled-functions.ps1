
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
function Ensure-DockerDesktop {
    param([switch]$AllowInstall)
    Write-Step "Sprawdzanie Docker Desktop"
    $docker = Get-DockerExecutable
    $desktop = Get-DockerDesktopExecutable
    if (-not $docker -and -not $desktop -and $AllowInstall) {
        Install-DockerDesktopFromOfficialSource
        $docker = Get-DockerExecutable
        $desktop = Get-DockerDesktopExecutable
        if ($docker -and $desktop) {
            throw 'Docker Desktop jest zainstalowany. Otworz go, zaakceptuj warunki i poczekaj na gotowosc silnika Linux, potem ponow instalacje O-RAN.'
        }
    }
    if (-not $docker -or -not $desktop) {
        throw 'Wymagany Docker Desktop z WSL2. Przy braku instalacji uzyj -InstallDocker. Przy niepelnej instalacji sprawdz https://docs.docker.com/desktop/setup/install/windows-install/'
    }

    $script:DockerExe = $docker
    $script:DockerDesktopExe = $desktop
    Write-Host "Docker CLI: $docker"
}

function Write-Step { }
function Get-DockerExecutable { return $null }
function Get-DockerDesktopExecutable { return $null }
$script:WingetExe=$null
$script:installs=0
function Install-DockerDesktopFromOfficialSource { $script:installs++ }
$rejected=$false
try { Ensure-DockerDesktop } catch { $rejected=$true }
if (-not $rejected -or $script:installs -ne 0) { throw 'Missing Docker attempted uncontrolled installer fallback' }
