/**
 * OperationResult 字段契约 / effect 枚举 / 错误映射 / probe 参数校验与环境判定。
 *
 * 用注入替身（platform/sessionName/execFileFn）模拟环境，不依赖真机企微：
 * 参数校验失败时不得触达任何系统命令；环境违规映射为稳定错误码。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mapExecutorError } from '../src/operations/errorMapping.js'
import {
  CancelledError,
  CodedOperationError,
  failResult,
  okResult,
  writeEffect,
  type OpContext,
} from '../src/operations/types.js'
import { createWecomProbeOperation } from '../src/operations/probe.js'
import { countWecomProcesses, isInteractiveSession, probeEnvironment } from '../src/platform/environment.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const EFFECT_VALUES = ['none', 'applied', 'partial', 'unknown']

// ---------- 统一结果字段契约 ----------

test('okResult/failResult：字段完整，effect 枚举合法', () => {
  const ok = okResult('rid', '完成', 'none')
  assert.equal(ok.success, true)
  assert.equal(ok.code, 'OK')
  assert.equal(ok.retryable, false)
  assert.deepEqual(ok.data, {})
  assert.ok(EFFECT_VALUES.includes(ok.effect))

  const fail = failResult('rid', 'BUSY', '占用中', 'none')
  assert.equal(fail.success, false)
  assert.ok(EFFECT_VALUES.includes(fail.effect))
})

test('retryable 只对写动作前的环境型失败开放（BUSY/WECOM_NOT_FOUND/NOT_LOGGED_IN）', () => {
  assert.equal(failResult('r', 'BUSY', '', 'none').retryable, true)
  assert.equal(failResult('r', 'WECOM_NOT_FOUND', '', 'none').retryable, true)
  assert.equal(failResult('r', 'NOT_LOGGED_IN', '', 'none').retryable, true)
  // 平台违规重试无意义；写动作 unknown/partial 永不自动重试
  assert.equal(failResult('r', 'WINDOWS_REQUIRED', '', 'none').retryable, false)
  assert.equal(failResult('r', 'EXECUTION_UNKNOWN', '', 'unknown').retryable, false)
  assert.equal(failResult('r', 'CUSTOMER_NOT_FOUND', '', 'none').retryable, false)
  assert.equal(failResult('r', 'CANCELLED', '', 'partial').retryable, false)
})

test('writeEffect：成功按完成量；EXECUTION_UNKNOWN/INTERNAL_ERROR=unknown；CANCELLED 按完成量 none/partial', () => {
  assert.equal(writeEffect(true, 'OK', 0), 'none')
  assert.equal(writeEffect(true, 'OK', 1), 'applied')
  assert.equal(writeEffect(false, 'EXECUTION_UNKNOWN', 0), 'unknown')
  assert.equal(writeEffect(false, 'INTERNAL_ERROR', 3), 'unknown')
  assert.equal(writeEffect(false, 'CANCELLED', 0), 'none')
  assert.equal(writeEffect(false, 'CANCELLED', 1), 'partial')
  assert.equal(writeEffect(false, 'UI_CHANGED', 5), 'none')
})

// ---------- errorMapping ----------

test('errorMapping：取消与主动 code 直接采用', () => {
  assert.equal(mapExecutorError(new CancelledError()).code, 'CANCELLED')
  assert.equal(mapExecutorError(new CodedOperationError('WINDOWS_REQUIRED', '仅 Windows')).code, 'WINDOWS_REQUIRED')
  assert.equal(mapExecutorError(new CodedOperationError('INTERACTIVE_SESSION_REQUIRED', '非交互')).code, 'INTERACTIVE_SESSION_REQUIRED')
})

test('errorMapping：系统命令缺失与兜底 → INTERNAL_ERROR', () => {
  const enoent = Object.assign(new Error('spawn where.exe ENOENT'), { code: 'ENOENT' })
  assert.equal(mapExecutorError(enoent).code, 'INTERNAL_ERROR')
  assert.equal(mapExecutorError(new Error('unexpected')).code, 'INTERNAL_ERROR')
  assert.equal(mapExecutorError('字符串错误').code, 'INTERNAL_ERROR')
})

// ---------- 环境判定原语 ----------

test('isInteractiveSession：Console/RDP-Tcp 交互；Services/缺失非交互', () => {
  assert.equal(isInteractiveSession('Console'), true)
  assert.equal(isInteractiveSession('RDP-Tcp#0'), true)
  assert.equal(isInteractiveSession('Services'), false)
  assert.equal(isInteractiveSession(undefined), false)
  assert.equal(isInteractiveSession(''), false)
})

test('countWecomProcesses：按 CSV 行统计，未运行时（GBK 提示行）为 0', () => {
  assert.equal(countWecomProcesses('"WXWork.exe","12345","Console","1","100,000 K"\r\n'), 1)
  assert.equal(
    countWecomProcesses('"WXWork.exe","1","Console","1","100,000 K"\n"WXWork.exe","2","Console","1","90,000 K"'),
    2,
  )
  // 中文 Windows 的「没有运行的任务」提示（GBK 读作 UTF-8 的乱码也不含 WXWork.exe）
  assert.equal(countWecomProcesses('Ϣ: ûеƥָ׼'), 0)
  assert.equal(countWecomProcesses('INFO: No tasks are running which match the specified criteria.'), 0)
})

// ---------- probe operation ----------

/** fake execFile：按命令脚本化返回 */
function fakeExec(responses: Record<string, string | Error>) {
  const calls: string[] = []
  const fn = async (file: string, args: string[]) => {
    calls.push(`${file} ${args.join(' ')}`)
    for (const [key, value] of Object.entries(responses)) {
      if (file.includes(key)) {
        if (value instanceof Error) throw value
        return { stdout: value }
      }
    }
    throw new Error(`未脚本化的命令：${file}`)
  }
  return { fn, calls }
}

