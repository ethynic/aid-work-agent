/**
 * 服务端代理凭据管理（serverProxy）契约测试：
 * - 未配置 AID_WEIXIN_SERVER_URL → disabled（本机降级或 CONFIG_MISSING 由驱动判定）
 * - 本地绑定命中 → 复用（不重复激活）
 * - 无绑定 + 有激活码 → 惰性激活，token 经 DPAPI 加密落盘（不明文）
 * - 激活失败 / 无激活码 → error 状态（驱动映射 CONFIG_MISSING）
 * DPAPI 与 fetch 均注入 fake，不触网、不调真实 PowerShell。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import {
  activateAndStore,
  loadBinding,
  resolveDriverProxyEnv,
  resolveProxyAuth,
  type ServerProxyDeps,
} from '../src/platform/serverProxy.js'

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), 'aid-weixin-proxy-test-'))
}

/** fake DPAPI：base64 变换模拟加解密往返（保证落盘内容不含明文，仅验证存取语义） */
const fakeDpapi = async (command: string, stdinText: string): Promise<string> =>
  command.includes('::Protect(')
    ? `dpapi.${Buffer.from(stdinText, 'utf8').toString('base64')}`
    : Buffer.from(stdinText.replace(/^dpapi\./, ''), 'base64').toString('utf8')

function makeDeps(stateDir: string, extra: Partial<ServerProxyDeps> = {}): ServerProxyDeps {
  return { stateDir, dpapiRunFn: fakeDpapi, hostnameFn: () => 'TEST-PC', ...extra }
}

test('未配置 AID_WEIXIN_SERVER_URL → disabled', async () => {
  const r = await resolveProxyAuth(makeDeps(makeTmpDir(), { env: {} as NodeJS.ProcessEnv }))
  assert.equal(r.status, 'disabled')
})

test('有 SERVER_URL 但无绑定无激活码 → error（提示设置激活码）', async () => {
  const r = await resolveProxyAuth(
    makeDeps(makeTmpDir(), { env: { AID_WEIXIN_SERVER_URL: 'https://agent.example.com/' } as NodeJS.ProcessEnv }),
  )
  assert.equal(r.status, 'error')
  if (r.status === 'error') {
    assert.equal(r.serverUrl, 'https://agent.example.com') // 尾部斜杠归一化
    assert.match(r.message, /AID_WEIXIN_ACTIVATION_CODE/)
  }
})

test('激活成功：token DPAPI 加密落盘，文件不含明文', async () => {
  const stateDir = makeTmpDir()
  try {
    let called = 0
    const fetchFn: ServerProxyDeps['fetchFn'] = async (url, init) => {
      called++
      assert.equal(url, 'https://agent.example.com/api/client/v1/activate')
      const body = JSON.parse(init.body) as Record<string, unknown>
      assert.equal(body.activation_code, 'AC-TESTCODE1234')
      assert.equal(body.machine_id, 'TEST-PC')
      return {
        status: 200,
        json: async () => ({ access_token: 'tok-secret-1', tenant_name: '测试租户' }),
      }
    }
    const auth = await activateAndStore(
      'https://agent.example.com',
      'ac-testcode1234', // 小写应被归一化为大写
      makeDeps(stateDir, { fetchFn }),
    )
    assert.equal(auth.accessToken, 'tok-secret-1')
    assert.equal(called, 1)

    const fileText = readFileSync(join(stateDir, 'binding.json'), 'utf8')
    assert.ok(!fileText.includes('tok-secret-1'), 'binding.json 不得含明文 token')
    assert.ok(fileText.includes('dpapi.'), 'binding.json 应存 DPAPI 密文 blob')

    // 读取回来能解出原 token
    const loaded = await loadBinding(makeDeps(stateDir))
    assert.equal(loaded?.accessToken, 'tok-secret-1')
    assert.equal(loaded?.tenantName, '测试租户')
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('激活失败（410 激活码已用）→ error，不落盘', async () => {
  const stateDir = makeTmpDir()
  const fetchFn: ServerProxyDeps['fetchFn'] = async () => ({
    status: 410,
    json: async () => ({ detail: 'ACTIVATION_CODE_USED' }),
  })
  const r = await resolveProxyAuth(
    makeDeps(stateDir, {
      env: {
        AID_WEIXIN_SERVER_URL: 'https://agent.example.com',
        AID_WEIXIN_ACTIVATION_CODE: 'AC-USED',
      } as NodeJS.ProcessEnv,
      fetchFn,
    }),
  )
  assert.equal(r.status, 'error')
  if (r.status === 'error') assert.match(r.message, /ACTIVATION_CODE_USED/)
  assert.equal(await loadBinding(makeDeps(stateDir)), null)
})

test('绑定命中 → 复用不重复激活；server_url 变更 → 重新激活', async () => {
  const stateDir = makeTmpDir()
  try {
    let called = 0
    const fetchFn: ServerProxyDeps['fetchFn'] = async () => {
      called++
      return { status: 200, json: async () => ({ access_token: `tok-${called}` }) }
    }
    const env = {
      AID_WEIXIN_SERVER_URL: 'https://a.example.com',
      AID_WEIXIN_ACTIVATION_CODE: 'AC-ONE',
    } as NodeJS.ProcessEnv
    const deps = makeDeps(stateDir, { env, fetchFn })

    const r1 = await resolveProxyAuth(deps)
    assert.equal(r1.status, 'ok')
    const r2 = await resolveProxyAuth(deps)
    assert.equal(r2.status, 'ok')
    assert.equal(called, 1, '绑定命中不得重复激活')

    // 换服务端地址 → 旧绑定失效，重新激活
    const r3 = await resolveProxyAuth(
      makeDeps(stateDir, { env: { ...env, AID_WEIXIN_SERVER_URL: 'https://b.example.com' }, fetchFn }),
    )
    assert.equal(r3.status, 'ok')
    assert.equal(called, 2)
    if (r3.status === 'ok') assert.equal(r3.auth.accessToken, 'tok-2')
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})

test('resolveDriverProxyEnv：ok → 注入 SERVER_URL+TOKEN；error → 注入 PROXY_ERROR；disabled → 空', async () => {
  const stateDir = makeTmpDir()
  try {
    const fetchFn: ServerProxyDeps['fetchFn'] = async () => ({
      status: 200,
      json: async () => ({ access_token: 'tok-env' }),
    })
    const okEnv = await resolveDriverProxyEnv(
      makeDeps(stateDir, {
        env: { AID_WEIXIN_SERVER_URL: 'https://a.example.com', AID_WEIXIN_ACTIVATION_CODE: 'AC-X' } as NodeJS.ProcessEnv,
        fetchFn,
      }),
    )
    assert.equal(okEnv.AID_WEIXIN_SERVER_URL, 'https://a.example.com')
    assert.equal(okEnv.AID_WEIXIN_ACCESS_TOKEN, 'tok-env')

    const errEnv = await resolveDriverProxyEnv(
      makeDeps(makeTmpDir(), { env: { AID_WEIXIN_SERVER_URL: 'https://a.example.com' } as NodeJS.ProcessEnv }),
    )
    assert.match(errEnv.AID_WEIXIN_PROXY_ERROR ?? '', /AID_WEIXIN_ACTIVATION_CODE/)
    assert.equal(errEnv.AID_WEIXIN_ACCESS_TOKEN, undefined)

    const disabledEnv = await resolveDriverProxyEnv(makeDeps(stateDir, { env: {} as NodeJS.ProcessEnv }))
    assert.deepEqual(disabledEnv, {})
  } finally {
    rmSync(stateDir, { recursive: true, force: true })
  }
})
