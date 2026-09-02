[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias('Input')]
    [string]$BackupPath,
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot '.runtime\venv\Scripts\python.exe'
$databasePath = Join-Path $projectRoot '.runtime\workspace\state\qra.sqlite3'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw '演示版尚未安装，请先运行 install_full_synthetic_demo.ps1。'
}
$arguments = @(
    '-m', 'db_qra',
    '--database', $databasePath,
    'restore',
    '--input', $BackupPath
)
if ($Replace) {
    $arguments += '--replace'
}
& $venvPython @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'QRA 演示数据库恢复失败。'
}
