
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
function Assert-SupportedHost {
    param([datetime]$Today = [datetime]::UtcNow.Date)
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    $sku = [int]$os.OperatingSystemSKU
    # Conservative release qualification windows. Unknown builds require a new review.
    $pro = $sku -in @(48, 49)
    $enterpriseEducation = $sku -in @(4, 27, 121, 122)
    $qualified = ($build -eq 26100 -and $enterpriseEducation -and $Today -lt [datetime]'2027-10-01') -or
        ($build -eq 26200 -and ($enterpriseEducation -or $pro) -and $Today -lt [datetime]'2027-10-01')
    if ($os.ProductType -ne 1 -or -not $qualified) {
        throw "Nieobslugiwany target: Windows 11 Pro/Enterprise/Education x64; SKU=$sku build=$build."
    }

    if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "Obraz symulatora wymaga Windows x64 (AMD64). Wykryta architektura: $env:PROCESSOR_ARCHITECTURE"
    }
    $processors = @(Get-CimInstance Win32_Processor)
    if (-not $processors.Count -or ($processors | Where-Object { $_.Architecture -ne 9 })) {
        throw 'Wymagany jest procesor x64, a nie emulacja x64 na ARM.'
    }
    if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent -and
        ($processors | Where-Object { -not $_.VirtualizationFirmwareEnabled -or -not $_.SecondLevelAddressTranslationExtensions })) {
        throw 'Wymagana jest aktywna wirtualizacja z SLAT.'
    }
    $ramGb = [math]::Round(([double]$os.TotalVisibleMemorySize * 1KB) / 1GB, 1)
    if ($ramGb -lt 8) { throw "Docker Desktop wymaga co najmniej 8 GB RAM; wykryto $ramGb GB." }

    return $os
}

function Get-CimInstance { param($ClassName)
    if ($ClassName -eq 'Win32_OperatingSystem') {
        return [pscustomobject]@{ProductType=1;OperatingSystemSKU=48;BuildNumber=19045;TotalVisibleMemorySize=16777216}
    }
    return [pscustomobject]@{Architecture=9;SecondLevelAddressTranslationExtensions=$true;VirtualizationFirmwareEnabled=$true}
}
$env:PROCESSOR_ARCHITECTURE='AMD64'
$rejected=$false
try { $null=Assert-SupportedHost -Today ([datetime]'2026-11-22') } catch { $rejected=$_.Exception.Message -match 'target|procesor' }
if ($rejected -eq $false) { throw 'Wrong host eligibility' }
