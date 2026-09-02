[CmdletBinding()]
param(
    [string]$Output = '',
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot '.runtime\venv\Scripts\python.exe'
$demoWorkspace = Join-Path $projectRoot '.runtime\workspace'
$databasePath = Join-Path $demoWorkspace 'state\qra.sqlite3'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw '演示版尚未安装，请先运行 install_full_synthetic_demo.ps1。'
}
if (-not $Output) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Output = Join-Path $projectRoot ".runtime\backups\qra-demo-$stamp.sqlite3"
}
$manifest = "$Output.manifest.json"
$arguments = @(
    '-m', 'db_qra',
    '--database', $databasePath,
    'backup',
    '--output', $Output,
    '--manifest', $manifest
)
if ($Replace) {
    $arguments += '--replace'
}
& $venvPython @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'QRA 演示数据库备份失败。'
}
