import assert from 'node:assert/strict'
import fs from 'node:fs'
import { buildEntryActions, selectDownload } from './download-select.mjs'

assert.equal(
  selectDownload({ platform: 'Win32', userAgent: 'Windows NT 10.0' }).target,
  'windows-x64',
)

assert.deepEqual(
  buildEntryActions({ repository: 'fyx88988-gif/85-media-assistant', platform: 'Win32', userAgent: 'Windows NT 10.0' }),
  {
    openUrl: 'http://127.0.0.1:8515/',
    repairUrl: 'mediaassistant85://open',
    downloadTitle: '下载 Windows 版',
    downloadUrl: 'https://github.com/fyx88988-gif/85-media-assistant/releases/latest/download/media-assistant-windows-x64-setup.exe',
  },
)
assert.equal(
  selectDownload({ platform: 'MacIntel', userAgent: 'Macintosh' }).target,
  'macos',
)
assert.equal(
  selectDownload({ platform: 'Linux x86_64', userAgent: 'Linux' }).target,
  'unsupported',
)

const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf8')
assert.match(html, /每台电脑只需首次安装一次/)
assert.match(html, /Windows/)
assert.match(html, /macOS/)
assert.match(html, /不会上传/)
assert.doesNotMatch(html, /从桌面图标进入|桌面入口会/)
assert.match(html, /不创建桌面图标/)
assert.match(html, /http:\/\/127\.0\.0\.1:8515/)
assert.match(html, /id="open-tool"/)
assert.match(html, /id="repair-tool"/)
assert.match(html, /启动或修复本地引擎/)
