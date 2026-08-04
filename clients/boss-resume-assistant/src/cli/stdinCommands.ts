/**
 * CLI stdin 命令解析（纯函数，可单测）。
 * 运行期用户在终端输入单字符命令控制会话：
 *   p = 暂停  r = 恢复  s = 停止  q = 退出（走 INTERRUPTED 清扫 + 关 Chrome）
 * 大小写不敏感，前后空白忽略；无法识别返回 null（调用方提示用法）。
 */
export type CliCommand = 'pause' | 'resume' | 'stop' | 'quit'

const COMMAND_MAP: Record<string, CliCommand> = {
  p: 'pause',
  r: 'resume',
  s: 'stop',
  q: 'quit',
}

export function parseStdinCommand(line: string): CliCommand | null {
  const key = line.trim().toLowerCase()
  return COMMAND_MAP[key] ?? null
}

/** 命令用法说明（未识别输入时打印） */
export const STDIN_USAGE = '命令: p=暂停 r=恢复 s=停止 q=退出'
