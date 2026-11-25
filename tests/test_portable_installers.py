"""Controlled installer function tests. Never execute installer top-level code.
External system/network/container/GUI operations are replaced at their boundary.
"""
import hashlib
import json
import pytest
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def ps_function(name, filename='install_windows.ps1'):
    text = (ROOT / filename).read_text(encoding='utf-8-sig')
    match = re.search(r'^function ' + re.escape(name) + r'\s*\{.*?^\}', text, re.M | re.S)
    assert match, f'Missing installer function {name}'
    return match.group()


def run_ps(tmp_path, functions, body, filename='install_windows.ps1'):
    harness = tmp_path / 'controlled-functions.ps1'
    guards = '''
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
'''
    harness.write_text(guards + '\n'.join(ps_function(n, filename) for n in functions) + '\n' + body, encoding='utf-8-sig')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(harness)], cwd=tmp_path, capture_output=True, timeout=30)
    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    assert '__CONTROLLED_ENTERED__' in output, 'INFRASTRUCTURE, not RED: ' + output
    assert 'UNMOCKED' not in output, 'Unexpected external seam, not RED: ' + output
    return result


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def check_ps(tmp_path, functions, body):
    result = run_ps(tmp_path, functions, body)
    assert result.returncode == 0, (result.stdout + result.stderr).decode('utf-8', errors='replace')


