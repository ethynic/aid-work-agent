/**
 * CLI 子命令 mcp：启动标准本地 MCP server（stdio）。
 *
 * 用法：
 *   node dist/src/cli/index.js mcp --stdio [--cdp-port 9222]
 *
 * stdout 只承载 MCP 协议（零日志）；Host 配置示例见设计文档 §10.4。
 */
import { startMcpServer } from '../../mcp/server.js'

export interface McpCommandOptions {
  stdio?: boolean
  cdpPort?: number
}

export async function mcpCommand(opts: McpCommandOptions): Promise<number> {
  if (!opts.stdio) {
    console.error('mcp 目前只支持 stdio transport：mcp --stdio')
    return 2
  }
  await startMcpServer({ cdpPort: opts.cdpPort })
  // server 常驻：Host 关闭 stdin 后进程退出（见 server.ts transport.onclose）
  return 0
}
