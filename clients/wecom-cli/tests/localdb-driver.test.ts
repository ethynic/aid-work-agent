/**
 * TS→py 驱动桥单测（mock spawn/resolvePythonFn，零真实 Python 依赖）：
 * 正常解析、多行取最后、无 LOCALDB_JSON 行、坏 JSON、超时 kill、abort 透传、stdin 密钥通道
 * （error 不含密钥）、解释器未解析到、spawn error、resolveLocaldbPython 回退链。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import {
  DEFAULT_LOCALDB_PY_TIMEOUT_MS,
  LOCALDB_JSON_PREFIX,
  LOCALDB_PYTHON_ENV_VAR,
  resolveLocaldbPython,
  runLocaldbPy,
  type LocaldbPyChild,
  type LocaldbPySpawnFn,
} from '../src/platform/localdb/driver.js'

const SECRET_HEX = '00112233445566778899aabbccddeeff' // 测试专用合成密钥
const DRIVER_SUFFIX = join('drivers', 'py', 'wecom_localdb.py')

/** stdout/stderr 流替身 */
interface FakeStream {
  setEncoding(encoding: string): void
  on(event: string, listener: (chunk?: unknown) => void): void
  emit(event: string, chunk?: unknown): void
  readonly enc: string
}

function makeStream(): FakeStream & { enc: string } {
  const handlers = new Map<string, Array<(chunk?: unknown) => void>>()
  const stream = {
    enc: '',
    setEncoding(encoding: string) {
      stream.enc = encoding
    },
    on(event: string, listener: (chunk?: unknown) => void) {
      const arr = handlers.get(event) ?? []
      arr.push(listener)
      handlers.set(event, arr)
    },
    emit(event: string, chunk?: unknown) {
      for (const l of handlers.get(event) ?? []) l(chunk)
    },
  }
  return stream
}

/** 子进程替身：记录 stdin/kill，测试手动 emitError/emitClose/emitStdinError */
interface FakeChild extends LocaldbPyChild {
  readonly killCalls: number
  stdinWritten: string[]
  stdinEnded: boolean
  emitData(stream: 'stdout' | 'stderr', chunk: string): void
  emitError(err: Error): void
  emitClose(code: number | null): void
  emitStdinError(err: Error): void
  readonly stdoutStream: FakeStream
  readonly stderrStream: FakeStream
}

function makeFakeChild(): FakeChild {
  const stdoutStream = makeStream()
  const stderrStream = makeStream()
  const errorListeners: Array<(err: Error) => void> = []
  const closeListeners: Array<(code: number | null) => void> = []
  const stdinErrorListeners: Array<(err: Error) => void> = []
  let kills = 0
  const child: FakeChild = {
    stdin: {
      write(chunk: string) {
        child.stdinWritten.push(chunk)
        return true
      },
      end() {
        child.stdinEnded = true
        return child
      },
      once(event: string, listener: (err: Error) => void) {
        if (event === 'error') stdinErrorListeners.push(listener)
        return child.stdin
      },
    },
    stdinWritten: [],
    stdinEnded: false,
    stdout: stdoutStream,
    stderr: stderrStream,
    stdoutStream,
    stderrStream,
    get killCalls() {
      return kills
    },
    kill() {
      kills++
      return true
    },
    on(event: string, listener: unknown): void {
      if (event === 'error') errorListeners.push(listener as (err: Error) => void)
      if (event === 'close') closeListeners.push(listener as (code: number | null) => void)
    },
    emitData(stream: 'stdout' | 'stderr', chunk: string) {
      ;(stream === 'stdout' ? stdoutStream : stderrStream).emit('data', chunk)
    },
    emitError(err: Error) {
      for (const l of errorListeners) l(err)
    },
    emitClose(code: number | null) {
      for (const l of closeListeners) l(code)
    },
    emitStdinError(err: Error) {
      for (const l of stdinErrorListeners) l(err)
    },
  }
  return child
}

interface SpawnCapture {
  file: string
  args: string[]
  signal?: AbortSignal
  child: FakeChild
  spawnCalls: number
}

/** mock spawn：捕获入参并返回 fake child */
function makeSpawn(): { spawnFn: LocaldbPySpawnFn; capture: SpawnCapture } {
  const capture: SpawnCapture = { file: '', args: [], spawnCalls: 0, child: null as unknown as FakeChild }
  const spawnFn: LocaldbPySpawnFn = (file, args, opts) => {
    capture.spawnCalls++
    capture.file = file
    capture.args = args
    capture.signal = opts.signal
    capture.child = makeFakeChild()
    return capture.child
  }
  return { spawnFn, capture }
}

