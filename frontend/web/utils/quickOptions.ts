/**
 * 工具结果 → 编号选择按钮（设计 §5.1 选择交互，前端纯增强）。
 *
 * 约定：工具成功结果带 data.options 数组（如 boss_jobs_list 返回职位选择元数据）时，
 * 在 assistant 消息下方渲染编号按钮，点击即发送对应序号（与手动回复数字等效，
 * agent 按列表顺序解析序号）；仅 ≥2 项才挂载——1 项时 SUBAGENT 约定复述确认后
 * 直用不列单，按钮无意义。不做历史持久化：刷新/切会话后按钮消失，对话文本里的
 * 编号列表仍在，用户可手动回复数字。
 */
import type { QuickOption } from '@/types'

/**
 * 校验并提取 result.data.options：
 * - 失败结果（success === false）/ 无 options / 形状不符 → 空数组
 * - 逐项过滤：key/label 必须为非空字符串（description 可缺省）
 * - 少于 2 项 → 空数组（1 项不列单，见约定）
 */
export function extractQuickOptions(result: unknown): QuickOption[] {
  if (!result || typeof result !== 'object') return []
  const r = result as { success?: unknown; data?: { options?: unknown } }
  if (r.success === false) return []
  const options = r.data?.options
  if (!Array.isArray(options)) return []
  const valid = options.filter((o): o is QuickOption => {
    if (!o || typeof o !== 'object') return false
    const item = o as { key?: unknown; label?: unknown }
    return typeof item.key === 'string' && item.key !== ''
      && typeof item.label === 'string' && item.label !== ''
  })
  return valid.length >= 2 ? valid : []
}
