
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
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedSha256
    )
    Write-Host "Pobieram: $Uri"
    if (([Uri]$Uri).Scheme -ne 'https') { throw 'Instalator wymaga HTTPS.' }
    $lastDownloadError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $partial = "$Destination.$([Guid]::NewGuid().ToString('N')).partial.exe"
        $backup = $null
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing -MaximumRedirection 0
            Assert-InstallerIntegrity -Path $partial -ExpectedSha256 $ExpectedSha256
            $target = Get-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            if ($target -and ($target.PSIsContainer -or ($target.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
                throw "Kolizja celu instalatora: $Destination"
            }
            if ($target) {
                # Use a real sibling backup: PowerShell can bind $null as an empty string,
                # and ReplaceFile can lose the old name on failure without a backup.
                $backup = [IO.Path]::ChangeExtension($partial, 'bak')
                [IO.File]::Replace($partial, $Destination, $backup)
            }
            else { [IO.File]::Move($partial, $Destination) }
            $lastDownloadError = $null
            break
        }
        catch {
            if ($backup -and (Test-Path -LiteralPath $backup -ErrorAction Stop)) {
                # Stop retrying after a partial replace; never overwrite a still-present target.
                try { [IO.File]::Move($backup, $Destination) }
                catch { throw "Nie przywrocono poprzedniego instalatora; zachowano kopie: $backup. $($_.Exception.Message)" }
                throw
            }
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
    if (-not $lastDownloadError -and $backup) {
        try { [IO.File]::Delete($backup) }
        catch { Write-Warning "Zachowano kopie poprzedniego instalatora: $backup. $($_.Exception.Message)" }
    }
    if ($lastDownloadError) {
        throw "Nie udało się pobrać pliku po 3 próbach: $Uri. Sprawdź połączenie z Internetem i DNS, a potem uruchom install_windows.bat ponownie. Szczegóły: $($lastDownloadError.Exception.Message)"
    }

}

function Start-Sleep { }
function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing)
    [IO.File]::WriteAllText($OutFile,'interrupted-body'); throw 'CONTROLLED_OFFLINE'
}
$failed=$false
try { Save-InstallerDownload -Uri 'https://invalid.example/probe' -Destination 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\mvp1d-controlled-retest\test_offline_download_preserve1\installer.exe' -ExpectedSha256 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' } catch { $failed=$_.Exception.Message -like '*CONTROLLED_OFFLINE*' }
if (-not $failed) { throw 'Offline download falsely succeeded' }
if ([IO.File]::ReadAllText('C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\mvp1d-controlled-retest\test_offline_download_preserve1\installer.exe') -ne 'previous-verified-artifact') { throw 'Retry destroyed existing artifact' }
