/**
 * Playwright E2E 测试配置。
 *
 * 依赖：
 *   1. 本地后端容器 aid-agent-api 在 8000 端口运行（vite dev 的 /api 代理指向它）
 *   2. 测试账号环境变量（见 playwright.env.example）——认证快照由
 *      e2e/scripts/refresh-auth.mjs 生成（读数据库验证码 + password_login）
 *
 * 认证方式（两者都配）：
 *   - storageState（默认）：项目 tenant-admin / platform-admin 复用 .auth/*.json 快照
 *   - UI 登录 flow：项目 ui-login 跑 login.spec.ts，走真实登录页（验证码从数据库读取填入）
 *
 * 常用命令：
 *   npx playwright test                跑全部（需先刷新认证：node e2e/scripts/refresh-auth.mjs）
 *   npx playwright test --project=ui-login  只跑 UI 登录流程
 *   npx playwright test --ui           UI 模式调试
 *   npx playwright show-report         查看 HTML 报告
 */
import { defineConfig } from '@playwright/test'
import { readFileSync } from 'node:fs'

/** 与 vite.config.ts 保持一致：从 frontend/.env 读 DEV_PORT（缺省 3000） */
function loadDevPort(): number {
  const fromEnv = process.env.PLAYWRIGHT_PORT
  if (fromEnv) return Number(fromEnv)
  try {
    const content = readFileSync(new URL('./.env', import.meta.url), 'utf-8')
    const m = content.match(/^\s*DEV_PORT\s*=\s*(\d+)/m)
    if (m) return Number(m[1])
  } catch {
    // 无 frontend/.env，回退默认端口
  }
  return 3000
}

const PORT = loadDevPort()
const BASE_URL = `http://localhost:${PORT}`

export default defineConfig({
  testDir: './e2e/specs',
  globalSetup: './e2e/global-setup.mjs',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    // 租户管理员（默认）：复用 storageState 认证快照，覆盖 /t/{tenant_id}/* 页面
    {
      name: 'tenant-admin',
      testMatch: /.*\.spec\.ts/,
      testIgnore: /login\.spec\.ts/,
      use: { storageState: 'e2e/.auth/tenant-admin.json' },
    },
    // UI 登录流程：不走 storageState，真实走登录页（验证码从数据库读取）
    {
      name: 'ui-login',
      testMatch: /login\.spec\.ts/,
      use: {},
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
