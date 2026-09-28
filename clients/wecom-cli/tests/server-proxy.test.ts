/**
 * 服务端代理（serverProxy，M10b 模型主通道）契约测试：
 * - 未配置 AID_WECOM_SERVER_URL → disabled
 * - 有 URL 无绑定无激活码 → config 错误；激活码被拒 → config；激活网络错误 → unavailable
 * - 激活成功：token DPAPI 加密落盘 server-binding.json（%LOCALAPPDATA%\AidWorkAgent\wecom-cli
 *   语义，测试注入 stateDir），文件不含明文，可读回；绑定命中复用；换 server_url 重激活
 * - parseSessionHistory：200 透传 / 401 清缓存重激活一次重试 / 重激活后仍 401 → config /
 *   402 → insufficient_credit / 502·网络错误·超时 → unavailable / 用户取消 → CancelledError /
 *   响应缺 messages → unavailable
 * DPAPI 与 fetch 均注入 fake，不触网、不调真实 PowerShell。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import {
  activateAndStore,
  clearBinding,
  loadBinding,
  parseSessionHistory,
  ProxyError,
  resolveProxyAuth,
  serverUrlConfigured,
  type FetchFn,
  type ServerProxyDeps,
} from '../src/platform/serverProxy.js'
import { CancelledError } from '../src/operations/types.js'

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), 'aid-wecom-proxy-test-'))
}

/** fake DPAPI：base64 变换模拟加解密往返（保证落盘内容不含明文，仅验证存取语义） */
const fakeDpapi = async (command: string, stdinText: string): Promise<string> =>
  command.includes('::Protect(')
    ? `dpapi.${Buffer.from(stdinText, 'utf8').toString('base64')}`
    : Buffer.from(stdinText.replace(/^dpapi\./, ''), 'base64').toString('utf8')

function makeDeps(stateDir: string, extra: Partial<ServerProxyDeps> = {}): ServerProxyDeps {
  return { stateDir, dpapiRunFn: fakeDpapi, hostnameFn: () => 'TEST-PC', ...extra }
}

const ENV_CONFIGURED = {
  AID_WECOM_SERVER_URL: 'https://agent.example.com',
  AID_WECOM_ACTIVATION_CODE: 'AC-TEST',
} as NodeJS.ProcessEnv

/** /session-history 200 响应样例（M10a 契约） */
const MODEL_RESPONSE = {
  messages: [
    { side: 'timeline', kind: 'timeline', text: '08:23' },
    { time: '08:23', side: 'peer', kind: 'text', text: '在吗' },
    { time: '08:23', side: 'self', kind: 'image', text: '[图片] 二维码' },
  ],
  pages: 2,
  latency_ms: { total: 18000, per_page: [9000, 8500] },
  model_usage: { prompt_tokens: 2100, completion_tokens: 180 },
  billing: { credits_charged: 1.2 },
}

test('serverUrlConfigured：未配置 → null；配置（含尾斜杠）→ 归一化 URL', () => {
  assert.equal(serverUrlConfigured({} as NodeJS.ProcessEnv), null)
  assert.equal(serverUrlConfigured({ AID_WECOM_SERVER_URL: '  ' } as NodeJS.ProcessEnv), null)
  assert.equal(
    serverUrlConfigured({ AID_WECOM_SERVER_URL: 'https://a.example.com/' } as NodeJS.ProcessEnv),
    'https://a.example.com',
  )
})

test('resolveProxyAuth：未配置 AID_WECOM_SERVER_URL → disabled', async () => {
  const r = await resolveProxyAuth(makeDeps(makeTmpDir(), { env: {} as NodeJS.ProcessEnv }))
  assert.equal(r.status, 'disabled')
})

test('resolveProxyAuth：有 URL 无绑定无激活码 → error（config，提示设置激活码）', async () => {
  const r = await resolveProxyAuth(
    makeDeps(makeTmpDir(), { env: { AID_WECOM_SERVER_URL: 'https://agent.example.com/' } as NodeJS.ProcessEnv }),
  )
  assert.equal(r.status, 'error')
  if (r.status === 'error') {
    assert.equal(r.kind, 'config')
    assert.equal(r.serverUrl, 'https://agent.example.com') // 尾部斜杠归一化
    assert.match(r.message, /AID_WECOM_ACTIVATION_CODE/)
  }
})

