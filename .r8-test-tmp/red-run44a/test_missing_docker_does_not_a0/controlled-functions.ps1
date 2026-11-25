
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
        if ($script:WingetExe) {
            try {
                Write-Host "Instaluję Docker Desktop przez winget, w profilu bieżącego użytkownika i z backendem WSL2."
                Invoke-Winget -Arguments @(
                    "install", "--id", "Docker.DockerDesktop", "--exact", "--source", "winget",
                    "--accept-source-agreements", "--accept-package-agreements",
                    "--override", "install --user --quiet --backend=wsl-2"
                )
                Update-SessionPath
                $docker = Get-DockerExecutable
                $desktop = Get-DockerDesktopExecutable
            }
            catch {
                Write-Warning "Instalacja Docker Desktop przez winget nie powiodła się: $($_.Exception.Message)"
                Update-SessionPath
                $docker = Get-DockerExecutable
                $desktop = Get-DockerDesktopExecutable
            }
        }
        if (-not $docker -or -not $desktop) {
            Install-DockerDesktopFromOfficialSource
        }
        $docker = Get-DockerExecutable
        $desktop = Get-DockerDesktopExecutable
    }
    if (-not $docker -or -not $desktop) {
        throw "Docker Desktop zainstalował się niekompletnie. Otwórz jego instalator z menu Start i uruchom ponownie install_windows.bat."
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
