# 85 数字多媒体下载助手：本地引擎稳定性实施计划

> 依据：`docs/superpowers/specs/2026-09-19-local-engine-reliability-design.md`  
> 目标版本：v1.3.0  
> 实施原则：在现有工程上增量加固；每一步先写失败测试，再写最小实现；每个任务单独提交。

## 总体交付结果

完成后只保留一套产品：GitHub Pages 统一入口 + 当前电脑的本地 WebUI。用户第一次在 Windows x64 或 macOS Apple Silicon 上安装本地引擎，以后打开统一网址即可进入；本地守护程序节能常驻、自动恢复，任务与设置可在异常退出后恢复。未来发布不再构建 macOS Intel 包。

## 任务 1：锁定恢复后的基线

**涉及文件**

- 检查：`README.md`
- 检查：`.github/workflows/release-local-webui.yml`
- 检查：`webui/engine/tests/`
- 检查：`webui/frontend/src/`
- 新建：`docs/verification/v1.3.0-baseline.md`

**步骤**

1. 记录仓库恢复基线 `b15b844`、当前工具链版本和支持平台。
2. 运行现有后端、前端、下载页和构建脚本测试，记录成功项与环境缺失项。
3. 运行前端正式构建，确认静态文件可生成。
4. 将基线结果写入验证文档；不得为通过测试而先改产品代码。

**验证命令**

```powershell
python -m pytest webui/engine/tests build/release/tests -q
npm test --prefix webui/frontend -- --run
npm run build --prefix webui/frontend
node distribution/download-page.test.mjs
```

**提交**

```text
test: record restored v1.3 baseline
```

## 任务 2：删除未来发布中的 macOS Intel 路径

**涉及文件**

- 修改：`.github/workflows/release-local-webui.yml`
- 修改：`build/Build-LocalWebUI-macOS.sh`
- 修改：`distribution/index.html`
- 修改：`distribution/download-select.mjs`
- 修改：`distribution/download-page.test.mjs`
- 修改：`README.md`
- 修改：`webui/engine/tests/test_macos_package.py`
- 修改：`webui/engine/tests/test_update_manifest.py`

**测试先行**

1. 在下载页测试中新增断言：页面不得包含 `media-assistant-macos-x64.dmg` 或 Intel 下载按钮。
2. 在发布测试中新增断言：工作流矩阵只有 `arm64`，更新清单样例不得产生 `macos/x64` 项。
3. 运行上述测试并确认因现有 Intel 路径而失败。

**实现**

1. 删除 Actions 的 Intel runner 和 x64 macOS 制品循环。
2. 将 macOS 构建脚本限制为 `arm64`，对 x64 参数返回清晰错误。
3. 删除入口页手动 Intel 下载按钮；Mac 用户统一收到 Apple 芯片安装包。
4. 更新 README 的发布清单，只列 Windows x64 与 macOS Apple Silicon。
5. 不删除历史 Release 中已经存在的 Intel 文件。

**验证命令**

```powershell
node distribution/download-page.test.mjs
python -m pytest webui/engine/tests/test_macos_package.py webui/engine/tests/test_update_manifest.py build/release/tests -q
```

**提交**

```text
build: support Apple Silicon macOS only
```

## 任务 3：实现有退避和熔断的轻量守护程序

**涉及文件**

- 新建：`webui/engine/src/media_assistant/supervisor.py`
- 新建：`webui/engine/tests/test_supervisor.py`
- 修改：`webui/engine/src/media_assistant/bootstrap.py`
- 修改：`webui/engine/tests/test_bootstrap.py`

**测试先行**

1. 测试健康时不启动新进程。
2. 测试异常时按 `2, 5, 15, 60` 秒退避，后续失败保持 60 秒上限。
3. 测试在限定时间窗口内连续失败达到阈值后进入熔断，不再无限启动。
4. 测试经过冷却期或显式修复请求后可以退出熔断并重试。
5. 测试同一用户会话只有一个守护程序实例。
6. 测试每次检查只执行健康探测，不加载 yt-dlp、FFmpeg、Deno 或浏览器。

**实现**

