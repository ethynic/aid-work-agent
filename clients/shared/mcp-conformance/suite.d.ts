/**
 * suite.mjs 类型声明（见 README.md 用法）
 */

export interface SpawnSpec {
  command: string
  args: string[]
  cwd?: string
  env?: Record<string, string>
}

export interface ToolCallProbe {
  name: string
  arguments: Record<string, unknown>
}

export interface BusyProbe {
  /** 给定悬挂端口（TCP 接受但不响应），返回指向该端口的 Provider 启动命令 */
  makeSpawn: (port: number) => SpawnSpec
  /** 会卡在 Chrome connect 阶段的 tool 调用 */
  tool: ToolCallProbe
  /** 单飞锁占用时返回的错误码（默认 'BUSY'） */
  busyCode?: string
}

export interface ConformanceOptions {
  /** 被测 Provider 启动命令（应启动 stdio MCP server） */
  spawn: SpawnSpec
  /** SDK 模块解析基点（文件 URL 或路径，通常是被测 CLI 包内的任一文件），缺省用套件自身位置 */
  requireBase?: string
  /** 期望的 tool 名集合（精确匹配，顺序无关） */
  expectTools?: string[]
  /** 一次快速返回的调用（无目标环境时应返回结构化失败，不得挂起） */
  callProbe: ToolCallProbe
  /** 参数非法的调用（应 isError 或结构化 INVALID_ARGUMENT） */
  invalidProbe: ToolCallProbe
  /** 并发/取消检查；缺省跳过 */
  busyProbe?: BusyProbe
  /** 单次检查超时（默认 15000ms） */
  timeoutMs?: number
}

export interface ConformanceCheckResult {
  name: string
  ok: boolean
  detail: string
}

export interface ConformanceReport {
  passed: number
  failed: number
  results: ConformanceCheckResult[]
}

export function runConformance(options: ConformanceOptions): Promise<ConformanceReport>
