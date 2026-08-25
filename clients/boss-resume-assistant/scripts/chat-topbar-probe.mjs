/**
 * 沟通页顶栏结构侦查（手动运行，不入 CI，只读不点击）。
 * 背景：搜索图标是 CSS 背景图标，2026-08-13 用固定坐标 (519,135) 标定（1249x1277 窗口），
 * 另一台机器分辨率不同点错位置。本脚本 dump 顶栏区域全部可定位节点，用于设计
 * 分辨率无关的动态锚定（nodeName/文本锚点/几何特征候选）。
 *
 * 用法：node scripts/chat-topbar-probe.mjs [endpoint]
 * 前提：Chrome 调试实例已登录 BOSS 并停留在沟通页（或任意 zhipin 页）。
 */
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { indexedValues } from '../dist/src/main/boss/domSnapshot.js'

const endpoint = process.argv[2] ?? 'http://127.0.0.1:9222'
const gw = new CdpGateway({ timeoutMs: 10000 })

function center(b) { return [Math.round(b[0] + b[2] / 2), Math.round(b[1] + b[3] / 2)] }

try {
  await gw.connect(endpoint)
  await gw.attachToRecommendPage()
  const snap = await gw.captureDomSnapshot()
  const vp = { w: snap.documents[0]?.layout.bounds[0]?.[2] ?? 0, h: snap.documents[0]?.layout.bounds[0]?.[3] ?? 0 }
  console.log(`[probe] viewport ≈ ${vp.w}x${vp.h}（doc0 根 bounds）`)

  for (let di = 0; di < snap.documents.length; di++) {
    const doc = snap.documents[di]
    if (!doc?.nodes?.nodeName || !doc?.nodes?.nodeValue) continue
    const nameByNode = new Map(indexedValues(doc.nodes.nodeName, 'nodeName'))
    const valueByNode = new Map(indexedValues(doc.nodes.nodeValue, 'nodeValue'))

    console.log(`\n===== document[${di}] =====`)
    const rows = []
    doc.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
      const b = doc.layout.bounds[layoutIndex]
      if (!b || b[2] <= 0 || b[3] <= 0) return
      const [cx, cy] = center(b)
      if (cy > 320 || cy < 30) return // 只看顶栏带（跳过图标碎片区）
      const nn = String(snap.strings[nameByNode.get(nodeIndex) ?? -1] ?? '')
      const txt = String(snap.strings[valueByNode.get(nodeIndex) ?? -1] ?? '').trim().slice(0, 20)
      rows.push({ cy, cx, nn, txt, w: Math.round(b[2]), h: Math.round(b[3]), x: Math.round(b[0]), y: Math.round(b[1]) })
    })
    rows.sort((a, b) => a.cy - b.cy || a.cx - b.cx)
    for (const r of rows) {
      const near = (Math.abs(r.cx - 519) < 80 && Math.abs(r.cy - 135) < 60) ? '  <-- 固定坐标(519,135)附近' : ''
      console.log(`y=${String(r.y).padStart(4)} x=${String(r.x).padStart(4)} ${r.w}x${r.h} ${r.nn.padEnd(8)} "${r.txt}"${near}`)
    }
    // 小方块按钮候选（可能是图标容器）
    console.log(`\n--- 小尺寸元素候选（20<=w<=64 且 20<=h<=64，图标容器特征）---`)
    doc.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
      const b = doc.layout.bounds[layoutIndex]
      if (!b || b[2] < 20 || b[2] > 64 || b[3] < 20 || b[3] > 64) return
      const [cx, cy] = center(b)
      if (cy > 320 || cy < 30) return
      const nn = String(snap.strings[nameByNode.get(nodeIndex) ?? -1] ?? '')
      console.log(`  center=(${cx},${cy}) ${Math.round(b[2])}x${Math.round(b[3])} ${String(nn)}`)
    })
  }
} catch (e) {
  console.error(`[probe] 失败: ${e.message}`)
  process.exit(1)
} finally {
  await gw.close().catch(() => {})
}
