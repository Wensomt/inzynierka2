
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
function Ensure-SimulatorImage {
    Write-Step "Przygotowanie obrazu symulatora"
    $configPath = Join-Path $script:ProjectRoot "config.json"
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $imageName = [string]$config.lab.simulator_image
    if ([string]::IsNullOrWhiteSpace($imageName)) { $imageName = "wcnc_tap" }
    if (Test-DockerImage -ImageName $imageName) {
        if ($env:ORAN_LAB_IMAGE_REF -and $imageName -ne $env:ORAN_LAB_IMAGE_REF) {
            throw "Istniejący tag $imageName nie potwierdza pochodzenia ORAN_LAB_IMAGE_REF. Wymagana jawna weryfikacja obrazu; tag nie zostanie zastąpiony."
        }
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
            throw "Brak obowiązkowego pliku SHA-256: $checksumPath"
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

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\green-run46b\test_archive_without_checksum_0\root'
$env:ORAN_LAB_IMAGE_REF=$null
function Write-Step { }
$script:checks=0
function Test-DockerImage { $script:checks++; return ($script:checks -gt 1) }
$script:loads=0
function Invoke-NativeCommand { $script:loads++; return 0 }
$rejected=$false
try { Ensure-SimulatorImage } catch { $rejected=$true }
if ($script:loads -ne 0 -or -not $rejected) { throw 'Unverified archive passed to Docker load' }
