
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
function Assert-ImageIdentity {
    param($Image,[string]$ExpectedId,[string]$ManifestHash)
    if (-not $Image -or $ExpectedId -cnotmatch '^sha256:[0-9a-f]{64}$' -or $Image.Id -ne $ExpectedId -or
        $Image.Os -ne 'linux' -or $Image.Architecture -ne 'amd64' -or
        $Image.Config.Labels.'org.oran-lab.source-manifest-sha256' -ne $ManifestHash) {
        throw 'Kolizja lub niezgodna tozsamosc obrazu. Nie zastapiono lokalnego tagu.'
    }
}

$expected='sha256:1111111111111111111111111111111111111111111111111111111111111111'
$hash='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$image=[pscustomobject]@{Id='sha256:2222222222222222222222222222222222222222222222222222222222222222';Os='linux';Architecture='amd64';
Config=[pscustomobject]@{Labels=[pscustomobject]@{'org.oran-lab.source-manifest-sha256'=$hash}}}
$rejected=$false
try { Assert-ImageIdentity $image $expected $hash } catch { $rejected=$_.Exception.Message -like '*tozsamosc*' }
if (-not $rejected) { throw 'Foreign image accepted' }
