
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
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force
            }
            Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
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
try { Save-InstallerDownload -Uri 'https://invalid.example/probe' -Destination 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\red-run44a\test_offline_download_preserve1\installer.exe' } catch { $failed=$true }
if (-not $failed) { throw 'Offline download falsely succeeded' }
if ([IO.File]::ReadAllText('C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\red-run44a\test_offline_download_preserve1\installer.exe') -ne 'previous-verified-artifact') { throw 'Retry destroyed existing artifact' }
