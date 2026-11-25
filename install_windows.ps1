param(
    [switch]$SkipLaunch,
    [switch]$InstallPython,
    [switch]$InstallWsl,
    [switch]$InstallDocker,
    [switch]$AllowWindowsHome,
    [ValidateSet('Source','Local')][string]$ImageMode,
    [ValidatePattern('^sha256:[0-9a-f]{64}$')][string]$LocalImageId,
    [switch]$LibraryOnly
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
}
catch {
    # Keep setup usable when PowerShell is hosted without a writable console.
}

$script:ProjectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$script:RebootRequired = $false
$script:TranscriptStarted = $false
$script:WslExe = Join-Path $env:SystemRoot "System32\wsl.exe"
$script:PythonInstallerUrl = "https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe"
$script:PythonInstallerSha256 = "fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b"
# Docker Desktop 4.100.1, Windows x64, published in the Docker release notes.
$script:DockerInstallerUrl = 'https://desktop.docker.com/win/main/amd64/247138/Docker%20Desktop%20Installer.exe'
$script:DockerInstallerSha256 = '98b24fd6f56313899953ca4d2ed05645d69963b598143b75c6585fa4f7f639b2'
function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 promotes native stderr to ErrorRecords. Keep
        # that behavior from terminating setup before LASTEXITCODE is checked.
        $ErrorActionPreference = "Continue"
        if ($Executable -eq $script:WslExe) {
            # wsl.exe can write UTF-16LE even when PowerShell 5.1 decodes the
            # redirected stream using the active console code page. Remove the
            # resulting NULs so both the console and transcript remain readable.
            $nativeOutput = @(& $Executable @Arguments 2>&1)
            $exitCode = $LASTEXITCODE
            foreach ($line in $nativeOutput) {
                $displayLine = ([string]$line -replace "`0", "").TrimEnd()
                if ($displayLine) {
                    Write-Host $displayLine
                }
            }
            return $exitCode
        }
        & $Executable @Arguments | Out-Host
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Get-InstallerDownloadPath {
    param([Parameter(Mandatory = $true)][string]$FileName)
    $downloadDirectory = Join-Path $script:ProjectRoot ".install\downloads"
    Assert-LocalPath $downloadDirectory
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    return Join-Path $downloadDirectory $FileName
}

function Assert-InstallerIntegrity {
    param([string]$Path, [string]$ExpectedSha256)
    $file = Get-Item -LiteralPath $Path -Force
    if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Oczekiwano zwyklego pliku instalatora: $Path"
    }
    if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $ExpectedSha256) {
        throw "Suma SHA-256 instalatora jest niezgodna: $Path"
    }
    if ((Get-AuthenticodeSignature -LiteralPath $Path).Status -ne 'Valid') {
        throw "Nieprawidlowy podpis Authenticode instalatora: $Path"
    }
}

function Save-InstallerDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedSha256
    )
    Write-Host "Pobieram: $Uri"
    if (([Uri]$Uri).Scheme -ne 'https') { throw 'Instalator wymaga HTTPS.' }
    $lastDownloadError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $partial = "$Destination.$([Guid]::NewGuid().ToString('N')).partial.exe"
        $backup = $null
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing -MaximumRedirection 0
            Assert-InstallerIntegrity -Path $partial -ExpectedSha256 $ExpectedSha256
            $target = Get-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            if ($target -and ($target.PSIsContainer -or ($target.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
                throw "Kolizja celu instalatora: $Destination"
            }
            if ($target) {
                # Use a real sibling backup: PowerShell can bind $null as an empty string,
                # and ReplaceFile can lose the old name on failure without a backup.
                $backup = [IO.Path]::ChangeExtension($partial, 'bak')
                [IO.File]::Replace($partial, $Destination, $backup)
            }
            else { [IO.File]::Move($partial, $Destination) }
            $lastDownloadError = $null
            break
        }
        catch {
            if ($backup -and (Test-Path -LiteralPath $backup -ErrorAction Stop)) {
                # Stop retrying after a partial replace; never overwrite a still-present target.
                try { [IO.File]::Move($backup, $Destination) }
                catch { throw "Nie przywrocono poprzedniego instalatora; zachowano kopie: $backup. $($_.Exception.Message)" }
                throw
            }
            $lastDownloadError = $_
            if ($attempt -lt 3) {
                $delaySeconds = 3 * $attempt
                Write-Warning "Pobieranie nie powiodło się (próba $attempt/3). Ponawiam za $delaySeconds s: $($_.Exception.Message)"
                Start-Sleep -Seconds $delaySeconds
            }
        }
        finally {
            if ($partial -and (Test-Path -LiteralPath $partial)) {
                Remove-Item -LiteralPath $partial -Force
            }
        }
    }
    if (-not $lastDownloadError -and $backup) {
        try { [IO.File]::Delete($backup) }
        catch { Write-Warning "Zachowano kopie poprzedniego instalatora: $backup. $($_.Exception.Message)" }
    }
    if ($lastDownloadError) {
        throw "Nie udało się pobrać pliku po 3 próbach: $Uri. Sprawdź połączenie z Internetem i DNS, a potem uruchom install_windows.bat ponownie. Szczegóły: $($lastDownloadError.Exception.Message)"
    }

}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Update-SessionPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $parts = @($userPath, $machinePath, $env:Path) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $env:Path = ($parts -join ";")
}

