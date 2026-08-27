/**
 * CLI argv 极简解析（纯函数，可单测）：--key value / --flag / 位置参数。
 *
 * 命名铁律（设计 §4.1）：动词子命令集合固定为
 * mcp / doctor / version / probe / search / collect / read / get-url / send / follow / lab；
 * 操作对象（搜一搜/文章/聊天/公众号）只能作为参数值表达（--domain <值>），
 * 不允许出现按对象命名的子命令。M1 只实现 mcp/doctor/version/probe，
 * parseDomainFlag 供后续 search/collect/read/get-url 动词复用。
 */

export interface ParsedArgs {
  positional: string[]
  flags: Map<string, string | boolean>
}

export function parseArgs(argv: string[]): ParsedArgs {
  const positional: string[] = []
  const flags = new Map<string, string | boolean>()
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]!
    if (arg.startsWith('--')) {
      const key = arg.slice(2)
      const next = argv[i + 1]
      if (next !== undefined && !next.startsWith('--')) {
        flags.set(key, next)
        i++
      } else {
        flags.set(key, true)
      }
    } else {
      positional.push(arg)
    }
  }
  return { positional, flags }
}

export function flagString(args: ParsedArgs, key: string): string | undefined {
  const v = args.flags.get(key)
  return typeof v === 'string' ? v : undefined
}

export function hasFlag(args: ParsedArgs, key: string): boolean {
  return args.flags.has(key)
}

/** 合法 --domain 值（设计 §4.1：search 必须显式指定；M2 落地 chat / chat-history / unread） */
export const CLI_DOMAINS = ['souyisou', 'article', 'chat', 'chat-history', 'unread'] as const
export type CliDomain = (typeof CLI_DOMAINS)[number]

/**
 * 解析 --domain 标志：未提供返回 undefined；合法值返回 domain；非法值返回 'invalid'。
 * 调用方（后续 search/collect/read 动词）对 'invalid' 打印错误并以退出码 2 终止。
 */
export function parseDomainFlag(args: ParsedArgs): CliDomain | undefined | 'invalid' {
  const raw = flagString(args, 'domain')
  if (raw === undefined) return undefined
  return (CLI_DOMAINS as readonly string[]).includes(raw) ? (raw as CliDomain) : 'invalid'
}
