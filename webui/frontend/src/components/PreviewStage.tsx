import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'

export interface PreviewDescriptor {
  mode: 'direct' | 'cover'
  url: string | null
  poster: string | null
  width: number | null
  height: number | null
  rotation: number
  reason: string | null
}

interface PreviewStageProps {
  preview: PreviewDescriptor | null
  title: string
  recognizing: boolean
  rotation?: number
  onGeometryChange?: (geometry: PreviewGeometry) => void
  resolutionId?: string
  resolutionOptions?: Array<{ id: string; label: string }>
  onResolutionChange?: (id: string) => void
}

export type PreviewOrientation = 'landscape' | 'portrait'

export interface PreviewGeometry {
  width: number
  height: number
  orientation: PreviewOrientation
  rotation: number
}

const playbackRates = [0.5, 0.75, 1, 1.25, 1.5, 2]

function formatPlaybackTime(value: number) {
  if (!Number.isFinite(value) || value < 0) return '00:00'
  const totalSeconds = Math.floor(value)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
  }
  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}

function isTypingTarget(target: EventTarget | null) {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  )
}

export function fitMediaFrame(
  containerWidth: number,
  containerHeight: number,
  mediaWidth: number,
  mediaHeight: number,
) {
  if (
    containerWidth <= 0 ||
    containerHeight <= 0 ||
    mediaWidth <= 0 ||
    mediaHeight <= 0
  ) return { width: 0, height: 0 }

  const mediaRatio = mediaWidth / mediaHeight
  const containerRatio = containerWidth / containerHeight
  return mediaRatio >= containerRatio
    ? { width: containerWidth, height: containerWidth / mediaRatio }
    : { width: containerHeight * mediaRatio, height: containerHeight }
}

