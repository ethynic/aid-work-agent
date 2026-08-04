/**
 * CLI argv 极简解析（纯函数，可单测）：--key value / --flag / 位置参数。
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
