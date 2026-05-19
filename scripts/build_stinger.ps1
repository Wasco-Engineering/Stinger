param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath,
    [switch]$InstallPyInstaller,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'

$projectPath = (Resolve-Path $ProjectRoot).ProviderPath
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$specPath = Join-Path $projectPath 'Stinger.spec'
$distRoot = Join-Path $projectPath 'dist'
$distPath = Join-Path $distRoot 'Stinger'
$workPath = Join-Path $projectPath 'build\pyinstaller'
$manifestPath = Join-Path $distPath 'build_manifest.json'

if (-not (Test-Path $pythonPath)) {
    throw "Missing virtualenv Python: $pythonPath"
}

if (-not (Test-Path $specPath)) {
    throw "Missing PyInstaller spec file: $specPath"
}

if ($InstallPyInstaller) {
    & $pythonPath -m pip install pyinstaller
}

if (-not $SkipTests) {
    & $pythonPath -m pytest -q tests/test_pressure_conversion.py tests/test_pressure_calibration.py
}

# Remove prior output so stale onedir files cannot be published with the onefile exe.
if (Test-Path $distPath) {
    Remove-Item -Recurse -Force $distPath -ErrorAction SilentlyContinue
    if (Test-Path $distPath) { Start-Sleep -Seconds 2; Remove-Item -Recurse -Force $distPath -ErrorAction SilentlyContinue }
}
New-Item -ItemType Directory -Path $distPath -Force | Out-Null

# Note: --clean omitted to avoid PermissionError on network/synced build dirs.
& $pythonPath -m PyInstaller --noconfirm --distpath "$distPath" --workpath "$workPath" "$specPath"

$exePath = Join-Path $distPath 'Stinger.exe'
if (-not (Test-Path $exePath)) {
    throw "Build succeeded but executable missing: $exePath"
}

$configSource = Join-Path $projectPath 'stinger_config.yaml'
if (Test-Path $configSource) {
    Copy-Item $configSource (Join-Path $distPath 'stinger_config.yaml') -Force
}

$gitCommit = ''
try {
    $gitCommit = (& git -C "$projectPath" rev-parse --short HEAD).Trim()
} catch {
    $gitCommit = ''
}

$manifest = [ordered]@{
    app_name = 'Stinger'
    artifact = 'Stinger.exe'
    build_timestamp_utc = (Get-Date).ToUniversalTime().ToString('o')
    git_commit = $gitCommit
    source_spec = 'Stinger.spec'
}

$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8

# Publish to shared drive: mirror build output (clears obsolete files, copies new)
$publishPath = 'Z:\Engineering\Program Builds\Python Builds\Stinger'
New-Item -ItemType Directory -Path $publishPath -Force | Out-Null
& robocopy $distPath $publishPath /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
# Robocopy exit: 0=nothing, 1=copied, 2=extra, 3=copied+extra, 4=mismatch. 8+=error
if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with exit $LASTEXITCODE" }
$global:LASTEXITCODE = 0

$obsoletePublishInternal = Join-Path $publishPath '_internal'
if (Test-Path $obsoletePublishInternal) {
    Remove-Item -Recurse -Force $obsoletePublishInternal -ErrorAction SilentlyContinue
}

# Copy to CalibrationUser desktop
$desktopPath = 'C:\Users\CalibrationUser\Desktop\Stinger'
if (Test-Path (Split-Path $desktopPath -Parent)) {
    New-Item -ItemType Directory -Path $desktopPath -Force | Out-Null
    & robocopy $distPath $desktopPath /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Robocopy to desktop failed with exit $LASTEXITCODE" }
    $global:LASTEXITCODE = 0
    $obsoleteDesktopInternal = Join-Path $desktopPath '_internal'
    if (Test-Path $obsoleteDesktopInternal) {
        Remove-Item -Recurse -Force $obsoleteDesktopInternal -ErrorAction SilentlyContinue
    }
    Write-Host "Deployed to desktop: $desktopPath"
} else {
    Write-Warning "CalibrationUser profile not found - skipping desktop deploy"
}

Write-Host "Build complete: $exePath"
Write-Host "Manifest: $manifestPath"
Write-Host "Published to: $publishPath"