function Get-ExecutablePath {
    param([Parameter(Mandatory = $true)][string]$Name)
    $entry = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($entry) {
        return $entry.Source
    }
    return $null
}

function Get-WslVersion {
    if (-not (Test-Path -LiteralPath $script:WslExe)) {
        return $null
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $script:WslExe --version 2>&1)
        $exitCode = $LASTEXITCODE
    }
    catch {
        return $null
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0 -or $output.Count -eq 0) {
        return $null
    }
    # WSL may emit UTF-16LE while Windows PowerShell 5.1 decodes the native
    # output as the active console code page, leaving a NUL between characters.
    # Strip those NULs before parsing so an installed WSL is not mistaken for
    # a missing one on every installer run.
    $firstLine = (([string]$output[0]) -replace "`0", "").Trim()
    if ($firstLine -match "(\d+\.\d+\.\d+)") {
        try {
            return [Version]$Matches[1]
        }
        catch {
            return $null
        }
    }
    return $null
}

function Ensure-Wsl2 {
    Write-Step "Sprawdzanie WSL2"
    if (-not (Test-Path -LiteralPath $script:WslExe)) {
        throw 'Nie znaleziono wsl.exe. Sprawdz wymagania Windows i instrukcje Microsoft: https://learn.microsoft.com/windows/wsl/install'
    }

    # Windows may report an installed WSL package before its optional features
    # have taken effect. Do not start Docker's installer until that reboot is done.
    $restartPendingKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"
    )
    foreach ($keyPath in $restartPendingKeys) {
        if (Test-Path -LiteralPath $keyPath) {
            Write-Host "Windows ma oczekujący restart systemowy ($keyPath). Najpierw uruchom ponownie komputer."
            $script:RebootRequired = $true
            return
        }
    }

    $version = Get-WslVersion
    if (-not $version -or $version -lt [Version]'2.1.5') {
        if (-not $InstallWsl) {
            throw 'Wymagany WSL2 (min. 2.1.5). Uzyj -InstallWsl lub postepuj wedlug https://learn.microsoft.com/windows/wsl/install'
        }
        $firstInstall = -not $version
        $arguments = if ($firstInstall) { @('--install','--no-distribution','--web-download') }
                     else { @('--update','--web-download') }
        Write-Host 'Windows poprosi o uprawnienia do przygotowania WSL2.'
        $process = Start-Process -FilePath $script:WslExe -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) {
            throw "Konfiguracja WSL zakonczyla sie kodem $($process.ExitCode)."
        }
        if ($firstInstall -or $process.ExitCode -eq 3010) {
            $script:RebootRequired = $true
            return
        }
        $version = Get-WslVersion
        if (-not $version -or $version -lt [Version]'2.1.5') {
            throw 'Po aktualizacji nie wykryto WSL 2.1.5 lub nowszego. Sprawdz stan WSL i uruchom ponownie Windows.'
        }
    }
    Write-Host "WSL $version jest gotowy."
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Prefix = @()
    )
    $probeArguments = @($Prefix) + @(
        "-I", "-B", "-c",
        "import json,platform,struct,sys,tkinter; print(json.dumps(dict(executable=sys.executable,version=platform.python_version(),implementation=sys.implementation.name,machine=platform.machine(),bits=8*struct.calcsize('P'),prefix=sys.prefix,base_prefix=sys.base_prefix)))"
    )
    $output = @(& $Executable @probeArguments 2>$null)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -or $output.Count -ne 1) {
        return $null
    }
    try { $probe = $output[0] | ConvertFrom-Json } catch { return $null }
    if ($probe.version -notmatch '^3\.13\.\d+$' -or $probe.implementation -ne 'cpython' -or
        $probe.machine -ne 'AMD64' -or $probe.bits -ne 64 -or -not $probe.executable -or
        -not $probe.prefix -or -not $probe.base_prefix) {
        return $null
    }
    return [PSCustomObject]@{
        Executable = $Executable
        Prefix = @($Prefix)
        Version = $probe.version
        Interpreter = $probe.executable
        Root = $probe.prefix
        Base = $probe.base_prefix
    }
}

