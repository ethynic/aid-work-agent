/**
 * MCP tool 契约定义（设计 §4/§5；上位规范 §5）。
 *
 * zodShape 是 registerTool 的输入；manifest digest 用同一来源推导的 JSON Schema，
 * 保证「Host 看到的 schema」与「manifest digest 的 schema」同源（SDK 1.30.0 内部同样
 * 用 zod v4 toJSONSchema 生成 list_tools 的 inputSchema）。
 *
 * M1 只有 weixin_probe（只读环境探测）。M2 新增 chat_search / message_send /
 * history_read / unread_list（底层为已真机验证的 PowerShell 驱动）。
 * 未过 probe 门禁的能力不得在此占位（设计 §10.3）。
 */
import { z } from 'zod'

export interface WeixinToolDef {
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

export const TOOL_DEFS: WeixinToolDef[] = [
  {
    name: 'weixin_probe',
    title: '微信环境探测',
    description:
      '只读探测微信操作环境：Windows 平台、交互桌面会话（已登录未锁屏）、PowerShell 可用性、Weixin.exe 进程存在性。' +
      '不激活窗口、不发送输入、不改剪贴板，无外部写副作用。',
    zodShape: {
      verbose: z.boolean().optional().describe('返回更多诊断字段（系统版本/Node 版本），默认 false'),
    },
    annotations: { title: '微信环境探测', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'weixin_chat_search',
    title: '微信聊天搜索',
    description:
      '在微信主窗口全局搜索好友/群（只读，不打开会话、不读历史消息）。' +
      '返回结果列表（label/section），每个结果附带 5 分钟有效的 target_ref，' +
      '供 weixin_message_send / weixin_history_read 定位目标。',
    zodShape: {
      query: z.string().min(1).max(100).describe('搜索词（好友昵称/群名关键字）'),
      type: z.enum(['friend', 'group', 'any']).optional().describe('结果类型过滤：friend 联系人 / group 群聊 / any 全部，默认 any'),
      limit: z.number().int().min(1).max(20).optional().describe('返回结果上限，默认 10，最大 20'),
    },
    annotations: { title: '微信聊天搜索', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  {
    name: 'weixin_message_send',
    title: '微信发送消息',
    description:
      '向 target_ref 指定的会话发送 1 条文本消息（写动作，单次单目标，1..500 字）。' +
      'target_ref 必须先经 weixin_chat_search 获取且未过期。发送前校验会话标题与输入框无残留草稿，' +
      '此旧路径发送后按OCR近似匹配核对最新本人消息；effect=unknown 表示消息可能已发出但无法确认，系统不会自动重试。名称会话v2路径仅记录发送操作完成，不做发送后OCR。',
    zodShape: {
      target_ref: z.string().min(1).describe('weixin_chat_search 返回的 target_ref（5 分钟有效）'),
      text: z.string().min(1).max(500).describe('要发送的文本（1..500 字，不支持换行）'),
    },
    annotations: { title: '微信发送消息', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'weixin_history_read',
    title: '微信聊天记录读取',
    description:
      '读取与 target_ref 目标的聊天记录（只读，但会打开会话窗口并清除该会话未读角标）。' +
      '返回 messages[{sender: me|peer|time, text}]；内联最多 200 条，超出返回最新 200 条并给出完整文本 file 路径。',
    zodShape: {
      target_ref: z.string().min(1).describe('weixin_chat_search 返回的 target_ref（5 分钟有效）'),
      since_days: z.number().positive().optional().describe('只抓最近 N 天的记录（可选，缺省按 max_pages 上限抓取）'),
      max_pages: z.number().int().min(1).max(10).optional().describe('最多向上翻页数，默认 3，最大 10'),
    },
    annotations: { title: '微信聊天记录读取', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  {
    name: 'weixin_session_observe',
    title: '微信会话观察（session_observer_v1）',
    description:
      '对已绑定会话执行常驻观察（截图→OCR→对齐→水位），返回 session_observer_v1 冻结契约结果。' +
      '真机截图接线未开放时返回 coverage=unavailable（不冒充无人回复）。',
    zodShape: {
      conversation_binding_id: z.string().min(1).describe('会话绑定 ID'),
      target_name: z.string().min(1).max(128).optional().describe('当前登录微信的目标联系人名称；OCR近似匹配必须唯一'),
      binding_version: z.number().int().min(0).describe('绑定版本（期望值，观察结果回显核对）'),
      account_identity_version: z.number().int().min(0).describe('账号身份版本（期望值）'),
      watermark: z
        .object({ last_local_message_id: z.string().nullable(), window_fingerprint: z.string() })
        .nullable()
        .describe('上次水位（null=建基线）'),
    },
    annotations: { title: '微信会话观察', readOnlyHint: true, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'weixin_unread_list',
    title: '微信未读消息列表',
    description:
      '读取主窗口会话列表中的未读会话（只读，不打开会话、不清除未读角标）。' +
      '返回 unread[{name, preview, unread_count}]；没有未读的会话不出现。',
    zodShape: {
      name: z.string().optional().describe('按会话名过滤（子串匹配，可选）'),
    },
    annotations: { title: '微信未读消息列表', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
]

TOOL_DEFS.push(
  { name:'weixin_name_resolve', title:'定位微信名称会话', description:'按名称及OCR近似匹配唯一定位；多个近似联系人拒绝选择。只返回定位证据，不返回历史正文。', zodShape:{target_name:z.string().min(1).max(128)}, annotations:{title:'定位微信名称会话',readOnlyHint:true,destructiveHint:false,idempotentHint:false,openWorldHint:true}},
  { name:'weixin_message_send_v2', title:'微信许可单条发送', description:'仅接受Runtime签名执行上下文；一条发送，新本人气泡后验，未知不重发。', zodShape:{context:z.string().min(1).max(32768),signature:z.string().regex(/^[a-f0-9]{64}$/)}, annotations:{title:'微信许可单条发送',readOnlyHint:false,destructiveHint:false,idempotentHint:false,openWorldHint:true}},
)
export const TOOL_NAMES = TOOL_DEFS.map((t) => t.name)
