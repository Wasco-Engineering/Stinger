# Set per-user environment variables for a Stinger stand.
# Usage: .\scripts\deploy_set_stand_env.ps1 -StandId STINGER_01
param(
    [Parameter(Mandatory = $true)]
    [string] $StandId
)

$ErrorActionPreference = 'Stop'
$configDir = Join-Path (Join-Path $env:LOCALAPPDATA 'Stinger') $StandId
$configDir = [System.IO.Path]::GetFullPath($configDir)

[System.Environment]::SetEnvironmentVariable('STINGER_STAND_ID', $StandId, 'User')
[System.Environment]::SetEnvironmentVariable('STINGER_CONFIG_DIR', $configDir, 'User')

Write-Host "Set User env:"
Write-Host "  STINGER_STAND_ID=$StandId"
Write-Host "  STINGER_CONFIG_DIR=$configDir"
Write-Host "Restart terminal/Cursor for apps to pick up new values."
