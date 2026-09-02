[CmdletBinding()]
param(
    [string]$HostAddress = '127.0.0.1',
    [int]$Port = 8766,
    [switch]$OpenBrowser,
    [switch]$ForceInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $projectRoot 'install_full_synthetic_demo.ps1'
if (-not (Test-Path -LiteralPath $installer)) {
    $installer = Join-Path $projectRoot 'Install-Demo.ps1'
}
$venvPython = Join-Path $projectRoot '.runtime\venv\Scripts\python.exe'
if ($ForceInstall -or -not (Test-Path -LiteralPath $venvPython)) {
    & $installer -ForceInstall:$ForceInstall
}

$demoWorkspace = Join-Path $projectRoot '.runtime\workspace'
$databasePath = Join-Path $demoWorkspace 'state\qra.sqlite3'
$runtimePath = Join-Path $demoWorkspace 'runtime'
$env:QRA_PROJECT_ROOT = $projectRoot
$env:QRA_WORKSPACE_ROOT = $demoWorkspace

& $venvPython -m db_qra --database $databasePath load-demo --runtime-root $runtimePath --actor 'one-click-demo-launcher'
if ($LASTEXITCODE -ne 0) {
    throw '全合成演示项目加载失败。'
}

$projectUrl = "http://${HostAddress}:$Port/projects/"
Write-Host "QRA 全合成端到端演示版已就绪：$projectUrl"
Write-Host '按 Ctrl+C 停止本地服务。'
if ($OpenBrowser) {
    Start-Process $projectUrl
}
& $venvPython -m db_qra --database $databasePath serve --host $HostAddress --port $Port
