param(
    [switch]$SkipLaunch,
    [ValidateSet("Install", "Update")]
    [string]$WslBootstrapOperation
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
}
catch {
    # Keep setup usable when PowerShell is hosted without a writable console.
}

$script:ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RebootRequired = $false
$script:TranscriptStarted = $false
$script:WslExe = Join-Path $env:SystemRoot "System32\wsl.exe"
$script:PythonInstallerUrl = "https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe"
$script:PythonInstallerSha256 = "fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b"
$script:FlexricCommit = "18c3325091ad6890a706ebb2bba38d3c0fb741e5"
$script:SimulatorRepositories = @(
    [PSCustomObject]@{
        Name = "e2sim-kpmv3"
        Uri = "https://github.com/MinaYonan123/e2sim-kpmv3-RCv1.03.git"
        Commit = "acf4f6b2baa8c645af566ea210146abd97de1f48"
    },
    [PSCustomObject]@{
        Name = "mmwave-LENA-oran"
        Uri = "https://github.com/MinaYonan123/mmwave-LENA-oran.git"
        Commit = "f2313c286eda2b0f833f089a2eb1c91dea578685"
    }
)

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
    $downloadDirectory = Join-Path $env:LOCALAPPDATA "O-RAN Energy Lab\downloads"
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    return Join-Path $downloadDirectory $FileName
}

function Save-InstallerDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Write-Host "Pobieram: $Uri"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $lastDownloadError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force
            }
            Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
            $lastDownloadError = $null
            break
        }
        catch {
            $lastDownloadError = $_
            if ($attempt -lt 3) {
                $delaySeconds = 3 * $attempt
                Write-Warning "Pobieranie nie powiodło się (próba $attempt/3). Ponawiam za $delaySeconds s: $($_.Exception.Message)"
                Start-Sleep -Seconds $delaySeconds
            }
        }
    }
    if ($lastDownloadError) {
        throw "Nie udało się pobrać pliku po 3 próbach: $Uri. Sprawdź połączenie z Internetem i DNS, a potem uruchom install_windows.bat ponownie. Szczegóły: $($lastDownloadError.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $Destination) -or (Get-Item -LiteralPath $Destination).Length -lt 1MB) {
        throw "Pobrany instalator jest pusty lub niekompletny: $Destination"
    }
}

if ($WslBootstrapOperation) {
    if (-not (Test-Path -LiteralPath $script:WslExe)) {
        Write-Error "Nie znaleziono wsl.exe: $script:WslExe"
        exit 1
    }

    if ($WslBootstrapOperation -eq "Install") {
        $bootstrapExitCode = Invoke-NativeCommand -Executable $script:WslExe -Arguments @("--install", "--no-distribution")
    }
    else {
        $bootstrapExitCode = Invoke-NativeCommand -Executable $script:WslExe -Arguments @("--update", "--web-download")
    }
    exit $bootstrapExitCode
}

