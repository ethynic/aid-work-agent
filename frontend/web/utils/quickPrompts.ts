/**
 * 子智能体聊天快捷按钮配置（ChatInput 顶部的常驻 chips）。
 *
 * 按 subagent_type 映射一组预设动作：点击即等价于用户手动发送 message。
 * 演示闭环（2026-08-17）：招聘操作智能体的「筛选简历」一键触发
 * 切职位 → 设筛选 → 批量读简历入库 →（询问后）打招呼，链路见
 * subagents/recruiting-operator/SUBAGENT.md「一键筛选简历」。
 */
export interface QuickPrompt {
  /** 按钮文案 */
  label: string
  /** 点击后发送的消息（与手动输入走同一发送链路） */
  message: string
}

const BY_SUBAGENT: Record<string, QuickPrompt[]> = {
  'recruiting-operator': [{ label: '筛选简历', message: '帮我筛选简历' }],
}

/** 当前子智能体的快捷按钮；无配置返回空数组（不渲染） */
export function quickPromptsForSubagent(subagentType: string | null | undefined): QuickPrompt[] {
  if (!subagentType) return []
  return BY_SUBAGENT[subagentType] ?? []
}
