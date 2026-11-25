
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

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\mvp1-controlled-first\test_lingering_archive_does_no0\root'
$script:id='sha256:1111111111111111111111111111111111111111111111111111111111111111'
$script:hash='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
function Get-ConfiguredImageName { 'controlled-image:latest' }
function Get-LocalImage {
    [pscustomobject]@{Id=$script:id;Os='linux';Architecture='amd64';
    Config=[pscustomobject]@{Labels=[pscustomobject]@{'org.oran-lab.source-manifest-sha256'=$script:hash}}}
}
function Read-Docker { throw 'UNMOCKED Docker' }
$image=Ensure-SelectedSimulator -Mode Local -ExpectedId $script:id -Contract @{Hash=$script:hash}
if ($image.Id -ne $script:id) { throw 'Explicit local image was not selected' }
