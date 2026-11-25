$ErrorActionPreference = "Stop"
function Get-InstallerDownloadPath {
    param([Parameter(Mandatory = $true)][string]$FileName)
    $downloadDirectory = Join-Path $env:LOCALAPPDATA "O-RAN Energy Lab\downloads"
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    return Join-Path $downloadDirectory $FileName
}
$script:ProjectRoot = 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\red-approved-20261116a\test_download_directory_belong0\O-RAN Lab'
$env:LOCALAPPDATA = 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\red-approved-20261116a\test_download_directory_belong0\foreign-profile'
$p = Get-InstallerDownloadPath -FileName 'probe.exe'
if ($p -ne (Join-Path $script:ProjectRoot '.install\downloads\probe.exe')) { throw 'Download escaped product root' }
