
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
function Assert-LocalDockerEnvironment {
    if ($env:ORAN_LAB_IMAGE_REF) { throw 'ORAN_LAB_IMAGE_REF nie jest obslugiwane w MVP; nie pobrano obrazu.' }
    foreach ($key in @('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_CONFIG','BUILDX_BUILDER','BUILDKIT_HOST','DOCKER_BUILDKIT')) {
        if ([Environment]::GetEnvironmentVariable($key, 'Process')) { throw "Niedozwolone nadpisanie: $key." }
    }
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
    $engine = Read-Docker -Arguments @('info','--format','{{json .}}') | ConvertFrom-Json
    if ($engine.OSType -ne 'linux' -or $engine.Architecture -notin @('x86_64','amd64') -or
        $engine.OperatingSystem -ne 'Docker Desktop' -or $engine.KernelVersion -notmatch 'microsoft.*WSL2') {
        throw 'Wymagany jest lokalny Docker Desktop z backendem WSL2 i Linux/amd64.'
    }
}

$env:ORAN_LAB_IMAGE_REF=$null
$env:DOCKER_HOST='tcp://invalid.example:2376'
function Read-Docker { throw 'UNMOCKED Docker CLI' }
$rejected=$false
try { Select-LocalDocker } catch { $rejected=$_.Exception.Message -like '*DOCKER_HOST*' }
if (-not $rejected) { throw 'Foreign Docker host accepted' }
