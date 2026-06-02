param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath,
    [switch]$InstallPyInstaller
)

$ErrorActionPreference = 'Stop'

$projectPath = (Resolve-Path $ProjectRoot).ProviderPath
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$specPath = Join-Path $projectPath 'MensorVacuumCheck.spec'
$distRoot = Join-Path $projectPath 'dist'
$distPath = Join-Path $distRoot 'MensorVacuumCheck'
$workPath = Join-Path $projectPath 'build\pyinstaller_mensor_vacuum'

if (-not (Test-Path $pythonPath)) {
    throw "Missing virtualenv Python: $pythonPath"
}

if ($InstallPyInstaller) {
    & $pythonPath -m pip install pyinstaller
}

if (Test-Path $distPath) {
    Remove-Item -Recurse -Force $distPath -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $distPath -Force | Out-Null

& $pythonPath -m PyInstaller --noconfirm --distpath "$distPath" --workpath "$workPath" "$specPath"

$exePath = Join-Path $distPath 'MensorVacuumCheck.exe'
if (-not (Test-Path $exePath)) {
    throw "Build succeeded but executable missing: $exePath"
}

Write-Host "Build complete: $exePath"
