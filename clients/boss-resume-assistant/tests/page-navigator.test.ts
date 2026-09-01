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

function deps(snaps: DomSnapshot[], urls: string[], opts: { navigateUrls?: string[] } = {}) {
  let snapCall = 0
  let urlCall = 0
  const clicks: ClickPoint[] = []
  const navigatedTo: string[] = []
  return {
    clicks,
    navigatedTo,
    deps: {
      snapshot: async () => snaps[Math.min(snapCall++, snaps.length - 1)]!,
      click: async (p: ClickPoint) => {
        clicks.push(p)
      },
      getUrl: async () => urls[Math.min(urlCall++, urls.length - 1)]!,
      // 页内导航兜底（可选能力）：记录导航目标，可选地把后续 URL 切到导航后的地址
      pageNavigate: async (url: string) => {
        navigatedTo.push(url)
        if (opts.navigateUrls) urls.push(...opts.navigateUrls)
      },
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

test('侧栏区命中多个同文案但 x 分散 → 取最左命中（2026-09-01 相对化：诱饵/真菜单共存是宽窗口常态，最左恒为真菜单）', async () => {
  const decoy = { text: '沟通', bounds: [60, 400, 80, 32] as [number, number, number, number] } // cx=100
  const { clicks, deps: d } = deps([navSnap([{ text: '沟通', bounds: [53, 356, 80, 32] }, decoy])], [RECOMMEND_URL, CHAT_URL])
  const nav = new PageNavigator(d)
  const r = await nav.navigate('chat')
  assert.equal(r.clicked, true)
  // 点的是最左命中（menu cx=93），诱饵（cx=100）被排除
  assert.deepEqual(clicks, [{ x: 93, y: 372 }])
})

test('侧栏区最左命中并列（Δx<5，布局异常）→ 无法确定，fail-loud', async () => {
  const dup = { text: '沟通', bounds: [55, 400, 80, 32] as [number, number, number, number] } // cx=95，与 menu cx=93 并列
  const { clicks, deps: d } = deps([navSnap([{ text: '沟通', bounds: [53, 356, 80, 32] }, dup])], [RECOMMEND_URL, RECOMMEND_URL])
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('chat'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /不唯一/)
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

test('已在沟通页执行 goto chat：幂等跳过点击（URL 已是 /web/chat/index）', async () => {
  const menuChat = { text: '沟通', bounds: [53, 356, 80, 32] as [number, number, number, number] }
  const { clicks, navigatedTo, deps: d } = deps([navSnap([menuChat])], [CHAT_URL])
  const nav = new PageNavigator(d)
  const result = await nav.navigate('chat')
  assert.equal(result.clicked, false)
  assert.equal(clicks.length, 0)
  assert.equal(navigatedTo.length, 0) // 不点击也不导航
})

test('推荐页点「沟通」URL 未变（SPA 不触发路由）→ /web/chat 区内 CDP 页内导航兜底', async () => {
  const menuChat = { text: '沟通', bounds: [53, 356, 80, 32] as [number, number, number, number] }
  // URL 序列：点击前=推荐页 → 点击后仍=推荐页 → 兜底导航后=沟通页
  const { clicks, navigatedTo, deps: d } = deps(
    [navSnap([menuChat])],
    [RECOMMEND_URL, RECOMMEND_URL],
    { navigateUrls: [CHAT_URL] },
  )
  const nav = new PageNavigator(d)
  const result = await nav.navigate('chat')
  assert.equal(result.clicked, true)
  assert.deepEqual(navigatedTo, ['https://www.zhipin.com/web/chat/index'])
  assert.equal(clicks.length, 1) // 先点了菜单，兜底在点击未生效之后
})

test('兜底导航后 URL 仍未变 → fail-loud 报当前 URL', async () => {
  const menuChat = { text: '沟通', bounds: [53, 356, 80, 32] as [number, number, number, number] }
  const { navigatedTo, deps: d } = deps(
    [navSnap([menuChat])],
    [RECOMMEND_URL, RECOMMEND_URL],
    { navigateUrls: [RECOMMEND_URL] }, // 导航后仍不在目标页
  )
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('chat'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /未跳转/)
    return true
  })
  assert.equal(navigatedTo.length, 1)
})

test('点击后 URL 未变但不在 /web/chat 区（职位详情页）→ 不兜底，fail-loud', async () => {
  const menuChat = { text: '沟通', bounds: [53, 356, 80, 32] as [number, number, number, number] }
  const JOB_URL = 'https://www.zhipin.com/job_detail/abc.html'
  const { navigatedTo, deps: d } = deps([navSnap([menuChat])], [JOB_URL, JOB_URL])
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('chat'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    return true
  })
  assert.equal(navigatedTo.length, 0)
})
