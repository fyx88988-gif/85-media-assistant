#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/../../.." && pwd)"
version="${VERSION:?请通过 VERSION 指定版本号，例如 1.2.0}"
target_arch="${TARGET_ARCH:-$(uname -m)}"
[[ "$target_arch" == "x86_64" ]] && target_arch="x64"

VERSION="$version" TARGET_ARCH="$target_arch" \
  "$project_root/build/Build-LocalWebUI-macOS.sh"

app="$project_root/.build/local-webui/macos-$target_arch/85数字多媒体下载助手.app"
stage="$project_root/.build/local-webui/macos-$target_arch/dmg"
output_dir="${OUTPUT_DIR:-$project_root/dist/macos}"
output="$output_dir/85数字多媒体下载助手-$version-$target_arch.dmg"

# Ad-hoc signing keeps the custom bundle internally consistent. A paid Apple
# Developer certificate can replace this signature in a future notarized release.
codesign --force --deep --sign - "$app"

rm -rf "$stage"
mkdir -p "$stage" "$output_dir"
cp -R "$app" "$stage/85数字多媒体下载助手.app"
ln -s /Applications "$stage/Applications"
rm -f "$output"
hdiutil create -volname "85数字多媒体下载助手" \
  -srcfolder "$stage" -ov -format UDZO "$output"

echo "$output"
