/**
 * 搜索入口点击后页面响应探测（手动运行）。CDP 点击入口坐标后 diff 全部可见节点，
 * 判定弹层是否出现、弹层里输入元素的真实 nodeName（input or contenteditable DIV）。
 * 用法：node scripts/chat-entry-click-probe.mjs [pageX pageY]
 */
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { indexedValues } from '../dist/src/main/boss/domSnapshot.js'

const endpoint = process.argv[2] ?? 'http://127.0.0.1:9222'
const px = Number(process.argv[3] ?? 520)
const py = Number(process.argv[4] ?? 141)
const gw = new CdpGateway({ timeoutMs: 10000 })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function collectNodes(snap) {
  const out = []
  for (const doc of snap.documents) {
    if (!doc?.nodes?.nodeName || !doc.layout) continue
    const nameByNode = new Map(indexedValues(doc.nodes.nodeName, 'nodeName'))
    doc.layout.nodeIndex.forEach((ni, li) => {
      const b = doc.layout.bounds[li]
      if (!b || b[2] <= 0 || b[3] <= 0) return
      out.push({ nn: String(nameByNode.get(ni) ?? ''), b: [Math.round(b[0]), Math.round(b[1]), Math.round(b[2]), Math.round(b[3])] })
    })
  }
  return out
}

try {
  await gw.connect(endpoint)
  await gw.attachToRecommendPage()
  const before = collectNodes(await gw.captureDomSnapshot())
  console.log(`[probe] 点击前可见节点 ${before.length} 个；CDP 点击 (${px},${py})`)
  await gw.dispatchMouse({ type: 'mousePressed', x: px, y: py, button: 'left', clickCount: 1 })
  await gw.dispatchMouse({ type: 'mouseReleased', x: px, y: py, button: 'left', clickCount: 1 })
  await sleep(1500)
  const after = collectNodes(await gw.captureDomSnapshot())
  const key = (n) => `${n.nn}@${n.b.join(',')}`
  const beforeKeys = new Set(before.map(key))
  const added = after.filter((n) => !beforeKeys.has(key(n)))
  console.log(`[probe] 点击后可见节点 ${after.length} 个，新增 ${added.length} 个：`)
  for (const n of added.slice(0, 25)) console.log(`  + ${n.nn} [${n.b.join(',')}]`)
  console.log('[probe] 完成（若新增 0 个 = CDP 点击也唤不起弹层）')
} catch (e) {
  console.error(`[probe] 失败: ${e.message}`)
  process.exit(1)
} finally {
  await gw.close().catch(() => {})
}
