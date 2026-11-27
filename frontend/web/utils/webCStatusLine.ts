/**
 * webC 状态行兜底（2026-11-26，PM 要求：结果出来前至少显示一句工作状态）。
 *
 * 背景：过程行原先只依赖 snapshot.progress（服务端只保留最新一条工具进度），
 * 两个空窗会让状态行凭空消失/从不出现，用户分不清在工作还是出错：
 * 1. 工具完成后 progress 被清空，到下一个 progress/正文出现前（显示十几秒后消失）
 * 2. 简单问答无工具调用，progress 从头到尾为空
 * 3. 发送后受理返回前
 */

/** 兜底工作文案：本轮执行中但暂无工具进度时显示 */
export const WEB_C_WORKING_FALLBACK_TEXT = '正在思考…'

export interface WebCStatusLineState {
  streamingActive: boolean
  isTerminal: boolean
  streamingText: string
  waitingText: string
  streamFailed: boolean
  progressText: string
}

/** 状态行判定：本轮执行中恒有值——有工具进度显示进度，否则兜底文案；非执行中返回空串 */
export function resolveWebCStatusLine(state: WebCStatusLineState): string {
  if (!state.streamingActive || state.isTerminal) return ''
  if (state.streamingText || state.waitingText || state.streamFailed) return ''
  return state.progressText || WEB_C_WORKING_FALLBACK_TEXT
}