@pytest.mark.parametrize('name', ['O-RAN Lab', "Zażółć O'RAN"])
def test_download_directory_belongs_to_product_root(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    check_ps(tmp_path, ['Assert-LocalPath', 'Get-InstallerDownloadPath'], f"$script:ProjectRoot = {quote(root)}\n$env:LOCALAPPDATA = {quote(tmp_path / 'foreign-profile')}\n$p = Get-InstallerDownloadPath -FileName 'probe.exe'\nif ($p -ne (Join-Path $script:ProjectRoot '.install\\downloads\\probe.exe')) {{ throw 'Download escaped product root' }}\n")


@pytest.mark.parametrize('profile', [r'C:\Users\Zażółć User', 'D:/Profile/O RAN'])
def test_python_msi_targetdir_uses_native_windows_separators(tmp_path, profile):
    # Production constructs ArgumentList. These profile strings never cause writes.
    check_ps(tmp_path, ['Install-PythonFromOfficialSource'], rf'''
$env:LOCALAPPDATA={quote(profile)}
$script:PythonInstallerSha256='controlled-hash'
function Get-InstallerDownloadPath {{ return 'C:\controlled\python.exe' }}
function Save-InstallerDownload {{ }}
function Assert-InstallerIntegrity {{ }}
function Update-SessionPath {{ }}
$script:captured=$null
function Start-Process {{ param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
    $script:captured=@($ArgumentList); return [pscustomobject]@{{ExitCode=0}}
}}
Install-PythonFromOfficialSource
$target=@($script:captured | Where-Object {{ $_ -like 'TargetDir=*' }})
if ($target.Count -ne 1) {{ throw 'TargetDir missing or duplicated' }}
$value=$target[0].Substring(10).Trim('"')
if ($value.Contains('/')) {{ throw 'MSI TargetDir contains forward slash' }}
if ($value -ne [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313'))) {{ throw 'MSI TargetDir changed profile' }}
if ($target[0] -notmatch '^TargetDir=".+"$') {{ throw 'MSI TargetDir lost quoting' }}
''')


def test_python_installer_rejects_hash_mismatch_before_process(tmp_path):
    check_ps(tmp_path, ['Install-PythonFromOfficialSource'], r'''
$script:PythonInstallerSha256='expected'
function Get-InstallerDownloadPath { return 'C:\controlled\python.exe' }
function Save-InstallerDownload { }
function Assert-InstallerIntegrity { throw 'SHA-256 mismatch' }
$rejected=$false
try { Install-PythonFromOfficialSource } catch { $rejected=$_.Exception.Message -like '*SHA-256*' }
if (-not $rejected) { throw 'Hash mismatch not rejected before launch' }
''')


@pytest.mark.parametrize('version,machine,bits,accepted', [
    ('3.12.9', 'AMD64', 64, False), ('3.13.16', 'AMD64', 64, True),
    ('3.13.16', 'x86', 32, False), ('3.14.9', 'AMD64', 64, False),
])
def test_python_candidate_obeys_313_contract(tmp_path, version, machine, bits, accepted):
    check_ps(tmp_path, ['Test-PythonCandidate'], rf'''
function Controlled-Python {{ $global:LASTEXITCODE=0; '{{"executable":"C:\\controlled\\python.exe","version":"{version}","implementation":"cpython","machine":"{machine}","bits":{bits},"prefix":"C:\\controlled","base_prefix":"C:\\controlled"}}' }}
$candidate=Test-PythonCandidate -Executable 'Controlled-Python'
if ([bool]$candidate -ne ${str(accepted).lower()}) {{ throw 'Python version violates 3.13 contract' }}
''')


@pytest.mark.parametrize('sku,build,arch,allow_home,accepted', [
    (48, 19045, 9, False, False), (101, 26200, 9, False, False), (48, 22000, 9, False, False),
    (48, 26200, 12, False, False), (48, 26200, 9, False, True), (4, 26100, 9, False, True),
    (4, 22631, 9, False, False), (101, 26300, 9, False, False), (101, 26300, 9, True, True),
    (101, 26200, 9, True, False),
])
def test_host_preflight_rejects_out_of_target(tmp_path, sku, build, arch, allow_home, accepted):
    check_ps(tmp_path, ['Assert-SupportedHost'], f'''
function Get-CimInstance {{ param($ClassName)
    if ($ClassName -eq 'Win32_OperatingSystem') {{
        return [pscustomobject]@{{ProductType=1;OperatingSystemSKU={sku};BuildNumber={build};TotalVisibleMemorySize=16777216}}
    }}
    return [pscustomobject]@{{Architecture={arch};SecondLevelAddressTranslationExtensions=$true;VirtualizationFirmwareEnabled=$true}}
}}
$env:PROCESSOR_ARCHITECTURE='AMD64'
$rejected=$false
try {{ $null=Assert-SupportedHost -Today ([datetime]'2026-11-22') -AllowWindowsHome:${str(allow_home).lower()} }} catch {{ $rejected=$_.Exception.Message -match 'SKU=|procesor' }}
if ($rejected -eq ${str(accepted).lower()}) {{ throw 'Wrong host eligibility' }}
''')


@pytest.mark.parametrize('existing', [False, True], ids=['partial-not-published', 'existing-preserved'])
def test_offline_download_preserves_final_destination(tmp_path, existing):
    dest = tmp_path / 'installer.exe'
    if existing:
        dest.write_bytes(b'previous-verified-artifact')
    check = (f"if ([IO.File]::ReadAllText({quote(dest)}) -ne 'previous-verified-artifact') {{ throw 'Retry destroyed existing artifact' }}"
             if existing else f"if (Test-Path -LiteralPath {quote(dest)}) {{ throw 'Partial download published as final installer' }}")
    check_ps(tmp_path, ['Save-InstallerDownload'], f'''
function Start-Sleep {{ }}
function Invoke-WebRequest {{ param($Uri,$OutFile,[switch]$UseBasicParsing)
    [IO.File]::WriteAllText($OutFile,'interrupted-body'); throw 'CONTROLLED_OFFLINE'
}}
$failed=$false
try {{ Save-InstallerDownload -Uri 'https://invalid.example/probe' -Destination {quote(dest)} -ExpectedSha256 '{'b' * 64}' }} catch {{ $failed=$_.Exception.Message -like '*CONTROLLED_OFFLINE*' }}
if (-not $failed) {{ throw 'Offline download falsely succeeded' }}
{check}
''')


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('good_hash,signature', [(False, 'Valid'), (True, 'NotSigned'), (True, 'Valid')])
def test_completed_download_is_verified_before_publish(tmp_path, existing, good_hash, signature):
    dest = tmp_path / 'installer.exe'
    old = b'prior verified artifact'
    body = b'R' * (1024 * 1024)
    if existing:
        dest.write_bytes(old)
    expected = hashlib.sha256(body).hexdigest() if good_hash else 'b' * 64
    allowed = good_hash and signature == 'Valid'
    check_ps(tmp_path, ['Assert-InstallerIntegrity', 'Save-InstallerDownload'], f'''
function Start-Sleep {{ }}
function Get-AuthenticodeSignature {{ param($LiteralPath)
    if ($LiteralPath -notlike '*.partial.exe') {{ throw 'Signature did not check staging file' }}
    [pscustomobject]@{{Status='{signature}'}}
}}
function Invoke-WebRequest {{ param($Uri,$OutFile,[switch]$UseBasicParsing,$MaximumRedirection)
    [IO.File]::WriteAllText($OutFile,('R' * 1MB),[Text.Encoding]::ASCII)
}}
$rejected=$false
try {{ Save-InstallerDownload -Uri 'https://invalid.example/probe' -Destination {quote(dest)} -ExpectedSha256 '{expected}' }} catch {{
    if ($_.Exception.Message -notmatch 'SHA-256|Authenticode') {{ throw }}
    $rejected=$true
}}
if ($rejected -eq ${str(allowed).lower()}) {{ throw 'Unexpected verification result' }}
''')
    assert list(tmp_path.glob('*.partial.exe')) == []
    if allowed:
        assert dest.read_bytes() == body
    elif existing:
        assert dest.read_bytes() == old
    else:
        assert not dest.exists()


def test_lingering_archive_does_not_override_explicit_local_image(tmp_path):
    root = tmp_path / 'root'
    (root / 'resources').mkdir(parents=True)
    (root / 'config.json').write_text(json.dumps({'lab': {'simulator_image': 'controlled-image'}}))
    (root / 'resources/wcnc_tap.tar').write_bytes(b'controlled-not-a-real-image')
    check_ps(tmp_path, ['Assert-ImageIdentity', 'Ensure-SelectedSimulator'], f'''
$script:ProjectRoot={quote(root)}
$script:id='sha256:{'1' * 64}'
$script:hash='{'a' * 64}'
function Get-ConfiguredImageName {{ 'controlled-image:latest' }}
function Get-LocalImage {{
    [pscustomobject]@{{Id=$script:id;Os='linux';Architecture='amd64';
    Config=[pscustomobject]@{{Labels=[pscustomobject]@{{'org.oran-lab.source-manifest-sha256'=$script:hash}}}}}}
}}
function Read-Docker {{ throw 'UNMOCKED Docker' }}
$image=Ensure-SelectedSimulator -Mode Local -ExpectedId $script:id -Contract @{{Hash=$script:hash}}
if ($image.Id -ne $script:id) {{ throw 'Explicit local image was not selected' }}
''')


@pytest.mark.parametrize('architecture', ['amd64', 'arm64'])
def test_source_mode_rejects_any_existing_tag_before_mutation(tmp_path, architecture):
    check_ps(tmp_path, ['Ensure-SelectedSimulator'], f'''
$script:ProjectRoot={quote(tmp_path)}
function Get-ConfiguredImageName {{ 'occupied:latest' }}
function Get-LocalImage {{ [pscustomobject]@{{Architecture='{architecture}'}} }}
function Read-Docker {{ throw 'UNMOCKED Docker' }}
$rejected=$false
try {{ Ensure-SelectedSimulator -Mode Source -Contract @{{Hash='controlled'}} }} catch {{ $rejected=$_.Exception.Message -like '*Kolizja*' }}
if (-not $rejected) {{ throw 'Occupied tag was not refused' }}
''')
    assert not (tmp_path / '.install').exists()


SETUP_BOUNDARIES = '''
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
'''


def test_skiplaunch_does_not_start_gui(tmp_path):
    check_ps(tmp_path, ['Invoke-Setup'], SETUP_BOUNDARIES + f'''
$script:ProjectRoot={quote(tmp_path)}
$SkipLaunch=$true
$code=Invoke-Setup
if ($code -ne 0 -or $script:later -ne 4) {{ throw 'SkipLaunch failed mocked stages' }}
''')


def test_restart_stops_before_later_stages(tmp_path):
    check_ps(tmp_path, ['Invoke-Setup'], SETUP_BOUNDARIES + f"$script:ProjectRoot={quote(tmp_path)}\n" + '''
$SkipLaunch=$true
function Ensure-Wsl2 { $script:RebootRequired=$true }
$code=Invoke-Setup
if ($code -ne 2 -or $script:later -ne 0) { throw 'Restart continued to later stages' }
''')


def test_restart_persists_resume_receipt(tmp_path):
    check_ps(tmp_path, ['Assert-LocalPath', 'Invoke-Setup'], SETUP_BOUNDARIES + rf'''
$script:ProjectRoot={quote(tmp_path)}
$script:ReceiptPath=Join-Path $script:ProjectRoot 'runtime.json'
{ps_function('Save-RuntimeState')}
$SkipLaunch=$true
function Ensure-Wsl2 {{ $script:RebootRequired=$true }}
$null=Invoke-Setup
$p=$script:ReceiptPath
if (-not (Test-Path -LiteralPath $p)) {{ throw 'Restart returned without persistent resume receipt' }}
$r=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json
if (-not $r.needs_restart -or -not $r.stage) {{ throw 'Receipt lost stage or restart flag' }}
''')


def test_missing_docker_does_not_attempt_uncontrolled_installer(tmp_path):
    check_ps(tmp_path, ['Ensure-DockerDesktop'], '''
function Write-Step { }
function Get-DockerExecutable { return $null }
function Get-DockerDesktopExecutable { return $null }
$script:WingetExe=$null
$script:installs=0
function Install-DockerDesktopFromOfficialSource { $script:installs++ }
$rejected=$false
try { Ensure-DockerDesktop } catch { $rejected=$true }
if (-not $rejected -or $script:installs -ne 0) { throw 'Missing Docker attempted uncontrolled installer fallback' }
''')


def test_explicit_docker_install_checks_integrity_before_start(tmp_path):
    check_ps(tmp_path, ['Install-DockerDesktopFromOfficialSource', 'Ensure-DockerDesktop'], '''
function Write-Step { }
function Get-DockerExecutable { if ($script:installed) { 'docker.exe' } }
function Get-DockerDesktopExecutable { if ($script:installed) { 'Docker Desktop.exe' } }
function Get-InstallerDownloadPath { 'installer.exe' }
function Save-InstallerDownload { }
function Assert-InstallerIntegrity { if (-not $script:trusted) { throw 'invalid signature' } }
function Start-Process {
    param($FilePath, [switch]$Wait, [switch]$PassThru)
    if (-not ($Wait -and $PassThru)) { throw 'installer must run interactively and wait' }
    $script:installed=$true
    [pscustomobject]@{ExitCode=0}
}
$script:trusted=$false
$script:installed=$false
try { Ensure-DockerDesktop -AllowInstall } catch { if ($_.Exception.Message -ne 'invalid signature') { throw } }
if ($script:installed) { throw 'started an unverified installer' }
$script:trusted=$true
try { Ensure-DockerDesktop -AllowInstall } catch { if ($_.Exception.Message -notlike '*zaakceptuj warunki*') { throw } }
if (-not $script:installed -or $script:DockerExe) { throw 'Docker must be installed but not ready' }
Ensure-DockerDesktop -AllowInstall
if (-not $script:DockerExe) { throw 'existing Docker was not found on retry' }
''')


def test_wsl_install_only_runs_when_requested_and_defers_to_reboot(tmp_path):
    check_ps(tmp_path, ['Ensure-Wsl2'], '''
function Write-Step { }
function Get-WslVersion { return $null }
function Test-Path { param($LiteralPath) ; $LiteralPath -eq 'wsl.exe' }
function Start-Process {
    param($FilePath, $ArgumentList, $Verb, [switch]$Wait, [switch]$PassThru)
    if ($FilePath -ne 'wsl.exe' -or $Verb -ne 'RunAs' -or -not ($Wait -and $PassThru) -or
        ($ArgumentList -join ' ') -ne '--install --no-distribution --web-download') { throw 'wrong WSL command' }
    $script:started=$true
    [pscustomobject]@{ExitCode=0}
}
$script:WslExe='wsl.exe'
$script:started=$false
$script:RebootRequired=$false
$InstallWsl=$false
try { Ensure-Wsl2 } catch { if ($_.Exception.Message -notlike '*-InstallWsl*') { throw } }
if ($script:started) { throw 'WSL installation without opt-in' }
$InstallWsl=$true
Ensure-Wsl2
if (-not ($script:started -and $script:RebootRequired)) { throw 'missing reboot barrier' }
''')


def test_repeating_source_setup_reuses_validated_ready_runtime(tmp_path):
    check_ps(tmp_path, ['Invoke-Setup'], SETUP_BOUNDARIES + '''
function Read-RuntimeState { @{stage='ready'; image_id='installed'; timestamps=@{created_utc='prior'}} }
function Assert-ReadyRuntime {
    param($Receipt)
    if ($Receipt.image_id -ne 'installed') { throw 'unexpected image' }
    $script:later++
}
$SkipLaunch=$true
if ((Invoke-Setup) -ne 0 -or $script:later -ne 3) { throw 'ready installation was not reused' }
''')


def test_resume_before_reboot_does_not_enter_wsl_or_other_stages(tmp_path):
    check_ps(tmp_path, ['Invoke-Setup'], SETUP_BOUNDARIES + '''
function Read-RuntimeState {
    [pscustomobject]@{stage='wsl'; restart_boot=$script:TargetOs.LastBootUpTime.ToUniversalTime().ToString('o'); timestamps=@{created_utc='prior'}}
}
function Ensure-Wsl2 { throw 'WSL repeated before restart' }
if ((Invoke-Setup) -ne 2 -or $script:later -ne 0) { throw 'Resume lost reboot barrier' }
''')


def test_root_lock_excludes_a_second_writer_and_recovers_after_release(tmp_path):
    check_ps(tmp_path, ['Assert-LocalPath', 'Enter-InstallLock'], f'''
$script:ProjectRoot={quote(tmp_path)}
$first=Enter-InstallLock
try {{
    $rejected=$false
    try {{ $second=Enter-InstallLock; $second.Dispose() }} catch {{ $rejected=$_.Exception.Message -like '*blokady*' }}
    if (-not $rejected) {{ throw 'Acquired concurrent lock' }}
}}
finally {{ $first.Dispose() }}
$after=Enter-InstallLock -ExistingOnly
$after.Dispose()
''')


def test_override_docker_host_refused_before_cli(tmp_path):
    check_ps(tmp_path, ['Assert-LocalDockerEnvironment', 'Select-LocalDocker'], '''
$env:ORAN_LAB_IMAGE_REF=$null
$env:DOCKER_HOST='tcp://invalid.example:2376'
function Read-Docker { throw 'UNMOCKED Docker CLI' }
$rejected=$false
try { Select-LocalDocker } catch { $rejected=$_.Exception.Message -like '*DOCKER_HOST*' }
if (-not $rejected) { throw 'Foreign Docker host accepted' }
''')


@pytest.mark.parametrize('platform,candidate', [('arm64', '1'), ('amd64', '2')])
def test_explicit_local_image_identity_rejects_mismatches(tmp_path, platform, candidate):
    check_ps(tmp_path, ['Assert-ImageIdentity'], f'''
$expected='sha256:{'1' * 64}'
$hash='{'a' * 64}'
$image=[pscustomobject]@{{Id='sha256:{candidate * 64}';Os='linux';Architecture='{platform}';
Config=[pscustomobject]@{{Labels=[pscustomobject]@{{'org.oran-lab.source-manifest-sha256'=$hash}}}}}}
$rejected=$false
try {{ Assert-ImageIdentity $image $expected $hash }} catch {{ $rejected=$_.Exception.Message -like '*tozsamosc*' }}
if (-not $rejected) {{ throw 'Foreign image accepted' }}
''')


@pytest.mark.parametrize('modified', [False, True])
def test_source_contract_uses_only_shipped_resources(tmp_path, modified):
    manifest_path = 'resources/simulator/sources.json'
    dockerfile_path = 'resources/simulator/Dockerfile'
    manifest = json.loads((ROOT / manifest_path).read_bytes())
    for name in [manifest_path, dockerfile_path, *(entry['path'] for entry in manifest['patches'])]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        body = (ROOT / name).read_bytes()
        target.write_bytes(body + b'\n# unexpected\n' if modified and name == dockerfile_path else body)
    check_ps(tmp_path, ['Assert-LocalPath', 'Get-SourceContract'], f'''
$script:ProjectRoot={quote(tmp_path)}
$rejected=$false
try {{ $contract=Get-SourceContract }} catch {{
    if ($_.Exception.Message -notlike '*Dockerfile*') {{ throw }}
    $rejected=$true
}}
if ($rejected -ne ${str(modified).lower()}) {{ throw 'Wrong source integrity result' }}
''')
    assert not (tmp_path / '.simulator-src').exists()


def test_foreign_venv_is_not_executed_or_cleared(tmp_path):
    venv = tmp_path / '.venv'
    venv.mkdir()
    sentinel = venv / 'foreign.txt'
    sentinel.write_bytes(b'keep me')
    check_ps(tmp_path, ['Assert-LocalPath', 'Assert-VenvOwnership', 'Ensure-PythonAndDependencies'], f'''
$script:ProjectRoot={quote(tmp_path)}
function Write-Step {{ }}
function Get-ProjectPython {{ [pscustomobject]@{{Interpreter='UNMOCKED-python.exe';Version='3.13.16'}} }}
$rejected=$false
try {{ Ensure-PythonAndDependencies }} catch {{ $rejected=$_.Exception.Message -like '*Obcy*' }}
if (-not $rejected) {{ throw 'Foreign venv not rejected' }}
''')
    assert sentinel.read_bytes() == b'keep me'


def test_changed_root_receipt_does_not_validate(tmp_path):
    (tmp_path / 'requirements-windows.lock').write_bytes(b'fixture')
    check_ps(tmp_path, ['Assert-LocalPath', 'New-RuntimeState', 'Save-RuntimeState', 'Read-RuntimeState'], f'''
$script:ProjectRoot={quote(tmp_path)}
$script:ReceiptPath=Join-Path $script:ProjectRoot 'runtime.json'
function Get-ConfiguredImageName {{ 'controlled:latest' }}
$contract=@{{Hash='fixture';Manifest=@{{release_id='fixture'}}}}
$expected=New-RuntimeState $contract
$state=New-RuntimeState $contract
$state.stage='wsl'; $state.needs_restart=$true; $state.restart_boot='fixture'
Save-RuntimeState $state
if ((Read-RuntimeState $expected).stage -ne 'wsl') {{ throw 'Valid fixture receipt could not be read' }}
$state.root=Join-Path $script:ProjectRoot 'foreign'
Save-RuntimeState $state
$rejected=$false
try {{ $null=Read-RuntimeState $expected }} catch {{ $rejected=$_.Exception.Message -like '*runtime.json: root*' }}
if (-not $rejected) {{ throw 'Foreign runtime root accepted' }}
''')


@pytest.mark.parametrize('fault', ['reparse', 'access-denied'])
def test_root_preflight_does_not_create_state_after_path_error(tmp_path, fault):
    check_ps(tmp_path, ['Assert-LocalPath', 'Enter-InstallLock'], f'''
$script:ProjectRoot={quote(tmp_path)}
$script:fault={quote(fault)}
function Get-Item {{ param($LiteralPath,[switch]$Force,$ErrorAction)
    if ($script:fault -eq 'reparse') {{ return [pscustomobject]@{{Attributes=[IO.FileAttributes]::ReparsePoint}} }}
    throw [UnauthorizedAccessException]::new('CONTROLLED_PERMISSION')
}}
$rejected=$false
try {{ $null=Enter-InstallLock }} catch {{ $rejected=$_.Exception.Message -match 'reparse point|CONTROLLED_PERMISSION' }}
if (-not $rejected) {{ throw 'Path error did not stop install state' }}
''')
    assert not (tmp_path / '.install').exists()


def test_disk_without_headroom_is_rejected(tmp_path):
    check_ps(tmp_path, ['Assert-InstallDisk'], '''
$rejected=$false
try { Assert-InstallDisk -Drive ([pscustomobject]@{DriveType=[IO.DriveType]::Fixed;AvailableFreeSpace=0}) }
catch { $rejected=$_.Exception.Message -like '*wolnego miejsca*' }
if (-not $rejected) { throw 'Insufficient space accepted' }
Assert-InstallDisk -Drive ([pscustomobject]@{DriveType=[IO.DriveType]::Fixed;AvailableFreeSpace=2GB})
''')


@pytest.mark.parametrize('version,base,accepted', [('3.12.9', 'base', False), ('3.14.1', 'base', False),
                                                   ('3.13.16', 'foreign', False), ('3.13.16', 'base', True)])
def test_venv_identity_matches_selected_python(tmp_path, version, base, accepted):
    check_ps(tmp_path, ['Assert-VenvIdentity'], f'''
$script:VenvDirectory='C:\\mock\\.venv'
$script:VenvPython=Join-Path $script:VenvDirectory 'Scripts\\python.exe'
function Test-PythonCandidate {{
    [pscustomobject]@{{Version='{version}';Base='{base}';Root=$script:VenvDirectory;Interpreter=$script:VenvPython}}
}}
$rejected=$false
try {{ Assert-VenvIdentity -Python ([pscustomobject]@{{Version='3.13.16';Base='base'}}) }}
catch {{ $rejected=$_.Exception.Message -like '*Niezgodny interpreter*' }}
if ($rejected -eq ${str(accepted).lower()}) {{ throw 'Wrong venv identity result' }}
''')