function Get-ProjectPython {
    $launcherCandidates = @()
    $launcher = Get-ExecutablePath -Name "py.exe"
    if ($launcher) { $launcherCandidates += $launcher }
    $launcherCandidates += @(
        (Join-Path $env:SystemRoot "py.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe")
    )
    $launcherCandidates = $launcherCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique

    foreach ($launcherPath in $launcherCandidates) {
        $candidate = Test-PythonCandidate -Executable $launcherPath -Prefix @('-3.13')
        if ($candidate -and $candidate.Root -eq $candidate.Base) { return $candidate }
    }

    $pythonCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:ProgramFiles "Python313\python.exe")
    )
    foreach ($pythonPath in $pythonCandidates) {
        if (Test-Path -LiteralPath $pythonPath) {
            $candidate = Test-PythonCandidate -Executable $pythonPath
            if ($candidate -and $candidate.Root -eq $candidate.Base) { return $candidate }
        }
    }

    $pathPython = Get-ExecutablePath -Name "python.exe"
    if ($pathPython -and $pathPython -notmatch "\\WindowsApps\\") {
        $candidate = Test-PythonCandidate -Executable $pathPython
        if ($candidate -and $candidate.Root -eq $candidate.Base) { return $candidate }
    }
    return $null
}

function Install-PythonFromOfficialSource {
    Write-Host "Nie znaleziono zgodnego Pythona. Weryfikuje oficjalny instalator Python 3.13.16."
    $installer = Get-InstallerDownloadPath -FileName "python-3.13.16-amd64.exe"
    if (-not (Test-Path -LiteralPath $installer)) {
        Save-InstallerDownload -Uri $script:PythonInstallerUrl -Destination $installer -ExpectedSha256 $script:PythonInstallerSha256
    }
    Assert-InstallerIntegrity -Path $installer -ExpectedSha256 $script:PythonInstallerSha256

    $pythonDirectory = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313"
    $arguments = @(
        "/quiet", "InstallAllUsers=0", "TargetDir=`"$pythonDirectory`"",
        "Include_launcher=1", "Include_pip=1", "Include_tcltk=1",
        "Include_test=0", "PrependPath=0", "Shortcuts=0"
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Instalator Pythona zakończył się kodem $($process.ExitCode)."
    }
    Update-SessionPath
}

function Assert-VenvOwnership {
    param($Python)
    Assert-LocalPath $script:VenvDirectory
    Assert-LocalPath (Split-Path -Parent $script:VenvPython)
    Assert-LocalPath $script:VenvPython
    $marker = Join-Path $script:VenvDirectory '.oran-lab-owner'
    $cfg = Join-Path $script:VenvDirectory 'pyvenv.cfg'
    Assert-LocalPath $marker
    Assert-LocalPath $cfg
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
        [IO.File]::ReadAllText($marker) -ne ($script:ProjectRoot + "`n" + $Python.Interpreter)) {
        throw 'Obcy lub niekompletny .venv: zachowano zawartosc. Uzyj czystego katalogu instalacji.'
    }
    $settings = [IO.File]::ReadAllText($cfg)
    if ($settings -notmatch '(?im)^include-system-site-packages\s*=\s*false\s*$') {
        throw 'Venv musi izolowac pakiety od bazowego CPythona.'
    }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($script:VenvDirectory)
    while ($pending.Count) {
        foreach ($item in (Get-ChildItem -LiteralPath ($pending.Pop()) -Force)) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Niedozwolony reparse point w venv: $($item.FullName)" }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Assert-VenvIdentity {
    param($Python)
    $venv = Test-PythonCandidate -Executable $script:VenvPython
    if (-not $venv -or $venv.Version -ne $Python.Version -or $venv.Base -ne $Python.Base -or
        $venv.Root -ne $script:VenvDirectory -or $venv.Interpreter -ne $script:VenvPython) {
        throw 'Niezgodny interpreter, baza lub katalog .venv; niczego nie wyczyszczono.'
    }
}

function Test-RuntimePins {
    $lock = Join-Path $script:ProjectRoot 'requirements-windows.lock'
    $expected = @{}
    foreach ($line in [IO.File]::ReadAllLines($lock)) {
        if ($line -match '^\s*(#|$)') { continue }
        if ($line -notmatch '^([a-z0-9-]+)==([0-9.]+) --hash=sha256:[0-9a-f]{64}$') { throw 'Nieprawidlowy lock runtime.' }
        $expected.Add($Matches[1], $Matches[2])
    }
    if (-not $expected.ContainsKey('docker')) { throw 'Niekompletny lock runtime.' }
    $inventory = @(& $script:VenvPython -I -B -c "import json,re,importlib.metadata as m; print(json.dumps([(re.sub('[-_.]+','-',d.metadata['Name']).lower(),d.version) for d in m.distributions()]))")
    if ($LASTEXITCODE -ne 0 -or $inventory.Count -ne 1) { throw 'Nie mozna odczytac pakietow venv.' }
    $actual = @{}
    foreach ($pair in (ConvertFrom-Json -InputObject $inventory[0])) {
        if ($pair[0] -eq 'pip') { continue }
        if (-not $expected.ContainsKey($pair[0]) -or $actual.ContainsKey($pair[0])) { throw 'Nieoczekiwane pakiety w dedykowanym venv.' }
        $actual.Add($pair[0], $pair[1])
    }
    foreach ($name in $expected.Keys) {
        if ($actual[$name] -ne $expected[$name]) { return $false }
    }
    & $script:VenvPython -I -B -c 'import docker, tkinter' | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Ensure-PythonAndDependencies {
    Write-Step "Sprawdzanie Pythona i bibliotek aplikacji"
    $script:VenvDirectory = Join-Path $script:ProjectRoot '.venv'
    $script:VenvPython = Join-Path $script:VenvDirectory 'Scripts\python.exe'
    $marker = Join-Path $script:VenvDirectory '.oran-lab-owner'
    Assert-LocalPath $marker
    $existingVenv = Test-Path -LiteralPath $script:VenvDirectory
    if ($existingVenv -and -not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'Obcy .venv: zachowano zawartosc.' }
    $python = Get-ProjectPython
    if (-not $python) {
        if ($existingVenv) { throw 'Nie znaleziono Pythona istniejacego venv; brak automatycznej reinstalacji.' }
        if (-not $InstallPython) { throw 'Wymagany CPython 3.13 x64 z Tk. Zainstaluj recznie lub jawnie wybierz -InstallPython.' }
        Install-PythonFromOfficialSource
        $python = Get-ProjectPython
    }
    if (-not $python) {
        throw "Nie znaleziono zgodnego CPython 3.13 x64 z Tk."
    }

    Write-Host "Python $($python.Version): $($python.Interpreter)"
    if ($existingVenv) {
        Assert-VenvOwnership -Python $python
    }
    else {
        New-Item -ItemType Directory -Path $script:VenvDirectory | Out-Null
        [IO.File]::WriteAllText($marker, $script:ProjectRoot + "`n" + $python.Interpreter)
        & $python.Interpreter -I -m venv $script:VenvDirectory
        if ($LASTEXITCODE -ne 0) {
            throw "Nie udało się utworzyć środowiska aplikacji w $script:VenvDirectory."
        }
    }

    Assert-VenvOwnership -Python $python
    Assert-VenvIdentity -Python $python
    if (-not (Test-RuntimePins)) {
        & $script:VenvPython -I -m pip --isolated install --require-hashes --only-binary=:all: --no-deps --disable-pip-version-check -r (Join-Path $script:ProjectRoot 'requirements-windows.lock')
        if ($LASTEXITCODE -ne 0 -or -not (Test-RuntimePins)) { throw 'Nie mozna potwierdzic zamknietych pinow runtime.' }
    }
    $script:BasePython = $python
}


function Get-DockerExecutable {
    $docker = Get-ExecutablePath -Name "docker.exe"
    if ($docker) { return $docker }
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe")
    )
    foreach ($candidatePath in $candidates) {
        if (Test-Path -LiteralPath $candidatePath) {
            $dockerDirectory = Split-Path -Parent $candidatePath
            $env:Path = "$dockerDirectory;$env:Path"
            return $candidatePath
        }
    }
    return $null
}

function Get-DockerDesktopExecutable {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe")
    )
    foreach ($candidatePath in $candidates) {
        if (Test-Path -LiteralPath $candidatePath) { return $candidatePath }
    }
    return $null
}

