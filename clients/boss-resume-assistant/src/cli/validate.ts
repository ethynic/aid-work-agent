/**
 * CLI 子命令参数校验层（fail-loud，纯函数可单测）。
 *
 * 背景（2026-08 真机事故）：此前未知 flag 被解析器静默忽略——用户执行 `greet --help`
 * 想看用法，结果 greet 按默认参数直接执行了真实写动作（向推荐列表顶部发了招呼）；
 * 审计同时发现 `greet 冯修业` / `greet --names 冯修业` 里姓名被静默丢弃、按非定向
 * 「从顶打 10 人」模式跑掉。对带真实写动作的 CLI，fail-open 的参数处理是安全事故。
 *
 * 规则（main() 在 parseArgs 之后、switch 分发之前调用，ok:false 时 exit 2）：
 * 1) 未知 flag 一律拒绝（白名单制），消息含不认识的参数与用法提示；
 * 2) 多余位置参数一律拒绝（按命令允许个数）；
 * 3) greet 必须显式选择模式：--names（定向）与 --all（全量）二选一；
 * 4) --help / -h 短路放行：help 必须先于一切拒绝生效（main() 打印 USAGE 后退出 0）。
 */
import type { ParsedArgs } from './args.js'

export type ValidateResult = { ok: true } | { ok: false; message: string }

/** 各子命令允许的专属 flag（不含全局 flag）。新增命令/flag 必须在此登记，否则一律拒绝 */
const COMMAND_FLAGS: Readonly<Record<string, readonly string[]>> = {
  'filter-options': [],
  filter: ['experience', 'education', 'salary', 'clear'],
  greet: ['limit', 'names', 'all'],
  goto: [], // 无专属 flag（只带 1 个位置参数）
  accept: ['limit', 'no-preview'],
  reject: [],
  interview: ['remark'],
  'send-to': ['message', 'dry-run'],
  'send-current': ['message', 'dry-run'],
  'list-jobs': [],
  'select-job': [],
  'resume-detail': ['name', 'save-image'],
  'resume-batch': ['limit', 'save-dir'],
  mcp: ['stdio'],
  doctor: [],
  version: ['json'],
}

/** 全局 flag：任何命令都可带（cdp-port 连接端口；help/h 查看用法——help 在入口处短路，列于此处仅为白名单自洽） */
const GLOBAL_FLAGS: readonly string[] = ['cdp-port', 'help', 'h']

/** 各命令允许的额外位置参数个数（命令名本身是 positional[0]，不计入；未登记的命令默认 0） */
const COMMAND_POSITIONALS: Readonly<Record<string, number>> = {
  goto: 1, // goto recommend|chat
  'send-to': 1, // send-to <姓名>
  'select-job': 1, // select-job <职位名>
}

/** greet 定向名单上限（与 operation 层 bossGreet、MCP schema 的 3 人硬上限一致，CLI 提前拦） */
const GREET_NAMES_MAX = 3

/**
 * 原型链安全查表：plain object 直接 `table[key]` 会命中 Object.prototype 继承成员——
 * command 传 `__proto__` / `constructor` / `toString` 等时返回非 undefined 的继承属性，
 * 后续 `cmdFlags.includes(...)` 直接抛 TypeError（exit 1 栈打印，而非干净的「未知子命令」exit 2）。
 * 必须按自有属性查表（flag 一侧用 Map 存储，无此问题）。
 */
function ownEntry<T>(table: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined
}

/**
 * 校验已解析的 CLI 参数。command 传 positional[0]（无任何参数时为 undefined——
 * 未知/缺省子命令不在本层报错，交回 main() 的 default 分支保持既有「未知子命令」文案）。
 */
