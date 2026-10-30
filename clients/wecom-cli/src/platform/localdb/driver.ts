/**
 * TS→py 驱动桥（wecom_localdb.py 的 spawn 通道；C6/C7/C8 的统一 Python 调用入口）。
 *
 * - 解释器解析 resolveLocaldbPython（对齐 _common.ps1 Resolve-WeComOcrPython 的存在性探测
 *   思路 + matrix.ts 的相对包根解析）：
 *   ① 环境变量 AID_WECOM_LOCALDB_PYTHON（显式覆盖，文件存在才用）；
 *   ② 仓库根 venv\Scripts\python.exe（开发态；相对包根上一级解析，与 package.json test:py
 *      的 ..\..\venv 同位）；
 *   ③ 打包态 ocr-python\python.exe（发行包内解释器，存在才用）；
 *   ④ 全部不可用 → null（调用方按 CONFIG_MISSING 语义处理）。
 * - runLocaldbPy：spawn python -I -B drivers/py/wecom_localdb.py <args>，收集 stdout 最后一行
 *   `LOCALDB_JSON: {...}` 解析（前缀协议对齐 py emit()/ps1 DRIVER_JSON 惯例）；opts.stdin
 *   写入子进程 stdin（密钥通道——密钥 hex 只进管道，绝不进命令行参数/错误消息/日志）；超时
 *   kill；AbortSignal 透传（spawn 前 fast-fail；运行中 kill 后【等 close 事件到达】才以取消
 *   语义 resolve——resolve 不早于子进程退出，评审 v6）。
 * - 结果语义（桥级）：ok=true 且 data=parsed 当且仅当找到 LOCALDB_JSON 行且解析为 JSON 对象；
 *   业务成败看 data.ok（py 协议字段）。桥级失败（无行/坏 JSON/超时/取消/无解释器）→
 *   ok:false + data:null + raw_last_line 保留 + error 说明。
 * - 密钥安全：error 消息只含可执行路径/退出码/stderr 摘要（stderr 由 py 保证不含密钥），
 *   绝不内嵌 opts.stdin 或 raw 行内容（raw_last_line 可能含协议载荷如 keyscan key_hex，
 *   只经返回值内存通道交付调用方，调用方不得写日志）。
 * - 驱动脚本路径按源码树定位（dist/src/platform/localdb → 包根 drivers/py，ps1 驱动同约定；
 *   py 不进 dist，打包时 drivers/ 资产登记属组合任务，见开发计划）。
 *
 * 测试注入 spawnFn/resolvePythonFn/driverPath 替身，覆盖正常/无行/坏 JSON/超时/abort/stdin/
 * 无解释器，零真实 Python 依赖；真机冒烟见开发计划登记区。
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, toNamespacedPath } from 'node:path'
import { fileURLToPath } from 'node:url'

/** 解释器覆盖环境变量名 */
export const LOCALDB_PYTHON_ENV_VAR = 'AID_WECOM_LOCALDB_PYTHON'

/** 默认执行超时（毫秒）：快照/查询类秒级完成，留足慢机余量；keyscan 大内存场景由调用方放宽 */
export const DEFAULT_LOCALDB_PY_TIMEOUT_MS = 120_000

/** py 驱动 stdout 协议前缀（对齐 drivers/py/wecom_localdb.py emit()） */
export const LOCALDB_JSON_PREFIX = 'LOCALDB_JSON:'

/**
 * 驱动脚本路径：dist/src/platform/localdb → 包根 drivers/py（py 不进 dist，按源码树定位；
 * 打包登记关系留组合任务，见开发计划）。
 */
const PY_DRIVER_PATH = fileURLToPath(new URL('../../../../drivers/py/wecom_localdb.py', import.meta.url))
const PY_BOOTSTRAP_PATH = fileURLToPath(new URL('../../../../drivers/py/python_bootstrap.py', import.meta.url))