test('probe：非 win32 → WINDOWS_REQUIRED，effect=none，不触达任何系统命令', async () => {
  const { fn, calls } = fakeExec({})
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => probeEnvironment({ ...opts, platform: 'linux' as NodeJS.Platform, execFileFn: fn }),
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WINDOWS_REQUIRED')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.ok(r.run_id.length > 0)
  assert.equal(calls.length, 0)
})

test('probe：服务会话 → INTERACTIVE_SESSION_REQUIRED', async () => {
  const { fn } = fakeExec({ 'where.exe': 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\r\n' })
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => probeEnvironment({ ...opts, sessionName: 'Services', execFileFn: fn }),
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.code, 'INTERACTIVE_SESSION_REQUIRED')
  assert.equal(r.effect, 'none')
})

test('probe：正常环境 → OK + 能力矩阵 data（企微未运行不是失败，不启动驱动）', async () => {
  const { fn } = fakeExec({
    'where.exe': 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\r\n',
    tasklist: '信息: 没有运行的任务匹配指定标准。',
  })
  let driverCalled = 0
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => probeEnvironment({ ...opts, sessionName: 'Console', execFileFn: fn }),
    runDriverFn: async () => {
      driverCalled++
      return {}
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  assert.equal(r.data.interactive_session, true)
  assert.equal(r.data.powershell_available, true)
  assert.equal(r.data.wecom_running, false)
  assert.equal(r.data.wecom_processes, 0)
  assert.equal(r.data.login_state, 'offline')
  assert.equal(r.data.main_window, null)
  assert.equal(driverCalled, 0, '进程未运行时不得启动窗口级驱动')
})

test('probe：企微进程在运行 → 走驱动（mock）带回窗口/登录态/当前页', async () => {
  const { fn } = fakeExec({
    'where.exe': 'C:\\powershell.exe\r\n',
    tasklist: '"WXWork.exe","12345","Console","1","100,000 K"\r\n',
  })
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => probeEnvironment({ ...opts, sessionName: 'Console', execFileFn: fn }),
    runDriverFn: async () => ({
      login_state: 'online',
      main_window: { hwnd: 69334, x: 1466, y: 331, w: 1089, h: 828 },
      current_page: '通讯录',
    }),
  })
  const r = await op.execute({ verbose: true }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.wecom_running, true)
  assert.equal(r.data.wecom_processes, 1)
  assert.equal(r.data.login_state, 'online')
  assert.equal(r.data.current_page, '通讯录')
  const mw = r.data.main_window as { hwnd: number }
  assert.equal(mw.hwnd, 69334)
  assert.ok(typeof r.data.os_release === 'string')
  assert.ok(typeof r.data.node_version === 'string')
})

test('probe：驱动失败（mock 抛 CodedOperationError）→ 错误码透传，不 reject', async () => {
  const { fn } = fakeExec({
    'where.exe': 'C:\\powershell.exe\r\n',
    tasklist: '"WXWork.exe","12345","Console","1","100,000 K"\r\n',
  })
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => probeEnvironment({ ...opts, sessionName: 'Console', execFileFn: fn }),
    runDriverFn: async () => {
      throw new CodedOperationError('WINDOW_AMBIGUOUS', '多个主窗口候选')
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WINDOW_AMBIGUOUS')
  assert.equal(r.effect, 'none')
})

test('probe：verbose 非布尔 → INVALID_ARGUMENT，不触达系统命令', async () => {
  const { fn, calls } = fakeExec({})
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => probeEnvironment({ ...opts, execFileFn: fn }),
  })
  // @ts-expect-error 故意传非法值模拟 Host 侧绕过 schema
  const r = await op.execute({ verbose: 'yes' }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.equal(calls.length, 0)
})

test('probe：args 非对象 → INVALID_ARGUMENT', async () => {
  const op = createWecomProbeOperation()
  // @ts-expect-error 故意传非法值
  const r = await op.execute('nope', silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
})
