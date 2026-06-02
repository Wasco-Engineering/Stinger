# Build Stinger + QualityCal + MensorVacuumCheck and install to Desktop and Z:\bin
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath,
    [switch]$InstallPyInstaller,
    [switch]$SkipTests,
    [string]$StandId = 'STINGER_01',
    [string]$TargetUser = 'CalibrationUser',
    [switch]$SetMachineEnv,
    [switch]$SkipCalibrationUserDesktop
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path $ProjectRoot).ProviderPath

& (Join-Path $projectPath 'scripts\deploy_init_stand.ps1') -StandId $StandId -EquipmentId $StandId
& (Join-Path $projectPath 'scripts\deploy_set_stand_env.ps1') -StandId $StandId

if ($SetMachineEnv) {
    & (Join-Path $projectPath 'scripts\deploy_set_machine_env.ps1') -StandId $StandId
}

$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$env:STINGER_STAND_ID = $StandId
$env:STINGER_CONFIG_DIR = Join-Path (Join-Path $env:LOCALAPPDATA 'Stinger') $StandId

& $pythonPath (Join-Path $projectPath 'scripts\bootstrap_qf87_template.py')
& $pythonPath (Join-Path $projectPath 'scripts\apply_transducer_calibration.py')

$buildArgs = @{ ProjectRoot = $projectPath }
if ($InstallPyInstaller) { $buildArgs['InstallPyInstaller'] = $true }
if ($SkipTests) { $buildArgs['SkipTests'] = $true }

& (Join-Path $projectPath 'scripts\build_stinger.ps1') @buildArgs
& (Join-Path $projectPath 'scripts\build_quality_cal.ps1') @buildArgs
& (Join-Path $projectPath 'scripts\build_mensor_vacuum_check.ps1') @buildArgs

$binDir = 'Z:\Engineering\Program Builds\Python Builds\Stinger\bin'
$desktopRoot = Join-Path $env:USERPROFILE 'Desktop\Stinger'
New-Item -ItemType Directory -Path $desktopRoot -Force | Out-Null
foreach ($pair in @(
    @{ Sub = 'Stinger'; Exe = 'Stinger.exe' },
    @{ Sub = 'QualityCal'; Exe = 'QualityCal.exe' },
    @{ Sub = 'MensorVacuumCheck'; Exe = 'MensorVacuumCheck.exe' }
)) {
    $src = Join-Path $projectPath "dist\$($pair.Sub)\$($pair.Exe)"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $desktopRoot $pair.Exe) -Force
        if (Test-Path (Split-Path $binDir -Parent)) {
            New-Item -ItemType Directory -Path $binDir -Force | Out-Null
            Copy-Item $src (Join-Path $binDir $pair.Exe) -Force
        }
    }
}

if (-not $SkipCalibrationUserDesktop) {
    & (Join-Path $projectPath 'scripts\deploy_install_desktop.ps1') -ProjectRoot $projectPath -TargetUser $TargetUser
}

$manifest = [ordered]@{
    build_timestamp_utc = (Get-Date).ToUniversalTime().ToString('o')
    hostname = $env:COMPUTERNAME
    stand_id = $StandId
    config_dir = $env:STINGER_CONFIG_DIR
    artifacts = @('Stinger.exe', 'QualityCal.exe', 'MensorVacuumCheck.exe')
}
if (Test-Path $binDir) {
    $manifest | ConvertTo-Json | Set-Content (Join-Path $binDir 'build_manifest.json') -Encoding UTF8
}

Write-Host ""
Write-Host "Config: $env:STINGER_CONFIG_DIR"
Write-Host "Desktop: $desktopRoot"
Write-Host "Z bin:   $binDir"
