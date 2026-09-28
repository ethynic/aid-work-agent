/**
 * 服务端代理（serverProxy，M10c 直连长期 token 模式）契约测试：
 * - serverUrlConfigured：未配置/空白 → null；含尾斜杠 → 归一化 URL
 * - resolveProxyAuth：未配置 URL → disabled；有 URL 缺 TOKEN → config 错误
 *   （提示两个环境变量名）；URL+TOKEN 齐备 → ok（token 直读环境变量，无本地状态）
 * - parseSessionHistory：200 透传（Bearer env token + client=wecom + session_title
 *   请求形态）/ 401 → config（token 无效，不重试不降级——直报让用户修配置）/
 *   402 → insufficient_credit / 502·500·422·网络错误·超时 → unavailable /
 *   用户取消 → CancelledError / 响应缺 messages → unavailable
 * M10c 起无激活码、无 DPAPI、无 server-binding.json 本地缓存；fetch 注入 fake，
 * 不触网。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  parseSessionHistory,
  ProxyError,
  resolveProxyAuth,
  serverUrlConfigured,
  type FetchFn,
  type ServerProxyDeps,
} from '../src/platform/serverProxy.js'
import { CancelledError } from '../src/operations/types.js'

const ENV_CONFIGURED = {
  AID_WECOM_SERVER_URL: 'https://agent.example.com',
  AID_WECOM_SERVER_TOKEN: 'tok-static-1',
} as NodeJS.ProcessEnv

function makeDeps(extra: Partial<ServerProxyDeps> = {}): ServerProxyDeps {
  return { ...extra }
}

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

test('resolveProxyAuth：未配置 AID_WECOM_SERVER_URL → disabled', () => {
  const r = resolveProxyAuth(makeDeps({ env: {} as NodeJS.ProcessEnv }))
  assert.equal(r.status, 'disabled')
})

test('resolveProxyAuth：有 URL 缺 TOKEN → error（config，提示两个环境变量名）', () => {
  const r = resolveProxyAuth(
    makeDeps({ env: { AID_WECOM_SERVER_URL: 'https://agent.example.com/' } as NodeJS.ProcessEnv }),
  )
  assert.equal(r.status, 'error')
  if (r.status === 'error') {
    assert.equal(r.kind, 'config')
    assert.equal(r.serverUrl, 'https://agent.example.com') // 尾部斜杠归一化
    assert.match(r.message, /AID_WECOM_SERVER_TOKEN/)
    assert.match(r.message, /AID_WECOM_SERVER_URL/)
  }
})

test('resolveProxyAuth：URL+TOKEN 齐备 → ok（token 直读环境变量，无本地状态/网络请求）', () => {
  const r = resolveProxyAuth(
    makeDeps({
      env: {
        AID_WECOM_SERVER_URL: 'https://agent.example.com/',
        AID_WECOM_SERVER_TOKEN: '  tok-static-1  ',
      } as NodeJS.ProcessEnv,
    }),
  )
  assert.equal(r.status, 'ok')
  if (r.status === 'ok') {
    assert.equal(r.auth.serverUrl, 'https://agent.example.com')
    assert.equal(r.auth.accessToken, 'tok-static-1') // 两端空白剥除
  }
})

test('parseSessionHistory：缺 TOKEN → 直接 config 错误（不发起网络请求）', async () => {
  let called = 0
  const fetchFn: FetchFn = async () => {
    called++
    return { status: 200, json: async () => MODEL_RESPONSE }
  }
  await assert.rejects(
    parseSessionHistory(
      { images: ['aGk='] },
      makeDeps({ env: { AID_WECOM_SERVER_URL: 'https://agent.example.com' } as NodeJS.ProcessEnv, fetchFn }),
    ),
    (err: unknown) => {
      assert.ok(err instanceof ProxyError)
      assert.equal(err.kind, 'config')
      assert.match(err.message, /AID_WECOM_SERVER_TOKEN/)
      return true
    },
  )
  assert.equal(called, 0, '配置缺失不得发起网络请求')
})

/** session-history fake：按规则应答并计数 */
function makeSessionFetch(
  onSession: (call: number, init: { headers: Record<string, string>; body: string }) => {
    status: number
    json(): Promise<Record<string, unknown>>
  },
): { fetchFn: FetchFn; sessionCalls: () => number } {
  let sessionCalls = 0
  const fetchFn: FetchFn = async (url, init) => {
    if (url.endsWith('/api/client/v1/session-history')) {
      sessionCalls++
      return onSession(sessionCalls, init as { headers: Record<string, string>; body: string })
    }
    throw new Error(`unexpected url ${url}`) // 直连模式只应调 session-history（无 /activate）
  }
  return { fetchFn, sessionCalls: () => sessionCalls }
}

