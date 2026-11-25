
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
function Invoke-Setup {
    $contract = Get-SourceContract
    $state = New-RuntimeState $contract
    $prior = Read-RuntimeState $state
    if ($prior) { $state.timestamps.created_utc = $prior.timestamps.created_utc }
    if ($prior -and $prior.stage -eq 'ready') {
        if (($ImageMode -eq 'Local' -and $LocalImageId -ne $prior.image_id) -or
            ($LocalImageId -and $ImageMode -ne 'Local')) { throw 'Nowy wybor obrazu koliduje z gotowa instalacja.' }
        Ensure-DockerDesktop
        Select-LocalDocker
        Assert-ReadyRuntime $prior
        if ($SkipLaunch) { return 0 }
        return (Invoke-ReadyLaunch $prior)
    }
    if (-not $ImageMode -or ($ImageMode -eq 'Local' -and -not $LocalImageId) -or ($ImageMode -eq 'Source' -and $LocalImageId)) {
        throw 'Wybierz jawnie -ImageMode Source lub -ImageMode Local -LocalImageId sha256:...'
    }
    $boot = ([datetime]$script:TargetOs.LastBootUpTime).ToUniversalTime().ToString('o')
    if ($prior -and $prior.stage -eq 'wsl' -and $prior.restart_boot -eq $boot) { return 2 }
    Ensure-Wsl2
    if ($script:RebootRequired) {
        if ($prior -and $prior.stage -eq 'backend') { return 2 } # nie trac progresu backendu
        $state.stage = 'wsl'
        $state.needs_restart = $true
        $state.restart_boot = $boot
        Save-RuntimeState $state
        Write-Host 'Wymagany restart Windows. Po restarcie ponow ten sam jawny wybor instalacji.'
        return 2
    }

    Ensure-DockerDesktop -AllowInstall:$InstallDocker
    Select-LocalDocker
    if ($prior -and $prior.stage -eq 'backend') {
        Assert-ReceiptPython $prior
        $state.python_path = $prior.python_path
        $state.python_sha256 = $prior.python_sha256
        $state.venv_python_sha256 = $prior.venv_python_sha256
        $state.python_version = $prior.python_version
    }
    else {
        Ensure-PythonAndDependencies
        $state.python_path = $script:BasePython.Interpreter
        $state.python_sha256 = (Get-FileHash -LiteralPath $state.python_path -Algorithm SHA256).Hash.ToLowerInvariant()
        $state.venv_python_sha256 = (Get-FileHash -LiteralPath $script:VenvPython -Algorithm SHA256).Hash.ToLowerInvariant()
        $state.python_version = $script:BasePython.Version
    }
    $state.stage = 'backend'
    Save-RuntimeState $state
    $image = Ensure-SelectedSimulator -Mode $ImageMode -ExpectedId $LocalImageId -Contract $contract
    $state.docker_context = $script:DockerContext
    $state.docker_endpoint = $script:DockerEndpoint
    $state.image_id = $image.Id
    $state.repo_digest = $image.RepoDigests | Select-Object -First 1
    $state.stage = 'ready'
    Save-RuntimeState $state
    if ($SkipLaunch) { return 0 }
    return (Invoke-ReadyLaunch $state)
}

$env:ORAN_LAB_IMAGE_REF=$null
$ImageMode='Source'
$script:TargetOs=[pscustomobject]@{LastBootUpTime=[datetime]'2026-11-22T10:00:00Z'}
$script:RebootRequired=$false
$script:later=0
function Get-SourceContract { [pscustomobject]@{Hash='controlled'} }
function New-RuntimeState { @{timestamps=@{created_utc='controlled'}} }
function Read-RuntimeState { return $null }
function Ensure-Wsl2 { }
function Ensure-PythonAndDependencies { $script:later++; $script:BasePython=[pscustomobject]@{Interpreter='controlled';Version='3.13.16'} }
function Get-FileHash { [pscustomobject]@{Hash='controlled'} }
function Ensure-DockerDesktop { $script:later++ }
function Select-LocalDocker { $script:later++ }
function Ensure-SelectedSimulator { $script:later++; [pscustomobject]@{Id='controlled';RepoDigests=@()} }
function Save-RuntimeState { param($State); $script:saved=$State }
function Invoke-ReadyLaunch { throw 'GUI must not start' }

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\codex-full-fff469841cd04a27942eba7fddbb1b5a\test_restart_persists_resume_r0'
$script:ReceiptPath=Join-Path $script:ProjectRoot 'runtime.json'
function Save-RuntimeState {
    param($State)
    $State.timestamps.updated_utc = [datetime]::UtcNow.ToString('o')
    Assert-LocalPath $script:ReceiptPath
    $temporary = "$($script:ReceiptPath).$([guid]::NewGuid().ToString('N')).tmp"
    $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($State | ConvertTo-Json -Depth 5))
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
    try {
        if (Test-Path -LiteralPath $script:ReceiptPath) {
            # Keep the previous receipt recoverable if ReplaceFile fails after renaming it.
            $backup = [IO.Path]::ChangeExtension($temporary, 'bak')
            try { [IO.File]::Replace($temporary, $script:ReceiptPath, $backup) }
            catch {
                if (Test-Path -LiteralPath $backup -ErrorAction Stop) {
                    try { [IO.File]::Move($backup, $script:ReceiptPath) }
                    catch { throw "Nie przywrocono poprzedniego runtime.json; zachowano kopie: $backup. $($_.Exception.Message)" }
                }
                throw
            }
            try { [IO.File]::Delete($backup) }
            catch { Write-Warning "Zachowano kopie poprzedniego runtime.json: $backup. $($_.Exception.Message)" }
        }
        else { [IO.File]::Move($temporary, $script:ReceiptPath) }
    }
    finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
}
$SkipLaunch=$true
function Ensure-Wsl2 { $script:RebootRequired=$true }
$null=Invoke-Setup
$p=$script:ReceiptPath
if (-not (Test-Path -LiteralPath $p)) { throw 'Restart returned without persistent resume receipt' }
$r=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json
if (-not $r.needs_restart -or -not $r.stage) { throw 'Receipt lost stage or restart flag' }
