// 探针：备注事项逐字输入验证（表单已打开状态运行：点占位文本聚焦 textarea → CDP char 逐字输入 → 截图验证）
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { WinMouseClicker } from '../dist/src/main/input/WinMouseClicker.js'
import { findNodesByString, accumulateOwnerOffset, boundsCenter } from '../dist/src/main/boss/domSnapshot.js'
import fs from 'node:fs'

const gw = new CdpGateway({ timeoutMs: 10000 })
await gw.connect('http://127.0.0.1:9222')
await gw.attachToRecommendPage()
const clicker = new WinMouseClicker()
const vp = { width: 1917, height: 1905 }

function findAll(snap, text) {
  const hits = []
  snap.strings.forEach((s, i) => {
    if (s.trim() !== text) return
    snap.documents.forEach((doc, di) => {
      for (const { bounds } of findNodesByString(doc, i)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const off = accumulateOwnerOffset(snap, di)
        const c = boundsCenter(bounds)
        hits.push({ x: off.x + c.x - (doc.scrollOffsetX ?? 0), y: off.y + c.y - (doc.scrollOffsetY ?? 0) })
      }
    })
  })
  return hits
}

const snap = await gw.captureDomSnapshot()
// 备注事项 textarea 占位文本无布局节点，改用右下角「/140」字数计数器锚定：点击其左上偏 300/40 处（textarea 内部）
const counter = findAll(snap, '/140')
console.log('counter hits:', JSON.stringify(counter))
if (counter.length !== 1) { console.log('「/140」计数器未唯一命中，退出'); process.exit(1) }
const focusPoint = { x: counter[0].x - 300, y: counter[0].y - 40 }
console.log('focus point:', JSON.stringify(focusPoint))
await clicker.click(focusPoint, vp)
await new Promise((r) => setTimeout(r, 800))

// 逐字输入前 3 个字验证（「请带好」），每字 150ms
for (const ch of '请带好') {
  await gw.dispatchKey({ type: 'char', key: ch, text: ch })
  await new Promise((r) => setTimeout(r, 150))
}
await new Promise((r) => setTimeout(r, 500))
const b64 = await gw.captureScreenshot({ format: 'png' })
fs.writeFileSync('C:/tmp/interview-typing.png', Buffer.from(b64, 'base64'))
console.log('saved C:/tmp/interview-typing.png')
await gw.close()
