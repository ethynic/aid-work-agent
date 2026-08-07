// 只读探针：定位「本科」在筛选面板为何 2 个可见命中（locateRowOption 失败排查）
// 前提：推荐页筛选面板已打开。不点击任何东西。
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { accumulateOwnerOffset, findNodesByString } from '../dist/src/main/boss/domSnapshot.js'

const endpoint = process.argv[2] ?? 'http://127.0.0.1:9222'
const gw = new CdpGateway({ timeoutMs: 10000 })

function visibleHits(snap, pred) {
  const hits = []
  snap.strings.forEach((s, stringIndex) => {
    const text = (s ?? '').trim()
    if (!pred(text)) return
    snap.documents.forEach((doc, docIdx) => {
      for (const { bounds, nodeIndex } of findNodesByString(doc, stringIndex)) {
        if (bounds[2] > 0 && bounds[3] > 0) hits.push({ docIdx, nodeIndex, bounds, text })
      }
    })
  })
  return hits
}

const globalOf = (snap, h) => {
  const o = accumulateOwnerOffset(snap, h.docIdx)
  return { x: o.x + h.bounds[0], y: o.y + h.bounds[1], cx: o.x + h.bounds[0] + h.bounds[2] / 2, cy: o.y + h.bounds[1] + h.bounds[3] / 2, w: h.bounds[2], h: h.bounds[3] }
}

try {
  await gw.connect(endpoint)
  const targets = await gw.getTargets()
  const boss = targets.find((t) => t.type === 'page' && t.url.includes('zhipin.com'))
  if (!boss) { console.error('未找到 BOSS 页面'); process.exit(2) }
  await gw.attachToTarget(boss.targetId)
  const snap = await gw.captureDomSnapshot()

  // 行标签
  for (const prefix of ['经验要求', '学历要求', '薪资待遇', '年龄', '活跃度', '性别', '近期没有看过', '求职意向']) {
    const hits = visibleHits(snap, (s) => s.startsWith(prefix))
    for (const h of hits) console.log(`LABEL ${h.text} @ ${JSON.stringify(globalOf(snap, h))} doc=${h.docIdx}`)
  }
  // 全部「本科」可见命中
  const hits = visibleHits(snap, (s) => s === '本科')
  console.log(`\n「本科」可见命中 ${hits.length} 个:`)
  for (const h of hits) console.log(`  doc=${h.docIdx} node=${h.nodeIndex} @ ${JSON.stringify(globalOf(snap, h))}`)
} finally {
  await gw.close().catch(() => {})
}
