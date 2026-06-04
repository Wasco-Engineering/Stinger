# Install built EXEs to a user Desktop\Stinger folder. Run elevated when targeting another user.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath,
    [string]$TargetUser = 'CalibrationUser',
    [string]$SourceDir = ''
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path $ProjectRoot).ProviderPath

if ([string]::IsNullOrWhiteSpace($SourceDir)) {
    $SourceDir = Join-Path $projectPath 'dist'
}

$artifacts = @(
    @{ Name = 'SPS Calibration Stand.exe'; SubDir = 'SPS Calibration Stand' },
    @{ Name = 'QualityCal.exe'; SubDir = 'QualityCal' }
)

if ($TargetUser -eq $env:USERNAME) {
    $desktopRoot = Join-Path $env:USERPROFILE 'Desktop\Stinger'
} else {
    $desktopRoot = "C:\Users\$TargetUser\Desktop\Stinger"
}

New-Item -ItemType Directory -Path $desktopRoot -Force | Out-Null

foreach ($item in $artifacts) {
    $src = Join-Path $SourceDir $item.SubDir $item.Name
    if (-not (Test-Path $src)) {
        Write-Warning "Skip missing: $src"
        continue
    }
    Copy-Item $src (Join-Path $desktopRoot $item.Name) -Force
    Write-Host "Installed: $(Join-Path $desktopRoot $item.Name)"
}

Write-Host ""
Write-Host "Desktop deploy folder: $desktopRoot"
if ($TargetUser -ne $env:USERNAME) {
    Write-Host "If files are missing, re-run this script as Administrator."
}
