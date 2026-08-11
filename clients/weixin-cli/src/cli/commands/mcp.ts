/**
 * CLI 子命令 mcp：启动标准本地 MCP server（stdio）。
 *
 * 用法：
 *   aid-weixin mcp --stdio
 *
 * stdout 只承载 MCP 协议（零日志）；Host 关闭 stdin 后进程退出（见 server.ts transport.onclose）。
 */
import { startMcpServer } from '../../mcp/server.js'

export interface McpCommandOptions {
  stdio?: boolean
}

export async function mcpCommand(opts: McpCommandOptions): Promise<number> {
  if (!opts.stdio) {
    console.error('mcp 目前只支持 stdio transport：mcp --stdio')
    return 2
  }
  await startMcpServer()
  // server 常驻：Host 关闭 stdin 后进程退出
  return 0
}
