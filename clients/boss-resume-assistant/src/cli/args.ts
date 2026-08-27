/**
 * CLI argv 极简解析（纯函数，可单测）：--key value / --flag / 位置参数 / -h 短 flag。
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
    if (arg === '-h') {
      // -h 是唯一的单横线短 flag（--help 的别名，任何命令可用）。
      // 只特判 -h、不做通用单横线解析：通用化会把 `--limit -3` 这类负值误吞成 flag
      flags.set('h', true)
      continue
    }
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