1. 提取 `SupervisorPolicy`、`SupervisorState` 与纯状态转换逻辑，时间源和休眠函数可注入，保证测试无需真实等待。
2. 守护程序空闲检查周期设为约 15 秒；仅在健康失败时进入恢复序列。
3. 将错误原因、连续失败次数、下次重试时间写入原子状态文件。
4. `bootstrap.py --background` 改为调用新守护程序；前台协议启动则请求立即恢复并在健康后打开浏览器。
5. 保留现有进程无窗口启动和单实例锁。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_supervisor.py webui/engine/tests/test_bootstrap.py -q
```

**提交**

```text
feat(engine): add backoff and circuit breaker supervisor
```

## 任务 4：增加滚动日志、脱敏和诊断报告

**涉及文件**

- 新建：`webui/engine/src/media_assistant/logging_config.py`
- 新建：`webui/engine/src/media_assistant/diagnostics.py`
- 新建：`webui/engine/tests/test_logging_config.py`
- 新建：`webui/engine/tests/test_diagnostics.py`
- 修改：`webui/engine/src/media_assistant/bootstrap.py`
- 修改：`webui/engine/src/media_assistant/launcher.py`

**测试先行**

1. 测试日志按大小轮换且历史文件总数受限。
2. 测试会话令牌、Cookie、带签名媒体 URL 查询参数不会写入日志。
3. 测试诊断结果覆盖：端口占用、启动项、当前版本指针、组件存在性、磁盘可写性和服务健康。
4. 测试诊断只返回安全摘要，不返回本地密钥和完整平台会话。

**实现**

1. 建立统一日志初始化，分别标记 supervisor、api、recognition、download、update。
2. 使用标准库滚动日志处理器，限制单文件和备份数量。
3. 建立诊断数据模型和检查器；检查本身只读，不自动删除数据。
4. 启动失败时写入可理解的错误码和建议动作。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_logging_config.py webui/engine/tests/test_diagnostics.py -q
```

**提交**

```text
feat(engine): add safe diagnostics and rolling logs
```

## 任务 5：建立 SQLite 本地状态仓库

**涉及文件**

- 新建：`webui/engine/src/media_assistant/state_store.py`
- 新建：`webui/engine/tests/test_state_store.py`
- 修改：`webui/engine/src/media_assistant/models.py`
- 修改：`webui/engine/src/media_assistant/install_layout.py`

**测试先行**

1. 测试首次启动自动创建数据库和版本表。
2. 测试作品、下载任务、设置和恢复元数据可事务写入并重新加载。
3. 测试重复任务 ID 使用幂等更新，不创建副本。
4. 测试写入中断时保留上一个完整状态。
5. 测试未知的新数据库版本会安全拒绝启动，而不是破坏数据。

**实现**

1. 使用 Python 标准库 `sqlite3`，数据库位于用户数据目录，不引入额外数据库运行时。
2. 开启外键、合理的 busy timeout 和 WAL；提供显式迁移版本。
3. 仓库 API 只接受领域模型，不让业务代码散落 SQL。
4. 所有结构化状态写入事务；数据库损坏时保留原文件并报告修复需求。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_state_store.py -q
```

**提交**

```text
feat(engine): persist local state in sqlite
```

## 任务 6：持久化作品队列和识别状态

**涉及文件**

- 修改：`webui/engine/src/media_assistant/items.py`
- 修改：`webui/engine/src/media_assistant/launcher.py`
- 修改：`webui/engine/tests/test_items_api.py`
- 新建：`webui/engine/tests/test_item_recovery.py`

**测试先行**

1. 测试引擎重启后已识别作品仍存在并保持顺序。
2. 测试启动时把遗留的 `recognizing` 状态转为可重试状态，不永久卡住。
3. 测试删除和清空操作同时更新内存与数据库。
4. 测试平台临时会话失败只把当前作品标记失败，不终止工作进程。
5. 测试重复提交同一恢复请求不会产生重复作品。

**实现**

1. `ItemService` 接收状态仓库，在每次状态转换后事务保存。
2. 服务启动时加载作品并恢复可安全重试的任务。
3. 保留现有 EventBus 行为，恢复后前端能收到一致状态。
4. 将异常转换为现有失败信息模型，并在日志中保留安全错误码。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_items_api.py webui/engine/tests/test_item_recovery.py webui/engine/tests/test_recognition.py -q
```

**提交**

```text
feat(engine): recover persisted recognition queue
```

## 任务 7：持久化下载任务并支持安全恢复

**涉及文件**

- 修改：`webui/engine/src/media_assistant/downloads.py`
- 修改：`webui/engine/src/media_assistant/processes.py`
- 修改：`webui/engine/src/media_assistant/launcher.py`
- 修改：`webui/engine/tests/test_downloads.py`
- 新建：`webui/engine/tests/test_download_recovery.py`

