/**
 * BOSS 不合适原因选项（设计文档 §10.2）。
 * 本工具支持的原因选项保守子集：BOSS 原因弹层文案可能随版本调整，
 * 弹层中定位不到选项时执行器会停止（UNKNOWN），不会乱选。
 */

/** BOSS 不合适原因选项（本工具支持映射的子集） */
export const REJECT_REASON_OPTIONS = [
  '经验不匹配',
  '学历不匹配',
  '专业不匹配',
  '薪资期望不符',
  '通勤距离不合适',
  '稳定性不足',
] as const

export type RejectReasonOption = (typeof REJECT_REASON_OPTIONS)[number]
