import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { UpdateStatus } from './UpdateStatus'

describe('UpdateStatus', () => {
  it('keeps an offline update informational and retryable', () => {
    const onCheck = vi.fn()
    render(<UpdateStatus status={{
      state: 'offline',
      currentVersion: '1.2.0',
      message: '暂时无法检查更新，正在使用当前版本。',
    }} onCheck={onCheck} />)

    expect(screen.getByText('当前版本 1.2.0')).toBeVisible()
    expect(screen.getByText(/暂时无法检查更新/)).toBeVisible()
    const button = screen.getByRole('button', { name: '立即检查更新' })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    expect(onCheck).toHaveBeenCalledOnce()
  })

  it('disables duplicate checks while checking', () => {
    render(<UpdateStatus status={{
      state: 'checking',
      currentVersion: '1.2.0',
      message: '正在检查更新。',
    }} onCheck={vi.fn()} />)

    expect(screen.getByRole('button', { name: '正在检查' })).toBeDisabled()
  })
})