function Install-DockerDesktopFromOfficialSource {
    $installer = Get-InstallerDownloadPath -FileName 'Docker-Desktop-4.100.1-amd64.exe'
    if (-not (Test-Path -LiteralPath $installer)) {
        Save-InstallerDownload -Uri $script:DockerInstallerUrl -Destination $installer -ExpectedSha256 $script:DockerInstallerSha256
    }
    Assert-InstallerIntegrity -Path $installer -ExpectedSha256 $script:DockerInstallerSha256
    Write-Host 'W kreatorze Docker Desktop wybierz instalacje dla swojego konta oraz backend WSL 2.'
    $process = Start-Process -FilePath $installer -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Instalator Docker Desktop zakonczyl sie kodem $($process.ExitCode)." }
}

function Ensure-DockerDesktop {
    param([switch]$AllowInstall)
    Write-Step "Sprawdzanie Docker Desktop"
    $docker = Get-DockerExecutable
    $desktop = Get-DockerDesktopExecutable
    if (-not $docker -and -not $desktop -and $AllowInstall) {
        Install-DockerDesktopFromOfficialSource
        $docker = Get-DockerExecutable
        $desktop = Get-DockerDesktopExecutable
        if ($docker -and $desktop) {
            throw 'Docker Desktop jest zainstalowany. Otworz go, zaakceptuj warunki i poczekaj na gotowosc silnika Linux, potem ponow instalacje O-RAN.'
        }
    }
    if (-not $docker -or -not $desktop) {
        throw 'Wymagany Docker Desktop z WSL2. Przy braku instalacji uzyj -InstallDocker. Przy niepelnej instalacji sprawdz https://docs.docker.com/desktop/setup/install/windows-install/'
    }

    $script:DockerExe = $docker
    $script:DockerDesktopExe = $desktop
    Write-Host "Docker CLI: $docker"
}


