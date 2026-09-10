import { useCallback, useEffect, useMemo, useState } from 'react'

import { localApi, type DownloadJob, type UpdateStatusData, type WorkItem } from '../api'
import { PreviewStage, type PreviewDescriptor, type PreviewGeometry, type PreviewOrientation } from './PreviewStage'
import { UpdateStatus } from './UpdateStatus'

const primaryPlatforms = [['抖音', '♪'], ['快手', '快'], ['小红书', '书'], ['YouTube', '▶'], ['Instagram', '◎'], ['X', 'X']]
const statusText: Record<string, string> = { queued: '等待识别', recognizing: '正在识别', ready: '已识别', failed: '识别失败' }

interface WorkbenchProps { sessionToken?: string }

function formatLabel(value: WorkItem['formats'][number]) {
  const size = value.width && value.height ? `${value.width} × ${value.height}` : '源画质'
  return `${value.note || size} · ${(value.extension || '媒体').toUpperCase()}${value.fps ? ` · ${value.fps} FPS` : ''}`
}

export function Workbench({ sessionToken = '' }: WorkbenchProps) {
  const [input, setInput] = useState('')
  const [items, setItems] = useState<WorkItem[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewDescriptor | null>(null)
  const [previewFormatId, setPreviewFormatId] = useState('')
  const [previewOrientation, setPreviewOrientation] = useState<PreviewOrientation>('landscape')
  const [manualRotation, setManualRotation] = useState(0)
  const [selectedFormat, setSelectedFormat] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('等待粘贴作品链接')
  const [download, setDownload] = useState<DownloadJob | null>(null)
  const [outputDir, setOutputDir] = useState('Downloads')
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusData>({ state: 'current', currentVersion: '0.1.0', message: '当前已是最新版本。' })

  const selected = useMemo(() => items.find((item) => item.id === selectedId) ?? items[0] ?? null, [items, selectedId])

  const refresh = useCallback(async () => {
    try {
      const response = await localApi.listItems(sessionToken)
      setItems(response.items)
      if (!selectedId && response.items[0]) setSelectedId(response.items[0].id)
    } catch { /* local engine may still be starting */ }
  }, [selectedId, sessionToken])

  useEffect(() => {
    void localApi.version(sessionToken).then(setUpdateStatus).catch(() => undefined)
  }, [sessionToken])

  useEffect(() => {
    if (!items.some((item) => item.status === 'queued' || item.status === 'recognizing')) return
    const timer = window.setInterval(() => void refresh(), 900)
    return () => window.clearInterval(timer)
  }, [items, refresh])

  useEffect(() => {
    if (!download || download.status !== 'downloading') return
    const timer = window.setInterval(() => {
      void localApi.listDownloads(sessionToken).then(({ jobs }) => {
        const latest = jobs.find((job) => job.id === download.id)
        if (!latest) return
        setDownload(latest)
        if (latest.status === 'completed') {
          setMessage('下载完成，文件已保存到指定位置。')
          const AudioContextClass = window.AudioContext
          if (AudioContextClass) {
            const context = new AudioContextClass()
            const oscillator = context.createOscillator()
            const gain = context.createGain()
            oscillator.connect(gain); gain.connect(context.destination)
            oscillator.frequency.value = 740; gain.gain.value = .055
            oscillator.start(); oscillator.stop(context.currentTime + .16)
          }
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('85数字多媒体下载助手', { body: '视频下载完成。' })
          }
        } else if (latest.status === 'failed') setMessage(latest.failure || '下载失败，请重试。')
        else if (latest.status === 'cancelled') setMessage('下载已停止。')
      }).catch(() => undefined)
    }, 800)
    return () => window.clearInterval(timer)
  }, [download?.id, download?.status, sessionToken])

  useEffect(() => {
    setManualRotation(0)
    setPreviewOrientation('landscape')
  }, [selected?.id])

  useEffect(() => {
    if (!selected || selected.status !== 'ready') {
      setPreview(null)
      setPreviewFormatId('')
      setSelectedFormat('')
      return
    }
    setSelectedFormat((current) => current || selected.formats.find((value) => value.has_video)?.format_id || selected.formats[0]?.format_id || '')
    void localApi.preview(selected.id, sessionToken).then((descriptor) => {
      setPreview(descriptor)
      const formatId = descriptor.url
        ? new URL(descriptor.url, window.location.origin).searchParams.get('format_id') ?? ''
        : ''
      setPreviewFormatId(formatId)
    }).catch((error: Error) => setMessage(error.message))
  }, [selected?.id, selected?.status, sessionToken])

  async function recognize() {
    if (!input.trim() || busy) return
    setBusy(true)
    setMessage('正在提取链接并识别作品…')
    try {
      const extracted = await localApi.extract(input, sessionToken)
      if (!extracted.links.length) throw new Error('没有识别到有效链接，请检查后重试。')
      const response = await localApi.recognize(extracted.links.map((link) => link.url), sessionToken)
      setItems((current) => [...current, ...response.items])
      setSelectedId(response.items[0]?.id ?? null)
      setSelectedFormat(response.items[0]?.formats.find((value) => value.has_video)?.format_id ?? '')
      setMessage(`已加入 ${response.items.length} 个作品，正在读取媒体信息。`)
    } catch (error) { setMessage(error instanceof Error ? error.message : '识别失败，请重试。') }
    finally { setBusy(false) }
  }

  async function paste() {
    try { setInput(await navigator.clipboard.readText()) }
    catch { setMessage('无法读取剪贴板，请使用 Ctrl+V 或 ⌘V 粘贴。') }
  }

  async function removeCurrent() {
    if (!selected) return
    try {
      await localApi.removeItem(selected.id, sessionToken)
      const remaining = items.filter((item) => item.id !== selected.id)
      setItems(remaining)
      setSelectedId(remaining[0]?.id ?? null)
      setPreview(null)
      setMessage('已清除当前作品。')
    } catch (error) { setMessage(error instanceof Error ? error.message : '清除失败。') }
  }

  async function startDownload() {
    if (!selected || !selectedFormat) return
    try {
      const job = await localApi.startDownload(selected.id, selectedFormat, outputDir, sessionToken)
      setDownload(job)
      setMessage('正在下载')
    } catch (error) { setMessage(error instanceof Error ? error.message : '下载启动失败。') }
  }

  async function cancelDownload() {
    if (!download || download.status !== 'downloading') return
    try { setDownload(await localApi.cancelDownload(download.id, sessionToken)); setMessage('下载已停止。') }
    catch (error) { setMessage(error instanceof Error ? error.message : '停止下载失败。') }
  }

  async function checkUpdate() {
    setUpdateStatus((current) => ({ ...current, state: 'checking', message: '正在检查更新。' }))
    try { setUpdateStatus(await localApi.checkUpdate(sessionToken)) }
    catch { setUpdateStatus((current) => ({ ...current, state: 'error', message: '自动更新暂时不可用，不影响当前功能。' })) }
  }

  const recognizing = selected?.status === 'queued' || selected?.status === 'recognizing'
  const previewFormats = useMemo(() => selected?.formats.filter((value) => (
    value.has_video &&
    value.has_audio &&
    Boolean(value.preview_url) &&
    ['mp4', 'webm', 'mov', 'm4v'].includes((value.extension ?? '').toLowerCase())
  )) ?? [], [selected])
  const previewResolutionOptions = previewFormats.map((value) => ({
    id: value.format_id,
    label: value.width && value.height ? `${value.width}×${value.height}` : value.note || '原画',
  }))

  const handlePreviewGeometry = useCallback((geometry: PreviewGeometry) => {
    setPreviewOrientation(geometry.orientation)
  }, [])

  function changePreviewResolution(formatId: string) {
    if (!selected) return
    const mediaFormat = previewFormats.find((value) => value.format_id === formatId)
    if (!mediaFormat) return
    const rotated = selected.rotation % 180 === 90
    setPreviewFormatId(formatId)
    setPreview((current) => current ? {
      ...current,
      url: `/api/v1/items/${selected.id}/media?format_id=${encodeURIComponent(formatId)}`,
      width: rotated ? mediaFormat.height : mediaFormat.width,
      height: rotated ? mediaFormat.width : mediaFormat.height,
    } : current)
  }

  return (
    <div className="app-shell" data-testid="app-shell" data-orientation={previewOrientation} style={{ height: '100dvh', overflow: 'hidden' }}>
      <header className="app-header" role="banner">
        <div className="brand-lockup"><div className="brand-symbol" aria-hidden="true"><span>8</span><span>5</span></div><div><strong>85数字多媒体下载助手</strong><span>发现 · 预览 · 选择 · 保存</span></div></div>
        <div className="header-actions"><span className="engine-state"><i />本地引擎已连接</span><button type="button" className="ghost-button">下载记录</button><button type="button" className="ghost-button">设置</button></div>
      </header>

      <div
        className="workbench-grid"
        data-testid="workbench-grid"
        data-orientation={previewOrientation}
        style={{
          minHeight: 0,
          overflow: 'hidden',
          gridTemplateColumns: previewOrientation === 'portrait'
            ? 'minmax(210px, 0.8fr) clamp(520px, 34vw, 720px) minmax(300px, 1.2fr)'
            : undefined,
        }}
      >
        <aside className="panel queue-panel" aria-label="作品队列">
          <div className="panel-heading"><div><strong>作品队列</strong><span>识别多个链接后逐条预览</span></div><span className="count-badge">{items.length}</span></div>
          {items.length ? <div className="queue-list">{items.map((item) => <button type="button" key={item.id} className={`queue-item ${selected?.id === item.id ? 'is-active' : ''}`} onClick={() => setSelectedId(item.id)}><span className="queue-thumb">{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" /> : '85'}</span><span><strong>{item.title}</strong><small>{item.platform} · {statusText[item.status] || item.status}</small></span></button>)}</div> : <div className="empty-queue"><span className="queue-glyph">＋</span><strong>尚无作品</strong><span>粘贴链接后，作品会按顺序出现在这里</span></div>}
        </aside>

        <main className="main-workspace">
          <section className="link-card"><div className="link-copy"><strong>粘贴作品链接</strong><span>支持分享文案，也支持每行一个链接</span></div><div className="link-row"><textarea aria-label="作品链接" value={input} onChange={(event) => setInput(event.target.value)} placeholder="在这里粘贴抖音、快手、小红书、YouTube 等作品链接…" /><button type="button" className="paste-button" onClick={() => void paste()}>从剪贴板粘贴</button><button type="button" className="primary-button" disabled={busy || !input.trim()} onClick={() => void recognize()}>{busy ? '正在提交' : '立即识别'}</button></div></section>
          <section className="preview-card"><PreviewStage preview={preview} title={selected?.title ?? ''} recognizing={recognizing} rotation={manualRotation} onGeometryChange={handlePreviewGeometry} resolutionId={previewFormatId} resolutionOptions={previewResolutionOptions} onResolutionChange={changePreviewResolution} /><div className="media-meta"><div><strong>{selected?.title ?? '等待识别作品'}</strong><span>{selected?.failure?.message ?? selected?.author ?? '识别完成后显示真实媒体信息'}</span></div><div className="meta-actions"><button type="button" className="quiet-button" disabled={!selected} onClick={() => void removeCurrent()}>清除当前</button><button type="button" className="quiet-button" disabled={!preview} aria-label="旋转 90°" onClick={() => setManualRotation((current) => (current + 90) % 360)}>↻ 旋转 90°</button></div></div></section>
        </main>

        <aside className="panel settings-panel" aria-label="下载参数">
          <div className="panel-heading"><div><strong>下载参数</strong><span>跟随当前作品，可逐条调整</span></div></div>
          <section className="control-section"><h2>平台</h2><div className="platform-list">{primaryPlatforms.map(([platform, glyph]) => <span className="platform-chip" key={platform}><i>{glyph}</i>{platform}</span>)}<span className="platform-chip"><i>•••</i>更多⌄</span></div></section>
          <section className="control-section"><h2>当前作品</h2><p className="muted">{selected ? `${selected.platform} · ${statusText[selected.status] || selected.status}` : '尚未选择作品'}</p></section>
          <section className="control-section grow-section"><h2>可下载格式</h2>{selected?.formats.length ? <div className="format-list">{selected.formats.filter((value) => value.has_video).map((value) => <label key={value.format_id} className={selectedFormat === value.format_id ? 'is-selected' : ''}><input type="radio" name="format" value={value.format_id} checked={selectedFormat === value.format_id} onChange={() => setSelectedFormat(value.format_id)} /><span>{formatLabel(value)}</span></label>)}</div> : <div className="format-empty">识别后显示源媒体格式</div>}<button type="button" className="details-button">⌄ 技术详情</button></section>
          <section className="control-section"><h2>保存位置</h2><input className="output-path" aria-label="保存位置" value={outputDir} onChange={(event) => setOutputDir(event.target.value)} /></section>
          <section className="control-section"><h2>下载内容</h2><div className="content-grid">{['视频', '音频 MP3', '作品原图', '封面图', '视频画面图', '文案'].map((label, index) => <button key={label} type="button" className={index === 0 ? 'is-selected' : ''}>{label}</button>)}</div></section>
          <section className="control-section"><h2>引擎与维护</h2><UpdateStatus status={updateStatus} onCheck={() => void checkUpdate()} /></section>
        </aside>
      </div>

      <footer className="task-bar"><div className="task-status"><span>{download?.status === 'downloading' ? '正在下载' : message}</span><i style={{ width: `${download?.progress ?? 0}%` }} /></div><button type="button" className="ghost-button">播放预览</button>{download?.status === 'downloading' ? <button type="button" className="danger-button" onClick={() => void cancelDownload()}>停止下载</button> : <button type="button" className="primary-button" disabled={!selected || selected.status !== 'ready' || !selectedFormat} onClick={() => void startDownload()}>开始下载</button>}</footer>
    </div>
  )
}
