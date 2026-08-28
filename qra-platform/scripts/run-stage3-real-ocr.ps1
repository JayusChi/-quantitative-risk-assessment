$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$acceptance = Join-Path $projectRoot "tools\run_stage3_acceptance.py"
$record = Join-Path $projectRoot "docs\project\stage3\real-ocr-acceptance.json"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "项目虚拟环境不存在：$python"
}

& $python $acceptance --require-real-ocr --json --record $record
exit $LASTEXITCODE
