/**
 * 状态机状态中文显示 + badge 样式（登录页 / 控制台共用）
 */
import type { SessionState } from '@shared/ipc'

export const STATE_LABELS: Record<SessionState, string> = {
  IDLE: '空闲',
  WAITING_MANUAL_LOGIN: '等待手动登录',
  CONNECTING_CDP: '已连接，待开始',
  READING_LIST: '读取推荐列表',
  OPENING_DETAIL: '打开详情',
  CAPTURING_DETAIL: '详情截图',
  OCR_AND_NORMALIZE: 'OCR 与归一化',
  SCREENING: '筛选评估',
  WAITING_REVIEW: '等待人工复核',
  EXECUTING_ACTION: '执行动作',
  CLOSING_DETAIL: '关闭详情',
  CHECKPOINT: '检查点',
  PAUSED: '已暂停',
  STOPPED: '已停止',
  COMPLETED: '已完成',
}

export function stateBadgeClass(state: SessionState): string {
  if (state === 'PAUSED') return 'badge badge-warn'
  if (state === 'COMPLETED') return 'badge badge-ok'
  if (state === 'STOPPED') return 'badge badge-neutral'
  if (state === 'IDLE' || state === 'WAITING_MANUAL_LOGIN' || state === 'CONNECTING_CDP') {
    return 'badge badge-neutral'
  }
  return 'badge badge-ok'
}
