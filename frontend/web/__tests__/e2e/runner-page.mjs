/** Original page smoke with fictional auth and every business request intercepted.
 * No existing Playwright config, saved auth, user browser profile or real API is used.
 */
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdtemp, rm, writeFile, open } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.RUNNER_TEST_PLAYWRIGHT_MODULE || '@playwright/test')

const root = await mkdtemp(join(tmpdir(), 'runner-page-owned-'))
const reserve = createServer()
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve))
const port = reserve.address().port
await new Promise(resolve => reserve.close(resolve))
const origin = `http://127.0.0.1:${port}`
const log = await open(join(root, 'vite.log'), 'w')
const vite = spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
  cwd: process.cwd(), env: { ...process.env, VITE_API_BASE_URL: '/api' }, detached: true, stdio: ['ignore', log.fd, log.fd],
})
let browser, context
const observed = [], unknown = [], external = []
const report = { scope: 'Original browser UI only, all APIs fictional; real worker proved separately', passed: [], cleanup: false }
const tenant = 'fixture-tenant'
const user = { user_id: 'fixture-user', username: 'Fixture user', role: 'tenant_admin', tenant_id: tenant, status: 'active' }
const company = { tenant_id: tenant, company_name: 'Fixture Company', status: 'active', credit_balance: 1000 }
const sessions = ['A', 'B'].map(id => ({ session_id: id, user_id: user.user_id, tenant_id: tenant, title: `Fixture Session ${id}`,
  subagent_id: 'main', created_at: '2026-10-01T12:00:00Z', updated_at: '2026-10-01T12:00:00Z' }))
