import assert from 'node:assert/strict'
import test from 'node:test'
import { PageNavigator, NavError } from '../src/main/boss/PageNavigator.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/**
 * 构造单文档 snapshot：node 0 根（视口 1917x1905），items 为各文本节点 bounds。
 * 真机参考（2026-08-05 沟通页实测）：侧栏「推荐牛人」cx=114、「沟通」cx=93；
 * 顶部诱饵「沟通」cx=303、「推荐牛人」cx=324（必须被 x<200 侧栏区过滤）。
 */
function navSnap(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const idx = [0, ...items.map((_, i) => i + 1)]
  // 相同文案共享一个 string table 下标（与真实 DOMSnapshot interning 一致）
  const stringIds = items.map((it) => 1 + items.findIndex((o) => o.text === it.text))
  return {
    strings: ['', ...items.map((it) => it.text).filter((t, i, arr) => arr.indexOf(t) === i)],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: [0, ...stringIds] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: {
          nodeIndex: idx,
          bounds: [[0, 0, 1917, 1905], ...items.map((it) => it.bounds)],
        },
      },
    ],
  }
}

function deps(snaps: DomSnapshot[], urls: string[]) {
  let snapCall = 0
  let urlCall = 0
  const clicks: ClickPoint[] = []
  return {
    clicks,
    deps: {
      snapshot: async () => snaps[Math.min(snapCall++, snaps.length - 1)]!,
      click: async (p: ClickPoint) => {
        clicks.push(p)
      },
      getUrl: async () => urls[Math.min(urlCall++, urls.length - 1)]!,
      sleep: async () => {},
    },
  }
}

const CHAT_URL = 'https://www.zhipin.com/web/chat/index'
const RECOMMEND_URL = 'https://www.zhipin.com/web/chat/recommend'
const MENU_RECOMMEND = { text: '推荐牛人', bounds: [64, 212, 100, 32] as [number, number, number, number] } // 中心 (114,228)
const DECOY_RECOMMEND = { text: '推荐牛人', bounds: [284, 14, 80, 32] as [number, number, number, number] } // 顶部诱饵 (324,30)

test('从沟通页跳推荐牛人：点侧栏菜单（顶部同文案诱饵被排除），URL 校验通过', async () => {
  const { clicks, deps: d } = deps(
    [navSnap([MENU_RECOMMEND, DECOY_RECOMMEND])],
    [CHAT_URL, RECOMMEND_URL],
  )
  const nav = new PageNavigator(d)
  const result = await nav.navigate('recommend')
  assert.equal(result.clicked, true)
  // 点的必须是侧栏那个 (114,228)，不是顶部诱饵 (324,30)
  assert.deepEqual(clicks, [{ x: 114, y: 228 }])
})

test('已在目标页：跳过点击（幂等，不打扰列表滚动位置）', async () => {
  const { clicks, deps: d } = deps([navSnap([MENU_RECOMMEND])], [RECOMMEND_URL])
  const nav = new PageNavigator(d)
  const result = await nav.navigate('recommend')
  assert.equal(result.clicked, false)
  assert.equal(clicks.length, 0)
})

test('页面没有菜单文案（未登录/页面异常）→ NavError，不盲点', async () => {
  const { deps: d } = deps([navSnap([])], [CHAT_URL])
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('recommend'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /推荐牛人/)
    return true
  })
})

test('侧栏区命中多个同文案（布局异常）→ 无法唯一确定，fail-loud', async () => {
  const dup = { text: '沟通', bounds: [60, 400, 80, 32] as [number, number, number, number] }
  const { clicks, deps: d } = deps([navSnap([{ text: '沟通', bounds: [53, 356, 80, 32] }, dup])], [RECOMMEND_URL, RECOMMEND_URL])
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('chat'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /无法唯一确定/)
    return true
  })
  assert.equal(clicks.length, 0)
})

test('点击后 URL 未跳转（点击被拦截/页面结构变了）→ fail-loud 报当前 URL', async () => {
  const { clicks, deps: d } = deps([navSnap([MENU_RECOMMEND])], [CHAT_URL, CHAT_URL])
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('recommend'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /未跳转/)
    return true
  })
  assert.equal(clicks.length, 1)
})

test('跳转沟通页同理：侧栏「沟通」命中，顶部「沟通」诱饵被排除', async () => {
  const menuChat = { text: '沟通', bounds: [53, 356, 80, 32] as [number, number, number, number] } // (93,372)
  const decoyChat = { text: '沟通', bounds: [263, 14, 80, 32] as [number, number, number, number] } // (303,30)
  const { clicks, deps: d } = deps([navSnap([menuChat, decoyChat])], [RECOMMEND_URL, CHAT_URL])
  const nav = new PageNavigator(d)
  const result = await nav.navigate('chat')
  assert.equal(result.clicked, true)
  assert.deepEqual(clicks, [{ x: 93, y: 372 }])
})
