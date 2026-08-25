/**
 * UI 登录流程用例（project=ui-login，不走 storageState）。
 *
 * 依赖测试账号环境变量：PLAYWRIGHT_TENANT_ID / PLAYWRIGHT_IDENTIFIER / PLAYWRIGHT_PASSWORD
 * 图形验证码从数据库 captchas 表读取（登录页触发生成后测试取最近一条）。
 */
import { test, expect } from '../fixtures/auth'

const TENANT_ID = process.env.PLAYWRIGHT_TENANT_ID
const IDENTIFIER = process.env.PLAYWRIGHT_IDENTIFIER
const PASSWORD = process.env.PLAYWRIGHT_PASSWORD

test.describe('UI 登录流程', () => {
  test('登录页可访问', async ({ page }) => {
    test.skip(!TENANT_ID, '未配置 PLAYWRIGHT_TENANT_ID，跳过')
    await page.goto(`/t/${TENANT_ID}/login`)
    await expect(page.getByPlaceholder('请输入手机号或用户名')).toBeVisible()
    await expect(page.getByPlaceholder('请输入图形验证码')).toBeVisible()
  })

  test('通过 UI 登录后进入租户前台', async ({ page, loginViaUI }) => {
    test.skip(!TENANT_ID || !IDENTIFIER || !PASSWORD, '未配置测试账号环境变量，跳过')
    await loginViaUI({ tenantId: TENANT_ID, identifier: IDENTIFIER, password: PASSWORD })
    await expect(page).toHaveURL(new RegExp(`/t/${TENANT_ID}(/|$)`))
  })
})
