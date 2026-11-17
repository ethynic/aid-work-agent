import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

test('add-friend vision calls the authenticated billing proxy even when direct model keys are set', {
  skip: process.platform !== 'win32',
  timeout: 20_000,
}, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'weixin-friend-proxy-'))
  const requests: Array<{ url?: string; auth?: string; body: string }> = []
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk)
    requests.push({ url: req.url, auth: req.headers.authorization, body: Buffer.concat(chunks).toString('utf8') })
    const status = req.url === '/api/client/v1/llm/chat' ? [200, 402, 401][requests.length - 1] ?? 500 : 404
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(status === 200 ? { content: 'json result: {"x":12,"y":34}' } : { detail: 'test error' }))
  })
  try {
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    const url = `http://127.0.0.1:${address.port}`
    const common = fileURLToPath(new URL('../../drivers/ps1/_common.ps1', import.meta.url)).replace(/'/g, "''")
    const script = join(dir, 'proxy-test.ps1')
    writeFileSync(script, `\ufeff
$ErrorActionPreference = 'Stop'
. '${common}'
$script:ZhipuApiBase = '${url}/direct-glm'
$script:KimiApiBase = '${url}/direct-kimi'
$image = [Text.Encoding]::UTF8.GetBytes('synthetic image bytes')
$calls = @(foreach ($i in 1..4) {
    if ($i -eq 4) { $env:AID_WEIXIN_ACCESS_TOKEN = '' }
    try {
        Invoke-KimiVision -ImageBytes $image -SystemPrompt 'Find the control.' -UserText 'Return coordinates.' -ArtifactPrefix 'weixin-driver-friend-add-test' -ProxyOnly
    } catch {
        ([string]$_.Exception.Message).Split('|')[1]
    }
})
Write-DriverJson @{ ok = $true; data = @{ calls = $calls } }
`)
    const { stdout } = await execFileAsync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script], {
      windowsHide: true,
      timeout: 15_000,
      env: {
        ...process.env,
        TEMP: dir,
        AID_WEIXIN_SERVER_URL: url,
        AID_WEIXIN_ACCESS_TOKEN: 'test-client-binding',
        AID_WEIXIN_PROXY_ERROR: '',
        AID_WEIXIN_VISION_PROVIDER: 'glm',
        AID_WEIXIN_ZHIPU_API_KEY: 'test-glm-key',
        AID_WEIXIN_KIMI_API_KEY: 'test-kimi-key',
      },
    })
    const line = stdout.split(/\r?\n/).find((text) => text.startsWith('DRIVER_JSON: '))
    assert.ok(line)
    assert.deepEqual(JSON.parse(line.slice('DRIVER_JSON: '.length)).data.calls, [
      { x: 12, y: 34 }, 'INSUFFICIENT_CREDIT', 'CONFIG_MISSING', 'CONFIG_MISSING',
    ])
    assert.equal(requests.length, 3)
    for (const request of requests) {
      assert.equal(request.url, '/api/client/v1/llm/chat')
      assert.equal(request.auth, 'Bearer test-client-binding')
      const payload = JSON.parse(request.body)
      assert.equal(payload.model, 'kimi-k3')
      assert.match(payload.purpose, /^weixin-vision-/)
      assert.equal(payload.messages[1].content[0].image_url.url,
        `data:image/png;base64,${Buffer.from('synthetic image bytes').toString('base64')}`)
    }
    assert.deepEqual(readdirSync(dir), ['proxy-test.ps1'])
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
    rmSync(dir, { recursive: true, force: true })
  }
})
