# Initialize machine-local Stinger config for one stand.
#
# Usage:
#   .\scripts\deploy_init_stand.ps1 -StandId STINGER_02 -EquipmentId STINGER_02
#
param(
    [Parameter(Mandatory = $true)]
    [string] $StandId,

    [string] $EquipmentId = '',
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$destRoot = Join-Path (Join-Path $env:LOCALAPPDATA 'Stinger') $StandId
$destRoot = [System.IO.Path]::GetFullPath($destRoot)

New-Item -ItemType Directory -Path $destRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $destRoot 'logs') -Force | Out-Null

$stingerSrc = Join-Path $repoRoot 'stinger_config.yaml'
$qualitySrc = Join-Path $repoRoot 'quality_cal_config.yaml'
$stingerDst = Join-Path $destRoot 'stinger_config.yaml'
$qualityDst = Join-Path $destRoot 'quality_cal_config.yaml'

if (-not $EquipmentId) { $EquipmentId = $StandId }

function Copy-ConfigFile {
    param([string] $Source, [string] $Destination)
    if ((Test-Path $Destination) -and -not $Force) {
        Write-Host "SKIP (exists): $Destination"
        return
    }
    if (-not (Test-Path $Source)) {
        throw "Missing template: $Source"
    }
    Copy-Item -Path $Source -Destination $Destination -Force
    Write-Host "Wrote: $Destination"
}

Copy-ConfigFile -Source $stingerSrc -Destination $stingerDst
Copy-ConfigFile -Source $qualitySrc -Destination $qualityDst

# Patch equipment_id in stinger config (simple line replace)
$content = Get-Content -Path $stingerDst -Raw
$content = $content -replace 'equipment_id:\s*".*"', "equipment_id: `"$EquipmentId`""
Set-Content -Path $stingerDst -Value $content -NoNewline

Write-Host ""
Write-Host "Stand config directory: $destRoot"
Write-Host ""
Write-Host "Set persistent environment (User):"
Write-Host "  STINGER_STAND_ID=$StandId"
Write-Host "  STINGER_CONFIG_DIR=$destRoot"
Write-Host ""
Write-Host "[System.Environment]::SetEnvironmentVariable('STINGER_STAND_ID', '$StandId', 'User')"
Write-Host "[System.Environment]::SetEnvironmentVariable('STINGER_CONFIG_DIR', '$destRoot', 'User')"
Write-Host ""
Write-Host "Edit COM ports, DB credentials, and Mensor port in the YAML files above."
