#Requires -Version 7.0
param(
    [string]$Python = 'python',
    [string]$Config = '',
    [string]$OutputApk = '',
    [switch]$ExportModels,
    [switch]$KeepBuildWork
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$arguments = @((Join-Path $PSScriptRoot 'build.py'))
if ($Config) { $arguments += @('--config', [IO.Path]::GetFullPath($Config)) }
if ($OutputApk) { $arguments += @('--output', [IO.Path]::GetFullPath($OutputApk)) }
if ($ExportModels) { $arguments += '--export-models' }
if ($KeepBuildWork) { $arguments += '--keep-build-work' }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'FL Royale build failed. See the command output above.' }