**测试先行**

1. 测试任务创建、进度、完成、失败和取消状态全部持久化。
2. 测试重启后遗留的 `downloading` 状态变为“等待恢复”，而不是误报完成。
3. 测试临时媒体文件保留，允许 yt-dlp 使用 `.part` 文件继续下载。
4. 测试恢复和浏览器重复点击不会启动两个相同任务。
5. 测试成功输出先验证，再标记完成；失败输出不播放完成提示音。
6. 测试取消时终止整个子进程树并释放进程引用。
7. 测试活动下载计数供更新系统和守护程序读取。

**实现**

1. 为 `DownloadService` 注入状态仓库和稳定任务 ID。
2. 启动参数保留 yt-dlp 默认 `.part` 行为，并避免清理可恢复分片。
3. 引擎启动时恢复未完成任务元数据；由用户继续或按安全策略自动重试。
4. 完成、失败和取消后及时移除进程/任务对象，确保重型进程不常驻。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_downloads.py webui/engine/tests/test_download_recovery.py webui/engine/tests/test_processes.py -q
```

**提交**

```text
feat(engine): recover interrupted download jobs
```

## 任务 8：提供健康、恢复和一键修复 API

**涉及文件**

- 修改：`webui/engine/src/media_assistant/app.py`
- 修改：`webui/engine/src/media_assistant/config.py`
- 修改：`webui/engine/src/media_assistant/security.py`
- 修改：`webui/engine/tests/test_app.py`
- 新建：`webui/engine/tests/test_repair_api.py`

**测试先行**

1. 测试公开健康接口只返回产品 ID、版本和最小状态。
2. 测试诊断和修复接口必须通过本地会话验证。
3. 测试允许的统一入口 Origin 可读取最小健康状态，其他 Origin 被拒绝。
4. 测试修复不删除下载文件、作品记录、设置或会话目录。
5. 测试修复可清理安全临时目录、重建启动配置并触发守护程序重试。

**实现**

1. 新增 `/api/v1/diagnostics` 与 `/api/v1/repair`。
2. 健康接口添加 supervisor 状态字段，但不返回敏感路径或令牌。
3. 仅对正式 GitHub Pages Origin 开放最小 CORS；业务 API 仍要求本地令牌。
4. 修复动作采用白名单，任何用户数据删除均不包含在一键修复内。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_app.py webui/engine/tests/test_repair_api.py webui/engine/tests/test_local_session.py -q
```

**提交**

```text
feat(engine): expose guarded diagnostics and repair
```

## 任务 9：让本地 WebUI 在短暂断线后自动恢复

**涉及文件**

- 修改：`webui/frontend/src/api.ts`
- 新建：`webui/frontend/src/engine-connection.ts`
- 修改：`webui/frontend/src/components/Workbench.tsx`
- 修改：`webui/frontend/src/components/Workbench.test.tsx`
- 修改：`webui/frontend/src/components/Workbench.integration.test.tsx`

**测试先行**

1. 测试 API 网络失败后按有限退避重连，不高频轮询。
2. 测试断线时保留输入框、当前作品、选择格式和下载按钮状态。
3. 测试恢复后从后端重新同步作品和下载任务，不重复提交。
4. 测试熔断或需要修复时显示明确操作按钮。
5. 测试页面卸载时清理计时器和请求控制器。

**实现**

1. 建立 `connected / reconnecting / repair-required` 连接状态。
2. 将临时 UI 选择保存在 `sessionStorage`，后端仍是作品与任务状态真源。
3. 用单一连接控制器统一 API 错误与恢复，不在各组件复制定时器。
4. 下载完成提示音只在任务首次从非完成状态进入完成状态时播放。

**验证命令**

```powershell
npm test --prefix webui/frontend -- --run Workbench
npm run build --prefix webui/frontend
```

**提交**

```text
feat(webui): recover state after local engine reconnect
```

## 任务 10：锁定预览比例与播放器回归行为

**涉及文件**

- 修改：`webui/frontend/src/components/PreviewStage.test.tsx`
- 修改：`webui/frontend/src/components/PreviewStage.tsx`
- 修改：`webui/frontend/src/components/Workbench.integration.test.tsx`
- 修改：`webui/frontend/src/styles/layout.css`

**测试先行**

