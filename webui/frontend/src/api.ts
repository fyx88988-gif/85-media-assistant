export type WorkStatus = 'queued' | 'recognizing' | 'ready' | 'failed' | 'downloading' | 'completed' | 'cancelled'

export interface MediaFormat {
  format_id: string
  note: string | null
  extension: string | null
  width: number | null
  height: number | null
  fps: number | null
  video_codec: string | null
  audio_codec: string | null
  file_size: number | null
  has_video: boolean
  has_audio: boolean
  preview_url: string | null
}

export interface WorkItem {
  id: string
  source_url: string
  platform: string
  status: WorkStatus
  title: string
  author: string | null
  description: string | null
  thumbnail_url: string | null
  media_kind: string
  width: number | null
  height: number | null
  duration: number | null
  rotation: number
  formats: MediaFormat[]
  failure: { code: string; message: string; retryable: boolean } | null
}

export interface DownloadJob {
  id: string
  status: 'queued' | 'downloading' | 'completed' | 'failed' | 'cancelled'
  progress: number
  failure: string | null
}

export type UpdateState = 'current' | 'checking' | 'available' | 'staged' | 'restart-required' | 'offline' | 'security-error' | 'rollback-complete' | 'error'

export interface UpdateStatusData {
  state: UpdateState
  currentVersion: string
  availableVersion?: string | null
  progress?: number | null
  checkedAt?: string | null
  message: string
}

async function request<T>(path: string, sessionToken: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
      ...(sessionToken ? { 'X-85-Session': sessionToken } : {}),
    },
  })
  if (!response.ok) {
    let message = `操作失败（${response.status}）`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) message = body.detail
    } catch { /* keep safe status message */ }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const localApi = {
  extract: (text: string, token: string) => request<{ links: Array<{ url: string }> }>('/api/v1/input/extract', token, { method: 'POST', body: JSON.stringify({ text }) }),
  recognize: (urls: string[], token: string) => request<{ items: WorkItem[] }>('/api/v1/items/recognize', token, { method: 'POST', body: JSON.stringify({ urls }) }),
  listItems: (token: string) => request<{ items: WorkItem[] }>('/api/v1/items', token),
  preview: (id: string, token: string) => request<import('./components/PreviewStage').PreviewDescriptor>(`/api/v1/items/${id}/preview`, token),
  removeItem: (id: string, token: string) => request<void>(`/api/v1/items/${id}`, token, { method: 'DELETE' }),
  startDownload: (itemId: string, formatId: string, outputDir: string, token: string) => request<DownloadJob>('/api/v1/downloads', token, { method: 'POST', body: JSON.stringify({ item_id: itemId, format_id: formatId, output_dir: outputDir }) }),
  listDownloads: (token: string) => request<{ jobs: DownloadJob[] }>('/api/v1/downloads', token),
  cancelDownload: (id: string, token: string) => request<DownloadJob>(`/api/v1/downloads/${id}/cancel`, token, { method: 'POST' }),
  version: (token: string) => request<UpdateStatusData>('/api/v1/version', token),
  checkUpdate: (token: string) => request<UpdateStatusData>('/api/v1/version/check', token, { method: 'POST' }),
}
