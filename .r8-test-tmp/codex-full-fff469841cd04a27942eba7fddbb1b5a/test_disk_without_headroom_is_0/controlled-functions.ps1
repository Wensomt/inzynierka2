
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
function Assert-InstallDisk {
    param($Drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($script:ProjectRoot)))
    if ($Drive.DriveType -ne [IO.DriveType]::Fixed -or $Drive.AvailableFreeSpace -lt 1GB) {
        throw 'Wymagany lokalny dysk z co najmniej 1 GiB wolnego miejsca na pliki startowe (nie jest to budzet backendu).'
    }
}

$rejected=$false
try { Assert-InstallDisk -Drive ([pscustomobject]@{DriveType=[IO.DriveType]::Fixed;AvailableFreeSpace=0}) }
catch { $rejected=$_.Exception.Message -like '*wolnego miejsca*' }
if (-not $rejected) { throw 'Insufficient space accepted' }
Assert-InstallDisk -Drive ([pscustomobject]@{DriveType=[IO.DriveType]::Fixed;AvailableFreeSpace=2GB})