1. 测试 16:9、9:16、1:1 和旋转 90° 后的有效宽高比。
2. 测试横版铺满横版媒体框，竖版扩大到视口可用高度且不裁剪。
3. 测试作品切换后整个中间工作区按真实媒体方向更新。
4. 测试旋转、时间码、拖动、倍速、分辨率切换和全屏均调用真实播放器行为。
5. 测试小高度屏幕下底部操作栏始终可见，不依赖页面整体滚动。

**实现**

1. 继续以视频元数据 `videoWidth/videoHeight` 为最高优先级，识别数据只作加载前占位。
2. 将旋转后的有效尺寸统一传给工作台布局，不在 CSS 中写死竖版宽度。
3. 使用 `object-fit: contain` 保持完整画面；16:9 容器与视频同尺度时消除无意义内边距。
4. 保持播放器控制条半透明，并确保所有显示控件均有实际功能。

**验证命令**

```powershell
npm test --prefix webui/frontend -- --run PreviewStage Workbench
npm run build --prefix webui/frontend
```

**提交**

```text
fix(webui): keep preview geometry and controls functional
```

## 任务 11：重做统一入口页连接状态机

**涉及文件**

- 修改：`distribution/index.html`
- 修改：`distribution/download-select.mjs`
- 新建：`distribution/engine-connection.mjs`
- 修改：`distribution/download-page.test.mjs`

**测试先行**

1. 测试未安装、连接中、已连接、恢复中、需要修复五种显示状态。
2. 测试所有主要按钮点击后立即更新文字或进度，禁止无反馈。
3. 测试 Windows 和 macOS Apple Silicon 下载选择正确，Intel 与 Linux 不产生错误包链接。
4. 测试本地健康探测超时后显示安装/修复入口，而不是无限等待。
5. 测试浏览器阻止健康探测时仍可用 `mediaassistant85://open` 作为用户触发的后备路径。

**实现**

1. 将状态判断从 HTML 内联代码提取为可测试模块。
2. 使用短超时访问最小健康接口；失败不自动反复弹外部协议。
3. “打开工具”直接访问本地地址；返回后仍失败则显示“启动或修复”。
4. 首次使用只显示匹配系统的推荐安装包，同时保留清晰的手动系统选择。
5. 明确说明第一次安装需系统确认，不能宣传无确认静默安装。

**验证命令**

```powershell
node distribution/download-page.test.mjs
```

**提交**

```text
feat(distribution): add deterministic local engine states
```

## 任务 12：完善 Windows 安装、启动和无窗口行为

**涉及文件**

- 修改：`build/installer/windows/85-media-assistant.iss`
- 修改：`build/Build-LocalWebUI.ps1`
- 修改：`webui/engine/tests/test_windows_package.py`
- 修改：`webui/engine/tests/test_windows_build_script.py`
- 修改：`webui/engine/tests/test_install_layout.py`

**测试先行**

1. 测试安装包注册 HKCU 登录启动和 `mediaassistant85://`。
2. 测试不创建桌面图标，只保留开始菜单卸载/启动入口。
3. 测试安装结束启动守护程序，健康成功后打开浏览器。
4. 测试后台进程及其子进程使用无窗口标志，不弹终端。
5. 测试覆盖升级保留数据目录。

**实现**

1. 保留用户级安装和 HKCU Run；后台启动固定加 `--background`。
2. 安装后先启动守护程序，再使用协议或前台启动器打开本地 WebUI。
3. 检查 PyInstaller 构建类型为 windowed/noconsole；所有子进程继承无窗口策略。
4. 明确卸载范围：删除程序与启动项，不默认删除用户下载及状态库。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_windows_package.py webui/engine/tests/test_windows_build_script.py webui/engine/tests/test_install_layout.py -q
```

**提交**

```text
fix(windows): launch local webui silently after install
```

## 任务 13：完善 macOS Apple Silicon 安装和 LaunchAgent

**涉及文件**

- 修改：`build/installer/macos/com.85digital.media-assistant.plist`
- 修改：`build/installer/macos/build-dmg.sh`
- 修改：`build/Build-LocalWebUI-macOS.sh`
- 修改：`webui/engine/src/media_assistant/bootstrap.py`
- 修改：`webui/engine/tests/test_macos_package.py`
- 修改：`webui/engine/tests/test_bootstrap.py`

**测试先行**

1. 测试构建脚本拒绝非 arm64 目标。
2. 测试 LaunchAgent 使用当前用户域、登录启动、后台进程类型和正确路径。
3. 测试重复安装幂等更新 LaunchAgent，不产生多个实例。
4. 测试自定义协议注册在应用包中并能请求打开本地 WebUI。
5. 测试不启动 Terminal，所有日志写入产品日志目录。

**实现**

1. 将 `KeepAlive` 改为与应用熔断策略协调的配置，避免 launchd 与内部守护程序互相造成快速重启。
2. 安装/首次启动时幂等 bootstrap LaunchAgent。
3. App Bundle 只声明 arm64，并保留 URL Scheme。
4. 在 Gatekeeper 未确认时给出明确步骤；确认后直接启动守护程序和浏览器。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_macos_package.py webui/engine/tests/test_bootstrap.py -q
```

