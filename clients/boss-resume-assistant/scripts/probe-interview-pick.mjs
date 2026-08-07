// 探针：在已打开的日历面板里点击日期「7」（明天），验证选中效果
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { WinMouseClicker } from '../dist/src/main/input/WinMouseClicker.js'
import { findNodesByString, accumulateOwnerOffset, boundsCenter } from '../dist/src/main/boss/domSnapshot.js'
import fs from 'node:fs'

const gw = new CdpGateway({ timeoutMs: 10000 })
await gw.connect('http://127.0.0.1:9222')
await gw.attachToRecommendPage()
const clicker = new WinMouseClicker()
const vp = { width: 1917, height: 1905 }

const snap = await gw.captureDomSnapshot()
// 日历区域：截图实测 x 650~1150, y 1120~1600；在里面找「7」
const hits = []
snap.strings.forEach((s, i) => {
  if (s.trim() !== '7') return
  snap.documents.forEach((doc, di) => {
    for (const { bounds } of findNodesByString(doc, i)) {
      if (bounds[2] <= 0 || bounds[3] <= 0) continue
      const off = accumulateOwnerOffset(snap, di)
      const c = boundsCenter(bounds)
      const x = off.x + c.x - (doc.scrollOffsetX ?? 0)
      const y = off.y + c.y - (doc.scrollOffsetY ?? 0)
      if (x > 650 && x < 1150 && y > 1120 && y < 1600) hits.push({ x, y })
    }
  })
})
console.log('日历区域「7」hits:', JSON.stringify(hits))
if (hits.length !== 1) { console.log('未唯一命中，退出'); process.exit(1) }
await clicker.click(hits[0], vp)
await new Promise((r) => setTimeout(r, 1200))
const b64 = await gw.captureScreenshot({ format: 'png' })
fs.writeFileSync('C:/tmp/interview-date-picked.png', Buffer.from(b64, 'base64'))
console.log('saved C:/tmp/interview-date-picked.png')
await gw.close()
