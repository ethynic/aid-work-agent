/**
 * 子智能体聊天快捷按钮配置（ChatInput 顶部的常驻 chips）。
 *
 * 按 subagent_type 映射一组预设动作：点击将 message 填入输入框，
 * 由用户补充细节后手动发送，避免预设指令不完整直接执行。
 */
export interface QuickPrompt {
  /** 按钮文案 */
  label: string
  /** 点击后填入输入框的预设消息 */
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