test('激活成功：token DPAPI 加密落盘 server-binding.json，文件不含明文，可读回', async () => {
  const stateDir = makeTmpDir()
  try {
    let called = 0
    const fetchFn: FetchFn = async (url, init) => {
      called++
      assert.equal(url, 'https://agent.example.com/api/client/v1/activate')
      const body = JSON.parse(init.body) as Record<string, unknown>
      assert.equal(body.activation_code, 'AC-TESTCODE1234') // 小写归一化为大写
      assert.equal(body.machine_id, 'TEST-PC')
      assert.equal(body.client_name, 'wecom-cli@TEST-PC')
      return {
        status: 200,
        json: async () => ({ access_token: 'tok-secret-1', tenant_name: '测试租户' }),
      }
    }
    const auth = await activateAndStore(
      'https://agent.example.com',
      'ac-testcode1234',
      makeDeps(stateDir, { fetchFn }),
    )
    assert.equal(auth.accessToken, 'tok-secret-1')
    assert.equal(called, 1)

    const fileText = readFileSync(join(stateDir, 'server-binding.json'), 'utf8')
    assert.ok(!fileText.includes('tok-secret-1'), 'server-binding.json 不得含明文 token')
    assert.ok(fileText.includes('dpapi.'), 'server-binding.json 应存 DPAPI 密文 blob')

    const loaded = await loadBinding(makeDeps(stateDir))
    assert.equal(loaded?.accessToken, 'tok-secret-1')
    assert.equal(loaded?.tenantName, '测试租户')
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('激活码被拒（410 已用）→ config 错误，不落盘；激活网络错误 → unavailable', async () => {
  // 410：服务端有响应但拒绝 → 用户配置问题（不降级）
  const stateDir = makeTmpDir()
  const rejected = await resolveProxyAuth(
    makeDeps(stateDir, {
      env: { ...ENV_CONFIGURED, AID_WECOM_ACTIVATION_CODE: 'AC-USED' },
      fetchFn: async () => ({ status: 410, json: async () => ({ detail: 'ACTIVATION_CODE_USED' }) }),
    }),
  )
  assert.equal(rejected.status, 'error')
  if (rejected.status === 'error') {
    assert.equal(rejected.kind, 'config')
    assert.match(rejected.message, /ACTIVATION_CODE_USED/)
  }
  assert.equal(await loadBinding(makeDeps(stateDir)), null)

  // 网络错误：服务端不可达 → 模型通道整体不可用（可降级）
  const netErr = await resolveProxyAuth(
    makeDeps(makeTmpDir(), {
      env: ENV_CONFIGURED,
      fetchFn: async () => {
        throw new TypeError('fetch failed')
      },
    }),
  )
  assert.equal(netErr.status, 'error')
  if (netErr.status === 'error') assert.equal(netErr.kind, 'unavailable')
})

test('绑定命中 → 复用不重复激活；server_url 变更 → 重新激活', async () => {
  const stateDir = makeTmpDir()
  try {
    let called = 0
    const fetchFn: FetchFn = async () => {
      called++
      return { status: 200, json: async () => ({ access_token: `tok-${called}` }) }
    }
    const env = { ...ENV_CONFIGURED, AID_WECOM_ACTIVATION_CODE: 'AC-ONE' } as NodeJS.ProcessEnv
    const deps = makeDeps(stateDir, { env, fetchFn })

    const r1 = await resolveProxyAuth(deps)
    assert.equal(r1.status, 'ok')
    const r2 = await resolveProxyAuth(deps)
    assert.equal(r2.status, 'ok')
    assert.equal(called, 1, '绑定命中不得重复激活')

    // 换服务端地址 → 旧绑定失效，重新激活
    const r3 = await resolveProxyAuth(
      makeDeps(stateDir, { env: { ...env, AID_WECOM_SERVER_URL: 'https://b.example.com' }, fetchFn }),
    )
    assert.equal(r3.status, 'ok')
    assert.equal(called, 2)
    if (r3.status === 'ok') assert.equal(r3.auth.accessToken, 'tok-2')
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('clearBinding：删除绑定文件（幂等，不存在不抛）', async () => {
  const stateDir = makeTmpDir()
  try {
    const deps = makeDeps(stateDir)
    clearBinding(deps) // 不存在 → 不抛
    let called = 0
    const fetchFn: FetchFn = async () => {
      called++
      return { status: 200, json: async () => ({ access_token: 'tok-x' }) }
    }
    const d2 = makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn })
    await resolveProxyAuth(d2) // 建立绑定
    assert.equal((await loadBinding(d2))?.accessToken, 'tok-x')
    clearBinding(d2)
    assert.equal(await loadBinding(d2), null)
    assert.equal(called, 1)
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

/** session-history fake：激活按 activationCalls 计发 token，会话接口按规则应答 */
function makeSessionFetch(opts: {
  activateTokens?: string[]
  onSession: (call: number, init: { headers: Record<string, string>; body: string }) => { status: number; json(): Promise<Record<string, unknown>> }
}): { fetchFn: FetchFn; activateCalls: () => number; sessionCalls: () => number } {
  let activateCalls = 0
  let sessionCalls = 0
  const fetchFn: FetchFn = async (url, init) => {
    if (url.endsWith('/api/client/v1/activate')) {
      activateCalls++
      const token = opts.activateTokens?.[activateCalls - 1] ?? `tok-${activateCalls}`
      return { status: 200, json: async () => ({ access_token: token }) }
    }
    if (url.endsWith('/api/client/v1/session-history')) {
      sessionCalls++
      return opts.onSession(sessionCalls, init as { headers: Record<string, string>; body: string })
    }
    throw new Error(`unexpected url ${url}`)
  }
  return { fetchFn, activateCalls: () => activateCalls, sessionCalls: () => sessionCalls }
}

test('parseSessionHistory：200 → messages/pages/billing 透传，Bearer+client=wecom+session_title 请求形态正确', async () => {
  const stateDir = makeTmpDir()
  try {
    const seen: { body?: Record<string, unknown>; auth?: string } = {}
    const { fetchFn } = makeSessionFetch({
      onSession: (_call, init) => {
        seen.body = JSON.parse(init.body) as Record<string, unknown>
        seen.auth = init.headers.Authorization ?? ''
        return { status: 200, json: async () => MODEL_RESPONSE }
      },
    })
    const resp = await parseSessionHistory(
      { images: ['aGk=', 'eXo='], sessionTitle: '陆伟 @微信' },
      makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn }),
    )
    assert.equal(seen.auth, 'Bearer tok-1')
    assert.equal(seen.body!.client, 'wecom')
    assert.equal(seen.body!.session_title, '陆伟 @微信')
    assert.deepEqual(seen.body!.images, ['aGk=', 'eXo=']) // 原样数组（时间序旧→新由调用方保证）
    assert.equal(resp.messages.length, 3)
    assert.equal(resp.messages[1]!.time, '08:23')
    assert.equal(resp.messages[2]!.kind, 'image')
    assert.equal(resp.pages, 2)
    assert.deepEqual(resp.billing, { credits_charged: 1.2 })
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('parseSessionHistory：401 → 清缓存重激活一次重试成功（新 token 生效）', async () => {
  const stateDir = makeTmpDir()
  try {
    const { fetchFn, activateCalls, sessionCalls } = makeSessionFetch({
      onSession: (call, init) => {
        if (call === 1) return { status: 401, json: async () => ({ detail: 'UNAUTHORIZED' }) }
        return init.headers.Authorization === 'Bearer tok-2'
          ? { status: 200, json: async () => MODEL_RESPONSE }
          : { status: 401, json: async () => ({ detail: 'UNAUTHORIZED' }) }
      },
    })
    const resp = await parseSessionHistory(
      { images: ['aGk='] },
      makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn }),
    )
    assert.equal(resp.pages, 2)
    assert.equal(activateCalls(), 2, '401 后应清缓存重新激活一次')
    assert.equal(sessionCalls(), 2, '激活后应重试一次 session-history')
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('parseSessionHistory：重激活后仍 401 → config（不降级）；无激活码重激活 → config', async () => {
  // 场景 1：重激活拿到新 token 仍被拒（服务端配置问题）
  const stateDir = makeTmpDir()
  try {
    const { fetchFn } = makeSessionFetch({
      onSession: () => ({ status: 401, json: async () => ({ detail: 'UNAUTHORIZED' }) }),
    })
    await assert.rejects(
      parseSessionHistory({ images: ['aGk='] }, makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn })),
      (err: unknown) => {
        assert.ok(err instanceof ProxyError)
        assert.equal(err.kind, 'config')
        assert.match(err.message, /401/)
        return true
      },
    )
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }

  // 场景 2：缓存 token 401 后无激活码可重激活（一次性码已消耗完的常见形态）
  const stateDir2 = makeTmpDir()
  try {
    const depsWithCode = makeDeps(stateDir2, { env: ENV_CONFIGURED })
    const { fetchFn } = makeSessionFetch({ onSession: () => ({ status: 200, json: async () => MODEL_RESPONSE }) })
    // 先建立绑定（tok-1）
    await parseSessionHistory({ images: ['aGk='] }, { ...depsWithCode, fetchFn })
    // 之后 token 失效且环境不再有激活码
    const { fetchFn: rejectAll } = makeSessionFetch({
      onSession: () => ({ status: 401, json: async () => ({ detail: 'UNAUTHORIZED' }) }),
    })
    await assert.rejects(
      parseSessionHistory(
        { images: ['aGk='] },
        makeDeps(stateDir2, { env: { AID_WECOM_SERVER_URL: ENV_CONFIGURED.AID_WECOM_SERVER_URL }, fetchFn: rejectAll }),
      ),
      (err: unknown) => {
        assert.ok(err instanceof ProxyError)
        assert.equal(err.kind, 'config')
        assert.match(err.message, /重新激活失败/)
        return true
      },
    )
  } finally {
    rmSync(stateDir2, { recursive: true, force: true })
  }
})

test('parseSessionHistory：402 → insufficient_credit（不降级标志）；502/网络错误 → unavailable', async () => {
  for (const [label, onSession, wantKind] of [
    ['402 余额不足', () => ({ status: 402, json: async () => ({ detail: 'NO_CREDIT' }) }), 'insufficient_credit'],
    ['502 模型全败', () => ({ status: 502, json: async () => ({ detail: 'MODEL_UNAVAILABLE: 全部页面解析失败' }) }), 'unavailable'],
    ['500 服务端错误', () => ({ status: 500, json: async () => ({}) }), 'unavailable'],
    ['422 图片超限', () => ({ status: 422, json: async () => ({ detail: 'IMAGE_TOO_LARGE' }) }), 'unavailable'],
  ] as const) {
    const stateDir = makeTmpDir()
    try {
      const { fetchFn } = makeSessionFetch({ onSession: () => onSession() })
      await assert.rejects(
        parseSessionHistory({ images: ['aGk='] }, makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn })),
        (err: unknown) => {
          assert.ok(err instanceof ProxyError, label)
          assert.equal(err.kind, wantKind, label)
          return true
        },
      )
    } finally {
      rmSync(stateDir, { recursive: true, force: true })
    }
  }

  // 网络错误（session-history 请求本身不可达）
  const stateDir = makeTmpDir()
  try {
    await assert.rejects(
      parseSessionHistory(
        { images: ['aGk='] },
        makeDeps(stateDir, {
          env: ENV_CONFIGURED,
          fetchFn: async () => {
            throw new TypeError('fetch failed')
          },
        }),
      ),
      (err: unknown) => {
        assert.ok(err instanceof ProxyError)
        assert.equal(err.kind, 'unavailable')
        return true
      },
    )
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

/** 激活立即成功、会话解析挂起仅响应 abort 的 fake（超时/取消用例共用） */
const ACTIVATE_OK_SESSION_HANG: FetchFn = (url, init) => {
  if (url.endsWith('/api/client/v1/activate')) {
    return Promise.resolve({ status: 200, json: async () => ({ access_token: 'tok-1' }) })
  }
  return new Promise((_resolve, reject) => {
    if (init.signal?.aborted) {
      reject(new DOMException('aborted', 'AbortError'))
      return
    }
    init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })
}

test('parseSessionHistory：超时 → unavailable（注入短 timeoutMs）', async () => {
  const stateDir = makeTmpDir()
  try {
    await assert.rejects(
      parseSessionHistory(
        { images: ['aGk='] },
        makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn: ACTIVATE_OK_SESSION_HANG, timeoutMs: 60 }),
      ),
      (err: unknown) => {
        assert.ok(err instanceof ProxyError)
        assert.equal(err.kind, 'unavailable')
        assert.match(err.message, /超时/)
        return true
      },
    )
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('parseSessionHistory：用户取消 → CancelledError（绝不归并为可降级 unavailable）', async () => {
  const stateDir = makeTmpDir()
  try {
    const controller = new AbortController()
    // 挂起中取消：模型调用等待期被用户中止
    const pending = parseSessionHistory(
      { images: ['aGk='], signal: controller.signal },
      makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn: ACTIVATE_OK_SESSION_HANG, timeoutMs: 10_000 }),
    )
    setTimeout(() => controller.abort(), 30)
    await assert.rejects(pending, (err: unknown) => {
      assert.ok(err instanceof CancelledError)
      return true
    })

    // 已取消的 signal 直接传入 → 立即 CancelledError
    const aborted = new AbortController()
    aborted.abort()
    await assert.rejects(
      parseSessionHistory(
        { images: ['aGk='], signal: aborted.signal },
        makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn: ACTIVATE_OK_SESSION_HANG }),
      ),
      (err: unknown) => {
        assert.ok(err instanceof CancelledError)
        return true
      },
    )
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('parseSessionHistory：200 但响应缺 messages 数组 → unavailable（契约异常）', async () => {
  const stateDir = makeTmpDir()
  try {
    const { fetchFn } = makeSessionFetch({ onSession: () => ({ status: 200, json: async () => ({ pages: 1 }) }) })
    await assert.rejects(
      parseSessionHistory({ images: ['aGk='] }, makeDeps(stateDir, { env: ENV_CONFIGURED, fetchFn })),
      (err: unknown) => {
        assert.ok(err instanceof ProxyError)
        assert.equal(err.kind, 'unavailable')
        assert.match(err.message, /messages/)
        return true
      },
    )
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})
