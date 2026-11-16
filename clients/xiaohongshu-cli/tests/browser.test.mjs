import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { chromium } from 'playwright-core'
import { chromeExecutable, HOME, XiaohongshuBrowser } from '../dist/src/browser.js'

const executablePath = chromeExecutable()
const signedIn = '<li class="user side-bar-component"><a href="/user/profile/fixture">个人页</a></li>'
const signedOut = `<div class="side-bar-component login-btn"><button onclick="document.querySelector('.login-modal').hidden=false">登录</button></div>
  <div class="login-modal" hidden><img width="100" height="100" class="qrcode-img" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100'/%3E%3C/svg%3E"></div>`

test('同一 Profile 关闭后复用网页会话，另一 Profile 仍需要登录', { skip: !executablePath }, async () => {
  const output = fileURLToPath(new URL('../dist', import.meta.url))
  await mkdir(output, { recursive: true })
  const root = await mkdtemp(join(output, 'fixture-profiles-'))
  const currentContexts = new Map()
  const launch = async key => {
    const context = await chromium.launchPersistentContext(join(root, key), { executablePath, headless: true })
    currentContexts.set(key, context)
    await context.route(HOME, async route => {
      const headers = await route.request().allHeaders()
      const body = headers.cookie?.includes('fixture_login=scan-confirmed') ? signedIn : signedOut
      await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body })
    })
    return context
  }
  const first = new XiaohongshuBrowser(launch)
  const second = new XiaohongshuBrowser(launch)
  const key = 'a'.repeat(64)
  try {
    assert.equal(await first.run('login', key), 'qr_ready')
    await assert.rejects(first.run('open', key), { code: 'XHS_LOGIN_REQUIRED' })
    // 模拟扫码服务端设置持久 Cookie，由 Chrome 在关闭和重开之间保管。
    await currentContexts.get(key).addCookies([{ name: 'fixture_login', value: 'scan-confirmed',
      url: HOME, expires: Math.floor(Date.now() / 1000) + 600 }])
    await first.close()
    assert.equal(await second.run('open', key), 'opened')
    await assert.rejects(second.run('open', 'b'.repeat(64)), { code: 'XHS_LOGIN_REQUIRED' })
  } finally {
    await first.close()
    await second.close()
    if (dirname(root) === output) await rm(root, { recursive: true, force: true })
  }
})
