/**
 * MCP tool 契约定义（设计 §4/§5；上位规范 §5）。
 *
 * zodShape 是 registerTool 的输入；manifest digest 用同一来源推导的 JSON Schema，
 * 保证「Host 看到的 schema」与「manifest digest 的 schema」同源（SDK 1.30.0 内部同样
 * 用 zod v4 toJSONSchema 生成 list_tools 的 inputSchema）。
 *
 * M1 只有 weixin_probe（只读环境探测）。未过 probe 门禁的能力不得在此占位（设计 §10.3）。
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
]

export const TOOL_NAMES = TOOL_DEFS.map((t) => t.name)
