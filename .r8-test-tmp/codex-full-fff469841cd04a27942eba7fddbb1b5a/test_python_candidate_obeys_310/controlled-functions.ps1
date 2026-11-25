
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

function Controlled-Python { $global:LASTEXITCODE=0; '{"executable":"C:\\controlled\\python.exe","version":"3.12.9","implementation":"cpython","machine":"AMD64","bits":64,"prefix":"C:\\controlled","base_prefix":"C:\\controlled"}' }
$candidate=Test-PythonCandidate -Executable 'Controlled-Python'
if ([bool]$candidate -ne $false) { throw 'Python version violates 3.13 contract' }
