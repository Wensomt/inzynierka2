
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
function Save-InstallerDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Write-Host "Pobieram: $Uri"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $lastDownloadError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            $partial = "$Destination.$([Guid]::NewGuid().ToString('N')).partial"
            Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing
            if ((Get-Item -LiteralPath $partial).Length -lt 1MB) {
                throw "Pobrany instalator jest pusty lub niekompletny: $Uri"
            }
            if (Test-Path -LiteralPath $Destination) {
                [IO.File]::Replace($partial, $Destination, $null)
            }
            else {
                [IO.File]::Move($partial, $Destination)
            }
            $lastDownloadError = $null
            break
        }
        catch {
            $lastDownloadError = $_
            if ($attempt -lt 3) {
                $delaySeconds = 3 * $attempt
                Write-Warning "Pobieranie nie powiodło się (próba $attempt/3). Ponawiam za $delaySeconds s: $($_.Exception.Message)"
                Start-Sleep -Seconds $delaySeconds
            }
        }
        finally {
            if ($partial -and (Test-Path -LiteralPath $partial)) {
                Remove-Item -LiteralPath $partial -Force
            }
        }
    }
    if ($lastDownloadError) {
        throw "Nie udało się pobrać pliku po 3 próbach: $Uri. Sprawdź połączenie z Internetem i DNS, a potem uruchom install_windows.bat ponownie. Szczegóły: $($lastDownloadError.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $Destination) -or (Get-Item -LiteralPath $Destination).Length -lt 1MB) {
        throw "Pobrany instalator jest pusty lub niekompletny: $Destination"
    }
}

function Start-Sleep { }
function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing)
    [IO.File]::WriteAllText($OutFile,'interrupted-body'); throw 'CONTROLLED_OFFLINE'
}
$failed=$false
try { Save-InstallerDownload -Uri 'https://invalid.example/probe' -Destination 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\qa-run48-green\test_offline_download_preserve0\installer.exe' } catch { $failed=$true }
if (-not $failed) { throw 'Offline download falsely succeeded' }
if (Test-Path -LiteralPath 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\qa-run48-green\test_offline_download_preserve0\installer.exe') { throw 'Partial download published as final installer' }