export function PreviewStage({
  preview,
  title,
  recognizing,
  rotation = 0,
  onGeometryChange,
  resolutionId = '',
  resolutionOptions = [],
  onResolutionChange,
}: PreviewStageProps) {
  const stageRef = useRef<HTMLElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const previousUrl = useRef(preview?.url)
  const hideTimer = useRef<number | undefined>(undefined)
  const [controlsVisible, setControlsVisible] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [fullscreen, setFullscreen] = useState(false)
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 })
  const [mediaSize, setMediaSize] = useState(() => ({
    width: preview?.width && preview.width > 0 ? preview.width : 16,
    height: preview?.height && preview.height > 0 ? preview.height : 9,
  }))

  const revealControls = useCallback(() => {
    window.clearTimeout(hideTimer.current)
    setControlsVisible(true)
    hideTimer.current = window.setTimeout(() => setControlsVisible(false), 2200)
  }, [])

  const togglePlayback = useCallback(async () => {
    const video = videoRef.current
    if (!video) return
    if (video.paused) {
      await video.play()
      setPlaying(true)
    } else {
      video.pause()
      setPlaying(false)
    }
    revealControls()
  }, [revealControls])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.code !== 'Space' ||
        isTypingTarget(event.target) ||
        isTypingTarget(document.activeElement)
      ) return
      event.preventDefault()
      void togglePlayback()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.clearTimeout(hideTimer.current)
    }
  }, [togglePlayback])

  useEffect(() => {
    if (previousUrl.current === preview?.url) return
    previousUrl.current = preview?.url
    const video = videoRef.current
    if (video) {
      video.pause()
      video.currentTime = 0
    }
    setPlaying(false)
    setCurrentTime(0)
    setDuration(0)
  }, [preview?.url])

  useEffect(() => {
    const onFullscreenChange = () => {
      setFullscreen(document.fullscreenElement === videoRef.current?.parentElement)
    }
    document.addEventListener('fullscreenchange', onFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange)
  }, [])

  useEffect(() => {
    setMediaSize({
      width: preview?.width && preview.width > 0 ? preview.width : 16,
      height: preview?.height && preview.height > 0 ? preview.height : 9,
    })
  }, [preview?.url, preview?.width, preview?.height])

  useLayoutEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const updateSize = () => {
      const bounds = stage.getBoundingClientRect()
      setStageSize({ width: bounds.width, height: bounds.height })
    }
    updateSize()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateSize)
      return () => window.removeEventListener('resize', updateSize)
    }
    const observer = new ResizeObserver(updateSize)
    observer.observe(stage)
    return () => observer.disconnect()
  }, [])

  const normalizedRotation = ((rotation % 360) + 360) % 360
  const isQuarterTurn = normalizedRotation === 90 || normalizedRotation === 270
  const width = isQuarterTurn ? mediaSize.height : mediaSize.width
  const height = isQuarterTurn ? mediaSize.width : mediaSize.height
  const orientation: PreviewOrientation = height > width ? 'portrait' : 'landscape'
  const stageStyle = {
    '--media-aspect': `${width} / ${height}`,
    ...(preview ? { aspectRatio: `${width} / ${height}` } : {}),
  } as CSSProperties
  const frameStyle = {
    aspectRatio: `${width} / ${height}`,
    width: '100%',
    height: '100%',
  } as CSSProperties
  const videoStyle = {
    width: isQuarterTurn && stageSize.height > 0 ? `${stageSize.height}px` : '100%',
    height: isQuarterTurn && stageSize.width > 0 ? `${stageSize.width}px` : '100%',
    maxWidth: isQuarterTurn ? 'none' : '100%',
    maxHeight: isQuarterTurn ? 'none' : '100%',
    objectFit: 'contain',
    transform: `rotate(${normalizedRotation}deg)`,
    transformOrigin: 'center',
  } as CSSProperties

  useEffect(() => {
    if (!preview) return
    onGeometryChange?.({ width, height, orientation, rotation: normalizedRotation })
  }, [height, normalizedRotation, onGeometryChange, orientation, preview, width])

  const mode = recognizing ? 'recognizing' : preview ? 'media' : 'idle'
  const progress = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0

  const seek = (value: number) => {
    const video = videoRef.current
    if (!video || !Number.isFinite(value)) return
    video.currentTime = value
    setCurrentTime(value)
    revealControls()
  }

  const cyclePlaybackRate = () => {
    const video = videoRef.current
    if (!video) return
    const index = playbackRates.indexOf(playbackRate)
    const nextRate = playbackRates[(index + 1) % playbackRates.length]
    video.playbackRate = nextRate
    setPlaybackRate(nextRate)
    revealControls()
  }

  const toggleFullscreen = async () => {
    const frame = videoRef.current?.parentElement
    if (!frame) return
    if (document.fullscreenElement) await document.exitFullscreen()
    else await frame.requestFullscreen()
    revealControls()
  }

  return (
    <section
      ref={stageRef}
      className="preview-stage"
      data-testid="preview-stage"
      data-mode={mode}
      data-orientation={orientation}
      style={stageStyle}
      aria-label="媒体预览"
      onPointerMove={revealControls}
      onPointerEnter={revealControls}
      onFocus={revealControls}
    >
      <div className="preview-ambient" aria-hidden="true" />

      {recognizing ? (
        <div className="signal-explorer" aria-live="polite">
          <div className="signal-grid" aria-hidden="true" />
          <div className="signal-orbit signal-orbit-outer" aria-hidden="true" />
          <div className="signal-orbit signal-orbit-inner" aria-hidden="true" />
          <div className="signal-sweep" aria-hidden="true" />
          <div className="signal-core" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
          <div className="signal-copy">
            <strong>正在探索媒体信号</strong>
            <span>解析来源 · 建立预览 · 检索可用格式</span>
          </div>
        </div>
      ) : preview?.mode === 'direct' && preview.url ? (
        <div
          className="media-frame"
          data-testid="preview-frame"
          data-orientation={orientation}
          style={frameStyle}
        >
          <video
            ref={videoRef}
            data-testid="preview-video"
            src={preview.url}
            poster={preview.poster ?? undefined}
            aria-label={title || '当前视频'}
            playsInline
            preload="metadata"
            style={videoStyle}
            onLoadedMetadata={(event) => {
              const video = event.currentTarget
              if (video.videoWidth > 0 && video.videoHeight > 0) {
                setMediaSize({ width: video.videoWidth, height: video.videoHeight })
              }
              setDuration(Number.isFinite(video.duration) ? video.duration : 0)
              setCurrentTime(Number.isFinite(video.currentTime) ? video.currentTime : 0)
            }}
            onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
            onTimeUpdate={(event) => {
              setCurrentTime(Number.isFinite(event.currentTarget.currentTime) ? event.currentTarget.currentTime : 0)
              setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)
            }}
            onRateChange={(event) => setPlaybackRate(event.currentTarget.playbackRate)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onClick={() => void togglePlayback()}
          />
          <div className={`player-controls ${controlsVisible ? 'is-visible' : ''}`}>
            <button type="button" onClick={() => void togglePlayback()}>
              <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
              <span className="sr-only">{playing ? '暂停' : '播放'}</span>
            </button>
            <input
              className="player-track"
              type="range"
              aria-label="播放进度"
              min="0"
              max={duration > 0 ? duration : 0}
              step="0.01"
              value={Math.min(currentTime, duration || 0)}
              disabled={duration <= 0}
              style={{ '--playback-progress': `${progress}%` } as CSSProperties}
              onChange={(event) => seek(Number(event.currentTarget.value))}
            />
            <span className="player-time">{formatPlaybackTime(currentTime)} / {formatPlaybackTime(duration)}</span>
            <button
              type="button"
              className="text-control"
              aria-label={`播放速度 ${playbackRate}×`}
              title="切换播放速度"
              onClick={cyclePlaybackRate}
            >{playbackRate}×</button>
            {resolutionOptions.length > 0 ? (
              <select
                className="resolution-control"
                aria-label="预览分辨率"
                value={resolutionId}
                onChange={(event) => onResolutionChange?.(event.currentTarget.value)}
              >
                {resolutionOptions.map((option) => (
                  <option key={option.id} value={option.id}>{option.label}</option>
                ))}
              </select>
            ) : (
              <span className="resolution-readout">{width > 0 && height > 0 ? `${width}×${height}` : '原画'}</span>
            )}
            <button type="button" aria-label={fullscreen ? '退出全屏' : '进入全屏'} onClick={() => void toggleFullscreen()}>⛶</button>
          </div>
        </div>
      ) : preview?.mode === 'cover' && preview.poster ? (
        <div
          className="media-frame cover-frame"
          data-testid="preview-frame"
          data-orientation={orientation}
          style={frameStyle}
        >
          <img src={preview.poster} alt={title || '作品封面'} />
          <div className="cover-message">
            <span className="play-mark">▶</span>
            <strong>当前作品仅提供封面预览</strong>
            <span>{preview.reason}</span>
          </div>
        </div>
      ) : (
        <div className="signal-explorer is-idle">
          <div className="signal-grid" aria-hidden="true" />
          <div className="signal-orbit signal-orbit-outer" aria-hidden="true" />
          <div className="signal-orbit signal-orbit-inner" aria-hidden="true" />
          <div className="signal-core" aria-hidden="true"><i /><i /><i /></div>
          <div className="signal-copy">
            <strong>等待探索</strong>
            <span>粘贴作品链接，媒体内容将在这里呈现</span>
          </div>
        </div>
      )}
    </section>
  )
}
