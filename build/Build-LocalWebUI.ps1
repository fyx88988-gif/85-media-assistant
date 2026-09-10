param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,
    [string]$OutputRoot = "D:\导出的文件夹\85数字多媒体下载助手",
    [string]$RuntimeRoot,
    [string]$UpdatePublicKey = "",
    [string]$ReleaseRepository = "OWNER/REPOSITORY",
    [switch]$StagingOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $projectRoot "webui\frontend"
$engine = Join-Path $projectRoot "webui\engine"
$localPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $localPython -PathType Leaf) {
    $localPython
} else {
    Get-Command python.exe -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source -First 1
}
if (-not $python) {
    throw "未找到可用的 Python；请安装 Python 3.12，或在项目目录创建 .venv。"
}
$sourceRuntime = if ($RuntimeRoot) {
    (Resolve-Path $RuntimeRoot).Path
} else {
    (Resolve-Path (Join-Path $projectRoot "..\..\runtime")).Path
}
$buildRoot = Join-Path $projectRoot ".build\local-webui"
$stagingRoot = Join-Path $buildRoot "windows-staging"
$pyinstallerRoot = Join-Path $buildRoot "pyinstaller-windows"

function Reset-TaskDirectory([string]$Path, [string]$RequiredParent) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullParent = [IO.Path]::GetFullPath($RequiredParent).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($fullParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理任务目录之外的路径：$fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $fullPath | Out-Null
}

foreach ($required in @(
    $python,
    (Join-Path $sourceRuntime "yt-dlp\yt-dlp.exe"),
    (Join-Path $sourceRuntime "ffmpeg\ffmpeg.exe"),
    (Join-Path $sourceRuntime "ffmpeg\ffprobe.exe"),
    (Join-Path $sourceRuntime "deno\deno.exe")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "首次安装包缺少必需组件：$required"
    }
}

Push-Location $frontend
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "WebUI 生产构建失败。" }
} finally {
    Pop-Location
}

$staticDir = Join-Path $engine "src\media_assistant\static"
$staticAssets = Join-Path $staticDir "assets"
if (Test-Path -LiteralPath $staticAssets) {
    Remove-Item -LiteralPath $staticAssets -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staticDir | Out-Null
Copy-Item -LiteralPath (Join-Path $frontend "dist\index.html") -Destination (Join-Path $staticDir "index.html") -Force
Copy-Item -LiteralPath (Join-Path $frontend "dist\assets") -Destination $staticDir -Recurse -Force

Reset-TaskDirectory $stagingRoot $buildRoot
Reset-TaskDirectory $pyinstallerRoot $buildRoot

$launcherDist = Join-Path $pyinstallerRoot "launcher-dist"
$engineDist = Join-Path $pyinstallerRoot "engine-dist"
$updaterDist = Join-Path $pyinstallerRoot "updater-dist"

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "85数字多媒体下载助手" --paths (Join-Path $engine "src") `
    --distpath $launcherDist --workpath (Join-Path $pyinstallerRoot "launcher-work") `
    --specpath (Join-Path $pyinstallerRoot "launcher-spec") (Join-Path $engine "run_launcher.py")
if ($LASTEXITCODE -ne 0) { throw "启动器构建失败。" }

& $python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name "85数字多媒体下载助手引擎" --paths (Join-Path $engine "src") `
    --add-data "$staticDir;media_assistant/static" --distpath $engineDist `
    --workpath (Join-Path $pyinstallerRoot "engine-work") --specpath (Join-Path $pyinstallerRoot "engine-spec") `
    (Join-Path $engine "run_webui.py")
if ($LASTEXITCODE -ne 0) { throw "本地引擎构建失败。" }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "85数字多媒体下载助手更新器" --paths (Join-Path $engine "src") `
    --distpath $updaterDist --workpath (Join-Path $pyinstallerRoot "updater-work") `
    --specpath (Join-Path $pyinstallerRoot "updater-spec") (Join-Path $engine "run_updater.py")
if ($LASTEXITCODE -ne 0) { throw "自动更新器构建失败。" }

$launcherTarget = Join-Path $stagingRoot "launcher"
$versionTarget = Join-Path $stagingRoot "versions\$Version"
$engineTarget = Join-Path $versionTarget "engine"
$webuiTarget = Join-Path $versionTarget "webui"
$componentsTarget = Join-Path $stagingRoot "components"
New-Item -ItemType Directory -Force -Path $launcherTarget, $versionTarget, $componentsTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $launcherDist "85数字多媒体下载助手.exe") -Destination $launcherTarget -Force
Copy-Item -LiteralPath (Join-Path $updaterDist "85数字多媒体下载助手更新器.exe") -Destination $launcherTarget -Force
Copy-Item -LiteralPath (Join-Path $engineDist "85数字多媒体下载助手引擎") -Destination $engineTarget -Recurse -Force
Copy-Item -LiteralPath (Join-Path $frontend "dist") -Destination $webuiTarget -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sourceRuntime "yt-dlp") -Destination (Join-Path $componentsTarget "yt-dlp") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sourceRuntime "ffmpeg") -Destination (Join-Path $componentsTarget "ffmpeg") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sourceRuntime "deno") -Destination (Join-Path $componentsTarget "deno") -Recurse -Force

$douyinTarget = Join-Path $componentsTarget "douyin"
New-Item -ItemType Directory -Force -Path $douyinTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "src\VideoDownloader.Douyin.psm1") -Destination $douyinTarget -Force
Copy-Item -LiteralPath (Join-Path $engine "scripts\Refresh-DouyinSession.ps1") -Destination $douyinTarget -Force

$pointer = @{ version = $Version } | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $stagingRoot "current.json"), $pointer, [Text.UTF8Encoding]::new($false))

if ($UpdatePublicKey) {
    $configTarget = Join-Path $stagingRoot "config"
    New-Item -ItemType Directory -Force -Path $configTarget | Out-Null
    $channel = @{
        channel = "stable"
        manifestUrl = "https://github.com/$ReleaseRepository/releases/latest/download/update-manifest.json"
        manifestSignatureUrl = "https://github.com/$ReleaseRepository/releases/latest/download/update-manifest.sig"
        mirrors = @()
        publicKey = $UpdatePublicKey
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $configTarget "update-channel.json"), $channel, [Text.UTF8Encoding]::new($false))
}

if ($StagingOnly) {
    Write-Output $stagingRoot
    exit 0
}

$isccCandidates = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    "D:\APP\Inno Setup 6\ISCC.exe",
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
$iscc = $isccCandidates | Select-Object -First 1
if (-not $iscc) { throw "未找到 Inno Setup 6，无法生成最终单一安装包。" }

$windowsOutput = Join-Path $OutputRoot "Windows"
New-Item -ItemType Directory -Force -Path $windowsOutput | Out-Null
& $iscc "/DAppVersion=$Version" "/DSourceRoot=$stagingRoot" "/DOutputRoot=$windowsOutput" (Join-Path $projectRoot "build\installer\windows\85-media-assistant.iss")
if ($LASTEXITCODE -ne 0) { throw "Windows 安装包生成失败。" }
Write-Output (Join-Path $windowsOutput "85数字多媒体下载助手-Setup-$Version.exe")
