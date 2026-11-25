
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
    Write-Step "Sprawdzanie Docker Desktop"
    $docker = Get-DockerExecutable
    $desktop = Get-DockerDesktopExecutable
    if (-not $docker -or -not $desktop) {
        throw "Zainstaluj ręcznie zweryfikowany Docker Desktop z backendem WSL2 i zaakceptuj jego warunki, a następnie ponów instalację. Automatyczna instalacja Docker jest wyłączona."
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
