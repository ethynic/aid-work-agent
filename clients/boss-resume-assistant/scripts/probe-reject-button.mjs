// 探针：沟通页「不合适」按钮定位检查（只读，不点击）
import { CdpGateway } from '../dist/src/main/cdp/CdpGateway.js'
import { findNodesByString, accumulateOwnerOffset, boundsCenter } from '../dist/src/main/boss/domSnapshot.js'

const gw = new CdpGateway({ timeoutMs: 10000 })
await gw.connect('http://127.0.0.1:9222')
await gw.attachToRecommendPage()
const snap = await gw.captureDomSnapshot()

const idxs = []
snap.strings.forEach((s, i) => {
  if (s.trim() === '不合适') idxs.push(i)
})
console.log(`「不合适」string 下标: [${idxs.join(', ')}]`)
for (const si of idxs) {
  snap.documents.forEach((doc, di) => {
    for (const { bounds } of findNodesByString(doc, si)) {
      const off = accumulateOwnerOffset(snap, di)
      const c = boundsCenter(bounds)
      console.log(`  doc${di} bounds=[${bounds.join(',')}] 中心=(${off.x + c.x}, ${off.y + c.y}) 滚动=(${doc.scrollOffsetX ?? 0},${doc.scrollOffsetY ?? 0})`)
    }
  })
}
// 顺便看「确定」和原因选项
for (const t of ['确定', '取消', '经验不匹配', '学历不匹配']) {
  console.log(`「${t}」在 strings 中: ${snap.strings.some((s) => s.trim() === t)}`)
}
await gw.close()