const rows = []
let latest = 'A'
let rejectNextSubmit = false
try {
  for (let attempt = 0; attempt < 40; attempt++) {
    try { if ((await fetch(origin)).ok) break } catch { /* local Vite not ready */ }
    if (attempt === 39) throw new Error('Owned Vite failed to start')
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
  context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1280, height: 900 } })
  await context.addInitScript(({ user, company, tenant }) => {
    localStorage.setItem(`saas_token_${tenant}`, 'fictional-page-token')
    localStorage.setItem(`saas_admin_${tenant}`, JSON.stringify(user))
    localStorage.setItem(`saas_tenant_${tenant}`, JSON.stringify(company))
  }, { user, company, tenant })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    if (url.origin !== origin) { external.push(path); await route.abort(); return }
    if (!path.startsWith('/api/')) { await route.continue(); return }
    observed.push({ method: request.method(), path })
    let value, status = 200
    if (path === '/api/saas/auth/me') value = { success: true, user, tenant: company }
    else if (path === '/api/saas/connection-sources') value = { success: true, data: [], sources: [] }
    else if (path === '/api/saas/channels') value = { success: true, data: [], channels: [] }
    else if (path.endsWith('/my/allowed-agents')) value = { success: true, data: [{ agent_id: 'main', name: 'Fixture main', description: 'Fixture assistant', type: 'builtin' }] }
    else if (path.includes('/billing/') && path.endsWith('/balance')) value = { success: true, balance: { credit_balance: 1000, renewal_pending: false } }
    else if (path === '/api/sessions/latest') value = { session: sessions.find(item => item.session_id === latest) }
    else if (path === '/api/sessions' && request.method() === 'GET') value = { sessions, total: 2, page: 1, page_size: 20 }
    else if (/^\/api\/sessions\/[AB]\/messages$/.test(path)) value = { messages: [] }
    else if (/^\/api\/sessions\/[AB]$/.test(path)) value = { success: true, session: sessions.find(item => item.session_id === path.split('/').at(-1)) }
    else if (path.endsWith('/greeting')) value = { success: true, greeting: 'Fixture greeting' }
    else if (path === '/api/chat/runners/capabilities') value = { web_enabled: true, observe_existing: true, contract_version: 1, transport: 'runner_poll' }
    else if (path === '/api/chat/runners' && request.method() === 'POST') {
      if (rejectNextSubmit) {
        rejectNextSubmit = false; value = { success: false, code: 'NO_CREDIT', error: '积分余额已耗尽，数字员工无法工作' }; status = 403
      } else {
      const body = request.postDataJSON(), id = 'fixture-runner-' + (rows.length + 1)
      const row = { runner_id: id, client_request_id: body.client_request_id, queue_order: rows.length + 1,
        profile_id: 'main', session: { kind: 'web', session_id: body.session_id }, status: 'running', cancel_requested: false,
        view_revision: 1, accepted_at: '2026-10-01T12:00:00Z', updated_at: '2026-10-01T12:00:01Z',
        snapshot: { input: { message_id: id + ':user', text: body.message, attachments: [] }, output: '' } }
      rows.push(row); value = { success: true, created: true, runner: row }; status = 202
      }
    } else if (/^\/api\/chat\/sessions\/[AB]\/runners$/.test(path)) {
      const selected = rows.filter(item => item.session.session_id === path.split('/')[4])
      value = { runners: selected, active_runners: selected.filter(item => item.status === 'running'), next_cursor: null, has_more: false }
    } else if (/^\/api\/chat\/runners\/fixture-runner-\d+(\/cancel)?$/.test(path)) {
      const row = rows.find(item => item.runner_id === path.split('/')[4])
      if (path.endsWith('/cancel')) { row.status = 'cancelled'; row.cancel_requested = true; row.view_revision++; row.result = { status: 'cancelled', output: '' } }
      value = { success: true, runner: row }
    } else { unknown.push(path); status = 503; value = { success: false, error: 'UNEXPECTED_FIXTURE_API' } }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
  })
  let page = await context.newPage()
  await page.goto(origin + `/t/${tenant}`)
  await page.locator('textarea').waitFor({ state: 'visible', timeout: 15000 })
  await page.locator('textarea').fill('Fixture first original page task')
  await page.locator('textarea').press('Enter')
  await page.waitForFunction(() => document.body.textContent.includes('Fixture first original page task'))
  for (let attempt = 0; rows.length < 1 && attempt < 40; attempt++) await page.waitForTimeout(100)
  assert.equal(rows.length, 1)
  await page.close()
  assert.equal(observed.filter(item => item.path.endsWith('/cancel')).length, 0)
  report.passed.push('Closing original page detaches without cancel')
  rows[0].status = 'completed'; rows[0].view_revision++; rows[0].result = { status: 'completed', output: 'Fixture first completed while page closed' }
  page = await context.newPage()
  await page.goto(origin + `/t/${tenant}`)
  await page.getByText('Fixture first completed while page closed', { exact: true }).waitFor({ timeout: 15000 })
  assert.equal(await page.getByText('Fixture first original page task', { exact: true }).count(), 1)
  report.passed.push('Fresh original page discovers terminal runner and submitted input exactly once')
  await page.getByText('Fixture Session B', { exact: true }).click()
  await page.locator('textarea').fill('Fixture B independent task')
  await page.locator('textarea').press('Enter')
  await page.getByTitle('停止生成', { exact: true }).waitFor({ timeout: 10000 })
  // The existing stop control is already visible during capability selection;
  // only the received POST proves acceptance of the second runner.
  for (let attempt = 0; rows.length < 2 && attempt < 40; attempt++) await page.waitForTimeout(100)
  assert.equal(rows.length, 2)
  assert.equal(rows[1].session.session_id, 'B')
  assert.equal(await page.getByText('Fixture first completed while page closed', { exact: true }).count(), 0)
  report.passed.push('Existing session sidebar switches to an independent Runner view')
  page.once('dialog', async dialog => {
    assert.equal(dialog.type(), 'confirm')
    assert.equal(dialog.message(), '确定要停止生成吗？')
    await dialog.accept()
  })
  await page.getByTitle('停止生成', { exact: true }).click()
  await page.getByText('用户已取消本轮回复', { exact: true }).first().waitFor({ timeout: 10000 })
  assert.equal(rows[1].status, 'cancelled')
  assert.equal(observed.filter(item => item.path.endsWith('/cancel')).length, 1)
  report.passed.push('Existing stop control requests explicit durable cancellation and displays stopped state')
  rejectNextSubmit = true
  await page.locator('textarea').fill('Fixture explicit no-credit response')
  await page.locator('textarea').press('Enter')
  await page.getByText('积分余额已耗尽，数字员工无法工作', { exact: true }).waitFor({ timeout: 10000 })
  assert.equal(rows.length, 2)
  report.passed.push('Existing no-credit toast is visible without accepting another runner')
  await page.screenshot({ path: '/private/tmp/aid-agent-runner-m3-page.png', fullPage: true })
} catch (error) {
  report.failure = { type: error.constructor.name, message: error.message }
  process.exitCode = 1
} finally {
  if (context) await context.close()
  if (browser) await browser.close()
  try { process.kill(-vite.pid, 'SIGTERM') } catch { /* already stopped */ }
  await new Promise(resolve => { if (vite.exitCode !== null) resolve(); else vite.once('exit', resolve) })
  await log.close(); await rm(root, { recursive: true, force: true })
  report.cleanup = true; report.unknownAPI = [...new Set(unknown)]; report.blockedExternalPaths = [...new Set(external)]
  report.observed = observed
  await writeFile('/private/tmp/aid-agent-runner-m3-page-evidence.json', JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed, failure: report.failure, cleanup: report.cleanup, unknownAPI: report.unknownAPI }))
}
