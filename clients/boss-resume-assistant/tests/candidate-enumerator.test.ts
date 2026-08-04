import assert from 'node:assert/strict'
import test from 'node:test'
import {
  enumerateCandidates,
  looksLikeCandidateName,
} from '../src/main/workflow/candidateEnumerator.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

/** 构造单 document snapshot：每个 item 一个有布局的文本节点 */
function snapWith(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const strings: string[] = []
  const nodeValue: number[] = []
  const layoutNodeIndex: number[] = []
  const boundsArr: number[][] = []
  items.forEach((it, nodeIdx) => {
    let si = strings.findIndex((s) => s === it.text)
    if (si < 0) {
      strings.push(it.text)
      si = strings.length - 1
    }
    nodeValue.push(si)
    layoutNodeIndex.push(nodeIdx)
    boundsArr.push([...it.bounds])
  })
  return {
    strings,
    documents: [
      { nodes: { nodeValue, contentDocumentIndex: [] }, layout: { nodeIndex: layoutNodeIndex, bounds: boundsArr } },
    ],
  }
}

const VIEWPORT = { width: 1280, height: 800 }

test('姓名模式识别：2~4 汉字，排除 UI 词与写动作文案', () => {
  assert.equal(looksLikeCandidateName('张三'), true)
  assert.equal(looksLikeCandidateName('欧阳娜娜'), true)
  assert.equal(looksLikeCandidateName('阿不都·外力'), false) // 6 字符超上限
  assert.equal(looksLikeCandidateName('推荐'), false)
  assert.equal(looksLikeCandidateName('打招呼'), false)
  assert.equal(looksLikeCandidateName('继续沟通'), false)
  assert.equal(looksLikeCandidateName('张'), false) // 单字
  assert.equal(looksLikeCandidateName('3年经验'), false)
  assert.equal(looksLikeCandidateName(''), false)
})

test('枚举：提取安全区内候选人，排除 UI 词与非安全区节点', () => {
  const snap = snapWith([
    { text: '张三', bounds: [100, 200, 60, 24] },
    { text: '推荐', bounds: [100, 300, 60, 24] }, // UI 词
    { text: '打招呼', bounds: [400, 400, 80, 32] }, // 写动作
    { text: '李四', bounds: [100, 10, 60, 24] }, // 顶部 header 区（y<150）
    { text: '王五', bounds: [100, 795, 60, 24] }, // 底部安全区外（center y=807 > 780）
    { text: '3年经验', bounds: [200, 200, 80, 24] }, // 非姓名
  ])
  const result = enumerateCandidates(snap, { viewport: VIEWPORT })
  assert.deepEqual(result.map((c) => c.name), ['张三'])
})

test('枚举去重：同名只出现一次（歧义交给 locate 阶段 fail-loud）', () => {
  const snap = snapWith([
    { text: '张三', bounds: [100, 200, 60, 24] },
    { text: '张三', bounds: [100, 400, 60, 24] },
  ])
  const result = enumerateCandidates(snap, { viewport: VIEWPORT })
  assert.equal(result.length, 1)
  assert.equal(result[0]!.name, '张三')
})

test('bandText：收集同纵向行带内的其它文本', () => {
  const snap = snapWith([
    { text: '张三', bounds: [100, 200, 60, 24] },
    { text: '本科', bounds: [200, 205, 50, 24] },
    { text: '5年经验', bounds: [300, 210, 80, 24] },
    { text: '很远文本不在带内', bounds: [100, 600, 200, 24] },
  ])
  const [c] = enumerateCandidates(snap, { viewport: VIEWPORT })
  assert.ok(c)
  assert.ok(c!.bandText.includes('本科'))
  assert.ok(c!.bandText.includes('5年经验'))
  assert.ok(!c!.bandText.includes('很远文本不在带内'))
})
