/**
 * 生成/刷新 Playwright 认证快照（storageState）。
 *
 * 两种运行方式：
 *   1. 独立运行：node e2e/scripts/refresh-auth.mjs（需先配置测试账号环境变量）
 *   2. Playwright globalSetup 自动调用（登录用 UI 不现实，这里走 API + 读数据库验证码）
 *
 * 认证流程（本项目登录需要图形验证码，明文只存数据库 captchas 表）：
 *   GET  /api/auth/captcha           → 拿 captcha_id
 *   查询数据库 captchas 表           → 拿明文 code
 *   POST /api/saas/auth/password_login → 拿 token + user + tenant
 *   写 storageState（localStorage：saas_token_{tid} / saas_admin_{tid} / saas_tenant_{tid}）
 *
 * 环境变量：
 *   PLAYWRIGHT_BASE_URL      前端地址（默认 http://localhost:3000）
 *   PLAYWRIGHT_DATABASE_URL  数据库连接串（缺省读项目根 .env 的 DATABASE_URL）
 *   PLAYWRIGHT_TENANT_ID     测试租户 ID（如 tenant_e9b2fab93a2f）
 *   PLAYWRIGHT_IDENTIFIER    测试账号手机号/用户名
 *   PLAYWRIGHT_PASSWORD      测试账号密码
 *   PLAYWRIGHT_PLATFORM_IDENTIFIER / PLAYWRIGHT_PLATFORM_PASSWORD  平台管理员账号（可选）
 *
 * storageState 快照会过期（token 失效后需重新生成），过期由 useTenantAuth.init()
 * 检测到 401 后自动登出，此时测试会失败，重跑本脚本刷新即可。
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { Client } from 'pg'

const __dirname = dirname(fileURLToPath(import.meta.url))
const AUTH_DIR = join(__dirname, '..', '.auth')
const ROOT_ENV = join(__dirname, '..', '..', '..', '.env')

const { BASE_URL, DATABASE_URL, TENANT_ID, IDENTIFIER, PASSWORD, PLATFORM_IDENTIFIER, PLATFORM_PASSWORD } = loadConfig()

/** 读取项目根 .env（仅取 PLAYWRIGHT_* 与 DATABASE_URL，简单 key=value 解析） */
function loadConfig() {
  let envContent = ''
  try {
    envContent = readFileSync(ROOT_ENV, 'utf-8')
  } catch {
    // 无根 .env 时仅依赖进程环境变量
  }
  const env = { ...process.env }
  for (const line of envContent.split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/)
    if (m) env[m[1]] = m[2].replace(/^['"]|['"]$/g, '')
  }
  return {
    BASE_URL: process.env.PLAYWRIGHT_BASE_URL || env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000',
    DATABASE_URL: process.env.PLAYWRIGHT_DATABASE_URL || env.DATABASE_URL,
    TENANT_ID: process.env.PLAYWRIGHT_TENANT_ID || env.PLAYWRIGHT_TENANT_ID,
    IDENTIFIER: process.env.PLAYWRIGHT_IDENTIFIER || env.PLAYWRIGHT_IDENTIFIER,
    PASSWORD: process.env.PLAYWRIGHT_PASSWORD || env.PLAYWRIGHT_PASSWORD,
    PLATFORM_IDENTIFIER: process.env.PLAYWRIGHT_PLATFORM_IDENTIFIER || env.PLAYWRIGHT_PLATFORM_IDENTIFIER,
    PLATFORM_PASSWORD: process.env.PLAYWRIGHT_PLATFORM_PASSWORD || env.PLAYWRIGHT_PLATFORM_PASSWORD,
  }
}

/** 从数据库 captchas 表读取验证码明文（一次性验证码，需先调用 captcha 接口拿到 id） */
async function fetchCaptchaCode(captchaId) {
  if (!DATABASE_URL) throw new Error('未配置 PLAYWRIGHT_DATABASE_URL，无法读取验证码')
  const client = new Client({ connectionString: DATABASE_URL })
  await client.connect()
  try {
    const { rows } = await client.query(
      'SELECT code FROM captchas WHERE captcha_id = $1 AND expires_at > NOW()',
      [captchaId],
    )
    if (!rows.length) throw new Error(`验证码不存在或已过期: ${captchaId}`)
    return rows[0].code
  } finally {
    await client.end()
  }
}

/** 走完整登录链路：captcha 接口 → 读库拿明文 → password_login */
async function login({ tenantId, identifier, password, requiredRole }) {
  // 1. 获取验证码 id
  const captchaRes = await fetch(`${BASE_URL}/api/auth/captcha`)
  if (!captchaRes.ok) throw new Error(`captcha 接口失败: ${captchaRes.status}`)
  const captcha = await captchaRes.json()
  const captchaId = captcha.captcha_id

  // 2. 读数据库拿明文
  const code = await fetchCaptchaCode(captchaId)

  // 3. 密码登录
  const loginRes = await fetch(`${BASE_URL}/api/saas/auth/password_login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      identifier,
      password,
      captcha_code: code,
      captcha_id: captchaId,
      tenant_id: tenantId,
      required_role: requiredRole,
    }),
  })
  const body = await loginRes.json()
  if (!loginRes.ok || !body.success || !body.token) {
    throw new Error(`password_login 失败: ${loginRes.status} ${JSON.stringify(body)}`)
  }
  return { token: body.token, user: body.user, tenant: body.tenant }
}

function buildStorageState(entries) {
  return {
    cookies: [],
    origins: [
      {
        origin: BASE_URL,
        localStorage: Object.entries(entries).map(([name, value]) => ({ name, value })),
      },
    ],
  }
}

function writeAuth(name, entries) {
  mkdirSync(AUTH_DIR, { recursive: true })
  const file = join(AUTH_DIR, `${name}.json`)
  writeFileSync(file, JSON.stringify(buildStorageState(entries), null, 2))
  console.log(`[refresh-auth] 已写入 ${file}`)
}

export async function refreshStorageStates() {
  if (!TENANT_ID || !IDENTIFIER || !PASSWORD) {
    console.warn('[refresh-auth] 缺少测试账号环境变量（PLAYWRIGHT_TENANT_ID/IDENTIFIER/PASSWORD），跳过 storageState 生成')
    return false
  }

  // 租户管理员（token-usage 等租户前台页面需要 tenant_admin 角色）
  const admin = await login({ tenantId: TENANT_ID, identifier: IDENTIFIER, password: PASSWORD, requiredRole: 'tenant_admin' })
  const tid = admin.tenant?.tenant_id || TENANT_ID
  writeAuth('tenant-admin', {
    [`saas_token_${tid}`]: admin.token,
    [`saas_admin_${tid}`]: JSON.stringify(admin.user),
    [`saas_tenant_${tid}`]: JSON.stringify(admin.tenant),
  })

  // 平台管理员（可选）：无租户属性，key 为 portal_*（platform-admin 项目用）
  if (PLATFORM_IDENTIFIER && PLATFORM_PASSWORD) {
    const plat = await login({ identifier: PLATFORM_IDENTIFIER, password: PLATFORM_PASSWORD, requiredRole: 'platform_admin' })
    writeAuth('platform-admin', {
      portal_token: plat.token,
      portal_admin: JSON.stringify(plat.user),
      portal_tenant: JSON.stringify(plat.tenant),
    })
  }
  return true
}

// CLI 直接运行入口
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  refreshStorageStates()
    .then((ok) => process.exit(ok ? 0 : 1))
    .catch((e) => {
      console.error('[refresh-auth] 失败:', e.message)
      process.exit(1)
    })
}
