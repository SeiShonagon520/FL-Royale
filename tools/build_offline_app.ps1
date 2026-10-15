param(
    [string]$Python = 'python',
    [string]$Config = '',
    [string]$OutputApk = '',
    [switch]$ExportModels,
    [switch]$KeepBuildWork,
    [switch]$Overwrite
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$arguments = @((Join-Path $PSScriptRoot 'build.py'))
if ($Config) { $arguments += @('--config', [IO.Path]::GetFullPath($Config)) }
if ($OutputApk) { $arguments += @('--output', [IO.Path]::GetFullPath($OutputApk)) }
if ($ExportModels) { $arguments += '--export-models' }
if ($KeepBuildWork) { $arguments += '--keep-build-work' }
if ($Overwrite) { $arguments += '--overwrite' }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'FL Royale build failed. See the command output above.' }
