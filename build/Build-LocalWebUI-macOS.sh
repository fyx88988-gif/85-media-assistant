#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
frontend="$project_root/webui/frontend"
engine="$project_root/webui/engine"
version="${VERSION:?请通过 VERSION 指定版本号，例如 1.2.0}"
target_arch="${TARGET_ARCH:-$(uname -m)}"
case "$target_arch" in
  arm64|x64) ;;
  x86_64) target_arch="x64" ;;
  *) echo "不支持的 macOS 架构：$target_arch" >&2; exit 2 ;;
esac

host_arch="$(uname -m)"
if [[ "$target_arch" == "x64" && "$host_arch" != "x86_64" ]]; then
  echo "x64 安装包必须在 Intel macOS 构建机上生成。" >&2
  exit 2
fi
if [[ "$target_arch" == "arm64" && "$host_arch" != "arm64" ]]; then
  echo "arm64 安装包必须在 Apple Silicon 构建机上生成。" >&2
  exit 2
fi

runtime_source="${MEDIA_ASSISTANT_RUNTIME:-$project_root/runtime/macos-$target_arch}"
build_root="$project_root/.build/local-webui/macos-$target_arch"
pyinstaller_root="$build_root/pyinstaller"
app="$build_root/85数字多媒体下载助手.app"
macos="$app/Contents/MacOS"
resources="$app/Contents/Resources"
# Versioned application layout: Contents/Resources/versions/<version>.
version_root="$resources/versions/$version"

for required in \
  "$runtime_source/yt-dlp/yt-dlp" \
  "$runtime_source/ffmpeg/ffmpeg" \
  "$runtime_source/ffmpeg/ffprobe" \
  "$runtime_source/deno/deno"; do
  if [[ ! -f "$required" ]]; then
    echo "首次安装包缺少必需组件：$required" >&2
    exit 3
  fi
done

cd "$frontend"
npm ci
npm run build
rm -rf "$engine/src/media_assistant/static/assets"
mkdir -p "$engine/src/media_assistant/static"
cp "$frontend/dist/index.html" "$engine/src/media_assistant/static/index.html"
cp -R "$frontend/dist/assets" "$engine/src/media_assistant/static/assets"

rm -rf "$build_root"
mkdir -p "$pyinstaller_root" "$macos" \
  "$version_root/engine" "$version_root/webui" \
  "$resources/components" "$resources/templates" "$resources/config"

python3 -m PyInstaller --noconfirm --clean --onefile \
  --name "85数字多媒体下载助手" --paths "$engine/src" \
  --distpath "$pyinstaller_root/launcher-dist" \
  --workpath "$pyinstaller_root/launcher-work" \
  --specpath "$pyinstaller_root/launcher-spec" "$engine/run_launcher.py"

python3 -m PyInstaller --noconfirm --clean --onefile \
  --name "85数字多媒体下载助手引擎" --paths "$engine/src" \
  --add-data "$engine/src/media_assistant/static:media_assistant/static" \
  --distpath "$pyinstaller_root/engine-dist" \
  --workpath "$pyinstaller_root/engine-work" \
  --specpath "$pyinstaller_root/engine-spec" "$engine/run_webui.py"

python3 -m PyInstaller --noconfirm --clean --onefile \
  --name "85数字多媒体下载助手更新器" --paths "$engine/src" \
  --distpath "$pyinstaller_root/updater-dist" \
  --workpath "$pyinstaller_root/updater-work" \
  --specpath "$pyinstaller_root/updater-spec" "$engine/run_updater.py"

cp "$pyinstaller_root/launcher-dist/85数字多媒体下载助手" \
  "$macos/85数字多媒体下载助手"
cp "$pyinstaller_root/updater-dist/85数字多媒体下载助手更新器" \
  "$macos/85数字多媒体下载助手更新器"
cp "$pyinstaller_root/engine-dist/85数字多媒体下载助手引擎" \
  "$version_root/engine/85数字多媒体下载助手引擎"
cp -R "$frontend/dist/." "$version_root/webui/"
cp -R "$runtime_source/yt-dlp" "$resources/components/yt-dlp"
cp -R "$runtime_source/ffmpeg" "$resources/components/ffmpeg"
cp -R "$runtime_source/deno" "$resources/components/deno"
cp "$project_root/build/installer/macos/com.85digital.media-assistant.plist" \
  "$resources/templates/com.85digital.media-assistant.plist"
printf '{"version":"%s"}' "$version" > "$resources/current.json"

if [[ -n "${MEDIA_ASSISTANT_RELEASE_PUBLIC_KEY:-}" ]]; then
  release_repository="${GITHUB_REPOSITORY:-OWNER/REPOSITORY}"
  printf '{"channel":"stable","manifestUrl":"https://github.com/%s/releases/latest/download/update-manifest.json","manifestSignatureUrl":"https://github.com/%s/releases/latest/download/update-manifest.sig","mirrors":[],"publicKey":"%s"}' \
    "$release_repository" "$release_repository" "$MEDIA_ASSISTANT_RELEASE_PUBLIC_KEY" \
    > "$resources/config/update-channel.json"
fi

mkdir -p "$resources/launcher"
ln -s "../../MacOS/85数字多媒体下载助手" \
  "$resources/launcher/85数字多媒体下载助手"
ln -s "../../MacOS/85数字多媒体下载助手更新器" \
  "$resources/launcher/85数字多媒体下载助手更新器"

cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDisplayName</key><string>85数字多媒体下载助手</string>
  <key>CFBundleExecutable</key><string>85数字多媒体下载助手</string>
  <key>CFBundleIdentifier</key><string>com.85digital.media-assistant</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>85数字多媒体下载助手</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

chmod +x \
  "$macos/85数字多媒体下载助手" \
  "$macos/85数字多媒体下载助手更新器" \
  "$version_root/engine/85数字多媒体下载助手引擎" \
  "$resources/components/yt-dlp/yt-dlp" \
  "$resources/components/ffmpeg/ffmpeg" \
  "$resources/components/ffmpeg/ffprobe" \
  "$resources/components/deno/deno"

echo "$app"
