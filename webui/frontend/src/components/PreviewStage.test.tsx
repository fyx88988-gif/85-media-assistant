import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  fitMediaFrame,
  PreviewStage,
  type PreviewDescriptor,
} from './PreviewStage'

const portraitPreview: PreviewDescriptor = {
  mode: 'direct',
  url: 'https://cdn.example.com/portrait.mp4',
  poster: 'https://cdn.example.com/cover.jpg',
  width: 1080,
  height: 1920,
  rotation: 0,
  reason: null,
}

const landscapePreview: PreviewDescriptor = {
  ...portraitPreview,
  url: 'https://cdn.example.com/landscape.mp4',
  width: 1920,
  height: 1080,
}

describe('PreviewStage', () => {
  afterEach(() => vi.restoreAllMocks())

  it('fits portrait and landscape windows to their real aspect ratios', () => {
    expect(fitMediaFrame(1200, 700, 1080, 1920)).toEqual({
      width: 393.75,
      height: 700,
    })
    expect(fitMediaFrame(1200, 700, 1920, 1080)).toEqual({
      width: 1200,
      height: 675,
    })
  })

  it('uses the complete preview canvas and preserves portrait ratio', () => {
    render(
      <PreviewStage preview={portraitPreview} title="竖屏作品" recognizing={false} />,
    )

    const stage = screen.getByTestId('preview-stage')
    expect(stage).toHaveAttribute('data-mode', 'media')
    expect(stage.style.getPropertyValue('--media-aspect')).toBe('1080 / 1920')
    expect(stage).toHaveClass('preview-stage')
    expect(screen.getByTestId('preview-video')).toHaveAttribute(
      'src',
      portraitPreview.url,
    )
    expect(screen.getByTestId('preview-video')).toHaveStyle({
      width: '100%',
      height: '100%',
      objectFit: 'contain',
    })
    expect(screen.getByTestId('preview-frame')).toHaveAttribute(
      'data-orientation',
      'portrait',
    )
    expect(screen.getByTestId('preview-frame')).toHaveStyle({
      aspectRatio: '1080 / 1920',
    })
  })

  it('makes a landscape preview canvas match the video so 16:9 media fills it', () => {
    render(
      <PreviewStage preview={landscapePreview} title="横屏作品" recognizing={false} />,
    )

    expect(screen.getByTestId('preview-stage')).toHaveStyle({
      aspectRatio: '1920 / 1080',
    })
    expect(screen.getByTestId('preview-frame')).toHaveStyle({
      width: '100%',
      height: '100%',
      aspectRatio: '1920 / 1080',
    })
  })

  it('reports rotated geometry when the user turns a landscape video', () => {
    const onGeometryChange = vi.fn()
    render(
      <PreviewStage
        preview={landscapePreview}
        title="横屏作品"
        recognizing={false}
        rotation={90}
        onGeometryChange={onGeometryChange}
      />,
    )

    expect(screen.getByTestId('preview-stage')).toHaveAttribute(
      'data-orientation',
      'portrait',
    )
    expect(screen.getByTestId('preview-stage')).toHaveStyle({
      aspectRatio: '1080 / 1920',
    })
    expect(screen.getByTestId('preview-video')).toHaveStyle({
      transform: 'rotate(90deg)',
    })
    expect(onGeometryChange).toHaveBeenLastCalledWith({
      width: 1080,
      height: 1920,
      orientation: 'portrait',
      rotation: 90,
    })
  })

  it('switches the preview window when video metadata reveals a portrait video', () => {
    render(
      <PreviewStage
        preview={{ ...portraitPreview, width: null, height: null }}
        title="未知比例作品"
        recognizing={false}
      />,
    )
    const video = screen.getByTestId('preview-video') as HTMLVideoElement
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 720 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 1280 })

    fireEvent.loadedMetadata(video)

    expect(screen.getByTestId('preview-frame')).toHaveAttribute(
      'data-orientation',
      'portrait',
    )
    expect(screen.getByTestId('preview-frame')).toHaveStyle({
      aspectRatio: '720 / 1280',
    })
  })

  it('toggles playback with Space while focus is outside text input', () => {
    render(
      <PreviewStage preview={portraitPreview} title="测试" recognizing={false} />,
    )
    const video = screen.getByTestId('preview-video') as HTMLVideoElement
    Object.defineProperty(video, 'paused', { configurable: true, value: false })
    const pause = vi.spyOn(video, 'pause').mockImplementation(() => undefined)

    fireEvent.keyDown(window, { code: 'Space' })

    expect(pause).toHaveBeenCalledTimes(1)
  })

  it('reports real playback time and seeks through the media', () => {
    render(
      <PreviewStage preview={portraitPreview} title="测试" recognizing={false} />,
    )
    const video = screen.getByTestId('preview-video') as HTMLVideoElement
    Object.defineProperty(video, 'duration', { configurable: true, value: 125 })
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      writable: true,
      value: 65,
    })

    fireEvent.loadedMetadata(video)
    fireEvent.timeUpdate(video)

    expect(screen.getByText('01:05 / 02:05')).toBeVisible()
    fireEvent.change(screen.getByRole('slider', { name: '播放进度' }), {
      target: { value: '90' },
    })
    expect(video.currentTime).toBe(90)
  })

  it('changes the real playback speed and enters fullscreen', async () => {
    render(
      <PreviewStage preview={portraitPreview} title="测试" recognizing={false} />,
    )
    const video = screen.getByTestId('preview-video') as HTMLVideoElement
    Object.defineProperty(video, 'playbackRate', {
      configurable: true,
      writable: true,
      value: 1,
    })
    const frame = screen.getByTestId('preview-frame')
    const requestFullscreen = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(frame, 'requestFullscreen', {
      configurable: true,
      value: requestFullscreen,
    })

    fireEvent.click(screen.getByRole('button', { name: '播放速度 1×' }))
    expect(video.playbackRate).toBe(1.25)
    expect(screen.getByRole('button', { name: '播放速度 1.25×' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '进入全屏' }))
    expect(requestFullscreen).toHaveBeenCalledTimes(1)
  })

  it('offers real preview resolutions and reports the selected format', () => {
    const onResolutionChange = vi.fn()
    render(
      <PreviewStage
        preview={portraitPreview}
        title="测试"
        recognizing={false}
        resolutionId="full-hd"
        resolutionOptions={[
          { id: 'full-hd', label: '1080×1920' },
          { id: 'hd', label: '720×1280' },
        ]}
        onResolutionChange={onResolutionChange}
      />,
    )

    fireEvent.change(screen.getByRole('combobox', { name: '预览分辨率' }), {
      target: { value: 'hd' },
    })

    expect(onResolutionChange).toHaveBeenCalledWith('hd')
  })

  it('does not hijack Space while the link input is focused', () => {
    render(
      <>
        <input aria-label="作品链接" />
        <PreviewStage preview={portraitPreview} title="测试" recognizing={false} />
      </>,
    )
    const video = screen.getByTestId('preview-video') as HTMLVideoElement
    const play = vi.spyOn(video, 'play').mockResolvedValue(undefined)
    play.mockClear()
    const input = screen.getByLabelText('作品链接')
    input.focus()

    fireEvent.keyDown(window, { code: 'Space' })

    expect(play).not.toHaveBeenCalled()
  })

  it('fills the stage with the recognizing visual instead of a framed image', () => {
    render(<PreviewStage preview={null} title="" recognizing />)

    const stage = screen.getByTestId('preview-stage')
    expect(stage).toHaveAttribute('data-mode', 'recognizing')
    expect(screen.getByText('正在探索媒体信号')).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
