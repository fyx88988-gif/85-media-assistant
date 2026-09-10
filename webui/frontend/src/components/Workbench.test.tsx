import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Workbench } from './Workbench'

describe('Workbench', () => {
  it('renders one unified desktop workbench with preview as the main region', () => {
    render(<Workbench />)

    expect(screen.getByRole('banner')).toHaveTextContent('85数字多媒体下载助手')
    expect(screen.getByRole('complementary', { name: '作品队列' })).toBeVisible()
    expect(screen.getByRole('main')).toContainElement(screen.getByTestId('preview-stage'))
    expect(screen.getByRole('complementary', { name: '下载参数' })).toBeVisible()
  })

  it('locks the desktop shell to the viewport so the task bar stays visible', () => {
    render(<Workbench />)

    expect(screen.getByTestId('app-shell')).toHaveStyle({
      height: '100dvh',
      overflow: 'hidden',
    })
    expect(screen.getByTestId('workbench-grid')).toHaveStyle({
      minHeight: 0,
      overflow: 'hidden',
    })
  })
})
