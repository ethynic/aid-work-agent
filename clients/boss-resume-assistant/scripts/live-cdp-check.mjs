/**
 * Phase 2 真机只读验证脚本（手动运行，不入 CI，不执行写动作）。
 * 连接已登录的 Chrome（--remote-debugging-port=9222），
 * 列 target、attach 推荐页、Page.enable、单次截图。
 * 验证产品化 CdpGateway 与协议策略在真机可用。
 *
 * 用法：node scripts/live-cdp-check.mjs [http-endpoint] [output-dir]
 * 截图写入 output-dir（默认临时目录），含候选人信息，验证后及时删除。
 */
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'

const endpoint = process.argv[2] ?? 'http://127.0.0.1:9222'
const outDir = process.argv[3] ?? path.join(os.tmpdir(), 'boss-resume-live-check')
mkdirSync(outDir, { recursive: true })

const gw = new CdpGateway({ timeoutMs: 10000 })

try {
  console.log(`[live-check] connecting ${endpoint} ...`)
  await gw.connect(endpoint)
  console.log('[live-check] connected to browser CDP')

  const targets = await gw.getTargets()
  console.log(`[live-check] targets: ${targets.length}`)
  for (const t of targets) {
    console.log(`  ${t.type} | ${t.url.slice(0, 80)} | ${t.title.slice(0, 30)}`)
  }

  const boss = targets.find((t) => t.type === 'page' && t.url.includes('zhipin.com'))
  if (!boss) {
    console.error('[live-check] NO BOSS page target found — abort (read-only)')
    process.exit(2)
  }
  console.log(`[live-check] attaching target: ${boss.targetId}`)
  const attach = await gw.attachToTarget(boss.targetId)
  console.log(`[live-check] attached sessionId=${attach.sessionId.slice(0, 8)}...`)

  await gw.pageEnable()
  console.log('[live-check] Page.enable OK')

  const frame = await gw.getFrameTree()
  const frameStr = JSON.stringify(frame)
  console.log(`[live-check] frame tree bytes: ${frameStr.length}`)
  if (frameStr.includes('/web/frame/recommend')) {
    console.log('[live-check] recommend frame present ✓')
  }

  const data = await gw.captureScreenshot({ format: 'png' })
  const shotPath = path.join(outDir, 'detail-baseline.png')
  writeFileSync(shotPath, Buffer.from(data, 'base64'))
  console.log(`[live-check] screenshot saved: ${shotPath} (${Math.round(data.length * 0.75 / 1024)} KB)`)

  console.log('[live-check] DONE — Phase 2 read-only gate passed')
} catch (e) {
  console.error('[live-check] FAILED:', e)
  process.exit(1)
} finally {
  await gw.close()
}
