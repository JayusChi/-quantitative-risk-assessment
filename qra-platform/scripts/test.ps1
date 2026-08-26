$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = Join-Path $projectRoot "src"
    Push-Location $projectRoot
    python ".\tools\check_architecture.py"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m unittest discover -s tests -t . -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
    $env:PYTHONPATH = $previousPythonPath
}