**提交**

```text
fix(macos): stabilize Apple Silicon background launch
```

## 任务 14：把活动任务保护接入签名更新与回退

**涉及文件**

- 修改：`webui/engine/src/media_assistant/updater.py`
- 修改：`webui/engine/src/media_assistant/update_runner.py`
- 修改：`webui/engine/src/media_assistant/bootstrap.py`
- 修改：`webui/engine/tests/test_updater.py`
- 修改：`webui/engine/tests/test_update_runner.py`
- 修改：`webui/engine/tests/test_upgrade_acceptance.py`

**测试先行**

1. 测试有活动下载时只暂存更新，不重启或切换引擎。
2. 测试任务结束后才应用已暂存更新。
3. 测试新版本签名、哈希和启动健康任一失败都回退旧版本。
4. 测试回退保留 SQLite 数据库、设置、平台隔离会话和下载文件。
5. 测试离线时继续运行当前版本并展示可理解状态。

**实现**

1. 将状态仓库的活动下载计数接入更新协调器。
2. 更新切换使用版本目录和原子指针，继续复用现有签名校验。
3. 新版本启动后进行限定时间健康检查；失败恢复旧指针并写入 `rollback-complete`。
4. 守护程序只在更新协调器给出安全重启信号后重启服务。

**验证命令**

```powershell
python -m pytest webui/engine/tests/test_updater.py webui/engine/tests/test_update_runner.py webui/engine/tests/test_upgrade_acceptance.py build/release/tests -q
```

**提交**

```text
feat(update): defer activation and rollback unhealthy versions
```

## 任务 15：全量验收、安装包构建和发布保护

**涉及文件**

- 修改：`.github/workflows/release-local-webui.yml`
- 修改：`README.md`
- 新建：`docs/verification/v1.3.0-release-checklist.md`
- 按需要修改：现有验收测试与构建测试

**步骤**

1. 将后端、前端、下载页和构建校验设为发布前置条件。
2. Actions 只允许 Windows x64 与 macOS arm64 构建成功后进入 Release 和 Pages。
3. 核对 Release 只包含两个安装入口及对应签名更新制品。
4. 在 Windows 真机验证：全新安装、覆盖升级、登录启动、无终端、统一入口、故障恢复、下载恢复、卸载保留数据。
5. 在 Apple Silicon Mac 真机验证：DMG 安装、Gatekeeper、LaunchAgent、Chrome/Safari 打开、本地协议、抖音隔离会话、升级与恢复。
6. 测量并记录两个系统的空闲 CPU、GPU 和内存；只发布实测值。
7. 验证横版、竖版、方形和旋转预览；播放器全部控件；小屏幕底部按钮可见。
8. 验证日志脱敏、一键修复、签名失败、离线、端口冲突、服务崩溃和熔断提示。

**最终验证命令**

```powershell
python -m pytest webui/engine/tests build/release/tests -q
npm test --prefix webui/frontend -- --run
npm run build --prefix webui/frontend
node distribution/download-page.test.mjs
```

**提交**

```text
release: validate local webui v1.3.0
```

## 实施顺序与停止条件

任务必须按 1 到 15 顺序实施。每个任务都遵循：新增失败测试 → 只运行目标测试并确认失败原因 → 最小实现 → 目标测试通过 → 相关回归通过 → 小提交。

出现以下情况立即停止发布但保留可运行旧版本：

- 现有公开链接识别或下载回归。
- 本地服务不再只监听回环地址。
- 安装/升级会删除用户数据。
- 更新签名或回退测试失败。
- Windows 或 macOS 出现终端弹窗、重复守护进程或快速重启循环。
- 统一入口按钮无状态反馈。

只有全部自动测试通过、Windows 真机验收通过、Apple Silicon Mac 真机验收通过，才创建 v1.3.0 标签并发布。
