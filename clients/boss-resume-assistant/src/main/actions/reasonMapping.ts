/**
 * 不合适原因映射（设计文档 §10.2）。
 * 评估理由（ScreeningResult.reason 等文本）必须显式映射到 BOSS 不合适原因选项，
 * 映射不上 → 返回 undefined，调用方不得执行动作，进人工复核。
 *
 * 选项列表是保守子集：BOSS 原因弹层文案可能随版本调整，
 * 弹层中定位不到映射选项时执行器同样会停止（UNKNOWN），不会乱选。
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

/** 关键词 → 原因选项。前面的规则优先命中。 */
const KEYWORD_MAP: ReadonlyArray<{ keywords: readonly string[]; option: RejectReasonOption }> = [
  { keywords: ['经验', '年限', '工作经历', '资历'], option: '经验不匹配' },
  { keywords: ['学历', '学位', '本科', '专科', '大专', '硕士', '博士'], option: '学历不匹配' },
  { keywords: ['专业'], option: '专业不匹配' },
  { keywords: ['薪资', '薪酬', '工资', '待遇'], option: '薪资期望不符' },
  { keywords: ['距离', '通勤', '城市', '地址', '地点'], option: '通勤距离不合适' },
  { keywords: ['跳槽', '稳定', '离职频繁'], option: '稳定性不足' },
]

/**
 * 将评估理由映射到 BOSS 不合适原因选项。
 * @returns 命中返回选项文案；无法映射返回 undefined（调用方必须停止执行）
 */
export function mapRejectReason(evalReason: string): RejectReasonOption | undefined {
  if (typeof evalReason !== 'string' || !evalReason.trim()) return undefined
  for (const rule of KEYWORD_MAP) {
    if (rule.keywords.some((k) => evalReason.includes(k))) return rule.option
  }
  return undefined
}