test('parseSessionHistory：200 → messages/pages/billing 透传，Bearer env token + client=wecom + session_title 请求形态正确', async () => {
  const seen: { body?: Record<string, unknown>; auth?: string } = {}
  const { fetchFn } = makeSessionFetch((_call, init) => {
    seen.body = JSON.parse(init.body) as Record<string, unknown>
    seen.auth = init.headers.Authorization ?? ''
    return { status: 200, json: async () => MODEL_RESPONSE }
  })
  const resp = await parseSessionHistory(
    { images: ['aGk=', 'eXo='], sessionTitle: '陆伟 @微信' },
    makeDeps({ env: ENV_CONFIGURED, fetchFn }),
  )
  assert.equal(seen.auth, 'Bearer tok-static-1')
  assert.equal(seen.body!.client, 'wecom')
  assert.equal(seen.body!.session_title, '陆伟 @微信')
  assert.deepEqual(seen.body!.images, ['aGk=', 'eXo=']) // 原样数组（时间序旧→新由调用方保证）
  assert.equal(resp.messages.length, 3)
  assert.equal(resp.messages[1]!.time, '08:23')
  assert.equal(resp.messages[2]!.kind, 'image')
  assert.equal(resp.pages, 2)
  assert.deepEqual(resp.billing, { credits_charged: 1.2 })
})

test('parseSessionHistory：401 → config（token 无效不重试不降级，只调一次服务端）', async () => {
  const { fetchFn, sessionCalls } = makeSessionFetch(() => ({
    status: 401,
    json: async () => ({ detail: 'UNAUTHORIZED' }),
  }))
  await assert.rejects(
    parseSessionHistory({ images: ['aGk='] }, makeDeps({ env: ENV_CONFIGURED, fetchFn })),
    (err: unknown) => {
      assert.ok(err instanceof ProxyError)
      assert.equal(err.kind, 'config')
      assert.equal(err.status, 401)
      assert.match(err.message, /AID_WECOM_SERVER_TOKEN/)
      return true
    },
  )
  assert.equal(sessionCalls(), 1, '直连模式 401 不重试（token 是手工配置的，重试无意义）')
})

test('parseSessionHistory：402 → insufficient_credit；502/500/422 → unavailable', async () => {
  for (const [label, onSession, wantKind] of [
    ['402 余额不足', () => ({ status: 402, json: async () => ({ detail: 'NO_CREDIT' }) }), 'insufficient_credit'],
    ['502 模型全败', () => ({ status: 502, json: async () => ({ detail: 'MODEL_UNAVAILABLE: 全部页面解析失败' }) }), 'unavailable'],
    ['500 服务端错误', () => ({ status: 500, json: async () => ({}) }), 'unavailable'],
    ['422 图片超限', () => ({ status: 422, json: async () => ({ detail: 'IMAGE_TOO_LARGE' }) }), 'unavailable'],
  ] as const) {
    const { fetchFn } = makeSessionFetch(() => onSession())
    await assert.rejects(
      parseSessionHistory({ images: ['aGk='] }, makeDeps({ env: ENV_CONFIGURED, fetchFn })),
      (err: unknown) => {
        assert.ok(err instanceof ProxyError, label)
        assert.equal(err.kind, wantKind, label)
        return true
      },
    )
  }
})

test('parseSessionHistory：网络错误 → unavailable', async () => {
  await assert.rejects(
    parseSessionHistory(
      { images: ['aGk='] },
      makeDeps({
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
})

/** 会话解析挂起仅响应 abort 的 fake（超时/取消用例共用） */
const SESSION_HANG: FetchFn = (_url, init) =>
  new Promise((_resolve, reject) => {
    if (init.signal?.aborted) {
      reject(new DOMException('aborted', 'AbortError'))
      return
    }
    init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })

test('parseSessionHistory：超时 → unavailable（注入短 timeoutMs）', async () => {
  await assert.rejects(
    parseSessionHistory(
      { images: ['aGk='] },
      makeDeps({ env: ENV_CONFIGURED, fetchFn: SESSION_HANG, timeoutMs: 60 }),
    ),
    (err: unknown) => {
      assert.ok(err instanceof ProxyError)
      assert.equal(err.kind, 'unavailable')
      assert.match(err.message, /超时/)
      return true
    },
  )
})

test('parseSessionHistory：用户取消 → CancelledError（绝不归并为可降级 unavailable）', async () => {
  const controller = new AbortController()
  // 挂起中取消：模型调用等待期被用户中止
  const pending = parseSessionHistory(
    { images: ['aGk='], signal: controller.signal },
    makeDeps({ env: ENV_CONFIGURED, fetchFn: SESSION_HANG, timeoutMs: 10_000 }),
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
      makeDeps({ env: ENV_CONFIGURED, fetchFn: SESSION_HANG }),
    ),
    (err: unknown) => {
      assert.ok(err instanceof CancelledError)
      return true
    },
  )
})

test('parseSessionHistory：200 但响应缺 messages 数组 → unavailable（契约异常）', async () => {
  const { fetchFn } = makeSessionFetch(() => ({ status: 200, json: async () => ({ pages: 1 }) }))
  await assert.rejects(
    parseSessionHistory({ images: ['aGk='] }, makeDeps({ env: ENV_CONFIGURED, fetchFn })),
    (err: unknown) => {
      assert.ok(err instanceof ProxyError)
      assert.equal(err.kind, 'unavailable')
      assert.match(err.message, /messages/)
      return true
    },
  )
})
