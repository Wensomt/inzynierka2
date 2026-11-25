
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
function Assert-InstallerIntegrity {
    param([string]$Path, [string]$ExpectedSha256)
    $file = Get-Item -LiteralPath $Path -Force
    if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Oczekiwano zwyklego pliku instalatora: $Path"
    }
    if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $ExpectedSha256) {
        throw "Suma SHA-256 instalatora jest niezgodna: $Path"
    }
    if ((Get-AuthenticodeSignature -LiteralPath $Path).Status -ne 'Valid') {
        throw "Nieprawidlowy podpis Authenticode instalatora: $Path"
    }
}
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
function Get-AuthenticodeSignature { param($LiteralPath)
    if ($LiteralPath -notlike '*.partial.exe') { throw 'Signature did not check staging file' }
    [pscustomobject]@{Status='Valid'}
}
function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing,$MaximumRedirection)
    [IO.File]::WriteAllText($OutFile,('R' * 1MB),[Text.Encoding]::ASCII)
}
$rejected=$false
try { Save-InstallerDownload -Uri 'https://invalid.example/probe' -Destination 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\codex-target-6857fac3342e4b9bbfa31462fffd5835\test_completed_download_is_ver1\installer.exe' -ExpectedSha256 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' } catch {
    if ($_.Exception.Message -notmatch 'SHA-256|Authenticode') { throw }
    $rejected=$true
}
if ($rejected -eq $false) { throw 'Unexpected verification result' }
