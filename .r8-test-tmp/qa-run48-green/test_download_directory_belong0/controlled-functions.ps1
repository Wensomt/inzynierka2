
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
function Get-InstallerDownloadPath {
    param([Parameter(Mandatory = $true)][string]$FileName)
    $downloadDirectory = Join-Path $script:ProjectRoot ".install\downloads"
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    return Join-Path $downloadDirectory $FileName
}
$script:ProjectRoot = 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\qa-run48-green\test_download_directory_belong0\O-RAN Lab'
$env:LOCALAPPDATA = 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\qa-run48-green\test_download_directory_belong0\foreign-profile'
$p = Get-InstallerDownloadPath -FileName 'probe.exe'
if ($p -ne (Join-Path $script:ProjectRoot '.install\downloads\probe.exe')) { throw 'Download escaped product root' }
