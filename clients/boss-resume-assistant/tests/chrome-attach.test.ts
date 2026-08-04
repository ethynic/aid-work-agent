import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { WebSocketServer } from 'ws'
import {
  probeChromeDebugEndpoint,
  diagnoseAttachError,
  ChromeAttachError,
} from '../src/main/chrome/ChromeAttacher.js'
import {
  initCliRuntime,
  attachChrome,
  confirmLogin,
  shutdownCliRuntime,
  sessionStatus,
} from '../src/cli/cliRuntime.js'

/**
 * CLI attach 模式（替代已废弃的 spawn 临时 profile 路径——后者触发 BOSS 风控封号）。
 * 覆盖：/json/version 解析、三类连接故障诊断 fail-loud、attach 门禁语义、
 * 退出只断开连接绝不 kill 用户 Chrome（cliRuntime 全程不接触任何 Chrome 进程）。
 */

/** 构造一个返回指定 /json/version 响应的 mock fetch */
function mockFetchJson(body: unknown, status = 200): typeof fetch {
  return (async () =>
    ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }) as Response) as typeof fetch
}

test('probe 正确解析 /json/version 的 webSocketDebuggerUrl 与 Browser 版本', async () => {
  const info = await probeChromeDebugEndpoint('http://127.0.0.1:9222', {
    fetchImpl: mockFetchJson({
      webSocketDebuggerUrl: 'ws://127.0.0.1:9222/devtools/browser/abc',
      Browser: 'Chrome/131.0.0.0',
    }),
  })
  assert.equal(info.webSocketDebuggerUrl, 'ws://127.0.0.1:9222/devtools/browser/abc')
  assert.equal(info.browser, 'Chrome/131.0.0.0')
})

test('probe 连接拒绝（ECONNREFUSED 等网络错误）→ kind=refused fail-loud', async () => {
  const fetchImpl = (async () => {
    // Node fetch 网络层错误形态：TypeError，cause.code 为 ECONNREFUSED
    throw Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNREFUSED' } })
  }) as typeof fetch
  const err = await probeChromeDebugEndpoint('http://127.0.0.1:9222', { fetchImpl }).then(
    () => null,
    (e) => e,
  )
  assert.ok(err instanceof ChromeAttachError)
  assert.equal(err.kind, 'refused')
  const msg = diagnoseAttachError(err, 'http://127.0.0.1:9222')
  assert.match(msg, /无法连接/)
  assert.match(msg, /未启动|--remote-debugging-port/)
})

test('probe 超时（AbortError）→ kind=timeout fail-loud', async () => {
  const fetchImpl = ((_url: string, init?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      // 模拟 fetch 对 abort 信号的响应：abort 后以 AbortError 拒绝
      init?.signal?.addEventListener('abort', () =>
        reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
      )
    })) as typeof fetch
  const err = await probeChromeDebugEndpoint('http://127.0.0.1:9222', { fetchImpl, timeoutMs: 10 }).then(
    () => null,
    (e) => e,
  )
  assert.ok(err instanceof ChromeAttachError)
  assert.equal(err.kind, 'timeout')
  assert.match(diagnoseAttachError(err, 'http://127.0.0.1:9222'), /超时/)
})

test('probe 响应非法（非 JSON / 缺 webSocketDebuggerUrl / 非 2xx）→ kind=invalid fail-loud', async () => {
  // 缺 webSocketDebuggerUrl
  const err1 = await probeChromeDebugEndpoint('http://127.0.0.1:9222', {
    fetchImpl: mockFetchJson({ Browser: 'Chrome/131' }),
  }).then(() => null, (e) => e)
  assert.ok(err1 instanceof ChromeAttachError)
  assert.equal(err1.kind, 'invalid')

  // 非 2xx（端口被其他程序占用）
  const err2 = await probeChromeDebugEndpoint('http://127.0.0.1:9222', {
    fetchImpl: mockFetchJson({}, 404),
  }).then(() => null, (e) => e)
  assert.ok(err2 instanceof ChromeAttachError)
  assert.equal(err2.kind, 'invalid')
  assert.match(diagnoseAttachError(err2, 'http://127.0.0.1:9222'), /响应非法|占用/)

  // 响应不是 JSON
  const fetchImpl = (async () =>
    ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError('Unexpected token')
      },
    }) as unknown as Response) as typeof fetch
  const err3 = await probeChromeDebugEndpoint('http://127.0.0.1:9222', { fetchImpl }).then(
    () => null,
    (e) => e,
  )
  assert.ok(err3 instanceof ChromeAttachError)
  assert.equal(err3.kind, 'invalid')
})

