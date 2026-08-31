/**
 * MCP tool 契约定义。
 *
 * zodShape 是 registerTool 的输入；manifest digest 用同一来源推导的 JSON Schema，
 * 保证「Host 看到的 schema」与「manifest digest 的 schema」同源（SDK 1.30.0 内部同样
 * 用 zod v4 toJSONSchema 生成 list_tools 的 inputSchema）。
 *
 * M1 有 wecom_probe（只读）与 wecom_add_customer（写）；M2 增加 wecom_chat_search（只读）
 * 与 wecom_message_send（写）；M3 增加 wecom_unread_list 与 wecom_watch_poll（读）。
 * wecom_history_read 只走 CLI read 动词不进 MCP（长滚动抓取不适合 Host 高频调用）；
 * 未真机验证的能力不得在此占位。
 */
import { z } from 'zod'

export interface WecomToolDef {
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

export const TOOL_DEFS: WecomToolDef[] = [
  {
    name: 'wecom_probe',
    title: '企业微信环境探测',
    description:
      '只读探测企业微信操作环境：Windows 平台、交互桌面会话（已登录未锁屏）、PowerShell 可用性、' +
      'WXWork.exe 进程存在性；进程运行时再解析主窗口 rect、登录态（online/need_login/offline）与当前内容页。' +
      '不激活窗口、不发送输入、不改剪贴板，无外部写副作用。',
    zodShape: {
      verbose: z.boolean().optional().describe('返回更多诊断字段（系统版本/Node 版本），默认 false'),
    },
    annotations: { title: '企业微信环境探测', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'wecom_add_customer',
    title: '企业微信添加客户',
    description:
      '按手机号在企业微信「通讯录 → 新的客户 → 添加」中检索微信用户并发送添加邀请（写动作，单次单号码）。' +
      'confirm 必须显式为 true 才会执行。成功后返回对方微信名与各步骤截图路径；' +
      '号码无检索结果返回 CUSTOMER_NOT_FOUND（不重试）；' +
      'effect=unknown 表示邀请可能已发出但校验失败，系统不会自动重试，请人工确认。',
    zodShape: {
      phone: z.string().regex(/^1\d{10}$/).describe('客户手机号（11 位数字，1 开头）'),
      confirm: z.literal(true).describe('显式确认：这是对外发送添加邀请的写动作，必须传 true'),
    },
    annotations: { title: '企业微信添加客户', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'wecom_chat_search',
    title: '企业微信搜索联系人/会话',
    description:
      '在企业微信主窗口搜索框按关键词检索联系人/群聊（只读，不打开会话、不读历史消息）。' +
      '返回候选列表（name/subtitle/section），每个候选带 target_ref（HMAC 签名短期句柄，有效期 5 分钟），' +
      '供 wecom_message_send 作为发送目标使用。无结果返回 TARGET_NOT_FOUND。',
    zodShape: {
      query: z.string().min(1).max(100).describe('搜索关键词（联系人/群名，1-100 字）'),
      type: z.enum(['contact', 'group', 'any']).optional().describe('过滤分区：contact=联系人，group=群聊，any=全部（默认）'),
      limit: z.number().int().min(1).max(20).optional().describe('返回候选上限（1-20，默认 10）'),
    },
    annotations: { title: '企业微信搜索联系人/会话', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'wecom_message_send',
    title: '企业微信发送文本消息',
    description:
      '向 target_ref 指定的联系人/群聊发送 1 条文本消息（写动作，单次单目标单条）。' +
      'target_ref 必须来自 wecom_chat_search（有效期 5 分钟，过期 TARGET_REF_STALE 需重新搜索）。' +
      '发送前 OCR 校验会话标题与目标一致、输入框无残留草稿，不一致即中止；' +
      '发送后做终态校验，失败返回 EXECUTION_UNKNOWN（消息可能已发出，系统不会自动重试，请人工确认）。',
    zodShape: {
      target_ref: z.string().min(1).describe('发送目标句柄（wecom_chat_search 返回的 target_ref）'),
      text: z.string().min(1).max(2000).describe('消息文本（单行，不含换行，最长 2000 字；更长请分段多次发送）'),
    },
    annotations: { title: '企业微信发送文本消息', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'wecom_unread_list',
    title: '企业微信未读会话快照',
    description:
      '读取企业微信主窗口会话列表的未读会话快照（只读，不打开会话、不清除未读角标）。' +
      '返回 [{name, preview, unread_count}]（无未读的会话不出现）；可选 name 过滤（子串匹配）。' +
      '适合作为新消息跟踪的轻量探测：有未读再调 wecom_watch_poll 取增量内容。',
    zodShape: {
      name: z.string().max(100).optional().describe('可选：按会话名子串过滤'),
    },
    annotations: { title: '企业微信未读会话快照', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'wecom_watch_poll',
    title: '企业微信新消息跟踪（单轮）',
    description:
      '新消息跟踪单轮（无会话归档时的核心读能力）：未读快照与本机水位 diff →' +
      '对有新增未读的会话读取当前屏消息 → 只返回增量。' +
      '返回 data.events：{type:"new_messages",session,unread_count,messages:[{side,text}]} 若干，' +
      '或本轮无新消息时 {type:"tick",unread_total}。' +
      '副作用：读取候选会话会清除其未读角标（企微客户端固有行为），并推进本机 watch-state.json 水位；' +
      '同一水位不会重复推送相同消息。长循环请用 CLI watch 动词（本工具每轮一次调用）。',
    zodShape: {},
    annotations: { title: '企业微信新消息跟踪（单轮）', readOnlyHint: true, destructiveHint: false, idempotentHint: false, openWorldHint: false },
  },
]

export const TOOL_NAMES = TOOL_DEFS.map((t) => t.name)
