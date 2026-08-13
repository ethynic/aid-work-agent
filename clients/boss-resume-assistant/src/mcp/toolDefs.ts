/**
 * 9 个 MCP tool 的契约定义（实施规格 m02 §6 / 标准 §5）。
 *
 * zodShape 是 registerTool 的输入；manifest digest 用同一来源推导的 JSON Schema，
 * 保证「Host 看到的 schema」与「manifest digest 的 schema」同源（SDK 1.30.0 内部同样
 * 用 zod v4 toJSONSchema 生成 list_tools 的 inputSchema）。
 *
 * 写动作硬上限在 schema 层收紧（设计 §14）：greet 单次最大 3、accept 最大 1、reject 固定 1；
 * CLI/operation 层的上限（100）不变。
 */
import { z } from 'zod'

export interface BossToolDef {
  name: string
  /** 中文标题 */
  title: string
  description: string
  /** registerTool 的 zod raw shape */
  zodShape: Record<string, z.ZodTypeAny>
  annotations: {
    title: string
    readOnlyHint: boolean
    destructiveHint: boolean
    idempotentHint: boolean
    openWorldHint: boolean
  }
}

export const TOOL_DEFS: BossToolDef[] = [
  {
    name: 'boss_filter',
    title: '设置筛选条件',
    description:
      '在 BOSS 直聘「推荐牛人」页设置筛选面板：经验要求/学历要求/薪资待遇（替换语义，先清除残留再选）。' +
      '写动作：会改动页面上的筛选状态。至少提供一个条件。',
    zodShape: {
      experience: z.string().min(1).optional().describe('经验要求行选项，如 "5-10年"'),
      educations: z.array(z.string().min(1)).optional().describe('学历要求行选项（多选），如 ["本科","硕士"]'),
      salary: z.string().min(1).optional().describe('薪资待遇行选项（单选），如 "10-20K"'),
    },
    annotations: { title: '设置筛选条件', readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_clear_filter',
    title: '清空筛选条件',
    description: '清空 BOSS 直聘「推荐牛人」页的全部筛选条件（开面板 → 清除 → 确定，徽章计数归零校验）。写动作。',
    zodShape: {},
    annotations: { title: '清空筛选条件', readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_goto',
    title: '跳转页面',
    description:
      '点击 BOSS 直聘左侧导航菜单跳转页面：recommend=推荐牛人，chat=沟通（看打招呼回复/附件简历请求）。' +
      '已在目标页时自动跳过（幂等）。无外部写副作用。',
    zodShape: {
      target: z.enum(['recommend', 'chat']).describe('目标页面：recommend=推荐牛人，chat=沟通'),
    },
    annotations: { title: '跳转页面', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_greet',
    title: '打招呼',
    description:
      '在 BOSS 直聘「推荐牛人」页逐个点击「打招呼」向候选人发起沟通（外部写动作，单次最大 3 人）。' +
      '当前屏点完自动向下滚动，到底或达到 limit 结束；触发付费墙（职位无开聊权益）会立即停止并返回 PAYWALL。' +
      '前置要求：当前在推荐牛人列表页，否则返回 WRONG_PAGE。',
    zodShape: {
      limit: z.number().int().min(1).max(3).default(1).describe('打招呼人数上限：默认 1，单次最大 3'),
    },
    annotations: { title: '打招呼', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_accept_resume',
    title: '同意接收附件简历',
    description:
      '在 BOSS 直聘「沟通」页逐个打开「对方想发送附件简历」的会话并点「同意」接收简历（外部写动作，单次固定 1 人）。' +
      '同意后默认点开附件预览再关闭（preview 可关）。不在沟通页时自动先跳转。',
    zodShape: {
      limit: z.number().int().min(1).max(1).default(1).describe('同意人数：固定 1（产品硬上限）'),
      preview: z.boolean().default(true).describe('同意后点开附件简历预览再关闭（默认开启）'),
    },
    annotations: { title: '同意接收附件简历', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_reject_current',
    title: '标记当前候选人不合适',
    description:
      '把 BOSS 直聘「沟通」页当前会话的候选人标记为「不合适」（外部写动作，每次固定 1 人）。' +
      '弹确认层自动点确定；若弹出原因选择层会停止并提示人工处理（绝不自动乱选原因）。',
    zodShape: {},
    annotations: { title: '标记当前候选人不合适', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_interview_demo',
    title: '约面试表单填充演示',
    description:
      '演示填充 BOSS 直聘「沟通」页当前会话的约面试表单：逐字填备注 + 面试时间选明天，然后点「取消」关闭。' +
      '⚠️ 只填不发送（sent 恒为 false），无外部写副作用。',
    zodShape: {
      remark: z.string().min(1).max(140).optional().describe('备注内容（缺省用默认文案，表单上限 140 字）'),
    },
    annotations: { title: '约面试表单填充演示', readOnlyHint: true, destructiveHint: false, idempotentHint: false, openWorldHint: false },
  },
  {
    name: 'boss_send_to',
    title: '搜索找人并发送消息',
    description:
      '在 BOSS 直聘「沟通」页搜索联系人姓名 → 进入对话 → 逐字输入消息并发送（外部写动作）。' +
      '默认真发送；dry_run=true 时只输入不点发送（测试链路）。' +
      '前置要求：当前在沟通页（不在时自动跳转）；搜索结果中存在该姓名的联系人，否则报错。',
    zodShape: {
      to: z.string().min(1).describe('联系人姓名（搜索关键词）'),
      message: z.string().min(1).describe('要发送的消息内容'),
      dry_run: z.boolean().default(false).describe('只输入不点发送（测试链路，默认 false 真发送）'),
    },
    annotations: { title: '搜索找人并发送消息', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_send_current',
    title: '向当前会话发送消息',
    description:
      '在 BOSS 直聘「沟通」页向当前已选中的会话逐字输入消息并发送（外部写动作）。' +
      '默认真发送；dry_run=true 时只输入不点发送（测试链路）。' +
      '前置要求：当前在沟通页且已选中一个会话（右侧面板有发送按钮），否则返回 WRONG_PAGE（请先选会话）。',
    zodShape: {
      message: z.string().min(1).describe('要发送的消息内容'),
      dry_run: z.boolean().default(false).describe('只输入不点发送（测试链路，默认 false 真发送）'),
    },
    annotations: { title: '向当前会话发送消息', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
]

export const TOOL_NAMES = TOOL_DEFS.map((t) => t.name)
