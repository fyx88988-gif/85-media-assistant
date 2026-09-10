import assert from 'node:assert/strict'
import fs from 'node:fs'
import { selectDownload } from './download-select.mjs'

assert.equal(
  selectDownload({ platform: 'Win32', userAgent: 'Windows NT 10.0' }).target,
  'windows-x64',
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
