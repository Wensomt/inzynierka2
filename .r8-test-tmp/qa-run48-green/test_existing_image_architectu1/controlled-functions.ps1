
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
function Test-DockerImage {
    param([Parameter(Mandatory = $true)][string]$ImageName)
    $previousPreference = $ErrorActionPreference
    try {
        # On Windows PowerShell 5.1, Docker's expected "image not found"
        # stderr must not terminate setup before the image build fallback.
        $ErrorActionPreference = "Continue"
        $architecture = @(& $script:DockerExe image inspect $ImageName --format "{{.Architecture}}" 2>&1)
        $imageExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    return ($imageExitCode -eq 0 -and $architecture.Count -eq 1 -and ([string]$architecture[0]).Trim() -eq "amd64")
}

$script:DockerExe='Controlled-Docker'
function Controlled-Docker { $global:LASTEXITCODE=0; 'amd64' }
$usable=Test-DockerImage -ImageName 'controlled-image'
if ($usable -ne $true) { throw 'Image architecture not validated' }