$logRoot = Join-Path $env:LOCALAPPDATA "O-RAN Energy Lab\install"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$script:LogPath = Join-Path $logRoot ("install-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

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

function Invoke-Winget {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $exitCode = Invoke-NativeCommand -Executable $script:WingetExe -Arguments $Arguments
    if ($exitCode -ne 0) {
        throw "winget zakończył się kodem $exitCode. Polecenie: winget $($Arguments -join ' ')"
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-WslBootstrapAsAdministrator {
    param([Parameter(Mandatory = $true)][ValidateSet("Install", "Update")][string]$Operation)

    if (Test-IsAdministrator) {
        if ($Operation -eq "Install") {
            $wslExitCode = Invoke-NativeCommand -Executable $script:WslExe -Arguments @("--install", "--no-distribution")
        }
        else {
            $wslExitCode = Invoke-NativeCommand -Executable $script:WslExe -Arguments @("--update", "--web-download")
        }
        if ($wslExitCode -ne 0) {
            throw "Polecenie WSL zakończyło się kodem $wslExitCode. Uruchom ponownie instalator jako administrator i sprawdź wirtualizację w BIOS/UEFI."
        }
        return
    }

    Write-Host "Windows poprosi teraz o jednorazowe potwierdzenie administratora, aby włączyć lub zaktualizować WSL2."
    $currentPowerShell = (Get-Process -Id $PID).Path
    $argumentLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -WslBootstrapOperation {1}' -f $PSCommandPath, $Operation
    try {
        $elevated = Start-Process -FilePath $currentPowerShell -Verb RunAs `
            -ArgumentList $argumentLine -Wait -PassThru
    }
    catch {
        throw "Nie uruchomiono administracyjnej konfiguracji WSL. Szczegóły: $($_.Exception.Message)"
    }
    if ($elevated.ExitCode -ne 0) {
        throw "Administracyjna konfiguracja WSL nie powiodła się (kod $($elevated.ExitCode))."
    }
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
        throw "Nie znaleziono wsl.exe. Wymagany jest Windows 10 22H2 lub Windows 11 z aktualnymi aktualizacjami."
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
    if (-not $version) {
        Write-Host "Włączam składniki Windows wymagane przez WSL2. Po instalacji trzeba będzie ponownie uruchomić komputer."
        Invoke-WslBootstrapAsAdministrator -Operation "Install"
        $script:RebootRequired = $true
        return
    }

    if ($version -lt [Version]"2.1.5") {
        Write-Host "WSL $version jest za stary dla Docker Desktop. Aktualizuję WSL."
        Invoke-WslBootstrapAsAdministrator -Operation "Update"
        $version = Get-WslVersion
        if (-not $version -or $version -lt [Version]"2.1.5") {
            $script:RebootRequired = $true
            return
        }
        $script:RebootRequired = $true
    }

    if (-not $script:RebootRequired) {
        $wslExitCode = Invoke-NativeCommand -Executable $script:WslExe -Arguments @("--set-default-version", "2")
        if ($wslExitCode -ne 0) {
            throw "Nie udało się ustawić WSL2 jako domyślnej wersji. Sprawdź aktualizacje WSL i uruchom instalator ponownie."
        }
        Write-Host "WSL $version jest gotowy."
    }
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Prefix = @()
    )
    $probeArguments = @($Prefix) + @(
        "-c",
        "import sys, tkinter; print(sys.executable); print('.'.join(map(str, sys.version_info[:2])))"
    )
    $output = @(& $Executable @probeArguments 2>$null)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -or $output.Count -lt 2) {
        return $null
    }
    $versionText = [string]$output[$output.Count - 1]
    if ($versionText -notmatch "^3\.(12|13)$") {
        return $null
    }
    return [PSCustomObject]@{
        Executable = $Executable
        Prefix = @($Prefix)
        Version = $versionText
        Interpreter = [string]$output[0]
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
        foreach ($version in @("3.13", "3.12")) {
            $candidate = Test-PythonCandidate -Executable $launcherPath -Prefix @("-$version")
            if ($candidate) { return $candidate }
        }
    }

    $pythonCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python313\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($pythonPath in $pythonCandidates) {
        if (Test-Path -LiteralPath $pythonPath) {
            $candidate = Test-PythonCandidate -Executable $pythonPath
            if ($candidate) { return $candidate }
        }
    }

    $pathPython = Get-ExecutablePath -Name "python.exe"
    if ($pathPython -and $pathPython -notmatch "\\WindowsApps\\") {
        return Test-PythonCandidate -Executable $pathPython
    }
    return $null
}

function Install-PythonFromOfficialSource {
    Write-Host "Nie znaleziono działającego Pythona ani winget. Pobieram oficjalny instalator Python 3.13.16."
    $installer = Get-InstallerDownloadPath -FileName "python-3.13.16-amd64.exe"
    Save-InstallerDownload -Uri $script:PythonInstallerUrl -Destination $installer
    $actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $script:PythonInstallerSha256) {
        throw "Suma SHA-256 instalatora Pythona jest niezgodna. Plik: $installer"
    }

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

function Ensure-PythonAndDependencies {
    Write-Step "Sprawdzanie Pythona i bibliotek aplikacji"
    $python = Get-ProjectPython
    if (-not $python) {
        if ($script:WingetExe) {
            try {
                Write-Host "Instaluję Python 3.13 przez winget."
                Invoke-Winget -Arguments @(
                    "install", "--id", "Python.Python.3.13", "--exact", "--scope", "user",
                    "--accept-source-agreements", "--accept-package-agreements", "--silent"
                )
                Update-SessionPath
                $python = Get-ProjectPython
            }
            catch {
                Write-Warning "Instalacja przez winget nie powiodła się: $($_.Exception.Message)"
                Update-SessionPath
                $python = Get-ProjectPython
            }
        }
        if (-not $python) {
            Install-PythonFromOfficialSource
            $python = Get-ProjectPython
        }
    }
    if (-not $python) {
        throw "Nie udało się znaleźć Pythona 3.12/3.13 z Tkinterem. Zainstaluj Python 3.13 i uruchom ponownie install_windows.bat."
    }

    Write-Host "Python $($python.Version): $($python.Interpreter)"
    $venvName = "venv-py{0}.{1}" -f $python.Version.Split(".")[0], $python.Version.Split(".")[1]
    $script:VenvDirectory = Join-Path $env:LOCALAPPDATA ("O-RAN Energy Lab\{0}" -f $venvName)
    $script:VenvPython = Join-Path $script:VenvDirectory "Scripts\python.exe"

    $rebuildVenv = -not (Test-Path -LiteralPath $script:VenvPython)
    if (-not $rebuildVenv) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $venvProbe = @(& $script:VenvPython -c "import tkinter, pip; print('venv: OK')" 2>&1)
            $venvProbeExitCode = $LASTEXITCODE
            if ($venvProbeExitCode -ne 0 -or ($venvProbe | Out-String) -match "Error processing line|Traceback \(most recent call last\)") {
                $rebuildVenv = $true
                Write-Warning "Wykryto uszkodzone lub zanieczyszczone środowisko Pythona; instalator odtworzy własne venv."
            }
        }
        catch {
            $rebuildVenv = $true
            Write-Warning "Nie można uruchomić istniejącego venv; instalator odtworzy środowisko."
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    if ($rebuildVenv) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $script:VenvDirectory) -Force | Out-Null
        $venvArguments = @($python.Prefix) + @("-m", "venv", "--clear", $script:VenvDirectory)
        & $python.Executable @venvArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Nie udało się utworzyć środowiska aplikacji w $script:VenvDirectory."
        }
    }

    & $script:VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Nie udało się zaktualizować pip w środowisku aplikacji." }
    & $script:VenvPython -m pip install -r (Join-Path $script:ProjectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Instalacja bibliotek z requirements.txt nie powiodła się." }
    & $script:VenvPython -c "import tkinter, docker; print('Python, Tkinter i Docker SDK: OK')"
    if ($LASTEXITCODE -ne 0) { throw "Kontrola Pythona, Tkintera lub Docker SDK nie powiodła się." }
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
    Write-Host "Pobieram oficjalny instalator Docker Desktop dla Windows x64."
    $installer = Get-InstallerDownloadPath -FileName "Docker-Desktop-Installer.exe"
    Save-InstallerDownload -Uri "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" `
        -Destination $installer
    $arguments = @("install", "--user", "--quiet", "--backend=wsl-2")
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Instalator Docker Desktop zakończył się kodem $($process.ExitCode)."
    }
    Update-SessionPath
}


function Ensure-DockerDesktop {
    Write-Step "Sprawdzanie Docker Desktop"
    $docker = Get-DockerExecutable
    $desktop = Get-DockerDesktopExecutable
    if (-not $docker -or -not $desktop) {
        if ($script:WingetExe) {
            try {
                Write-Host "Instaluję Docker Desktop przez winget, w profilu bieżącego użytkownika i z backendem WSL2."
                Invoke-Winget -Arguments @(
                    "install", "--id", "Docker.DockerDesktop", "--exact", "--source", "winget",
                    "--accept-source-agreements", "--accept-package-agreements",
                    "--override", "install --user --quiet --backend=wsl-2"
                )
                Update-SessionPath
                $docker = Get-DockerExecutable
                $desktop = Get-DockerDesktopExecutable
            }
            catch {
                Write-Warning "Instalacja Docker Desktop przez winget nie powiodła się: $($_.Exception.Message)"
                Update-SessionPath
                $docker = Get-DockerExecutable
                $desktop = Get-DockerDesktopExecutable
            }
        }
        if (-not $docker -or -not $desktop) {
            Install-DockerDesktopFromOfficialSource
        }
        $docker = Get-DockerExecutable
        $desktop = Get-DockerDesktopExecutable
    }
    if (-not $docker -or -not $desktop) {
        throw "Docker Desktop zainstalował się niekompletnie. Otwórz jego instalator z menu Start i uruchom ponownie install_windows.bat."
    }
    $script:DockerExe = $docker
    $script:DockerDesktopExe = $desktop
    Write-Host "Docker CLI: $docker"
}

function Wait-ForDockerEngine {
    Write-Step "Uruchamianie Linuxowego silnika Docker"
    $desktopProcess = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $desktopProcess) {
        Start-Process -FilePath $script:DockerDesktopExe | Out-Null
    }

    $deadline = (Get-Date).AddMinutes(10)
    $lastEngineOutput = @()
    do {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $engineOutput = @(& $script:DockerExe info --format "{{.OSType}}" 2>&1)
            $engineExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        $lastEngineOutput = $engineOutput
        if ($engineExitCode -eq 0) {
            $engineType = (([string]($engineOutput | Select-Object -Last 1)) -replace "`0", "").Trim()
            if ([string]::IsNullOrWhiteSpace($engineType)) {
                $lastEngineOutput = @("Docker Engine jeszcze się uruchamia; docker info zwróciło pustą odpowiedź.")
            }
            elseif ($engineType -ne "linux") {
                throw "Docker działa w trybie '$engineType'. W menu Docker Desktop wybierz 'Switch to Linux containers', a potem uruchom instalator ponownie."
            }
            else {
                Write-Host "Docker Engine działa w trybie Linux."
                return
            }
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    if ($lastEngineOutput.Count -gt 0) {
        Write-Host "Ostatnia odpowiedź Docker Engine: $($lastEngineOutput -join ' ')"
    }
    throw "Silnik Docker nie uruchomił się w ciągu 10 minut. Otwórz Docker Desktop, zaakceptuj jego warunki przy pierwszym uruchomieniu i sprawdź, czy backend WSL2 jest gotowy. Potem uruchom instalator ponownie."
}

function Test-DockerImage {
    param([Parameter(Mandatory = $true)][string]$ImageName)
    $previousPreference = $ErrorActionPreference
    try {
        # On Windows PowerShell 5.1, Docker's expected "image not found"
        # stderr must not terminate setup before the image build fallback.
        $ErrorActionPreference = "Continue"
        & $script:DockerExe image inspect $ImageName --format "{{.Architecture}}" 2>&1 | Out-Null
        $imageExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    return ($imageExitCode -eq 0)
}

function Ensure-SimulatorImage {
    Write-Step "Przygotowanie obrazu symulatora"
    $configPath = Join-Path $script:ProjectRoot "config.json"
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $imageName = [string]$config.lab.simulator_image
    if ([string]::IsNullOrWhiteSpace($imageName)) { $imageName = "wcnc_tap" }
    if (Test-DockerImage -ImageName $imageName) {
        Write-Host "Obraz $imageName jest już dostępny."
        return
    }

    $archive = Join-Path $script:ProjectRoot "resources\wcnc_tap.tar"
    if (Test-Path -LiteralPath $archive) {
        $checksumPath = "$archive.sha256"
        if (Test-Path -LiteralPath $checksumPath) {
            $expected = ([string](Get-Content -LiteralPath $checksumPath -Raw)).Trim() -split "\s+" |
                Select-Object -First 1
            if ($expected -notmatch "^[0-9a-fA-F]{64}$") {
                throw "Plik sumy obrazu ma niepoprawny format: $checksumPath"
            }
            $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
            if ($actual -ne $expected) { throw "Suma SHA-256 obrazu wcnc_tap.tar jest niezgodna." }
        }
        else {
            Write-Warning "Brak pliku SHA-256 dla resources\wcnc_tap.tar; obraz zostanie załadowany bez kontroli sumy."
        }
        $dockerExitCode = Invoke-NativeCommand -Executable $script:DockerExe -Arguments @("load", "--input", $archive)
        if ($dockerExitCode -ne 0) { throw "Nie udało się załadować obrazu z $archive." }
    }
    elseif ($env:ORAN_LAB_IMAGE_REF) {
        $imageRef = [string]$env:ORAN_LAB_IMAGE_REF
        if ($imageRef -notmatch "@sha256:[0-9a-fA-F]{64}$") {
            throw "ORAN_LAB_IMAGE_REF musi wskazywać niezmienny digest w formacie @sha256:..."
        }
        $dockerExitCode = Invoke-NativeCommand -Executable $script:DockerExe -Arguments @("pull", $imageRef)
        if ($dockerExitCode -ne 0) { throw "Nie udało się pobrać obrazu $imageRef. Sprawdź docker login i dostęp do rejestru." }
        $dockerExitCode = Invoke-NativeCommand -Executable $script:DockerExe -Arguments @("tag", $imageRef, $imageName)
        if ($dockerExitCode -ne 0) { throw "Nie udało się przypisać obrazu $imageRef do nazwy $imageName." }
    }
    else {
        $sourceContext = Join-Path $script:ProjectRoot ".simulator-src"
        New-Item -ItemType Directory -Path $sourceContext -Force | Out-Null
        $localDockerfile = Join-Path $sourceContext "mmwave-LENA-oran\ns3_docker\Dockerfile"
        if (Test-Path -LiteralPath $localDockerfile) {
            $dockerfile = $localDockerfile
        }
        else {
            $dockerfile = Join-Path $sourceContext "Dockerfile.upstream"
            if (-not (Test-Path -LiteralPath $dockerfile)) {
                $mmwaveRepository = $script:SimulatorRepositories | Where-Object { $_.Name -eq "mmwave-LENA-oran" } | Select-Object -First 1
                $dockerfileUri = "https://raw.githubusercontent.com/MinaYonan123/mmwave-LENA-oran/$($mmwaveRepository.Commit)/ns3_docker/Dockerfile"
                Write-Host "Pobieram przypięty szablon Dockerfile symulatora."
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -Uri $dockerfileUri -OutFile $dockerfile -UseBasicParsing
            }
            if (-not (Test-Path -LiteralPath $dockerfile) -or (Get-Item -LiteralPath $dockerfile).Length -lt 1000) {
                throw "Nie udało się pobrać poprawnego szablonu Dockerfile dla symulatora."
            }
        }

        $resourceStage = Join-Path $sourceContext "oran-lab-resources"
        New-Item -ItemType Directory -Path $resourceStage -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $script:ProjectRoot "resources\my_energy_scenario_optimized.cc") `
            -Destination (Join-Path $resourceStage "my_energy_scenario_optimized.cc") -Force
        Copy-Item -LiteralPath (Join-Path $script:ProjectRoot "resources\xapp_my_energy_optimized.c") `
            -Destination (Join-Path $resourceStage "xapp_my_energy_optimized.c") -Force

        $dockerIgnore = Join-Path $sourceContext ".dockerignore"
        $requiredDockerIgnorePatterns = @(
            "**/.git",
            "**/.git/**",
            "**/build",
            "**/build/**",
            "**/output",
            "**/output/**",
            "**/__pycache__",
            "**/previous",
            "**/previous/**",
            "e2sim-kpmv3",
            "mmwave-LENA-oran"
        )
        $existingDockerIgnorePatterns = @()
        if (Test-Path -LiteralPath $dockerIgnore) {
            $existingDockerIgnorePatterns = @(Get-Content -LiteralPath $dockerIgnore)
        }
        $missingDockerIgnorePatterns = @($requiredDockerIgnorePatterns | Where-Object { $_ -notin $existingDockerIgnorePatterns })
        if ($missingDockerIgnorePatterns.Count -gt 0) {
            if (Test-Path -LiteralPath $dockerIgnore) {
                Add-Content -LiteralPath $dockerIgnore -Value $missingDockerIgnorePatterns -Encoding ASCII
            }
            else {
                $requiredDockerIgnorePatterns | Set-Content -LiteralPath $dockerIgnore -Encoding ASCII
            }
        }

        # Clone sources inside Linux: Windows filesystems cannot represent
        # all case-sensitive filenames from the upstream repositories.
        $dockerfileContent = Get-Content -LiteralPath $dockerfile -Raw
        $windowsDockerfile = Join-Path $sourceContext "Dockerfile.windows"
        $windowsDockerfileContent = $dockerfileContent -replace "`r`n", "`n" -replace "`r", "`n"
        # Fix older local templates too: in a Dockerfile RUN shell, $$ expands
        # to the shell PID, so paths such as "$$xapp_dir" silently fail tests.
        $windowsDockerfileContent = $windowsDockerfileContent.Replace('$$xapp_dir', '$xapp_dir')
        $windowsDockerfileContent = $windowsDockerfileContent.Replace('$${CMAKE_SOURCE_DIR}', '${CMAKE_SOURCE_DIR}')
        $branchCheckout = "    git checkout dev && \`n"
        $pinnedCheckout = "    git checkout dev && \`n    git checkout --detach $($script:FlexricCommit) && \`n"
        if ($windowsDockerfileContent.Contains($branchCheckout) -and
            $windowsDockerfileContent -notmatch [regex]::Escape("git checkout --detach $($script:FlexricCommit)")) {
            $windowsDockerfileContent = $windowsDockerfileContent.Replace($branchCheckout, $pinnedCheckout)
        }
        $flexricCheckoutLine = "    git checkout --detach $($script:FlexricCommit) && \`n"
        $xappSourceRepair = @'
    sed -i 's/\r$//' /workspace/oran-lab-resources/xapp_my_energy_optimized.c && \
    sed -i '/^\/\/   if (msg_frm_1 == NULL || msg_frm_1->meas_info_lst_len == 0)$/ { n; /^    return;$/d; }' /workspace/oran-lab-resources/xapp_my_energy_optimized.c && \
'@
        $xappSourceRepair += "`n"
        if ($windowsDockerfileContent.Contains($flexricCheckoutLine) -and
            $windowsDockerfileContent -notmatch [regex]::Escape("sed -i 's/\r$//' /workspace/oran-lab-resources/xapp_my_energy_optimized.c")) {
            $windowsDockerfileContent = $windowsDockerfileContent.Replace(
                $flexricCheckoutLine,
                $flexricCheckoutLine + $xappSourceRepair
            )
        }

        $e2simRepository = $script:SimulatorRepositories | Where-Object { $_.Name -eq "e2sim-kpmv3" } | Select-Object -First 1
        $mmwaveRepository = $script:SimulatorRepositories | Where-Object { $_.Name -eq "mmwave-LENA-oran" } | Select-Object -First 1
        $e2simCloneCommand = @"
RUN git init /workspace/e2sim && \
    cd /workspace/e2sim && \
    git remote add origin $($e2simRepository.Uri) && \
    git fetch --depth 1 origin $($e2simRepository.Commit) && \
    git checkout --detach FETCH_HEAD && \
    git -c core.autocrlf=false submodule update --init --recursive --depth 1
"@
        $e2simCloneCommand = $e2simCloneCommand.TrimEnd()
        $mmwaveCloneCommand = @"
RUN git init /workspace/ns3-mmwave-oran && \
    cd /workspace/ns3-mmwave-oran && \
    git remote add origin $($mmwaveRepository.Uri) && \
    git fetch --depth 1 origin $($mmwaveRepository.Commit) && \
    git checkout --detach FETCH_HEAD && \
    git -c core.autocrlf=false submodule update --init --recursive --depth 1 && \
    mkdir -p .oran-lab-disabled-scratch && \
    if [ -f scratch/cttc-nr-demo.cc ]; then mv scratch/cttc-nr-demo.cc .oran-lab-disabled-scratch/; fi && \
    if [ -f scratch/orange-rf-channel-reconfiguration.cc ]; then mv scratch/orange-rf-channel-reconfiguration.cc .oran-lab-disabled-scratch/; fi && \
    if [ -f scratch/rf-channel-reconfiguration-scenario.cc ]; then mv scratch/rf-channel-reconfiguration-scenario.cc .oran-lab-disabled-scratch/; fi
"@
        $mmwaveCloneCommand = $mmwaveCloneCommand.TrimEnd()

        if ($windowsDockerfileContent -match "(?m)^COPY e2sim-kpmv3 /workspace/e2sim$") {
            $windowsDockerfileContent = [regex]::Replace(
                $windowsDockerfileContent,
                "(?m)^COPY e2sim-kpmv3 /workspace/e2sim$",
                $e2simCloneCommand,
                1
            )
        }
        if ($windowsDockerfileContent -match "(?m)^COPY mmwave-LENA-oran /workspace/ns3-mmwave-oran$") {
            $windowsDockerfileContent = [regex]::Replace(
                $windowsDockerfileContent,
                "(?m)^COPY mmwave-LENA-oran /workspace/ns3-mmwave-oran$",
                $mmwaveCloneCommand,
                1
            )
        }

        if ($windowsDockerfileContent -notmatch "(?m)^COPY oran-lab-resources /workspace/oran-lab-resources$") {
            $windowsDockerfileContent = [regex]::Replace(
                $windowsDockerfileContent,
                "(?m)^WORKDIR /workspace$",
                "WORKDIR /workspace`nCOPY oran-lab-resources /workspace/oran-lab-resources",
                1
            )
        }
        if ($windowsDockerfileContent -notmatch "RUN install -D -m 0644 /workspace/oran-lab-resources/my_energy_scenario_optimized\.cc") {
            $windowsDockerfileContent = [regex]::Replace(
                $windowsDockerfileContent,
                "(?m)^RUN \.\/ns3 configure && .$",
                "RUN install -D -m 0644 /workspace/oran-lab-resources/my_energy_scenario_optimized.cc /workspace/ns3-mmwave-oran/scratch/my_energy_scenario_optimized.cc && \`n    ./ns3 configure && \",
                1
            )
        }
        if ($windowsDockerfileContent -notmatch "(?m)^RUN find /workspace/ns3-mmwave-oran ") {
            $windowsDockerfileContent = [regex]::Replace(
                $windowsDockerfileContent,
                "(?m)^(WORKDIR /workspace/ns3-mmwave-oran)$",
                ('$1' + "`nRUN find /workspace/ns3-mmwave-oran -type f \( -name '*.sh' -o -name '*.py' -o -name 'Makefile' -o -name 'CMakeLists.txt' -o -name '*.cmake' \) -exec sed -i 's/\r$//' {} + && chmod +x ./ns3"),
                1
            )
        }

        if ($windowsDockerfileContent -notmatch "xapp_my_energy_optimized.c") {
            $xappBuildInjection = @'
    xapp_dir=examples/xApp/c/orange && \
    if [ ! -d "$xapp_dir" ]; then xapp_dir=examples/xApp/c/myxapp; fi && \
    test -d "$xapp_dir" && \
    install -m 0644 /workspace/oran-lab-resources/xapp_my_energy_optimized.c "$xapp_dir/xapp_my_energy_optimized.c" && \
    if ! grep -Fq 'add_executable(xapp_my_energy_optimized' "$xapp_dir/CMakeLists.txt"; then \
      printf '\nadd_executable(xapp_my_energy_optimized\n    xapp_my_energy_optimized.c\n    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c\n)\n\ntarget_link_libraries(xapp_my_energy_optimized\n    PUBLIC\n    e42_xapp\n    pthread\n    sctp\n    dl\n)\n' >> "$xapp_dir/CMakeLists.txt"; \
    fi && \
'@
            $checkoutMatch = [regex]::Match(
                $windowsDockerfileContent,
                "(?m)^    git checkout --detach $($script:FlexricCommit) && ."
            )
            if ($checkoutMatch.Success) {
                $insertionPoint = $checkoutMatch.Index + $checkoutMatch.Length
                $windowsDockerfileContent = $windowsDockerfileContent.Substring(0, $insertionPoint) +
                    "`n" + $xappBuildInjection.TrimEnd() +
                    $windowsDockerfileContent.Substring($insertionPoint)
            }
        }

        if ($windowsDockerfileContent -notmatch "COPY oran-lab-resources /workspace/oran-lab-resources" -or
            $windowsDockerfileContent -notmatch "my_energy_scenario_optimized\.cc" -or
            $windowsDockerfileContent -notmatch "xapp_my_energy_optimized.c" -or
            $windowsDockerfileContent -notmatch [regex]::Escape("git fetch --depth 1 origin $($e2simRepository.Commit)") -or
            $windowsDockerfileContent -notmatch [regex]::Escape("git fetch --depth 1 origin $($mmwaveRepository.Commit)") -or
            $windowsDockerfileContent -notmatch "RUN find /workspace/ns3-mmwave-oran") {
            throw "Nie można przygotować windowsowej kopii Dockerfile z wymaganymi źródłami symulatora."
        }
        [System.IO.File]::WriteAllText(
            $windowsDockerfile,
            $windowsDockerfileContent,
            ([System.Text.UTF8Encoding]::new($false))
        )

        Write-Host "Buduję $imageName z lokalnych źródeł. Pierwsza kompilacja może potrwać kilkadziesiąt minut."
        $dockerExitCode = Invoke-NativeCommand -Executable $script:DockerExe -Arguments @(
            "build", "--progress=plain", "--tag", $imageName, "--file", $windowsDockerfile, $sourceContext
        )
        if ($dockerExitCode -ne 0) { throw "Budowa obrazu $imageName nie powiodła się. Szczegóły są w logu instalatora." }
    }

    if (-not (Test-DockerImage -ImageName $imageName)) {
        throw "Po przygotowaniu nadal nie ma obrazu Docker o nazwie $imageName."
    }
    Write-Host "Obraz symulatora $imageName jest gotowy."
}

function New-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $launcher = Join-Path $script:ProjectRoot "run_windows.bat"
    if (-not $desktop -or -not (Test-Path -LiteralPath $launcher)) { return }
    $shortcutPath = Join-Path $desktop "Symulator O-RAN.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcher
    $shortcut.WorkingDirectory = $script:ProjectRoot
    $shortcut.Description = "Symulator efektywności energetycznej sieci O-RAN"
    $shortcut.Save()
    Write-Host "Utworzono skrót: $shortcutPath"
}

function Invoke-Setup {
    if (-not $env:LOCALAPPDATA) { throw "Nie znaleziono katalogu profilu LocalAppData." }
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    $caption = [string]$os.Caption
    if ($caption -match "Windows 11") {
        if ($build -lt 22631) { throw "Docker Desktop wymaga aktualnego Windows 11 (co najmniej build 22631). Zaktualizuj Windows i uruchom instalator ponownie." }
    }
    elseif ($caption -match "Windows 10") {
        if ($build -lt 19045) { throw "Docker Desktop wymaga Windows 10 22H2 (build 19045) lub Windows 11." }
    }
    else {
        throw "Nieobsługiwany system: $caption. Instalator jest przeznaczony dla Windows 10/11 64-bit."
    }

    if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "Obraz symulatora wymaga Windows x64 (AMD64). Wykryta architektura: $env:PROCESSOR_ARCHITECTURE"
    }
    $processors = @(Get-CimInstance Win32_Processor)
    if ($processors | Where-Object { $_.VirtualizationFirmwareEnabled -eq $false }) {
        throw "Włącz Intel VT-x/AMD-V (wirtualizację sprzętową) w BIOS/UEFI i uruchom instalator ponownie."
    }
    $ramGb = [math]::Round(([double]$os.TotalVisibleMemorySize * 1KB) / 1GB, 1)
    if ($ramGb -lt 8) { throw "Docker Desktop wymaga co najmniej 8 GB RAM; wykryto $ramGb GB." }

    $script:WingetExe = Get-ExecutablePath -Name "winget.exe"
    if ($script:WingetExe) {
        Write-Host "Dostępny winget: $script:WingetExe"
    }
    else {
        Write-Host "winget nie jest zainstalowany. Użyję oficjalnych instalatorów pobieranych bezpośrednio."
    }

    Ensure-Wsl2
    if ($script:RebootRequired) {
        Write-Host "`nWindows musi zostać uruchomiony ponownie, aby WSL2 zadziałał. Zapisano log; po restarcie kliknij install_windows.bat ponownie, aby kontynuować instalację." -ForegroundColor Yellow
        return 2
    }

    Ensure-PythonAndDependencies
    Ensure-DockerDesktop

    if ($script:RebootRequired) {
        Write-Host "`nPython, biblioteki i Docker Desktop są zainstalowane. Uruchom ponownie Windows, a potem ponownie kliknij install_windows.bat, aby przygotować obraz i uruchomić aplikację." -ForegroundColor Yellow
        return 2
    }

    Wait-ForDockerEngine
    Ensure-SimulatorImage
    New-DesktopShortcut

    if (-not $SkipLaunch) {
        Write-Step "Uruchamianie symulatora O-RAN"
        Push-Location $script:ProjectRoot
        try {
            & $script:VenvPython -m app.main
            if ($LASTEXITCODE -ne 0) { throw "Aplikacja zakończyła się kodem $LASTEXITCODE." }
        }
        finally {
            Pop-Location
        }
    }
    return 0
}

$exitCode = 0
try {
    Start-Transcript -LiteralPath $script:LogPath -Force | Out-Null
    $script:TranscriptStarted = $true
    $exitCode = Invoke-Setup
}
catch {
    $exitCode = 1
    Write-Host "`nInstalacja nie została dokończona: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log: $script:LogPath"
}
finally {
    if ($script:TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}

exit $exitCode
