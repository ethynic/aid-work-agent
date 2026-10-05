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

const reserve = createServer()
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve))
const port = reserve.address().port
await new Promise(resolve => reserve.close(resolve))
const root = await mkdtemp(join(tmpdir(), 'runner-recovery-page-owned-'))
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
const rows = ['A','B'].map((session, index) => ({ runner_id: 'fixture-runner-' + (index + 1),
  client_request_id: 'fictional-original-key-' + session, queue_order: index + 1, profile_id: 'main',
  session: { kind: 'web', session_id: session }, status: index === 0 ? 'paused' : 'interrupted', cancel_requested: false,
  view_revision: 1, accepted_at: '2026-10-01T12:00:00Z', updated_at: '2026-10-01T12:00:01Z',
  snapshot: { input: { message_id: 'fixture-runner-' + (index + 1) + ':user', text: 'Fixture original parked task ' + session, attachments: [] },
    output: 'Fixture retained original response ' + session } }))
const controls = []
let rejectNextResume = false
let latest = 'A'

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
    const observation = { method: request.method(), path }; observed.push(observation)
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
    else if (path === '/api/upload' && request.method() === 'POST') value = { success: true, file_id: 'fictional-page-upload',
      name: 'resume-page.txt', type: 'file', mime_type: 'text/plain', size: 18 }
    else if (path === '/api/chat/runners' && request.method() === 'POST') {
      unknown.push('UNEXPECTED_NEW_RUNNER'); status = 503; value = { success: false, error: 'NEW_RUNNER_NOT_PERMITTED' }
    } else if (/^\/api\/chat\/runners\/fixture-runner-\d+\/controls$/.test(path) && request.method() === 'POST') {
      const body = request.postDataJSON(), row = rows.find(item => item.runner_id === path.split('/')[4])
      let accepted = controls.find(item => item.runner_id === row.runner_id && item.client_request_id === body.client_request_id)
      const created = !accepted
      if (!accepted) {
        accepted = { control_id: 'fictional-page-control-' + (controls.length + 1), runner_id: row.runner_id,
          client_request_id: body.client_request_id, action: body.action, status: rejectNextResume ? 'rejected' : 'consumed',
          error_code: rejectNextResume ? 'RECOVERY_PROFILE_CHANGED' : null, body }
        controls.push(accepted)
        row.snapshot.supplementalInputs = [{ message_id: accepted.control_id + ':user', control_id: accepted.control_id,
          client_request_id: body.client_request_id, text: body.answer, accepted_at: '2026-10-02T00:00:00Z', attachments: body.attachments }]
        row.status = rejectNextResume ? 'interrupted' : 'running'; row.view_revision++; row.resume_requested = !rejectNextResume
        rejectNextResume = false
      }
      value = { success: true, created, control: Object.fromEntries(Object.entries(accepted).filter(([key]) => key !== 'body')), runner: row }; status = 202
    } else if (/^\/api\/chat\/runners\/fixture-runner-\d+\/controls\/fictional-page-control-\d+$/.test(path)) {
      const selected = controls.find(item => item.control_id === path.split('/').at(-1))
      value = { success: true, control: Object.fromEntries(Object.entries(selected).filter(([key]) => key !== 'body')) }
    } else if (/^\/api\/chat\/sessions\/[AB]\/runners$/.test(path)) {
      const selected = rows.filter(item => item.session.session_id === path.split('/')[4])
      value = { runners: selected, active_runners: selected.filter(item => ['running','waiting','paused','interrupted'].includes(item.status)), next_cursor: null, has_more: false }
    } else if (/^\/api\/chat\/runners\/fixture-runner-\d+(\/cancel)?$/.test(path)) {
      const row = rows.find(item => item.runner_id === path.split('/')[4])
      if (path.endsWith('/cancel')) { row.status = 'cancelled'; row.cancel_requested = true; row.view_revision++; row.result = { status: 'cancelled', output: '' } }
      value = { success: true, runner: row }
    } else { unknown.push(path); status = 503; value = { success: false, error: 'UNEXPECTED_FIXTURE_API' } }
    observation.httpStatus = status
    if (value?.control) observation.controlStatus = value.control.status
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
  })
  let page = await context.newPage()
  await page.goto(origin + `/t/${tenant}`)
  await page.getByText('Fixture original parked task A', { exact: true }).waitFor({ timeout: 15000 })
  await page.locator('input[type="file"]').setInputFiles({ name: 'resume-page.txt', mimeType: 'text/plain', buffer: Buffer.from('fictional UI bytes') })
  await page.getByText('resume-page.txt', { exact: true }).first().waitFor({ timeout: 10000 })
  await page.locator('textarea').fill('Fixture same runner supplemental input')
  await page.locator('textarea').press('Enter')
  for (let attempt = 0; controls.length < 1 && attempt < 40; attempt++) await page.waitForTimeout(100)
  assert.equal(controls.length, 1)
  assert.equal(controls[0].runner_id, 'fixture-runner-1')
  assert.equal(controls[0].body.action, 'resume')
  assert.equal(controls[0].body.answer, 'Fixture same runner supplemental input')
  assert.equal(controls[0].body.attachments.length, 1)
  assert.equal(controls[0].body.attachments[0].file_id, 'fictional-page-upload')
  assert.equal(rows[0].client_request_id, 'fictional-original-key-A')
  report.passed.push('Original Send continues the parked runner with its supplemental input and existing upload attachment')
  await page.close()
  assert.equal(observed.filter(item => item.path.endsWith('/cancel')).length, 0)
  page = await context.newPage(); await page.goto(origin + `/t/${tenant}`)
  await page.getByText('Fixture same runner supplemental input', { exact: true }).waitFor({ timeout: 15000 })
  assert.equal(await page.getByText('Fixture same runner supplemental input', { exact: true }).count(), 1)
  report.passed.push('Closing and refreshing preserves the stable supplemental user input once without cancelling')
  rows[0].status = 'completed'; rows[0].resume_requested = false; rows[0].view_revision++
  rows[0].result = { status: 'completed', output: 'Fixture resumed final result' }
  await page.getByText('Fixture resumed final result', { exact: true }).waitFor({ timeout: 15000 })
  await page.getByText('Fixture Session B', { exact: true }).click()
  await page.getByText('Fixture original parked task B', { exact: true }).waitFor({ timeout: 15000 })
  assert.equal(await page.getByText('Fixture same runner supplemental input', { exact: true }).count(), 0)
  rejectNextResume = true
  await page.locator('input[type="file"]').setInputFiles({ name: 'resume-page.txt', mimeType: 'text/plain', buffer: Buffer.from('fictional UI bytes') })
  await page.getByText('resume-page.txt', { exact: true }).first().waitFor({ timeout: 10000 })
  await page.locator('textarea').fill('Fixture rejected followup remains visible')
  await page.locator('textarea').press('Enter')
  await page.waitForFunction(() => document.body.textContent.includes('尚未应用'), null, { timeout: 10000 })
  assert.equal(controls.length, 2)
  assert.equal(controls[1].runner_id, 'fixture-runner-2')
  assert.equal(controls[1].status, 'rejected')
  assert.equal(controls[1].body.attachments[0].file_id, 'fictional-page-upload')
  assert.equal(await page.locator('textarea').inputValue(), 'Fixture rejected followup remains visible')
  await page.locator('button[title="移除附件"]').first().waitFor({ timeout: 5000 })
  await page.getByText('Fixture rejected followup remains visible', { exact: true }).waitFor({ timeout: 10000 })
  await page.screenshot({ path: '/private/tmp/aid-agent-runner-m4-page-rejected-feedback.png', fullPage: true })
  report.passed.push('Switching sessions preserves ownership and a rejected control shows the retained supplement plus the existing error feedback')
  await page.getByText('Fixture Session A', { exact: true }).click()
  await page.getByText('Fixture resumed final result', { exact: true }).waitFor({ timeout: 15000 })
  assert.equal(await page.getByText('Fixture same runner supplemental input', { exact: true }).count(), 1)
  assert.equal(await page.getByText('Fixture rejected followup remains visible', { exact: true }).count(), 0)
  report.passed.push('Returning to the original session restores its final response and input without another submission')
  assert.equal(observed.filter(item => item.method === 'POST' && item.path === '/api/chat/runners').length, 0)
  assert.equal(unknown.length, 0); assert.equal(external.length, 0)
  await page.screenshot({ path: '/private/tmp/aid-agent-runner-m4-page.png', fullPage: true })
} catch (error) {
  report.failure = { type: error.constructor.name, message: error.message }
  const failedPage = context?.pages().at(-1)
  if (failedPage) await failedPage.screenshot({ path: '/private/tmp/aid-agent-runner-m4-page-failure.png', fullPage: true })
  process.exitCode = 1
} finally {
  if (context) await context.close()
  if (browser) await browser.close()
  try { process.kill(-vite.pid, 'SIGTERM') } catch { /* already stopped */ }
  await new Promise(resolve => { if (vite.exitCode !== null) resolve(); else vite.once('exit', resolve) })
  await log.close(); await rm(root, { recursive: true, force: true })
  report.cleanup = true; report.unknownAPI = [...new Set(unknown)]; report.blockedExternalPaths = [...new Set(external)]
  report.observed = observed
  await writeFile('/private/tmp/aid-agent-runner-m4-page-evidence.json', JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed, failure: report.failure, cleanup: report.cleanup, unknownAPI: report.unknownAPI }))
}