function Get-SourceContract {
    $path = Join-Path $script:ProjectRoot 'resources\simulator\sources.json'
    $dockerfile = Join-Path $script:ProjectRoot 'resources\simulator\Dockerfile'
    Assert-LocalPath $path
    Assert-LocalPath $dockerfile
    $m = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($m.schema_version -ne 1 -or $m.release_id -ne 'portable-20261114-v1' -or $m.platform -ne 'linux/amd64' -or
        $m.base_image -cnotmatch '^docker\.io/library/ubuntu:22\.04@sha256:[0-9a-f]{64}$' -or
        $m.dockerfile_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash -LiteralPath $dockerfile -Algorithm SHA256).Hash -ne $m.dockerfile_sha256 -or
        (Get-Content -LiteralPath $dockerfile -TotalCount 1) -cne "FROM $($m.base_image) AS buildenv") {
        throw 'Niezgodny manifest zrodel lub Dockerfile.'
    }
    $expected = @('resources/my_energy_scenario_optimized.cc','resources/xapp_my_energy_optimized.c',
        'resources/Energy_saving_with_cell_utilization_scenario.cc','resources/patch_kpm_clock.py')
    $paths = @($m.patches | ForEach-Object { $_.path })
    if ($paths.Count -ne $expected.Count -or (Compare-Object $expected $paths -CaseSensitive)) {
        throw 'Niezgodna lista zasobow kompilacji/runtime.'
    }
    foreach ($entry in $m.patches) {
        $resource = Join-Path $script:ProjectRoot $entry.path
        Assert-LocalPath $resource
        if ($entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            (Get-FileHash -LiteralPath $resource -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Niezgodny zasob: $($entry.path)"
        }
    }
    $sourceText = Get-Content -LiteralPath $dockerfile -Raw -Encoding UTF8
    $ids = @($m.repositories | ForEach-Object { $_.id })
    if ($ids.Count -ne 4 -or (Compare-Object @('e2sim','mmwave','asn1c','flexric') $ids -CaseSensitive) -or
        -not $m.apt_sources -or -not $m.reproducibility_limitations) { throw 'Niekompletny manifest repozytoriow.' }
    foreach ($repo in $m.repositories) {
        if (-not $repo.PSObject.Properties['submodules'] -or ($repo.id -eq 'mmwave' -and @($repo.submodules).Count -ne 2)) { throw 'Brak deklaracji gitlinkow.' }
        foreach ($pin in (@($repo) + @($repo.submodules))) {
            if ($pin.commit -cnotmatch '^[0-9a-f]{40}$' -or $pin.url -notmatch '^https://[^\s]+$' -or
                -not $sourceText.Contains([string]$pin.commit) -or -not $sourceText.Contains([string]$pin.url)) {
                throw 'Dockerfile nie potwierdza przypietego zrodla.'
            }
        }
    }
    return [pscustomobject]@{ Hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant(); File = $dockerfile; Manifest = $m }
}