const resolve = (py: string | null) => () => py

test('runLocaldbPy：正常流——最后一行 LOCALDB_JSON 解析为 data，args 携带 -I -B + 驱动路径', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy(['snapshot-verify', 'C:\\snap\\m.db-wal'], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  const child = capture.child
  child.emitData('stdout', 'noise line\n')
  child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {"ok":true,"total_frames":1}\n`)
  child.emitClose(0)
  const r = await promise
  assert.equal(r.ok, true)
  assert.equal(r.error, undefined)
  assert.equal((r.data as Record<string, unknown>)['total_frames'], 1)
  assert.ok(r.raw_last_line!.startsWith(LOCALDB_JSON_PREFIX))
  assert.equal(capture.file, 'py.exe')
  assert.equal(capture.spawnCalls, 1)
  assert.deepEqual(capture.args.slice(0, 2), ['-I', '-B'])
  assert.ok(capture.args[2]!.endsWith(DRIVER_SUFFIX), `args[2]=${capture.args[2]}`)
  assert.deepEqual(capture.args.slice(3), ['snapshot-verify', 'C:\\snap\\m.db-wal'])
  assert.equal(child.stdinEnded, true, 'stdin 应关闭（防 py read 挂起）')
  assert.ok(child.stdoutStream.enc.startsWith('utf8'))
})

test('runLocaldbPy：多行 LOCALDB_JSON 取最后一行', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {"ok":true,"n":1}\n`)
  capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {"ok":true,"n":2}\n`)
  capture.child.emitClose(0)
  const r = await promise
  assert.equal(r.ok, true)
  assert.equal((r.data as Record<string, unknown>)['n'], 2)
  assert.ok(r.raw_last_line!.includes('"n":2'))
})

test('runLocaldbPy：无 LOCALDB_JSON 行 → ok:false + error（含 exit 与 stderr 摘要）+ raw=null', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', 'hello\nworld\n')
  capture.child.emitData('stderr', 'Traceback (most recent call last)')
  capture.child.emitClose(1)
  const r = await promise
  assert.equal(r.ok, false)
  assert.equal(r.data, null)
  assert.equal(r.raw_last_line, null)
  assert.ok(r.error!.includes('LOCALDB_JSON'))
  assert.ok(r.error!.includes('exit=1'))
  assert.ok(r.error!.includes('Traceback'))
})

test('runLocaldbPy：坏 JSON → ok:false，raw_last_line 保留原文', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {oops-not-json\n`)
  capture.child.emitClose(0)
  const r = await promise
  assert.equal(r.ok, false)
  assert.equal(r.data, null)
  assert.ok(r.raw_last_line!.includes('{oops-not-json'))
  assert.ok(r.error!.includes('合法 JSON'))
})

test('runLocaldbPy：非对象 JSON（数组/标量）→ ok:false', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} [1,2,3]\n`)
  capture.child.emitClose(0)
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('JSON 对象'))
})

test('runLocaldbPy：超时 kill → ok:false（error 含超时），kill 被调用', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], { timeoutMs: 20 }, { spawnFn, resolvePythonFn: resolve('py.exe') })
  setTimeout(() => capture.child.emitClose(0), 80) // 超时 kill 后 close 才到达
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('超时'))
  assert.ok(capture.child.killCalls >= 1)
})

test('runLocaldbPy：AbortSignal 运行中中止 → kill + 等 close 到达后才以取消语义 resolve（评审 v6）', async () => {
  const { spawnFn, capture } = makeSpawn()
  const controller = new AbortController()
  const order: string[] = []
  const promise = runLocaldbPy(
    ['decrypt', 'db', '-', 'out'],
    { stdin: SECRET_HEX, signal: controller.signal },
    { spawnFn, resolvePythonFn: resolve('py.exe') },
  )
  void promise.then(() => order.push('resolved'))
  // abort 后先只发 error（node spawn 原生 AbortError 形态），close 延后一拍再发——
  // 取消结果必须等 close 到达才 resolve，不得抢跑
  setTimeout(() => {
    controller.abort()
    order.push('abort')
    capture.child.kill()
    capture.child.emitError(new Error('This operation was aborted'))
    setImmediate(() => {
      order.push('close')
      capture.child.emitClose(1)
    })
  }, 10)
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('取消'))
  assert.ok(capture.child.killCalls >= 1)
  assert.ok(!r.error!.includes(SECRET_HEX), 'error 不得含 stdin 密钥')
  assert.ok(order.indexOf('close') !== -1 && order.indexOf('close') < order.indexOf('resolved'),
    `resolve 必须晚于 close 到达：${order.join(' → ')}`)
})

test('runLocaldbPy：cancel 结果保留 close 前已收到的 stdout（raw_last_line 透传）', async () => {
  const { spawnFn, capture } = makeSpawn()
  const controller = new AbortController()
  const promise = runLocaldbPy([], { signal: controller.signal }, { spawnFn, resolvePythonFn: resolve('py.exe') })
  setTimeout(() => {
    capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {"ok":true,"n":1}\n`)
    controller.abort()
    capture.child.emitClose(0)
  }, 10)
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('取消'))
  assert.ok(r.raw_last_line!.includes('"n":1'), '取消时 close 前的 stdout 仍应进 raw_last_line')
})

