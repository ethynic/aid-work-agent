// 探针：面试时间日期选择面板（点「选择日期」→ dump 新增文本 → 截图；不提交任何表单）
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

const snap0 = await gw.captureDomSnapshot()
const before = new Set(snap0.strings.map((s) => s.trim()))
// 「选择日期」placeholder 无布局节点，用「面试时间」标签锚定：点击其右侧 230px 处（选择日期下拉框）
const label = findAll(snap0, '面试时间')
console.log('面试时间 label hits:', JSON.stringify(label))
if (label.length !== 1) { console.log('未唯一命中，退出'); process.exit(1) }
const dtPoint = { x: label[0].x + 230, y: label[0].y }
console.log('date dropdown point:', JSON.stringify(dtPoint))
await clicker.click(dtPoint, vp)
await new Promise((r) => setTimeout(r, 1500))

const snap1 = await gw.captureDomSnapshot()
// dump 新增 strings（日期面板内容）
const added = []
snap1.strings.forEach((s, i) => {
  const t = s.trim()
  if (!t || before.has(t)) return
  snap1.documents.forEach((doc, di) => {
    for (const { bounds } of findNodesByString(doc, i)) {
      if (bounds[2] <= 0 || bounds[3] <= 0) continue
      const off = accumulateOwnerOffset(snap1, di)
      const c = boundsCenter(bounds)
      added.push(`「${t.slice(0, 20)}」(${Math.round(off.x + c.x)},${Math.round(off.y + c.y)})`)
    }
  })
})
console.log('新增可见文本（前 60 个）:')
added.slice(0, 60).forEach((a) => console.log(' ', a))
const b64 = await gw.captureScreenshot({ format: 'png' })
fs.writeFileSync('C:/tmp/interview-date.png', Buffer.from(b64, 'base64'))
console.log('saved C:/tmp/interview-date.png')
await gw.close()