function Assert-LocalDockerEnvironment {
    if ($env:ORAN_LAB_IMAGE_REF) { throw 'ORAN_LAB_IMAGE_REF nie jest obslugiwane w MVP; nie pobrano obrazu.' }
    foreach ($key in @('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_CONFIG','BUILDX_BUILDER','BUILDKIT_HOST','DOCKER_BUILDKIT')) {
        if ([Environment]::GetEnvironmentVariable($key, 'Process')) { throw "Niedozwolone nadpisanie: $key." }
    }
}

function Read-Docker {
    param([string[]]$Arguments, [switch]$WithoutHost)
    $argsForDocker = $Arguments
    if (-not $WithoutHost) { $argsForDocker = @('--host', $script:DockerEndpoint) + $Arguments }
    $output = @(& $script:DockerExe @argsForDocker)
    if ($LASTEXITCODE -ne 0) { throw "Nie mozna zweryfikowac lokalnego Docker Desktop: $($Arguments[0])." }
    return $output
}

function Select-LocalDocker {
    Assert-LocalDockerEnvironment
    $script:DockerContext = [string](Read-Docker -WithoutHost -Arguments @('context','show'))
    $script:DockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    $contextHost = [string](Read-Docker -WithoutHost -Arguments @('context','inspect',$script:DockerContext,'--format','{{.Endpoints.docker.Host}}'))
    if (($script:DockerContext -ne 'desktop-linux' -or $contextHost -ne $script:DockerEndpoint) -and
        ($script:DockerContext -ne 'default' -or $contextHost -ne 'npipe:////./pipe/docker_engine')) {
        throw 'Wymagany jest lokalny kontekst Docker Desktop (desktop-linux/default).'
    }
    try {
        $engine = Read-Docker -Arguments @('info','--format','{{json .}}') | ConvertFrom-Json
    }
    catch {
        throw "Otworz Docker Desktop i poczekaj na gotowosc silnika Linux z WSL2. Szczegoly: $($_.Exception.Message)"
    }
    if ($engine.OSType -ne 'linux' -or $engine.Architecture -notin @('x86_64','amd64') -or
        $engine.OperatingSystem -ne 'Docker Desktop' -or $engine.KernelVersion -notmatch 'microsoft.*WSL2') {
        throw 'Wymagany jest lokalny Docker Desktop z backendem WSL2 i Linux/amd64.'
    }
}

function Get-ConfiguredImageName {
    $path = Join-Path $script:ProjectRoot 'config.json'
    Assert-LocalPath $path
    $config = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $name = [string]$config.lab.simulator_image
    if (-not $name) { $name = 'wcnc_tap' }
    if ($name -cnotmatch '^[a-z0-9][a-z0-9._-]*(?::[a-z0-9][a-z0-9._-]*)?$') { throw 'Nieobslugiwalna lokalna nazwa obrazu.' }
    if ($name -notmatch ':') { $name += ':latest' }
    return $name
}

function Get-LocalImage {
    param([string]$Name)
    # A failed Docker query is never interpreted as an empty tag.
    $ids = @(Read-Docker -Arguments @('image','ls','--filter',"reference=$Name",'--no-trunc','--format','{{.ID}}') | Select-Object -Unique)
    if (-not $ids.Count) { return $null }
    $image = Read-Docker -Arguments @('image','inspect',$Name,'--format','{{json .}}') | ConvertFrom-Json
    if ($ids.Count -ne 1 -or $image.Id -ne $ids[0]) { throw 'Niejednoznaczna tozsamosc obrazu.' }
    return $image
}

function Assert-ImageIdentity {
    param($Image,[string]$ExpectedId,[string]$ManifestHash)
    if (-not $Image -or $ExpectedId -cnotmatch '^sha256:[0-9a-f]{64}$' -or $Image.Id -ne $ExpectedId -or
        $Image.Os -ne 'linux' -or $Image.Architecture -ne 'amd64' -or
        $Image.Config.Labels.'org.oran-lab.source-manifest-sha256' -ne $ManifestHash) {
        throw 'Kolizja lub niezgodna tozsamosc obrazu. Nie zastapiono lokalnego tagu.'
    }
}

