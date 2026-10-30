/**
 * C2 运行检测：tasklist 只读枚举 WXWork.exe 进程 → { running, pids[] }（设计 §3.2 C2）。
 *
 * 与 environment.ts 的 countWecomProcesses（计数、子串匹配）相比，本组件额外解析出 PID
 * 列表——密钥内存扫描（C6 keyscan，设计 §3.2 组件表）需要 PID 入参，运行检测与扫描预筛
 * 共用同一进程事实来源。
 *
 * 严格只读：tasklist /FI 过滤枚举，不触碰进程本身。编码注意：中文 Windows 下 tasklist
 * 输出可能是 GBK（utf8 解码会花），但映像名与 PID 是 ASCII，引号结构不受影响，按字段
 * 解析安全；INFO 提示行（中文/英文）不匹配进程名，天然跳过。
 *
 * 测试可注入 execFileFn 替身；解析器 parseTasklistCsv 单独导出，直接喂罐头 CSV。
 */
import { execFile } from 'node:child_process'
import type { ExecFileFn } from '../environment.js'

/** 默认命令执行：只读命令，5s 超时，不弹窗（与 environment.ts 的 defaultExecFile 同语义；该 helper 未导出，为不动现有文件在此等义复制） */
const defaultExecFile: ExecFileFn = (file, args) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { timeout: 5000, windowsHide: true, encoding: 'utf8' }, (err, stdout) => {
      if (err) reject(err)
      else resolve({ stdout: String(stdout) })
    })
  })

export interface DetectRunningDeps {
  execFileFn?: ExecFileFn
}

export interface RunningInfo {
  running: boolean
  /** WXWork.exe 的 PID 列表（可含主进程与子进程；内存扫描由调用方自行筛选） */
  pids: number[]
  /**
   * 探测可信度：'ok'=tasklist 正常返回（此时 pids 空=确实未运行）；
   * 'failed'=探测失败（命令缺失/超时/权限等）——此时 running=false 只是落空值，
   * 不可作为「未运行」结论使用。
   */
  probe: 'ok' | 'failed'
  /** probe='failed' 时的简短原因；成功时缺省 */
  error?: string
}

/** 单行 CSV 字段切分：处理双引号包裹与 "" 转义（内存列含千分位逗号 "12,345 K"，不能按逗号裸切） */
function splitCsvLine(line: string): string[] {
  const fields: string[] = []
  let cur = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"'
          i++
        } else {
          inQuotes = false
        }
      } else {
        cur += ch
      }
    } else if (ch === '"') {
      inQuotes = true
    } else if (ch === ',') {
      fields.push(cur)
      cur = ''
    } else {
      cur += ch
    }
  }
  fields.push(cur)
  return fields
}

/**
 * 解析 tasklist /FO CSV /NH 输出（单独导出供单测喂罐头字符串）。
 * 行格式："映像名称","PID","会话名","会话编号","内存使用"；取第 2 列 PID。
 * 进程名匹配大小写不敏感；INFO 无任务行、空行、字段缺失、PID 非纯数字/非正数的行一律跳过。
 */
export function parseTasklistCsv(csv: string): number[] {
  const pids: number[] = []
  for (const rawLine of csv.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    const fields = splitCsvLine(line)
    const name = (fields[0] ?? '').trim()
    if (!/^wxwork\.exe$/i.test(name)) continue
    // PID 先做完整数字校验再转换：parseInt 过松（"123oops"→123、"12.5"→12）会把脏数据当 PID
    const pidRaw = (fields[1] ?? '').trim()
    if (!/^\d+$/.test(pidRaw)) continue
    const pid = Number(pidRaw)
    if (pid <= 0) continue
    pids.push(pid)
  }
  return pids
}

/**
 * 运行检测入口：tasklist 只读枚举；best-effort 不抛出。
 * 结果按 probe 区分：'ok'（含 pids 空的确实未运行）/'failed'（exec 失败——running=false
 * 不可作为「未运行」结论，error 附简短原因）。
 */
export async function detectRunning(deps: DetectRunningDeps = {}): Promise<RunningInfo> {
  const execFileFn = deps.execFileFn ?? defaultExecFile
  try {
    const { stdout } = await execFileFn('tasklist', ['/FI', 'IMAGENAME eq WXWork.exe', '/FO', 'CSV', '/NH'])
    const pids = parseTasklistCsv(stdout)
    return { running: pids.length > 0, pids, probe: 'ok' }
  } catch (err) {
    return {
      running: false,
      pids: [],
      probe: 'failed',
      error: err instanceof Error ? err.message : String(err),
    }
  }
}
