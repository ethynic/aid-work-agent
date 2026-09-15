import type { SessionTask } from '@/api/sessionTasks'
export const statusLabels: Record<string, string> = { draft: '草稿', active: '已发布', paused: '已暂停', human_required: '需人工接管', blocked: '已阻断', completed: '目标已达成', stopped: '已停止（未达成目标）' }
const phases: Record<string, string> = { ready: '待调度', observing: '观察中', decision_pending: '等模型', send_ready: '等桌面', executing: '等桌面 · 正在执行', waiting_peer: '等客户', waiting_schedule: '等工作时段', sync_pending: '同步中', blocked: '已阻断' }
export function phaseLabel(task: SessionTask): string {
  const reason = task.blocked_reason || task.completion_reason || ''
  if (task.status === 'completed') return ({ rounds_reached: '已达到指定轮数', goal_judged: 'AI 根据对话判断已达成', peer_confirmed: '对方已明确确认' } as Record<string, string>)[reason] || '任务已完成（查看完成证据）'
  if (task.status === 'stopped') return /budget|cost|replies|decisions/.test(reason) ? '预算或次数上限耗尽 · 已停止' : /timeout|expired|deadline/.test(reason) ? '超时或期限已到 · 已停止' : '已停止（未达成目标）'
  if (/unknown/i.test(reason) || task.phase === 'unknown_send') return '发送结果不明 · 需人工核对'
  if (/gap|coverage/i.test(reason) || task.phase === 'observation_gap') return '观察缺口 · 需重新对齐'
  return task.status === 'active' ? phases[task.phase || ''] || '等待设备' : statusLabels[task.status] || task.status
}
export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : '请求失败，请重试' }
export function dateLabel(value?: string): string { return value ? new Date(value).toLocaleString() : '—' }
