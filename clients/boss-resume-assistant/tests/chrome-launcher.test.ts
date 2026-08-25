/**
 * ChromeLauncher 单测（全注入，不真启动 Chrome / 不真查注册表）。
 * 重点守护 fail-open 契约：任何失败形态都返回 false 且绝不抛——
 * 本模块不允许给 operation 引入新的失败形态。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  findChromeExe,
  ensureDebugChrome,
  DEFAULT_DEBUG_PROFILE_DIR,
  DEFAULT_INITIAL_URL,
} from '../src/main/chrome/ChromeLauncher.js'

function okFetch() {
  return Promise.resolve(new Response('{}', { status: 200 }))
}
function failFetch() {
  return Promise.reject(new Error('ECONNREFUSED'))
}

/** 走真实拉起路径的用例需要清掉全局开关（run-tests.mjs 给所有测试注入了
 *  AID_BOSS_AUTO_CHROME=0 防 MCP 测试拉真 Chrome，本文件的拉起路径用例已全量
 *  注入 deps 不需要该保护，不清会被开关拦截返回 false） */
async function withoutAutoChromeSwitch<T>(fn: () => Promise<T>): Promise<T> {
  const prev = process.env.AID_BOSS_AUTO_CHROME
  delete process.env.AID_BOSS_AUTO_CHROME
  try {
    return await fn()
  } finally {
    if (prev !== undefined) process.env.AID_BOSS_AUTO_CHROME = prev
  }
}

test('常量契约：固定 profile 目录与初始页', () => {
  assert.equal(DEFAULT_DEBUG_PROFILE_DIR, 'C:\\chrome-debug')
  assert.equal(DEFAULT_INITIAL_URL, 'https://www.zhipin.com')
})

test('findChromeExe：注册表 HKCU 命中直接返回', () => {
  const exe = findChromeExe({
    queryAppPaths: (hive) =>
      hive.startsWith('HKCU') ? 'C:\\Users\\x\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe' : undefined,
    existsSyncImpl: () => true,
  })
  assert.equal(exe, 'C:\\Users\\x\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe')
})

test('findChromeExe：注册表全空时常见路径兜底命中', () => {
  const exe = findChromeExe({
    queryAppPaths: () => undefined,
    existsSyncImpl: (p) => p === 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  })
  assert.equal(exe, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')
})

test('findChromeExe：注册表路径存在性校验，不存在则跳过', () => {
  const exe = findChromeExe({
    queryAppPaths: (hive) => (hive.startsWith('HKCU') ? 'C:\\ghost\\chrome.exe' : undefined),
    existsSyncImpl: (p) => p === 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  })
  // 注册表给的路径不存在 → 不用；落到候选路径第 2 个
  assert.equal(exe, 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe')
})

test('findChromeExe：全部无果返回 undefined（未装 Chrome）', () => {
  assert.equal(findChromeExe({ queryAppPaths: () => undefined, existsSyncImpl: () => false }), undefined)
})

test('ensureDebugChrome：端口已通则不拉起', async () => {
  let spawned = 0
  const launched = await withoutAutoChromeSwitch(() => ensureDebugChrome(
    9222,
    {},
    { fetchImpl: okFetch as typeof fetch, spawnImpl: () => { spawned++; return { unref() {} } }, sleep: () => Promise.resolve() },
  ))
  assert.equal(launched, false)
  assert.equal(spawned, 0)
})

test('ensureDebugChrome：端口不通→拉起→就绪返回 true，参数带固定 profile 与初始页', async () => {
  let probeCount = 0
  const fetchImpl = (() => {
    probeCount++
    return probeCount <= 1 ? Promise.reject(new Error('down')) : Promise.resolve(new Response('{}', { status: 200 }))
  }) as unknown as typeof fetch
  const calls: Array<{ exe: string; args: string[] }> = []
  const launched = await withoutAutoChromeSwitch(() => ensureDebugChrome(
    9223,
    {},
    {
      fetchImpl,
      queryAppPaths: () => 'C:\\Chrome\\chrome.exe',
      existsSyncImpl: () => true,
      spawnImpl: (exe, args) => { calls.push({ exe, args }); return { unref() {} } },
      sleep: () => Promise.resolve(),
    },
  ))
  assert.equal(launched, true)
  assert.equal(calls.length, 1)
  assert.equal(calls[0]!.exe, 'C:\\Chrome\\chrome.exe')
  assert.deepEqual(calls[0]!.args, ['--remote-debugging-port=9223', `--user-data-dir=${DEFAULT_DEBUG_PROFILE_DIR}`, DEFAULT_INITIAL_URL])
})

test('ensureDebugChrome：找不到 chrome.exe 返回 false 不抛', async () => {
  const launched = await withoutAutoChromeSwitch(() => ensureDebugChrome(
    9222,
    {},
    { fetchImpl: failFetch as typeof fetch, queryAppPaths: () => undefined, existsSyncImpl: () => false, sleep: () => Promise.resolve() },
  ))
  assert.equal(launched, false)
})

test('ensureDebugChrome：spawn 后端口一直不就绪（超时）返回 false 不抛', async () => {
  const launched = await withoutAutoChromeSwitch(() => ensureDebugChrome(
    9222,
    {},
    {
      fetchImpl: failFetch as typeof fetch,
      queryAppPaths: () => 'C:\\Chrome\\chrome.exe',
      existsSyncImpl: () => true,
      spawnImpl: () => ({ unref() {} }),
      sleep: () => Promise.resolve(),
    },
  ))
  assert.equal(launched, false)
})

test('ensureDebugChrome：spawn 抛异常也 fail-open 返回 false', async () => {
  const launched = await withoutAutoChromeSwitch(() => ensureDebugChrome(
    9222,
    {},
    {
      fetchImpl: failFetch as typeof fetch,
      queryAppPaths: () => 'C:\\Chrome\\chrome.exe',
      existsSyncImpl: () => true,
      spawnImpl: () => { throw new Error('spawn EPERM') },
      sleep: () => Promise.resolve(),
    },
  ))
  assert.equal(launched, false)
})

test('ensureDebugChrome：AID_BOSS_AUTO_CHROME=0 显式禁用时直接返回 false，不探测不拉起', async () => {
  const prev = process.env.AID_BOSS_AUTO_CHROME
  process.env.AID_BOSS_AUTO_CHROME = '0'
  let touched = 0
  try {
    const launched = await ensureDebugChrome(
      9222,
      {},
      {
        fetchImpl: (() => { touched++; return Promise.reject(new Error('should not probe')) }) as unknown as typeof fetch,
        spawnImpl: () => { touched++; return { unref() {} } },
        sleep: () => Promise.resolve(),
      },
    )
    assert.equal(launched, false)
    assert.equal(touched, 0)
  } finally {
    if (prev === undefined) delete process.env.AID_BOSS_AUTO_CHROME
    else process.env.AID_BOSS_AUTO_CHROME = prev
  }
})

test('ensureDebugChrome：已运行时绝不触碰 fetch 之外的任何依赖（保护现役实例）', async () => {
  let touched = 0
  const launched = await withoutAutoChromeSwitch(() => ensureDebugChrome(
    9222,
    {},
    {
      fetchImpl: okFetch as unknown as typeof fetch,
      queryAppPaths: () => { touched++; return undefined },
      existsSyncImpl: () => { touched++; return false },
      spawnImpl: () => { touched++; return { unref() {} } },
      sleep: () => { touched++; return Promise.resolve() },
    },
  ))
  assert.equal(launched, false)
  assert.equal(touched, 0)
})
