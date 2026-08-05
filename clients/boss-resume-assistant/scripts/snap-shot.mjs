// 截图诊断：node scripts/snap-shot.mjs [输出路径]
import { writeFileSync } from 'node:fs'
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
const out = process.argv[2] ?? '/tmp/boss-shot.png'
const gw = new CdpGateway({ timeoutMs: 10000 })
await gw.connect('http://127.0.0.1:9222')
const t = (await gw.getTargets()).find((x) => x.type === 'page' && x.url.includes('zhipin.com'))
await gw.attachToTarget(t.targetId)
writeFileSync(out, Buffer.from(await gw.captureScreenshot(), 'base64'))
await gw.close().catch(() => {})
console.log('saved:', out)
