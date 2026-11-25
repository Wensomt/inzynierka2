
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
function New-RuntimeState {
    param($Contract)
    $root = $script:ProjectRoot
    $lock = Join-Path $root 'requirements-windows.lock'
    Assert-LocalPath $lock
    $data = Join-Path $root 'data'
    $config = Join-Path $root 'config.json'
    Assert-LocalPath $data
    Assert-LocalPath $config
    if ((Test-Path -LiteralPath $data) -and -not (Test-Path -LiteralPath $data -PathType Container)) { throw 'Kolizja katalogu data.' }
    return [ordered]@{
        schema_version = 1; release_id = $Contract.Manifest.release_id; source_manifest_sha256 = $Contract.Hash;
        dependency_lock_sha256 = (Get-FileHash -LiteralPath $lock -Algorithm SHA256).Hash.ToLowerInvariant();
        root = $root; venv_path = Join-Path $root '.venv'; config_path = $config; data_path = $data;
        stage = 'preflight'; needs_restart = $false; python_path = $null; python_sha256 = $null; venv_python_sha256 = $null;
        python_version = $null; docker_context = $null; docker_endpoint = $null; image_id = $null; image_name = Get-ConfiguredImageName;
        repo_digest = $null; os = 'linux'; architecture = 'amd64'; provenance_sha256 = $Contract.Hash;
        restart_boot = $null; timestamps = [ordered]@{ created_utc = [datetime]::UtcNow.ToString('o'); updated_utc = $null };
    }
}
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
function Read-RuntimeState {
    param($Expected)
    Assert-LocalPath $script:ReceiptPath
    if (-not (Test-Path -LiteralPath $script:ReceiptPath)) { return $null }
    $r = Get-Content -LiteralPath $script:ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($key in $Expected.Keys) {
        if (-not $r.PSObject.Properties[$key]) { throw "Niekompletny runtime.json: $key." }
    }
    foreach ($key in @('schema_version','release_id','source_manifest_sha256','dependency_lock_sha256','root','venv_path',
        'config_path','data_path','image_name','os','architecture','provenance_sha256')) {
        if ($r.$key -ne $Expected[$key]) { throw "Obcy lub nieaktualny runtime.json: $key. Dane pozostaly bez zmian." }
    }
    if ($r.stage -notin @('wsl','backend','ready') -or $r.needs_restart -isnot [bool] -or
        $r.needs_restart -ne ($r.stage -eq 'wsl') -or -not $r.timestamps.created_utc -or -not $r.timestamps.updated_utc -or
        ($r.stage -eq 'wsl' -and -not $r.restart_boot) -or
        ($r.stage -in @('backend','ready') -and ($r.python_version -notmatch '^3\.13\.\d+$' -or $r.python_sha256 -notmatch '^[0-9a-f]{64}$' -or
            $r.venv_python_sha256 -notmatch '^[0-9a-f]{64}$' -or
            $r.python_path -notmatch '^[A-Za-z]:\\.*\\python\.exe$')) -or
        ($r.stage -eq 'ready' -and ($r.image_id -cnotmatch '^sha256:[0-9a-f]{64}$' -or
            -not $r.docker_context -or -not $r.docker_endpoint))) {
        throw 'Niespojny stan runtime.json.'
    }
    return $r
}

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\codex-full-fff469841cd04a27942eba7fddbb1b5a\test_changed_root_receipt_does0'
$script:ReceiptPath=Join-Path $script:ProjectRoot 'runtime.json'
function Get-ConfiguredImageName { 'controlled:latest' }
$contract=@{Hash='fixture';Manifest=@{release_id='fixture'}}
$expected=New-RuntimeState $contract
$state=New-RuntimeState $contract
$state.stage='wsl'; $state.needs_restart=$true; $state.restart_boot='fixture'
Save-RuntimeState $state
if ((Read-RuntimeState $expected).stage -ne 'wsl') { throw 'Valid fixture receipt could not be read' }
$state.root=Join-Path $script:ProjectRoot 'foreign'
Save-RuntimeState $state
$rejected=$false
try { $null=Read-RuntimeState $expected } catch { $rejected=$_.Exception.Message -like '*runtime.json: root*' }
if (-not $rejected) { throw 'Foreign runtime root accepted' }
