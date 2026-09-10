import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Workbench } from './Workbench'

const readyItem = {
  id: 'item-1',
  source_url: 'https://www.youtube.com/watch?v=demo',
  platform: 'youtube',
  status: 'ready',
  title: '测试作品',
  author: '85 Studio',
  description: null,
  thumbnail_url: 'https://cdn.example.com/cover.jpg',
  media_kind: 'video',
  width: 1920,
  height: 1080,
  duration: 15,
  rotation: 0,
  failure: null,
  formats: [{
    format_id: '137', note: '1080p', extension: 'mp4', width: 1920,
    height: 1080, fps: 30, video_codec: 'avc1', audio_codec: 'none',
    file_size: 1000, has_video: true, has_audio: false,
    preview_url: 'https://cdn.example.com/video.mp4',
  }],
}

describe('Workbench API integration', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/input/extract')) {
        return Response.json({ links: [{ url: readyItem.source_url, platform: 'youtube', original_index: 0 }] })
      }
      if (url.endsWith('/api/v1/items/recognize')) {
        expect(init?.headers).toMatchObject({ 'X-85-Session': 'session-token' })
        return Response.json({ items: [readyItem] }, { status: 202 })
      }
      if (url.endsWith('/api/v1/items/item-1/preview')) {
        return Response.json({
          mode: 'direct', url: 'https://cdn.example.com/video.mp4',
          poster: readyItem.thumbnail_url, width: 1920, height: 1080,
          rotation: 0, reason: null,
        })
      }
      if (url.endsWith('/api/v1/downloads')) {
        return Response.json({ id: 'job-1', status: 'downloading', progress: 0, request: {}, output: null, failure: null }, { status: 202 })
      }
      throw new Error(`Unexpected request: ${url}`)
    }))
  })

  it('recognizes input, shows formats and starts the selected download', async () => {
    render(<Workbench sessionToken="session-token" />)
    fireEvent.change(screen.getByLabelText('作品链接'), { target: { value: readyItem.source_url } })
    fireEvent.click(screen.getByRole('button', { name: '立即识别' }))

    expect((await screen.findAllByText('测试作品'))[0]).toBeVisible()
    expect(await screen.findByText(/1080p/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '开始下载' }))

    await waitFor(() => expect(screen.getByText('正在下载')).toBeVisible())
  })

  it('reflows the workbench and rotates the real preview when rotation changes', async () => {
    render(<Workbench sessionToken="session-token" />)
    fireEvent.change(screen.getByLabelText('作品链接'), { target: { value: readyItem.source_url } })
    fireEvent.click(screen.getByRole('button', { name: '立即识别' }))

    await screen.findByTestId('preview-video')
    const grid = screen.getByTestId('workbench-grid')
    expect(grid).toHaveAttribute('data-orientation', 'landscape')
    expect(screen.getByTestId('preview-frame')).toHaveAttribute('data-orientation', 'landscape')

    fireEvent.click(screen.getByRole('button', { name: '旋转 90°' }))

    expect(grid).toHaveAttribute('data-orientation', 'portrait')
    expect(grid.style.gridTemplateColumns).toBe(
      'minmax(210px, 0.8fr) clamp(520px, 34vw, 720px) minmax(300px, 1.2fr)',
    )
    expect(screen.getByTestId('preview-frame')).toHaveAttribute('data-orientation', 'portrait')
    expect(screen.getByTestId('preview-video')).toHaveStyle({ transform: 'rotate(90deg)' })
  })
})
