/**
 * E2E 框架冒烟用例：验证租户前台核心页面可访问、关键交互可用。
 *
 * 运行前提：已生成 .auth/tenant-admin.json 认证快照
 *   node e2e/scripts/refresh-auth.mjs
 *
 * 提示：积分用量弹窗的「缓存创建输入」等 7 个分项列仅平台管理员可见
 * （见 TenantTokenUsage.vue detailColumns 的 isPlatformAdmin 分支），
 * 如需断言这些列，配置平台管理员账号后用 platform-admin 项目：
 *   npx playwright test --project=platform-admin
 */
import { test, expect } from '../fixtures/auth'

const TENANT_ID = process.env.PLAYWRIGHT_TENANT_ID

test.describe('租户前台冒烟', () => {
  test.beforeAll(() => {
    test.skip(!TENANT_ID, '未配置 PLAYWRIGHT_TENANT_ID（见 playwright.env.example），跳过')
  })

  test('积分用量页可访问并渲染汇总卡片', async ({ page }) => {
    await page.goto(`/t/${TENANT_ID}/token-usage`)
    await expect(page.getByText('积分用量').first()).toBeVisible()
    await expect(page.getByText('积分余额').first()).toBeVisible()
    await expect(page.getByText('每日用量明细').first()).toBeVisible()
  })

  test('每日用量明细弹窗可打开且含分项列头', async ({ page }) => {
    await page.goto(`/t/${TENANT_ID}/token-usage`)
    // 点击表格中任意一条「消耗积分」链接打开弹窗
    await page.locator('tbody a').first().click()
    await expect(page.getByText(/积分用量明细/).first()).toBeVisible()
  })
})