export interface ResolveLocaldbPythonDeps {
  /** 环境变量替身（缺省 process.env） */
  env?: Record<string, string | undefined>
  /** 包根覆盖（缺省由 import.meta.url 解析到包根） */
  packageRoot?: string
  /** 存在性探测替身（缺省 existsSync） */
  existsFn?: (path: string) => boolean
}

/**
 * 解析本地库 Python 可执行（存在性探测逐级回退；全部不可用 → null）。
 * 顺序：AID_WECOM_LOCALDB_PYTHON → 仓库根 venv\Scripts\python.exe → <包根>\ocr-python\python.exe。
 */
export function resolveLocaldbPython(deps: ResolveLocaldbPythonDeps = {}): string | null {
  const env = deps.env ?? process.env
  const existsFn = deps.existsFn ?? existsSync
  const packageRoot = deps.packageRoot ?? fileURLToPath(new URL('../../../../', import.meta.url))
  const envPath = env[LOCALDB_PYTHON_ENV_VAR]
  if (typeof envPath === 'string' && envPath.length > 0 && existsFn(envPath)) return envPath
  // 开发态：仓库根 venv（clients/wecom-cli 包根的上两级；与 package.json test:py 的 ..\..\venv 同位）
  const devPython = join(packageRoot, '..', '..', 'venv', 'Scripts', 'python.exe')
  if (existsFn(devPython)) return devPython
  // 打包态：发行包内解释器（与 _common.ps1 Resolve-WeComOcrPython 的 bundled 同位）
  const bundledPython = join(packageRoot, 'ocr-python', 'python.exe')
  if (existsFn(bundledPython)) return bundledPython
  return null
}

/** stdin 通道最小面（Writable 结构子集；真实 ChildProcess 的 stdin 可赋值） */
export interface LocaldbPyStdin {
  write(chunk: string): unknown
  end(): unknown
  /**
   * 一次性错误监听（真实 stdin 是 net.Socket）：EPIPE 等异步写错误只能经 'error' 事件到达，
   * 无监听器的 'error' 事件会直接崩掉 Node 进程（评审 v6）。
   */
  once(event: 'error', listener: (err: Error) => void): unknown
}

/** stdout/stderr 流最小面（Readable 结构子集；真实 ChildProcess 的流可赋值） */
export interface LocaldbPyStream {
  setEncoding(encoding: string): void
  on(event: string, listener: (chunk?: unknown) => void): void
}

/** 子进程最小面（结构化替身接口；真实 spawn 返回值结构兼容） */
export interface LocaldbPyChild {
  stdin: LocaldbPyStdin | null
  stdout: LocaldbPyStream | null
  stderr: LocaldbPyStream | null
  on(event: 'error', listener: (err: Error) => void): void
  on(event: 'close', listener: (code: number | null) => void): void
  kill(): boolean
}

export type LocaldbPySpawnFn = (
  file: string,
  args: string[],
  opts: { signal?: AbortSignal },
) => LocaldbPyChild

/** 缺省 spawn：隐藏窗口、三管道、AbortSignal 交给 node spawn 原生透传（abort → kill + error） */
const defaultSpawn: LocaldbPySpawnFn = (file, args, opts) =>
  spawn(process.platform === 'win32' ? toNamespacedPath(file) : file,
    process.platform === 'win32' ? [...args.slice(0, 2), PY_BOOTSTRAP_PATH, ...args.slice(2)] : args,
    { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], signal: opts.signal })

export interface RunLocaldbPyOpts {
  /** 写入子进程 stdin 的文本（decrypt 模式的密钥 hex；密钥通道，不进参数/错误/日志） */
  stdin?: string
  /** 超时毫秒（超时 kill 子进程；缺省 DEFAULT_LOCALDB_PY_TIMEOUT_MS） */
  timeoutMs?: number
  /** 取消信号（spawn 前已中止 → 不启动；运行中中止 → kill 子进程） */
  signal?: AbortSignal
}

export interface RunLocaldbPyDeps {
  /** spawn 替身（测试注入，捕获入参/驱动 fake 子进程） */
  spawnFn?: LocaldbPySpawnFn
  /** 解释器解析替换（缺省 resolveLocaldbPython()） */
  resolvePythonFn?: () => string | null
  /** 驱动脚本路径覆盖（缺省包根 drivers/py/wecom_localdb.py） */
  driverPath?: string
}

