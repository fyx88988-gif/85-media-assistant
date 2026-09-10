# 85数字多媒体下载助手

一个以网页作为操作界面、在 Windows 与 macOS 本机完成识别和下载的多媒体工具。它用于保存您有权下载的公开作品；不会绕过付费、DRM、登录验证、地区限制或平台访问控制。

## 直接使用

- 统一网页：<https://fyx88988-gif.github.io/85-media-assistant/>
- Windows 与 macOS 使用同一个网页。
- 每台电脑第一次使用时，只需安装一次网页提供的本地引擎；yt-dlp、FFmpeg、FFprobe、Deno 与必要组件均已内置，无需逐个安装插件。
- 安装后仍从统一网页进入。网页会自动连接本机的 `http://127.0.0.1:8515`。
- 识别、预览、下载、合并和保存均在当前电脑运行，不需要付费云服务器，媒体与平台会话不会上传到本项目。

## 自动更新

安装包内置签名更新通道。程序、网页和媒体组件会从本仓库的 GitHub Releases 获取签名更新；更新失败或离线时继续使用当前版本，并保留设置、历史和下载文件。

Windows 可能显示 SmartScreen 提示，macOS 可能在首次打开时要求在“隐私与安全性”中确认。这是因为当前发布包未使用付费的微软/苹果商业代码签名证书，不表示还要安装第二个插件。

## 开发与验证

```powershell
python -m pip install -e "webui/engine[test]" pyinstaller pyyaml
npm ci --prefix webui/frontend
python -m pytest webui/engine/tests build/release/tests -q
npm test --prefix webui/frontend -- --run
npm run build --prefix webui/frontend
node distribution/download-page.test.mjs
```

正式版本由 `.github/workflows/release-local-webui.yml` 在版本标签推送后生成：

- Windows x64 单一安装程序
- macOS Apple Silicon DMG
- macOS Intel DMG
- 网页、本地引擎与内置组件的签名更新文件
- GitHub Pages 统一下载页

## 隐私与使用边界

- 不读取浏览器现有个人配置、历史或 Cookie；平台需要验证时，只使用本工具创建的隔离会话。
- 下载能力受作品公开状态、当前网络、平台规则、登录验证、地区限制和 DRM 影响。
- 请只下载您拥有权利或已获得许可的内容，并遵守作品所在地法律和平台条款。