function Ensure-SelectedSimulator {
    param([string]$Mode,[string]$ExpectedId,$Contract)
    $name = Get-ConfiguredImageName
    $mutex = [Threading.Mutex]::new($false, 'Global\ORAN-Lab-LocalDocker-ImagePublication-v1')
    $owned = $false
    try {
        try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
        if (-not $owned) { throw 'Inna instalacja publikuje obecnie obraz.' }
        $image = Get-LocalImage $name
        if ($Mode -eq 'Local') {
            Assert-ImageIdentity $image $ExpectedId $Contract.Hash
            return $image
        }
        if ($Mode -ne 'Source' -or $image) { throw 'Kolizja lub brak jawnego trybu. Nie modyfikowano obrazu.' }
        $context = Join-Path $script:ProjectRoot ('.install\build-' + [guid]::NewGuid().ToString('N'))
        $staging = Join-Path $context 'oran-lab-resources'
        New-Item -ItemType Directory -Path $context | Out-Null
        New-Item -ItemType Directory -Path $staging | Out-Null
        $file = Join-Path $context 'Dockerfile'
        Copy-Item -LiteralPath $Contract.File -Destination $file
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $Contract.Manifest.dockerfile_sha256) { throw 'Zmieniony staging Dockerfile.' }
        foreach ($entry in $Contract.Manifest.patches) {
            $output = Join-Path $staging (Split-Path -Leaf $entry.path)
            Copy-Item -LiteralPath (Join-Path $script:ProjectRoot $entry.path) -Destination $output
            if ((Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash -ne $entry.sha256) { throw 'Zmieniony zasob staging.' }
        }
        $iid = Join-Path $context 'image-id.txt'
        $code = Invoke-NativeCommand -Executable $script:DockerExe -Arguments @('--host',$script:DockerEndpoint,'build',
            '--platform','linux/amd64','--progress=plain','--iidfile',$iid,'--label',"org.oran-lab.source-manifest-sha256=$($Contract.Hash)",
            '--file',$file,$context)
        if ($code -ne 0) { throw 'Kompilacja obrazu nie powiodla sie.' }
        $id = [IO.File]::ReadAllText($iid).Trim()
        if ($id -cnotmatch '^sha256:[0-9a-f]{64}$') { throw 'Build nie zwrocil identyfikatora obrazu.' }
        $candidate = Read-Docker -Arguments @('image','inspect',$id,'--format','{{json .}}') | ConvertFrom-Json
        Assert-ImageIdentity $candidate $id $Contract.Hash
        if (Get-LocalImage $name) { throw 'Tag pojawil sie w trakcie kompilacji; nie zostal zastapiony.' }
        $null = Read-Docker -Arguments @('image','tag',$id,$name)
        $image = Get-LocalImage $name
        Assert-ImageIdentity $image $id $Contract.Hash
        return $image
    }
    finally {
        if ($owned) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Assert-SupportedHost {
    param([datetime]$Today = [datetime]::UtcNow.Date, [switch]$AllowWindowsHome)
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    $sku = [int]$os.OperatingSystemSKU
    # Conservative release qualification windows. Unknown builds require a new review.
    $pro = $sku -in @(48, 49)
    $enterpriseEducation = $sku -in @(4, 27, 121, 122)
    $qualified = ($build -eq 26100 -and $enterpriseEducation -and $Today -lt [datetime]'2027-10-01') -or
        ($build -eq 26200 -and ($enterpriseEducation -or $pro) -and $Today -lt [datetime]'2027-10-01') -or
        ($AllowWindowsHome -and $sku -eq 101 -and $build -eq 26300 -and $Today -lt [datetime]'2028-10-10')
    if ($os.ProductType -ne 1 -or -not $qualified) {
        throw "Nieobslugiwana konfiguracja Windows (SKU=$sku, build=$build). Obslugiwane sa Windows 11 25H2 Pro/Enterprise/Education i 24H2 Enterprise/Education. Home 26H2 (SKU=101, build=26300) wymaga -AllowWindowsHome."
    }

    if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "Obraz symulatora wymaga Windows x64 (AMD64). Wykryta architektura: $env:PROCESSOR_ARCHITECTURE"
    }
    $processors = @(Get-CimInstance Win32_Processor)
    if (-not $processors.Count -or ($processors | Where-Object { $_.Architecture -ne 9 })) {
        throw 'Wymagany jest procesor x64, a nie emulacja x64 na ARM.'
    }
    if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent -and
        ($processors | Where-Object { -not $_.VirtualizationFirmwareEnabled -or -not $_.SecondLevelAddressTranslationExtensions })) {
        throw 'Wymagana jest aktywna wirtualizacja z SLAT.'
    }
    $ramGb = [math]::Round(([double]$os.TotalVisibleMemorySize * 1KB) / 1GB, 1)
    if ($ramGb -lt 8) { throw "Docker Desktop wymaga co najmniej 8 GB RAM; wykryto $ramGb GB." }

    return $os
}

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