export interface LocaldbPyResult {
  /** 桥级成功 = 找到 LOCALDB_JSON 行且解析为 JSON 对象；业务成败看 data.ok */
  ok: boolean
  /** 解析出的协议对象（桥级失败 → null；业务失败时为含 ok:false 的完整 payload） */
  data: Record<string, unknown> | null
  /** 最后一行 LOCALDB_JSON 原文（含前缀；可能含协议载荷如 keyscan key_hex——只经内存通道
   *  交付，调用方不得写日志/错误消息）；无该行 → null */
  raw_last_line: string | null
  /** 桥级失败说明（不含 stdin 内容与 raw 载荷）；成功时缺省 */
  error?: string
}

const failResult = (error: string, rawLastLine: string | null = null): LocaldbPyResult => ({
  ok: false,
  data: null,
  raw_last_line: rawLastLine,
  error,
})

/** 从 stdout 提取最后一行 LOCALDB_JSON（剥 BOM 与行尾空白；无 → null） */
function extractLastJsonLine(stdout: string): string | null {
  const lines = stdout.split(/\r?\n/)
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.replace(/^\uFEFF/, '').trim()
    if (line.startsWith(LOCALDB_JSON_PREFIX)) {
      return line.slice(LOCALDB_JSON_PREFIX.length).trim()
    }
  }
  return null
}

/**
 * 运行本地库 py 驱动并解析 LOCALDB_JSON 协议。不抛出（桥级失败以 error 字段返回）；
 * 取消/超时也不抛——与 powershell.ts runPowerShellScript 的抛错语义刻意区分：本桥是
 * localdb 内部组件间的通道，调用方按 LocaldbPyResult 结构化分支，无需再接错误映射层。
 */
