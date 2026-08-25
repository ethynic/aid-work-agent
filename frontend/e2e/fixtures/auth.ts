/**
 * Playwright 认证 fixtures。
 *
 * 两种认证方式（与 playwright.config.ts 的 projects 对应）：
 *   1. storageState（默认）：tenant-admin / platform-admin 项目复用 .auth/*.json 快照，
 *      由 e2e/scripts/refresh-auth.mjs 生成（API 登录，无需走 UI）。
 *   2. loginViaUI（本文件）：ui-login 项目走真实登录页。图形验证码明文只存数据库
 *      captchas 表，测试从数据库读取最近一条验证码填入表单。
 *
 * 用法：
 *   import { test, expect } from '../fixtures/auth'
 *   test('登录后可进入租户前台', async ({ page }) => {
 *     await loginViaUI({ tenantId: TENANT_ID, identifier: IDENTIFIER, password: PASSWORD })
 *     await expect(page).toHaveURL(new RegExp(`/t/${TENANT_ID}`))
 *   })
 */
import { test as base, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { Client } from 'pg'

/** 解析数据库连接串：PLAYWRIGHT_DATABASE_URL > 根 .env 的 DATABASE_URL */
function loadDatabaseUrl(): string {
  const fromEnv = process.env.PLAYWRIGHT_DATABASE_URL
  if (fromEnv) return fromEnv
  try {
    const content = readFileSync(new URL('../../../.env', import.meta.url), 'utf-8')
    for (const line of content.split('\n')) {
      const m = line.match(/^\s*DATABASE_URL\s*=\s*(.*)\s*$/)
      if (m) return m[1].replace(/^['"]|['"]$/g, '')
    }
  } catch {
    // 无根 .env，交给后续报错
  }
  throw new Error('未配置 DATABASE_URL（PLAYWRIGHT_DATABASE_URL 或根 .env），无法读取验证码')
}

/** 读取数据库 captchas 表最近一条未过期验证码明文 */
async function readLatestCaptchaCode(dbUrl: string): Promise<string> {
  const client = new Client({ connectionString: dbUrl })
  await client.connect()
  try {
    const { rows } = await client.query(
      'SELECT code FROM captchas WHERE expires_at > NOW() ORDER BY created_at DESC LIMIT 1',
    )
    if (!rows.length) throw new Error('数据库无有效验证码，请先打开登录页触发验证码生成')
    return rows[0].code
  } finally {
    await client.end()
  }
}

/** 登录表单输入框（对应 UniversalLogin.vue 的 v-model 字段） */
const loginSelectors = {
  identifier: 'input[placeholder="请输入手机号或用户名"]',
  password: 'input[placeholder="请输入密码"]',
  captchaCode: 'input[placeholder="请输入图形验证码"]',
  captchaImg: 'img[alt="验证码"]',
  submit: 'button:has-text("登录")',
}

export const test = base.extend<{
  loginViaUI: (opts: {
    tenantId: string
    identifier: string
    password: string
    requiredRole?: string
  }) => Promise<void>
}>({
  loginViaUI: async ({ page }, use) => {
    await use(async ({ tenantId, identifier, password }) => {
      await page.goto(`/t/${tenantId}/login`)
      // 等待页面发起 captcha 请求并渲染验证码图片后，再从数据库取最近一条明文
      await page.waitForSelector(loginSelectors.captchaImg, { timeout: 15_000 })
      const dbUrl = loadDatabaseUrl()
      const code = await readLatestCaptchaCode(dbUrl)

      await page.fill(loginSelectors.identifier, identifier)
      await page.fill(loginSelectors.password, password)
      await page.fill(loginSelectors.captchaCode, code)
      await page.click(loginSelectors.submit)
      // 登录成功后跳到租户前台
      await page.waitForURL(new RegExp(`/t/${tenantId}(/|$)`), { timeout: 20_000 })
    })
  },
})

export { expect }
