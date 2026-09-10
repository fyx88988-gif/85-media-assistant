import type { UpdateStatusData } from '../api'

interface UpdateStatusProps {
  status: UpdateStatusData
  onCheck: () => void
}

const stateLabel: Record<UpdateStatusData['state'], string> = {
  current: '已是最新版本',
  checking: '正在检查',
  available: '发现新版本',
  staged: '更新已准备完成',
  'restart-required': '重启后完成更新',
  offline: '暂时离线',
  'security-error': '安全校验未通过',
  'rollback-complete': '已恢复上一版本',
  error: '更新暂时不可用',
}

export function UpdateStatus({ status, onCheck }: UpdateStatusProps) {
  const checking = status.state === 'checking'
  return (
    <div className={`update-status update-status-${status.state}`}>
      <div className="update-summary">
        <span>当前版本 {status.currentVersion}</span>
        <strong>{stateLabel[status.state]}</strong>
      </div>
      <p>{status.message}</p>
      {status.progress != null && <div className="update-progress" aria-label={`更新进度 ${status.progress}%`}><i style={{ width: `${status.progress}%` }} /></div>}
      <button type="button" className="details-button update-check" disabled={checking} onClick={onCheck}>
        {checking ? '正在检查' : '立即检查更新'}
      </button>
    </div>
  )
}
