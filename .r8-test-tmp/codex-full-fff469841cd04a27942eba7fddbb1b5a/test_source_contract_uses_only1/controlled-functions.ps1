
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

$script:ProjectRoot='C:\Users\Dawid\Documents\GitHub\inzynierka2\.r8-test-tmp\codex-full-fff469841cd04a27942eba7fddbb1b5a\test_source_contract_uses_only1'
$rejected=$false
try { $contract=Get-SourceContract } catch {
    if ($_.Exception.Message -notlike '*Dockerfile*') { throw }
    $rejected=$true
}
if ($rejected -ne $true) { throw 'Wrong source integrity result' }
