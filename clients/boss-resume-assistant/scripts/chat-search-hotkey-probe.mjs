/**
 * 沟通页搜索唤起方式探测（手动运行，只读+键盘事件，不点击不动鼠标）。
 * 目标：找到分辨率无关的搜索唤起方式（替代固定坐标点击搜索图标）。
 * 依次试探：Ctrl+K、'/'、'f'——每次发键后抓快照 diff 是否新增 INPUT/搜索浮层。
 * 用法：node scripts/chat-search-hotkey-probe.mjs
 */
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { indexedValues } from '../dist/src/main/boss/domSnapshot.js'

const endpoint = process.argv[2] ?? 'http://127.0.0.1:9222'
const gw = new CdpGateway({ timeoutMs: 10000 })

function collectInputs(snap) {
  const out = []
  for (const doc of snap.documents) {
    if (!doc?.nodes?.nodeName || !doc?.layout) continue
    const nameByNode = new Map(indexedValues(doc.nodes.nodeName, 'nodeName'))
    doc.layout.nodeIndex.forEach((ni, li) => {
      if (String(nameByNode.get(ni) ?? '') !== 'INPUT') return
      const b = doc.layout.bounds[li]
      if (b && b[2] > 0 && b[3] > 0) out.push([Math.round(b[0]), Math.round(b[1]), Math.round(b[2]), Math.round(b[3])])
    })
  }
  return out
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await gw.connect(endpoint)
  await gw.attachToRecommendPage()
  const snap0 = await gw.captureDomSnapshot()
  const vp = snap0.documents[0]?.layout.bounds[0] ?? [0, 0, 0, 0]
  console.log(`[probe] 视口 ${vp[2]}x${vp[3]}，基准 INPUT: ${JSON.stringify(collectInputs(snap0))}`)

  const trials = [
    { label: 'Ctrl+K', keys: [
      { type: 'keyDown', key: 'Control', code: 'ControlLeft', windowsVirtualKeyCode: 17, modifiers: 2 },
      { type: 'keyDown', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers: 2 },
      { type: 'keyUp', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers: 2 },
      { type: 'keyUp', key: 'Control', code: 'ControlLeft', windowsVirtualKeyCode: 17, modifiers: 0 },
    ] },
    { label: '/', keys: [
      { type: 'keyDown', key: '/', code: 'Slash', windowsVirtualKeyCode: 191 },
      { type: 'keyUp', key: '/', code: 'Slash', windowsVirtualKeyCode: 191 },
    ] },
    { label: 'f', keys: [
      { type: 'keyDown', key: 'f', code: 'KeyF', windowsVirtualKeyCode: 70 },
      { type: 'keyUp', key: 'f', code: 'KeyF', windowsVirtualKeyCode: 70 },
    ] },
  ]

  for (const t of trials) {
    const before = collectInputs(await gw.captureDomSnapshot())
    for (const k of t.keys) await gw.dispatchKey(k)
    await sleep(1200)
    const after = collectInputs(await gw.captureDomSnapshot())
    const added = after.filter((b) => !before.some((o) => Math.abs(o[0] - b[0]) < 10 && Math.abs(o[1] - b[1]) < 10 && Math.abs(o[2] - b[2]) < 30))
    console.log(`[probe] ${t.label}: before=${before.length} after=${after.length} 新增=${JSON.stringify(added)}`)
    // 收场：Escape 关掉可能弹出的浮层，保证下一个试探干净
    await gw.dispatchKey({ type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
    await gw.dispatchKey({ type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
    await sleep(600)
  }
  console.log('[probe] 完成')
} catch (e) {
  console.error(`[probe] 失败: ${e.message}`)
  process.exit(1)
} finally {
  await gw.close().catch(() => {})
}
