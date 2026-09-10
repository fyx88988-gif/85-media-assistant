param(
    [Parameter(Mandatory = $true)]
    [string]$SpecPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$generator = Join-Path $PSScriptRoot "new_update_manifest.py"

if (-not (Test-Path -LiteralPath $SpecPath -PathType Leaf)) {
    throw "发布说明文件不存在：$SpecPath"
}
if (-not $env:MEDIA_ASSISTANT_RELEASE_PRIVATE_KEY) {
    throw "缺少发布签名密钥 MEDIA_ASSISTANT_RELEASE_PRIVATE_KEY。"
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $projectRoot "webui\engine\src"
    & $Python $generator --spec $SpecPath --output-dir $OutputDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "更新清单生成失败。"
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

foreach ($name in @("update-manifest.json", "update-manifest.sig")) {
    $path = Join-Path $OutputDirectory $name
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    Write-Output "$path  SHA256=$hash"
}
