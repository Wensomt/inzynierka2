param([switch]$AllowWindowsHome)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$lock = $null
$exitCode = 1
try {
    . (Join-Path $root 'install_windows.ps1') -LibraryOnly -AllowWindowsHome:$AllowWindowsHome
    $null = Assert-SupportedHost -AllowWindowsHome:$AllowWindowsHome
    Assert-LocalDockerEnvironment
    $lock = Enter-InstallLock -ExistingOnly
    $script:ReceiptPath = Join-Path (Join-Path $root '.install') 'runtime.json'
    $contract = Get-SourceContract
    $receipt = Read-RuntimeState (New-RuntimeState $contract)
    if (-not $receipt -or $receipt.stage -ne 'ready') { throw 'Dokoncz instalacje przed uruchomieniem.' }
    Ensure-DockerDesktop
    Select-LocalDocker
    Assert-ReadyRuntime $receipt
    $exitCode = Invoke-ReadyLaunch $receipt
}
catch { Write-Host "Nie uruchomiono aplikacji: $($_.Exception.Message)" -ForegroundColor Red }
finally { if ($lock) { $lock.Dispose() } }
exit $exitCode
