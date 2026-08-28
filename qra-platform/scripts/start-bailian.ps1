param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [string]$ListenAddress = "127.0.0.1",
    [int]$Port = 8766,
    [string]$OcrModel = "qwen3.5-ocr",
    [string]$VisionModel = "qwen3.7-max",
    [int]$OcrTimeoutSeconds = 120,
    [switch]$TestOnly
)

$ErrorActionPreference = "Stop"
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$rows = @(Import-Csv -LiteralPath $resolvedConfig)
if ($rows.Count -eq 0) {
    throw "The configuration CSV is empty."
}
$columns = @($rows[0].PSObject.Properties.Name)
if ($columns.Count -lt 2) {
    throw "The configuration CSV must contain key and value columns."
}
$keyColumn = $columns[0]
$valueColumn = $columns[1]
$settings = @{}
foreach ($row in $rows) {
    $name = [string]$row.$keyColumn
    $value = [string]$row.$valueColumn
    if (-not [string]::IsNullOrWhiteSpace($name)) {
        $settings[$name.Trim()] = $value.Trim()
    }
}

foreach ($required in @("apiKey", "apiHost", "openAiCompatible", "dashScope")) {
    if ([string]::IsNullOrWhiteSpace([string]$settings[$required])) {
        throw "The configuration CSV is missing $required."
    }
}
$dashScopeUri = [Uri]$settings["dashScope"]
$openAiUri = [Uri]$settings["openAiCompatible"]
if ($dashScopeUri.Scheme -ne "https" -or $openAiUri.Scheme -ne "https") {
    throw "Bailian service URLs must use HTTPS."
}
if ($dashScopeUri.Host -ne $settings["apiHost"] -or $openAiUri.Host -ne $settings["apiHost"]) {
    throw "The API Host does not match the service URLs."
}
if (-not $dashScopeUri.AbsolutePath.TrimEnd("/").EndsWith("/api/v1")) {
    throw "The DashScope URL must end with /api/v1."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentNames = @(
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "QRA_OCR_PROVIDER",
    "QRA_ALIYUN_API_KEY",
    "QRA_ALIYUN_DASHSCOPE_URL",
    "QRA_ALIYUN_OPENAI_BASE_URL",
    "QRA_OCR_MODEL_VERSION",
    "QRA_VISION_MODEL_VERSION",
    "QRA_OCR_TIMEOUT_SECONDS",
    "QRA_OCR_MAX_RETRIES"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $env:PYTHONPATH = Join-Path $projectRoot "src"
    $env:PYTHONIOENCODING = "utf-8"
    $env:QRA_OCR_PROVIDER = "aliyun-bailian"
    $env:QRA_ALIYUN_API_KEY = $settings["apiKey"]
    $env:QRA_ALIYUN_DASHSCOPE_URL = $settings["dashScope"]
    $env:QRA_ALIYUN_OPENAI_BASE_URL = $settings["openAiCompatible"]
    $env:QRA_OCR_MODEL_VERSION = $OcrModel
    $env:QRA_VISION_MODEL_VERSION = $VisionModel
    $env:QRA_OCR_TIMEOUT_SECONDS = [string]$OcrTimeoutSeconds
    $env:QRA_OCR_MAX_RETRIES = "2"

    Write-Host "Bailian configuration loaded from CSV. The API Key is not printed or stored."
    Write-Host "OCR model: $OcrModel; reserved vision model: $VisionModel; host: $($dashScopeUri.Host)"
    Push-Location $projectRoot
    try {
        if ($TestOnly) {
            python .\tools\test_bailian_ocr.py
            if ($LASTEXITCODE -ne 0) {
                throw "The Bailian OCR connectivity test failed."
            }
        }
        else {
            Write-Host "Open http://${ListenAddress}:$Port/admin/ after the service starts."
            python -m db_qra serve --host $ListenAddress --port $Port
            if ($LASTEXITCODE -ne 0) {
                throw "The QRA service exited with code $LASTEXITCODE."
            }
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
}
