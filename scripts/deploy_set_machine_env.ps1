# Set Machine-level Stinger env so all users (e.g. CalibrationUser) share one config dir.
param(
    [Parameter(Mandatory = $true)]
    [string] $StandId,

    [string] $ConfigDir = ''
)

$ErrorActionPreference = 'Stop'
if (-not $ConfigDir) {
    $ConfigDir = 'C:\Stinger'
}
$ConfigDir = [System.IO.Path]::GetFullPath($ConfigDir)

try {
    [System.Environment]::SetEnvironmentVariable('STINGER_STAND_ID', $StandId, 'Machine')
    [System.Environment]::SetEnvironmentVariable('STINGER_CONFIG_DIR', $ConfigDir, 'Machine')
} catch {
    throw "Failed to set Machine environment (run PowerShell as Administrator): $($_.Exception.Message)"
}

Write-Host "Machine env set:"
Write-Host "  STINGER_STAND_ID=$StandId"
Write-Host "  STINGER_CONFIG_DIR=$ConfigDir"
Write-Host "Requires Administrator. Users must sign out/in or reboot to pick up Machine env."
