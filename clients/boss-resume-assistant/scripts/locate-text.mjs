// 定位筛选面板中指定文本选项的 page 坐标 + 视口 CSS 尺寸（供 Win32 点击换算）
// 用法：node scripts/locate-text.mjs "5-10年"
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { accumulateOwnerOffset } from '../dist/src/main/boss/domSnapshot.js'

const endpoint = process.argv[3] ?? 'http://127.0.0.1:9222'
const TEXT = process.argv[2] ?? '5-10年'
const gw = new CdpGateway({ timeoutMs: 10000 })

const iv = (arr) => (!Array.isArray(arr) ? [] : arr.map((v, i) => (v && typeof v === 'object' && 'index' in v ? [v.index, v.value] : [i, v])))

function findText(snap, text) {
  const hits = []
  ;(snap.documents ?? []).forEach((doc, docIdx) => {
    for (const [nodeIndex, stringIndex] of iv(doc.nodes?.nodeValue)) {
      const t = snap.strings[stringIndex]
      if (typeof t !== 'string' || t.trim() !== text) continue
      const li = (doc.layout?.nodeIndex ?? []).indexOf(nodeIndex)
      if (li < 0) continue
      const b = doc.layout.bounds[li]
      if (!b || b[2] <= 0 || b[3] <= 0) continue
      hits.push({ docIdx, x: b[0], y: b[1], w: b[2], h: b[3] })
    }
  })
  return hits
}

try {
  await gw.connect(endpoint)
  const targets = await gw.getTargets()
  const boss = targets.find((t) => t.type === 'page' && t.url.includes('zhipin.com'))
  if (!boss) { console.error('未找到 BOSS 页面'); process.exit(2) }
  await gw.attachToTarget(boss.targetId)

  const snap = await gw.captureDomSnapshot()
  const hits = findText(snap, TEXT)
  console.log(`「${TEXT}」命中 ${hits.length} 个可见节点:`, JSON.stringify(hits))

  // 视口 CSS 尺寸：截图 PNG 像素宽 ÷ 根节点 CSS 宽 得到 DPR，无需 Runtime/Page.getLayoutMetrics（白名单外）
  const shotB64 = await gw.captureScreenshot({ format: 'png' })
  const png = Buffer.from(shotB64, 'base64')
  const pngW = png.readUInt32BE(16)
  const pngH = png.readUInt32BE(20)
  const rootLi = snap.documents[0].layout // 文档根节点（第一个 layout 项）
  const rootB = rootLi.bounds[0]
  const dpr = pngW / rootB[2]
  console.log(`截图 ${pngW}x${pngH} (device px)，根节点 CSS ${rootB[2]}x${rootB[3]}，DPR=${dpr}`)

  if (hits.length >= 1) {
    const h = hits[0]
    const owner = h.docIdx === 0 ? { x: 0, y: 0 } : accumulateOwnerOffset(snap, h.docIdx)
    const cx = Math.round(owner.x + h.x + h.w / 2)
    const cy = Math.round(owner.y + h.y + h.h / 2)
    console.log(`中心点 page 坐标: (${cx}, ${cy})  docIdx=${h.docIdx} ownerOffset=(${owner.x},${owner.y})`)
    // 最后一行输出机读 JSON（cssW/cssH 为视口 CSS 尺寸，供 win-click 换算缩放）
    console.log(`RESULT ${JSON.stringify({ cx, cy, cssW: Math.round(rootB[2]), cssH: Math.round(rootB[3]), hits: hits.length })}`)
  }
} finally {
  await gw.close().catch(() => {})
}
