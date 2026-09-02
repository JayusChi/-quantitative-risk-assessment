[CmdletBinding()]
param(
    [switch]$ForceInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvRoot = Join-Path $projectRoot '.runtime\venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
$installMarker = Join-Path $venvRoot '.qra-demo-installed-v1'

if (-not (Test-Path -LiteralPath $venvPython)) {
    $venvCreated = $false
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv $venvRoot
        $venvCreated = ($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $venvPython)
    }

    if (-not $venvCreated) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) {
            throw '未找到 Python 3.10+。请先安装 Python，然后重新运行本脚本。'
        }
        & $python.Source -m venv $venvRoot
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw '创建演示版隔离 Python 环境失败。请确认 Python 3.10+ 可用。'
        }
    }
}

if ($ForceInstall -or -not (Test-Path -LiteralPath $installMarker)) {
    & $venvPython -m pip install --disable-pip-version-check --no-input $projectRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'QRA 演示版依赖安装失败。请检查网络和 Python 包索引配置。'
    }
    Set-Content -LiteralPath $installMarker -Encoding UTF8 -Value 'QRA full synthetic demo v1'
}

Write-Host "QRA 全合成演示版安装完成：$venvRoot"