function Assert-InstallDisk {
    param($Drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($script:ProjectRoot)))
    if ($Drive.DriveType -ne [IO.DriveType]::Fixed -or $Drive.AvailableFreeSpace -lt 1GB) {
        throw 'Wymagany lokalny dysk z co najmniej 1 GiB wolnego miejsca na pliki startowe (nie jest to budzet backendu).'
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

function Assert-ReceiptPython {
    param($Receipt)
    Assert-LocalPath $Receipt.python_path
    if ((Get-FileHash -LiteralPath $Receipt.python_path -Algorithm SHA256).Hash -ne $Receipt.python_sha256) { throw 'Zmienione bajty bazowego Pythona.' }
    $base = Test-PythonCandidate -Executable $Receipt.python_path
    if (-not $base -or $base.Version -ne $Receipt.python_version -or $base.Interpreter -ne $Receipt.python_path -or $base.Root -ne $base.Base) { throw 'Zmienil sie bazowy Python.' }
    $script:VenvDirectory = $Receipt.venv_path
    $script:VenvPython = Join-Path $script:VenvDirectory 'Scripts\python.exe'
    Assert-VenvOwnership -Python $base
    if ((Get-FileHash -LiteralPath $script:VenvPython -Algorithm SHA256).Hash -ne $Receipt.venv_python_sha256) { throw 'Zmienione bajty interpretera venv.' }
    Assert-VenvIdentity -Python $base
    if (-not (Test-RuntimePins)) { throw 'Zmienily sie przypiete zaleznosci venv.' }
}

function Assert-ReadyRuntime {
    param($Receipt)
    if (-not $Receipt -or $Receipt.stage -ne 'ready') { throw 'Brak gotowego runtime.json. Najpierw dokoncz instalacje.' }
    Assert-ReceiptPython $Receipt
    if ($script:DockerContext -ne $Receipt.docker_context -or $script:DockerEndpoint -ne $Receipt.docker_endpoint) { throw 'Zmienil sie punkt koncowy Docker.' }
    Assert-ImageIdentity (Get-LocalImage $Receipt.image_name) $Receipt.image_id $Receipt.source_manifest_sha256
}

function Invoke-ReadyLaunch {
    param($Receipt)
    $previous = @{}
    $overrides = @{ ORAN_LAB_DATA_DIR = $Receipt.data_path; ORAN_LAB_CONFIG_PATH = $Receipt.config_path; DOCKER_HOST = $Receipt.docker_endpoint }
    foreach ($key in $overrides.Keys) { $previous[$key] = [Environment]::GetEnvironmentVariable($key,'Process') }
    Push-Location -LiteralPath $script:ProjectRoot
    try {
        foreach ($key in $overrides.Keys) { [Environment]::SetEnvironmentVariable($key,$overrides[$key],'Process') }
        & $script:VenvPython -E -s -m app.main | Out-Host
        return $LASTEXITCODE
    }
    finally {
        foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key,$previous[$key],'Process') }
        Pop-Location
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

if ($LibraryOnly) { return }

$exitCode = 1
$installLock = $null
try {
    $script:TargetOs = Assert-SupportedHost -AllowWindowsHome:$AllowWindowsHome
    Assert-LocalDockerEnvironment
    Assert-InstallDisk
    $installLock = Enter-InstallLock
    $logRoot = Join-Path $script:ProjectRoot '.install\logs'
    Assert-LocalPath $logRoot
    $null = [IO.Directory]::CreateDirectory($logRoot)
    $script:LogPath = Join-Path $logRoot ('install-' + [guid]::NewGuid().ToString('N') + '.log')
    $script:ReceiptPath = Join-Path (Join-Path $script:ProjectRoot '.install') 'runtime.json'
    Start-Transcript -LiteralPath $script:LogPath -NoClobber | Out-Null
    $script:TranscriptStarted = $true
    $exitCode = Invoke-Setup
}
catch {
    $exitCode = 1
    Write-Host "`nInstalacja nie została dokończona: $($_.Exception.Message)" -ForegroundColor Red
    if ($script:TranscriptStarted) { Write-Host "Log: $script:LogPath" }
}
finally {
    try { if ($script:TranscriptStarted) { Stop-Transcript | Out-Null } }
    finally { if ($installLock) { $installLock.Dispose() } }
}

exit $exitCode
