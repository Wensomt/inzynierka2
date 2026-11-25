
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
function Assert-LocalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -notmatch '^[A-Za-z]:\\') { throw "Wymagana lokalna sciezka: $Path" }
    while ($full) {
        $item = $null
        try { $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { }
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Niedozwolony reparse point: $full" }
        $full = [IO.Path]::GetDirectoryName($full)
    }
}
function Get-InstallerDownloadPath {
    param([Parameter(Mandatory = $true)][string]$FileName)
    $downloadDirectory = Join-Path $script:ProjectRoot ".install\downloads"
    Assert-LocalPath $downloadDirectory
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    return Join-Path $downloadDirectory $FileName
}
$script:ProjectRoot = 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\mvp1d-controlled-retest\test_download_directory_belong1\Zażółć O''RAN'
$env:LOCALAPPDATA = 'C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\mvp1d-controlled-retest\test_download_directory_belong1\foreign-profile'
$p = Get-InstallerDownloadPath -FileName 'probe.exe'
if ($p -ne (Join-Path $script:ProjectRoot '.install\downloads\probe.exe')) { throw 'Download escaped product root' }
