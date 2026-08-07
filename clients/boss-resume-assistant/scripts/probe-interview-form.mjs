// 探针：约面试表单结构（点「约面试」→ dump 表单文本坐标 → 截图；不点发送，最后点取消关闭）
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { WinMouseClicker } from '../dist/src/main/input/WinMouseClicker.js'
import { findNodesByString, accumulateOwnerOffset, boundsCenter } from '../dist/src/main/boss/domSnapshot.js'
import fs from 'node:fs'

const gw = new CdpGateway({ timeoutMs: 10000 })
await gw.connect('http://127.0.0.1:9222')
await gw.attachToRecommendPage()
const clicker = new WinMouseClicker()
const snap0 = await gw.captureDomSnapshot()
const vp = { width: 1917, height: 1905 }

// 1. 定位「约面试」
function findAll(snap, text) {
  const hits = []
  snap.strings.forEach((s, i) => {
    if (s.trim() !== text) return
    snap.documents.forEach((doc, di) => {
      for (const { bounds } of findNodesByString(doc, i)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const off = accumulateOwnerOffset(snap, di)
        const c = boundsCenter(bounds)
        hits.push({ x: off.x + c.x - (doc.scrollOffsetX ?? 0), y: off.y + c.y - (doc.scrollOffsetY ?? 0), di })
      }
    })
  })
  return hits
}
const iv = findAll(snap0, '约面试').filter((h) => h.x > 850 && h.y > 0 && h.y < vp.height)
console.log('约面试 hits:', JSON.stringify(iv))
if (iv.length !== 1) { console.log('歧义，退出'); process.exit(1) }

// 2. Win32 点击
await clicker.click({ x: iv[0].x, y: iv[0].y }, vp)
await new Promise((r) => setTimeout(r, 2000))

// 3. dump 表单内容
const snap1 = await gw.captureDomSnapshot()
const KEYWORDS = ['面试', '备注', '时间', '日期', '取消', '发送', '确定', '上午', '下午', '晚上', '联系人', '地址', '星期', '今天', '明天']
const seen = new Map()
for (const kw of KEYWORDS) {
  snap1.strings.forEach((s, i) => {
    if (!s.includes(kw)) return
    snap1.documents.forEach((doc, di) => {
      for (const { bounds } of findNodesByString(doc, i)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const off = accumulateOwnerOffset(snap1, di)
        const c = boundsCenter(bounds)
        const x = off.x + c.x - (doc.scrollOffsetX ?? 0)
        const y = off.y + c.y - (doc.scrollOffsetY ?? 0)
        const key = `${s.trim()}@${Math.round(x)},${Math.round(y)}`
        if (seen.has(key)) continue
        seen.set(key, true)
        console.log(`  「${s.trim().slice(0, 40)}」 doc${di} (${Math.round(x)},${Math.round(y)})`)
      }
    })
  })
}
const b64 = await gw.captureScreenshot({ format: 'png' })
fs.writeFileSync('C:/tmp/interview-form.png', Buffer.from(b64, 'base64'))
console.log('saved C:/tmp/interview-form.png')
await gw.close()
// 表单保持打开，下一步直接在里面试填