test('attach 门禁：attachChrome 只探测不建连接，状态进 WAITING_MANUAL_LOGIN；退出不杀用户 Chrome', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cli-attach-'))
  try {
    initCliRuntime({ dataDir: dir })
    // 注入假探测：全程没有任何 Chrome 进程被 spawn（attach 模式根本不持有进程句柄）
    let probeCalls = 0
    const probeImpl = (async (endpoint: string) => {
      probeCalls++
      assert.equal(endpoint, 'http://127.0.0.1:9222')
      return { webSocketDebuggerUrl: 'ws://127.0.0.1:9222/devtools/browser/fake', browser: 'Chrome/131' }
    }) as typeof probeChromeDebugEndpoint

    const info = await attachChrome({ port: 9222, probeImpl })
    assert.equal(probeCalls, 1)
    assert.equal(info.httpEndpoint, 'http://127.0.0.1:9222')
    assert.equal(sessionStatus().state, 'WAITING_MANUAL_LOGIN')

    // 退出：只清扫 + 关 DB，没有任何可 kill 的 Chrome 进程句柄（成功返回即证明未触碰进程）
    await shutdownCliRuntime()
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('attach 探测失败 fail-loud：状态停留在 IDLE，不进入登录等待', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cli-attach-fail-'))
  try {
    initCliRuntime({ dataDir: dir })
    const probeImpl = (async () => {
      throw new ChromeAttachError('refused', '无法连接')
    }) as typeof probeChromeDebugEndpoint
    await assert.rejects(() => attachChrome({ port: 9222, probeImpl }), ChromeAttachError)
    assert.equal(sessionStatus().state, 'IDLE')
    await shutdownCliRuntime()
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

/** 1x1 PNG（connectCdp 视口实测用，合法 IHDR 即可） */
const PNG_1X1_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

/**
 * 回归：任务正常结束/退出时 shutdownCliRuntime 必须显式关闭 CDP WebSocket。
 * 打开的 ws 句柄会让 Node 事件循环无法排空——若不关，CLI 在「任务完成」后进程挂死不退
 * （q / Ctrl+C 路径有 process.exit 掩盖，正常完成路径没有）。
 */
test('shutdown 显式断开 CDP WebSocket（不关会导致进程挂死），且重复调用安全', async () => {
  // 假 CDP 端点：HTTP /json/version + ws 应答最小协议子集
  const wsServer = new WebSocketServer({ port: 0 })
  let serverSideClosed: (() => void) | null = null
  const closedPromise = new Promise<void>((resolve) => {
    serverSideClosed = resolve
  })
  wsServer.on('connection', (socket) => {
    socket.on('message', (data) => {
      const req = JSON.parse(String(data)) as { id: number; method: string; sessionId?: string }
      const results: Record<string, unknown> = {
        'Target.getTargets': {
          targetInfos: [{ targetId: 't1', type: 'page', url: 'https://www.zhipin.com/web/geek/recommend', title: 'BOSS' }],
        },
        'Target.attachToTarget': { sessionId: 's1' },
        'Page.enable': {},
        'Page.captureScreenshot': { data: PNG_1X1_BASE64 },
        'Target.detachFromTarget': {},
      }
      socket.send(JSON.stringify({ id: req.id, result: results[req.method] ?? {}, sessionId: req.sessionId }))
    })
    socket.on('close', () => serverSideClosed?.())
  })
  const wsPort = (wsServer.address() as { port: number }).port
  const httpServer = http.createServer((req, res) => {
    if (req.url === '/json/version') {
      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify({ webSocketDebuggerUrl: `ws://127.0.0.1:${wsPort}/devtools/browser/fake`, Browser: 'Chrome/131' }))
    } else {
      res.statusCode = 404
      res.end()
    }
  })
  await new Promise<void>((resolve) => httpServer.listen(0, '127.0.0.1', resolve))
  const httpPort = (httpServer.address() as { port: number }).port

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cli-shutdown-'))
  try {
    initCliRuntime({ dataDir: dir })
    await attachChrome({ port: httpPort }) // 真实 probe 打本地假端点
    const status = await confirmLogin()
    assert.equal(status.state, 'CONNECTING_CDP')
    assert.equal(status.connected, true)

    await shutdownCliRuntime()
    // 服务端必须观察到 ws 关闭（shutdown 前不关则此断言超时失败）
    await Promise.race([
      closedPromise,
      new Promise((_, reject) => setTimeout(() => reject(new Error('CDP WebSocket 未被 shutdown 关闭')), 3000)),
    ])
    // 重复 shutdown 不抛（quitNow / finally 双路径防御）
    await shutdownCliRuntime()
  } finally {
    await shutdownCliRuntime().catch(() => {})
    wsServer.close()
    httpServer.close()
    fs.rmSync(dir, { recursive: true, force: true })
  }
})
