# Set Machine-level Stinger env so all users (e.g. CalibrationUser) share one config dir.
param(
    [Parameter(Mandatory = $true)]
    [string] $StandId,

    [string] $ConfigDir = ''
)

$ErrorActionPreference = 'Stop'
if (-not $ConfigDir) {
    $ConfigDir = Join-Path (Join-Path $env:LOCALAPPDATA 'Stinger') $StandId
}
$ConfigDir = [System.IO.Path]::GetFullPath($ConfigDir)

[System.Environment]::SetEnvironmentVariable('STINGER_STAND_ID', $StandId, 'Machine')
[System.Environment]::SetEnvironmentVariable('STINGER_CONFIG_DIR', $ConfigDir, 'Machine')

Write-Host "Machine env set:"
Write-Host "  STINGER_STAND_ID=$StandId"
Write-Host "  STINGER_CONFIG_DIR=$ConfigDir"
Write-Host "Requires Administrator. Users must sign out/in or reboot to pick up Machine env."
