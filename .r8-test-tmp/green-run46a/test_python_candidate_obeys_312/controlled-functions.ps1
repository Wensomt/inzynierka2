
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
    if ($versionText -notmatch "^3\.13$") {
        return $null
    }
    return [PSCustomObject]@{
        Executable = $Executable
        Prefix = @($Prefix)
        Version = $versionText
        Interpreter = [string]$output[0]
    }
}

function Controlled-Python { $global:LASTEXITCODE=0; 'C:\controlled\python.exe'; '3.14' }
$candidate=Test-PythonCandidate -Executable 'Controlled-Python'
if ([bool]$candidate -ne $false) { throw 'Python version violates 3.13 contract' }
