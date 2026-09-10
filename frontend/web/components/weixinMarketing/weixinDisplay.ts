/**
 * 微信营销工作台共享展示工具（状态标签 / 徽标语义 / 时间与触发摘要）
 *
 * 枚举值来源见 api/weixinMarketing.ts 契约快照说明；本文件只做展示映射。
 */
import type { BaseBadgeIntent } from './badgeIntents'
import type {
  AutomationStatus,
  ContentBlockSpec,
  DeliveryState,
  GroupBindingState,
  RunState,
  TriggerConfig,
} from '@/api/weixinMarketing'
import { BLOCK_TEXT_FORBIDDEN_CHARS } from '@/api/weixinMarketing'

// ==================== 状态标签 ====================

export const AUTOMATION_STATUS_LABELS: Record<AutomationStatus, string> = {
  draft: '草稿',
  active: '运行中',
  paused: '已暂停',
  archived: '已归档',
}

export const AUTOMATION_STATUS_BADGE: Record<AutomationStatus, BaseBadgeIntent> = {
  draft: 'neutral',
  active: 'success',
  paused: 'warning',
  archived: 'neutral',
}

export const RUN_STATE_LABELS: Record<RunState, string> = {
  pending: '待开始',
  running: '执行中',
  waiting_device: '等待设备',
  succeeded: '已成功',
  failed: '已失败',
  cancelled: '已取消',
  partial: '部分完成',
  unknown: '结果未知',
  expired: '已过期',
}

export const RUN_STATE_BADGE: Record<RunState, BaseBadgeIntent> = {
  pending: 'neutral',
  running: 'info',
  waiting_device: 'warning',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'neutral',
  partial: 'warning',
  unknown: 'danger',
  expired: 'neutral',
}

export const DELIVERY_STATE_LABELS: Record<DeliveryState, string> = {
  pending: '待发送',
  dispatched: '已派发',
  succeeded: '已送达',
  failed: '失败',
  unknown: '结果未知',
  expired: '已过期',
  skipped: '已跳过',
}

export const DELIVERY_STATE_BADGE: Record<DeliveryState, BaseBadgeIntent> = {
  pending: 'neutral',
  dispatched: 'info',
  succeeded: 'success',
  failed: 'danger',
  unknown: 'danger',
  expired: 'neutral',
  skipped: 'neutral',
}

export const GROUP_BINDING_STATE_LABELS: Record<GroupBindingState, string> = {
  pending: '待核验',
  complete: '已核验',
  rejected: '已否决',
  disabled: '已停用',
}

export const GROUP_BINDING_STATE_BADGE: Record<GroupBindingState, BaseBadgeIntent> = {
  pending: 'warning',
  complete: 'success',
  rejected: 'danger',
  disabled: 'neutral',
}

export const TRIGGER_TYPE_LABELS: Record<TriggerConfig['type'], string> = {
  once: '一次性',
  interval: '间隔循环',
  calendar: '日历（cron）',
  event: '事件',
}

// ==================== 摘要 ====================

/** 触发配置一行摘要（列表/差异展示用） */
export function triggerSummary(trigger: TriggerConfig | null | undefined): string {
  if (!trigger) return '未配置'
  switch (trigger.type) {
    case 'once':
      return `一次性 · ${formatDateTime(trigger.run_at)}`
    case 'interval':
      return `间隔 · 每 ${formatDurationSeconds(trigger.interval_seconds)}${
        trigger.max_count ? ` · 最多 ${trigger.max_count} 次` : ''
      }`
    case 'calendar':
      return `日历 · ${trigger.cron_expr || '字段式'}`
    case 'event':
      return `事件 · ${trigger.source_ref}${trigger.event_type !== '*' ? ` / ${trigger.event_type}` : ''}`
  }
}

/** 内容块摘要（截断，供表格/试发确认展示） */
export function blockSummary(block: ContentBlockSpec, maxLen = 40): string {
  const raw =
    block.type === 'text' ? block.text_content : block.type === 'link' ? block.url : `图片 ${block.asset_id}`
  const line = raw.length > maxLen ? `${raw.slice(0, maxLen)}…` : raw
  return `[${block.type === 'text' ? '文字' : block.type === 'link' ? '网址' : '图片'}] ${line}`
}

export function formatDurationSeconds(seconds: number): string {
  if (seconds % 86400 === 0 && seconds >= 86400) return `${seconds / 86400} 天`
  if (seconds % 3600 === 0 && seconds >= 3600) return `${seconds / 3600} 小时`
  if (seconds % 60 === 0 && seconds >= 60) return `${seconds / 60} 分钟`
  return `${seconds} 秒`
}

// ==================== 时间 ====================
// 注意：本域后端时间为 tz-aware ISO（models._ensure_utc），与项目「naive 本地时间」
// 规范（frontend_dev.md）不同，直接 new Date(iso) 解析即可，禁止再拼 Z。

export function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** ISO（tz-aware）→ datetime-local 输入框值（浏览器本地时区，分钟精度） */
export function isoToLocalInput(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** datetime-local 值 → tz-aware ISO（按浏览器本地时区解释，输出 UTC Z 形态） */
export function localInputToIso(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

// ==================== 内容块前端校验（与后端 Text/Link 规则同口径） ====================

/** 单块校验：返回错误文案；空串 = 通过（与后端 Text/Link/Image 规则同口径） */
export function validateBlock(block: ContentBlockSpec): string {
  if (block.type === 'text') {
    if (!block.text_content.trim()) return '正文不能为空'
    if (BLOCK_TEXT_FORBIDDEN_CHARS.some(ch => block.text_content.includes(ch))) return '正文不允许换行/NUL 字符'
  } else if (block.type === 'link') {
    if (!block.url.trim()) return '网址不能为空'
    if (!/^https?:\/\//.test(block.url.trim())) return '网址必须以 http:// 或 https:// 开头'
  } else if (block.type === 'image') {
    if (!block.asset_id) return '未选择图片素材'
  }
  return ''
}

/** 保存前整体校验：非空且逐块通过 */
export function blocksValid(blocks: ContentBlockSpec[]): boolean {
  return blocks.length > 0 && blocks.every(b => !validateBlock(b))
}
