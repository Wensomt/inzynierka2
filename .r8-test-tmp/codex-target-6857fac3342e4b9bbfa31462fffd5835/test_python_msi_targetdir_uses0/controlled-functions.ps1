
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
function Install-PythonFromOfficialSource {
    Write-Host "Nie znaleziono zgodnego Pythona. Weryfikuje oficjalny instalator Python 3.13.16."
    $installer = Get-InstallerDownloadPath -FileName "python-3.13.16-amd64.exe"
    if (-not (Test-Path -LiteralPath $installer)) {
        Save-InstallerDownload -Uri $script:PythonInstallerUrl -Destination $installer -ExpectedSha256 $script:PythonInstallerSha256
    }
    Assert-InstallerIntegrity -Path $installer -ExpectedSha256 $script:PythonInstallerSha256

    $pythonDirectory = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313"
    $arguments = @(
        "/quiet", "InstallAllUsers=0", "TargetDir=`"$pythonDirectory`"",
        "Include_launcher=1", "Include_pip=1", "Include_tcltk=1",
        "Include_test=0", "PrependPath=0", "Shortcuts=0"
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Instalator Pythona zakończył się kodem $($process.ExitCode)."
    }
    Update-SessionPath
}

$env:LOCALAPPDATA='C:\Users\Zażółć User'
$script:PythonInstallerSha256='controlled-hash'
function Get-InstallerDownloadPath { return 'C:\controlled\python.exe' }
function Save-InstallerDownload { }
function Assert-InstallerIntegrity { }
function Update-SessionPath { }
$script:captured=$null
function Start-Process { param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
    $script:captured=@($ArgumentList); return [pscustomobject]@{ExitCode=0}
}
Install-PythonFromOfficialSource
$target=@($script:captured | Where-Object { $_ -like 'TargetDir=*' })
if ($target.Count -ne 1) { throw 'TargetDir missing or duplicated' }
$value=$target[0].Substring(10).Trim('"')
if ($value.Contains('/')) { throw 'MSI TargetDir contains forward slash' }
if ($value -ne [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313'))) { throw 'MSI TargetDir changed profile' }
if ($target[0] -notmatch '^TargetDir=".+"$') { throw 'MSI TargetDir lost quoting' }
