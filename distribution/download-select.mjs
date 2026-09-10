export function selectDownload({ platform = '', userAgent = '' } = {}) {
  const value = (platform + ' ' + userAgent).toLowerCase()
  if (value.includes('win')) {
    return {
      target: 'windows-x64',
      title: '下载 Windows 版',
      asset: 'media-assistant-windows-x64-setup.exe',
    }
  }
  if (value.includes('mac') || value.includes('darwin')) {
    return {
      target: 'macos',
      title: '下载 macOS 版（Apple 芯片）',
      asset: 'media-assistant-macos-arm64.dmg',
    }
  }
  return { target: 'unsupported', title: '当前系统暂不支持', asset: '' }
}