test('runLocaldbPy：真实管道 EPIPE——子进程立即退出不读 stdin，写 stdin 不崩进程且返回结构化失败（评审 v6）', async () => {
  // 真实 spawn（node -e 立即退出，不读 stdin）：写 1MB stdin 落在已关闭管道上 → 异步 EPIPE。
  // 修复前：无监听器的 'error' 事件会让本测试进程直接崩溃；修复后必须以结构化失败返回。
  const child = spawn(process.execPath, ['-e', 'setTimeout(() => process.exit(0), 50)'], {
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  const fakeSpawn: LocaldbPySpawnFn = () => child as unknown as LocaldbPyChild
  const r = await runLocaldbPy(['ignored'], { stdin: 'a'.repeat(1024 * 1024), timeoutMs: 15_000 }, {
    spawnFn: fakeSpawn,
    resolvePythonFn: () => process.execPath,
    driverPath: 'ignored.py',
  })
  assert.equal(r.ok, false, `EPIPE/子进程退出必须结构化失败（实际 error=${r.error ?? '无'}）`)
  assert.equal(r.data, null)
  assert.ok(r.error!.length > 0)
  assert.ok(!r.error!.includes('aaaa'), 'error 不得内嵌 stdin 内容')
  // 执行到这里本身就是「未崩溃」证据（修复前未处理 error 事件会让进程退出）
})

test('runLocaldbPy：spawn 前已中止 → 不启动进程直接返回取消', async () => {
  const { spawnFn, capture } = makeSpawn()
  const controller = new AbortController()
  controller.abort()
  const r = await runLocaldbPy([], { signal: controller.signal }, { spawnFn, resolvePythonFn: resolve('py.exe') })
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('取消'))
  assert.equal(capture.spawnCalls, 0)
})

test('runLocaldbPy：stdin 写入密钥通道——内容只进 stdin，正常流 error 缺省', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy(['decrypt', 'db', '-', 'out'], { stdin: SECRET_HEX }, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {"ok":true,"integrity":"ok"}\n`)
  capture.child.emitClose(0)
  const r = await promise
  assert.equal(r.ok, true)
  assert.deepEqual(capture.child.stdinWritten, [SECRET_HEX])
  assert.equal(capture.child.stdinEnded, true)
})

test('runLocaldbPy：stdin 异步 EPIPE（子进程已退出）→ close 前到达则结构化失败 + kill 协调退出（评审 v6）', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy(['decrypt', 'db', '-', 'out'], { stdin: SECRET_HEX }, { spawnFn, resolvePythonFn: resolve('py.exe') })
  // 子进程已退出（stdin 读端关闭）→ 写入触发异步 EPIPE：close 前到达
  capture.child.emitData('stdout', 'child exited early\n')
  capture.child.emitStdinError(new Error('write EPIPE'))
  capture.child.emitClose(1)
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('stdin 写入失败'), r.error)
  assert.ok(r.error!.includes('EPIPE'))
  assert.ok(!r.error!.includes(SECRET_HEX), 'error 不得含 stdin 密钥')
  assert.ok(capture.child.killCalls >= 1, 'stdin 失败应协调子进程退出（已退出时 kill 为无害 no-op）')
})

test('runLocaldbPy：stdin EPIPE 晚于 close 到达 → 结果按 close 语义；迟到的 error 不崩进程（评审 v6）', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', `${LOCALDB_JSON_PREFIX} {"ok":true}\n`)
  capture.child.emitClose(0)
  const r = await promise
  assert.equal(r.ok, true)
  // 已 settle 后 stdin 仍报 EPIPE（管道竞态）：once 监听已注册 → 事件被捕获，不影响已定结果
  capture.child.emitStdinError(new Error('write EPIPE'))
  assert.equal((r.data as Record<string, unknown>)['ok'], true)
})

test('runLocaldbPy：无 LOCALDB_JSON 且带 stdin 密钥 → error 不含密钥内容', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy(['decrypt', 'db', '-', 'out'], { stdin: SECRET_HEX }, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitData('stdout', 'python crashed\n')
  capture.child.emitClose(2)
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(!r.error!.includes(SECRET_HEX), 'error 不得含 stdin 密钥')
})

test('runLocaldbPy：解释器未解析到 → ok:false 明确 error，不 spawn；error 不含密钥', async () => {
  const { spawnFn, capture } = makeSpawn()
  const r = await runLocaldbPy(['decrypt', 'db', '-', 'out'], { stdin: SECRET_HEX }, { spawnFn, resolvePythonFn: resolve(null) })
  assert.equal(r.ok, false)
  assert.equal(r.data, null)
  assert.ok(r.error!.includes(LOCALDB_PYTHON_ENV_VAR))
  assert.ok(r.error!.includes('Python'))
  assert.ok(!r.error!.includes(SECRET_HEX))
  assert.equal(capture.spawnCalls, 0)
})

test('runLocaldbPy：spawn error（ENOENT 等）→ ok:false 启动失败说明', async () => {
  const { spawnFn, capture } = makeSpawn()
  const promise = runLocaldbPy([], {}, { spawnFn, resolvePythonFn: resolve('py.exe') })
  capture.child.emitError(new Error('spawn py.exe ENOENT'))
  const r = await promise
  assert.equal(r.ok, false)
  assert.ok(r.error!.includes('启动失败'))
  assert.ok(r.error!.includes('ENOENT'))
})

test('runLocaldbPy：默认注入路径（真实解析链 + 默认驱动路径）走通 LOCALDB_JSON 协议', async () => {
  // 不注入 spawnFn/resolvePythonFn/driverPath：真实 spawn（本机 venv 存在，package.json test:py
  // 同位）+ 源码树 drivers/py/wecom_localdb.py；未知模式参数 → py 以 BAD_ARGS JSON 兜底输出
  //（协议：任何错误也输出 JSON 且退出码 0）。venv 缺失的机器上退化为验证「未找到解释器」分支。
  if (resolveLocaldbPython() === null) {
    const rNoPy = await runLocaldbPy([], {}, {})
    assert.equal(rNoPy.ok, false)
    assert.ok(rNoPy.error!.includes('Python'))
    return
  }
  const r = await runLocaldbPy(['__definitely_not_a_mode__'], { timeoutMs: 30_000 }, {})
  assert.equal(DEFAULT_LOCALDB_PY_TIMEOUT_MS, 120_000)
  assert.equal(r.ok, true, `真实链路应能解析 LOCALDB_JSON（error=${r.error ?? '无'}）`)
  assert.equal((r.data as Record<string, unknown>)['error'], 'BAD_ARGS')
  assert.ok(r.raw_last_line!.startsWith(LOCALDB_JSON_PREFIX))
})

test('resolveLocaldbPython：环境变量优先（存在才用）；不存在时回退后续候选', () => {
  const envPath = 'C:\\custom\\python.exe'
  const devPython = join('C:\\pk', '..', '..', 'venv', 'Scripts', 'python.exe')
  // ① env 命中
  assert.equal(
    resolveLocaldbPython({ env: { [LOCALDB_PYTHON_ENV_VAR]: envPath }, packageRoot: 'C:\\pk', existsFn: (p) => p === envPath }),
    envPath,
  )
  // ② env 不存在 → 仓库 venv 命中
  let seen: string[] = []
  assert.equal(
    resolveLocaldbPython({
      env: { [LOCALDB_PYTHON_ENV_VAR]: envPath },
      packageRoot: 'C:\\pk',
      existsFn: (p) => {
        seen.push(p)
        return p === devPython
      },
    }),
    devPython,
  )
  assert.ok(seen.includes(envPath) && seen.includes(devPython), `探测序=${seen.join(' | ')}`)
})

test('resolveLocaldbPython：无 env → 仓库 venv 优先于打包 ocr-python；全不可用 → null', () => {
  const bundled = join('C:\\pk', 'ocr-python', 'python.exe')
  const devPython = join('C:\\pk', '..', '..', 'venv', 'Scripts', 'python.exe')
  // venv 与 bundled 同时存在 → venv 优先
  assert.equal(
    resolveLocaldbPython({ env: {}, packageRoot: 'C:\\pk', existsFn: (p) => p === devPython || p === bundled }),
    devPython,
  )
  // 仅 bundled 存在 → 打包态命中
  assert.equal(resolveLocaldbPython({ env: {}, packageRoot: 'C:\\pk', existsFn: (p) => p === bundled }), bundled)
  // 全不可用 → null
  assert.equal(resolveLocaldbPython({ env: {}, packageRoot: 'C:\\pk', existsFn: () => false }), null)
})
