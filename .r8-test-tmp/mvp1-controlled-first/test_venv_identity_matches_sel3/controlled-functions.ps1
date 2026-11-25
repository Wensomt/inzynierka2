
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
function Assert-VenvIdentity {
    param($Python)
    $venv = Test-PythonCandidate -Executable $script:VenvPython
    if (-not $venv -or $venv.Version -ne $Python.Version -or $venv.Base -ne $Python.Base -or
        $venv.Root -ne $script:VenvDirectory -or $venv.Interpreter -ne $script:VenvPython) {
        throw 'Niezgodny interpreter, baza lub katalog .venv; niczego nie wyczyszczono.'
    }
}

$script:VenvDirectory='C:\mock\.venv'
$script:VenvPython=Join-Path $script:VenvDirectory 'Scripts\python.exe'
function Test-PythonCandidate {
    [pscustomobject]@{Version='3.13.16';Base='base';Root=$script:VenvDirectory;Interpreter=$script:VenvPython}
}
$rejected=$false
try { Assert-VenvIdentity -Python ([pscustomobject]@{Version='3.13.16';Base='base'}) }
catch { $rejected=$_.Exception.Message -like '*Niezgodny interpreter*' }
if ($rejected -eq $true) { throw 'Wrong venv identity result' }
