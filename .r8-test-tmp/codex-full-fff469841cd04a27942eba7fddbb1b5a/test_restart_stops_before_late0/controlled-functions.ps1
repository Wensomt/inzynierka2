
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
$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\codex-full-fff469841cd04a27942eba7fddbb1b5a\test_restart_stops_before_late0'

$SkipLaunch=$true
function Ensure-Wsl2 { $script:RebootRequired=$true }
$code=Invoke-Setup
if ($code -ne 2 -or $script:later -ne 0) { throw 'Restart continued to later stages' }