export async function runLocaldbPy(
  args: string[],
  opts: RunLocaldbPyOpts = {},
  deps: RunLocaldbPyDeps = {},
): Promise<LocaldbPyResult> {
  if (opts.signal?.aborted) {
    return failResult('本地库驱动调用已取消（AbortSignal 已中止）')
  }
  const python = deps.resolvePythonFn ? deps.resolvePythonFn() : resolveLocaldbPython()
  if (!python) {
    // 明确 error 指出解析链全部落空（提示环境变量与两条候选位置）；不含任何密钥信息
    return failResult(
      `未找到本地库 Python 解释器（${LOCALDB_PYTHON_ENV_VAR}、仓库根 venv\\Scripts\\python.exe、` +
        '打包 ocr-python\\python.exe 均不可用）',
    )
  }
  const driverPath = deps.driverPath ?? PY_DRIVER_PATH
  const spawnFn = deps.spawnFn ?? defaultSpawn
  const timeoutMs = opts.timeoutMs ?? DEFAULT_LOCALDB_PY_TIMEOUT_MS

  return new Promise<LocaldbPyResult>((resolve) => {
    let child: LocaldbPyChild
    try {
      child = spawnFn(python, ['-I', '-B', driverPath, ...args], { signal: opts.signal })
    } catch (err) {
      resolve(failResult(`本地库驱动 spawn 失败：${err instanceof Error ? err.message : String(err)}`))
      return
    }
    let stdout = ''
    let stderr = ''
    let settled = false
    let timedOut = false
    let killIssued = false
    let stdinError: Error | null = null // 异步 stdin error（EPIPE 等）：捕获进结果，防未处理 error 崩进程
    let abortReason: string | null = null // 运行中取消：等 close 到达后再以取消语义 resolve
    let onAbort: (() => void) | null = null
    const killOnce = (): void => {
      if (killIssued) return // 已在退出流程（超时/取消/stdin 失败已 kill）不重复
      killIssued = true
      child.kill()
    }
    const timer = setTimeout(() => {
      timedOut = true
      killOnce()
    }, timeoutMs)
    const finish = (result: LocaldbPyResult) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (opts.signal !== undefined && onAbort !== null) {
        opts.signal.removeEventListener('abort', onAbort) // 正常完成：解除取消监听（信号可能被复用）
      }
      resolve(result)
    }
    child.stdout?.setEncoding('utf8')
    child.stdout?.on('data', (chunk) => {
      stdout += typeof chunk === 'string' ? chunk : String(chunk ?? '')
    })
    child.stderr?.setEncoding('utf8')
    child.stderr?.on('data', (chunk) => {
      stderr += typeof chunk === 'string' ? chunk : String(chunk ?? '')
    })

    // 密钥通道：stdin 只写管道并立即关闭（无 stdin 时也关闭，防 py 侧 read 挂起）。
    // 评审 v6：EPIPE（子进程已退出时写 stdin）是异步 'error' 事件，try/catch 捕获不到——
    // 写之前必须注册 once('error')，否则未处理 error 事件直接崩掉 Node 进程；错误转为本次
    // 调用的结构化失败（错误消息来自流本身，不含 stdin 内容）。
    if (child.stdin) {
      try {
        child.stdin.once('error', (err) => {
          if (stdinError !== null) return
          stdinError = err
          if (!settled) killOnce() // 子进程已退出时 kill 是无害 no-op；仍在运行则提前结束等待
        })
        if (opts.stdin !== undefined) child.stdin.write(opts.stdin)
        child.stdin.end()
      } catch {
        // 同步写失败交由子进程退出体现（异步路径由上面 once 监听兜住）
      }
    }

    // 运行中取消（评审 v6）：记录原因 + kill 子进程，但【等 close 事件到达再 resolve】——
    // 取消结果总是携带最终 stdout/stderr，且 resolve 不早于子进程退出。
    if (opts.signal !== undefined) {
      onAbort = () => {
        if (abortReason === null) abortReason = '本地库驱动调用已取消（AbortSignal）'
        killOnce()
      }
      opts.signal.addEventListener('abort', onAbort, { once: true })
    }
    child.on('error', (err) => {
      if (opts.signal?.aborted) {
        // abort 触发的 error（node spawn 原生 AbortError 等）：只记录取消原因，等 close 到达再出结果
        if (abortReason === null) abortReason = '本地库驱动调用已取消（AbortSignal）'
        return
      }
      // spawn ENOENT 等启动失败：进程从未存在，没有 close 可等 → 立即出结果
      finish(failResult(`本地库驱动进程启动失败：${err.message}`))
    })
    child.on('close', (code) => {
      const jsonBody = extractLastJsonLine(stdout)
      const rawLastLine = jsonBody !== null ? LOCALDB_JSON_PREFIX + ' ' + jsonBody : null
      if (opts.signal?.aborted || abortReason !== null) {
        finish(failResult(abortReason ?? '本地库驱动调用已取消（AbortSignal）', rawLastLine))
        return
      }
      if (timedOut) {
        finish(failResult(`本地库驱动执行超时（>${Math.round(timeoutMs / 1000)}s），已终止子进程`, rawLastLine))
        return
      }
      if (stdinError !== null) {
        finish(failResult(`本地库驱动 stdin 写入失败（子进程可能已提前退出）：${stdinError.message}`, rawLastLine))
        return
      }
      if (jsonBody === null) {
        const stderrSummary = stderr.trim().slice(0, 300)
        finish(
          failResult(
            `驱动未输出 LOCALDB_JSON 行（exit=${code}${stderrSummary ? `；stderr: ${stderrSummary}` : ''}）`,
          ),
        )
        return
      }
      let parsed: unknown
      try {
        parsed = JSON.parse(jsonBody)
      } catch {
        finish(failResult('LOCALDB_JSON 行不是合法 JSON', rawLastLine)) // 解析失败 raw 保留
        return
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        finish(failResult('LOCALDB_JSON 行不是 JSON 对象', rawLastLine))
        return
      }
      finish({ ok: true, data: parsed as Record<string, unknown>, raw_last_line: rawLastLine })
    })
  })
}
