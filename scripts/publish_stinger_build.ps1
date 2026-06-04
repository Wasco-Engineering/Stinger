param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$SourceBuildDir = '',
    [string]$PublishRoot = 'Z:\Engineering\Program Builds\Python Builds',
    [string]$ReleaseNotes = ''
)

$ErrorActionPreference = 'Stop'

$projectPath = (Resolve-Path $ProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($SourceBuildDir)) {
    $SourceBuildDir = Join-Path $projectPath 'dist\SPS Calibration Stand'
}

if (-not (Test-Path $SourceBuildDir)) {
    throw "Source build directory not found: $SourceBuildDir"
}

$exePath = Join-Path $SourceBuildDir 'SPS Calibration Stand.exe'
if (-not (Test-Path $exePath)) {
    throw "Build artifact missing: $exePath"
}

if (-not (Test-Path $PublishRoot)) {
    throw "Publish path not found or not mounted: $PublishRoot"
}

$manifestSource = Join-Path $SourceBuildDir 'build_manifest.json'
$buildTime = Get-Date
$stamp = $buildTime.ToString('yyyyMMdd_HHmmss')
$publishBase = Join-Path $PublishRoot 'Stinger'
$publishDir = Join-Path $publishBase $stamp

New-Item -ItemType Directory -Path $publishDir -Force | Out-Null

Copy-Item (Join-Path $SourceBuildDir '*') $publishDir -Recurse -Force

if (-not [string]::IsNullOrWhiteSpace($ReleaseNotes)) {
    Set-Content -Path (Join-Path $publishDir 'RELEASE_NOTES.txt') -Value $ReleaseNotes -Encoding UTF8
}

$latestJsonPath = Join-Path $publishBase 'latest.json'
$latestTxtPath = Join-Path $publishBase 'latest.txt'

$latest = [ordered]@{
    app_name = 'SPS Calibration Stand'
    latest_build = $stamp
    publish_path = $publishDir
    published_utc = (Get-Date).ToUniversalTime().ToString('o')
}
if (Test-Path $manifestSource) {
    $manifestData = Get-Content -Raw -Path $manifestSource | ConvertFrom-Json
    $latest.git_commit = $manifestData.git_commit
}

New-Item -ItemType Directory -Path $publishBase -Force | Out-Null
$latest | ConvertTo-Json -Depth 5 | Set-Content -Path $latestJsonPath -Encoding UTF8
Set-Content -Path $latestTxtPath -Value $stamp -Encoding UTF8

Write-Host "Published build: $publishDir"
Write-Host "Latest marker: $latestJsonPath"