export function validateCommandArgs(command: string | undefined, args: ParsedArgs): ValidateResult {
  // --help/-h 短路：help 必须先于一切拒绝生效（与 main() parse 后的短路一致）
  if (args.flags.has('help') || args.flags.has('h')) return { ok: true }
  // 未登记的子命令（含无参数时的 undefined、help）不在本层报错：
  // 交回 main() 的 switch default 分支，保持既有「未知子命令」文案
  if (command === undefined) return { ok: true }
  const cmdFlags = ownEntry(COMMAND_FLAGS, command)
  if (cmdFlags === undefined) return { ok: true }

  // 1) 未知 flag 拒绝：事故根因——静默忽略让 `greet --help` 按默认参数执行了真实写动作
  for (const key of args.flags.keys()) {
    if (!cmdFlags.includes(key) && !GLOBAL_FLAGS.includes(key)) {
      const supported = cmdFlags.length > 0 ? `支持的参数：${cmdFlags.map((f) => `--${f}`).join(' ')}` : '没有专属参数（只允许 --cdp-port / --help）'
      return {
        ok: false,
        message: `不认识的参数 --${key}：${command} ${supported}。运行 ${command} --help 查看完整用法`,
      }
    }
  }

  // 2) 多余位置参数拒绝：事故根因——`greet 冯修业` 的姓名被静默丢弃、按非定向全量模式跑掉
  const allowed = ownEntry(COMMAND_POSITIONALS, command) ?? 0
  if (args.positional.length - 1 > allowed) {
    const extras = args.positional.slice(1 + allowed).join(' ')
    if (command === 'greet') {
      return {
        ok: false,
        message:
          `不认识的参数 ${extras}：greet 不接受位置参数（姓名写在这里会被静默丢弃）。` +
          '定向打招呼请用 greet --names <姓名>（逗号分隔多个），全量模式请显式加 --all。' +
          '运行 greet --help 查看完整用法',
      }
    }
    return {
      ok: false,
      message:
        `不认识的参数 ${extras}：${command} ${allowed === 0 ? '不接受位置参数' : `最多接受 ${allowed} 个位置参数`}。` +
        `运行 ${command} --help 查看完整用法`,
    }
  }

  // 3) greet 显式意图门禁（防误触发真实写动作）
  if (command === 'greet') return validateGreetIntent(args)
  return { ok: true }
}

/** greet 意图校验：--names（定向）与 --all（全量）必须二选一，且 --names 值合法 */
function validateGreetIntent(args: ParsedArgs): ValidateResult {
  const namesVal = args.flags.get('names')
  const hasNames = namesVal !== undefined
  const hasAll = args.flags.has('all')
  if (hasNames && hasAll) {
    return {
      ok: false,
      message: '--names 与 --all 互斥：定向打指定人请用 greet --names <姓名>，全量从列表顶部逐个打请用 --all，二选一。运行 greet --help 查看完整用法',
    }
  }
  if (!hasNames && !hasAll) {
    return {
      ok: false,
      message:
        'greet 需要显式选择模式：为防止误触发真实写动作，greet 不再默认全量打招呼。' +
        '定向打指定人请用 greet --names <姓名>（半角/全角逗号分隔多个，最多 3 人）；' +
        '全量从列表顶部逐个打请显式加 --all。运行 greet --help 查看完整用法',
    }
  }
  if (hasNames) {
    if (typeof namesVal !== 'string') {
      return {
        ok: false,
        message: 'greet --names 需要姓名列表：greet --names 冯修业,李四（半角/全角逗号分隔，最多 3 人）',
      }
    }
    return parseGreetNames(namesVal)
  }
  // 走到这里只剩全量模式（--all）：无附加值校验
  return { ok: true }
}

export type ParseGreetNamesResult = { ok: true; names: string[] } | { ok: false; message: string }

/**
 * 解析 greet --names 姓名列表：半角/全角逗号分隔（与 filter --education 同款 /[,，]/）。
 * 空项（如 a,,b、以逗号开头/结尾、全空白）一律视为输入错误——静默过滤空项会让
 * 「打漏/打错」无从察觉。validateCommandArgs 校验与 main() greet case 取值共用此函数，
 * 保证两处规则永远一致。
 */
export function parseGreetNames(raw: string): ParseGreetNamesResult {
  const parts = raw.split(/[,，]/).map((s) => s.trim())
  if (parts.some((p) => p === '')) {
    return {
      ok: false,
      message: `--names 姓名列表含空项（不要以逗号开头/结尾或连用两个逗号）：${raw}`,
    }
  }
  if (parts.length > GREET_NAMES_MAX) {
    return {
      ok: false,
      message: `--names 最多 ${GREET_NAMES_MAX} 个姓名（当前 ${parts.length} 个）：${raw}`,
    }
  }
  return { ok: true, names: parts }
}
