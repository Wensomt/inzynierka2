
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
function Enter-InstallLock {
    param([switch]$ExistingOnly)
    $state = Join-Path $script:ProjectRoot '.install'
    $lock = Join-Path $state 'install.lock'
    Assert-LocalPath $lock
    $mode = [IO.FileMode]::Open
    $owner = Join-Path $state 'owner.txt'
    $identity = "$($script:ProjectRoot)`nportable-20261114-v1"
    Assert-LocalPath $owner
    if (-not $ExistingOnly -and -not (Test-Path -LiteralPath $state)) {
        New-Item -ItemType Directory -Path $state | Out-Null
        $claim = [IO.File]::Open($owner,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes($identity)
            $claim.Write($bytes,0,$bytes.Length)
            $claim.Flush($true)
        }
        finally { $claim.Dispose() }
    }
    if (-not (Test-Path -LiteralPath $owner -PathType Leaf) -or [IO.File]::ReadAllText($owner) -ne $identity) {
        throw 'Obcy lub niekompletny katalog .install; zachowano zawartosc.'
    }
    if (-not $ExistingOnly) {
        Assert-LocalPath $lock
        $mode = [IO.FileMode]::OpenOrCreate
    }
    try { return [IO.File]::Open($lock, $mode, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw "Nie mozna uzyskac blokady instalacji: $lock. $($_.Exception.Message)" }
}

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\mvp1-controlled-first\test_root_preflight_does_not_c1'
$script:fault='access-denied'
function Get-Item { param($LiteralPath,[switch]$Force,$ErrorAction)
    if ($script:fault -eq 'reparse') { return [pscustomobject]@{Attributes=[IO.FileAttributes]::ReparsePoint} }
    throw [UnauthorizedAccessException]::new('CONTROLLED_PERMISSION')
}
$rejected=$false
try { $null=Enter-InstallLock } catch { $rejected=$_.Exception.Message -match 'reparse point|CONTROLLED_PERMISSION' }
if (-not $rejected) { throw 'Path error did not stop install state' }
