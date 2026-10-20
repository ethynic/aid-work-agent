import { PagePostconditionError } from '../src/main/operations/pageOperation.js'
import assert from 'node:assert/strict'
import test from 'node:test'
import { PageNavigator, NavError } from '../src/main/boss/PageNavigator.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
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

function deps(
  snaps: DomSnapshot[],
  urls: string[],
  opts: { navigateUrls?: string[]; noPageNavigate?: boolean; clickThrows?: Error } = {},
) {
  let snapCall = 0
  let urlCall = 0
  const clicks: ClickPoint[] = []
  const navigatedTo: string[] = []
  const baseDeps = {
    snapshot: async () => snaps[Math.min(snapCall++, snaps.length - 1)]!,
    click: async (p: ClickPoint) => {
      if (opts.clickThrows) throw opts.clickThrows
      clicks.push(p)
    },
    getUrl: async () => urls[Math.min(urlCall++, urls.length - 1)]!,
    sleep: async () => {},
  }
  // 页内导航兜底（可选能力）：记录导航目标，可选地把后续 URL 切到导航后的地址；
  // noPageNavigate 模拟旧测试替身/精简会话（无导航能力）
  const d = opts.noPageNavigate
    ? baseDeps
    : {
        ...baseDeps,
        pageNavigate: async (url: string) => {
          navigatedTo.push(url)
          if (opts.navigateUrls) urls.push(...opts.navigateUrls)
        },
      }
  return { clicks, navigatedTo, deps: d }
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
  assert.equal(result.via, 'menu')
  // 点的必须是侧栏那个 (114,228)，不是顶部诱饵 (324,30)
  assert.deepEqual(clicks, [{ x: 114, y: 228 }])
})

test('已在目标页：跳过点击（幂等，不打扰列表滚动位置）', async () => {
  const { clicks, deps: d } = deps([navSnap([MENU_RECOMMEND])], [RECOMMEND_URL])
  const nav = new PageNavigator(d)
  const result = await nav.navigate('recommend')
  assert.equal(result.clicked, false)
  assert.equal(result.via, 'already')
  assert.equal(clicks.length, 0)
})

test('页面没有菜单文案 → 直接报错交共用恢复，零普通重试', async () => {
  const { clicks, navigatedTo, deps: d } = deps([navSnap([])], [CHAT_URL], { navigateUrls: [RECOMMEND_URL] })
  await assert.rejects(new PageNavigator(d).navigate('recommend'), NavError)
  assert.deepEqual(clicks, [])
  assert.deepEqual(navigatedTo, [])
})

test('菜单找不到且直跳后仍未到达（未登录被重定向登录页）→ NavError 报登录提示', async () => {
  const LOGIN_URL = 'https://www.zhipin.com/web/user/?ka=header-login'
  const { deps: d } = deps([navSnap([])], [CHAT_URL, CHAT_URL], { navigateUrls: [LOGIN_URL] })
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('recommend'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /登录/)
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

test('侧栏区最左命中并列 → 直接报错交共用恢复，零普通重试', async () => {
  const dup = { text: '沟通', bounds: [55, 400, 80, 32] as [number, number, number, number] }
  const { clicks, navigatedTo, deps: d } = deps([navSnap([{ text: '沟通', bounds: [53, 356, 80, 32] }, dup])], [RECOMMEND_URL])
  await assert.rejects(new PageNavigator(d).navigate('chat'), /最左命中不唯一/)
  assert.deepEqual(clicks, [])
  assert.deepEqual(navigatedTo, [])
})

test('无 pageNavigate（旧测试替身）且菜单定位失败 → 保持原 fail-loud 报错', async () => {
  const { clicks, navigatedTo, deps: d } = deps([navSnap([])], [CHAT_URL], { noPageNavigate: true })
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('recommend'), (e: unknown) => {
    assert.ok(e instanceof NavError)
    assert.match(e.message, /找不到菜单文案/)
    return true
  })
  assert.equal(clicks.length, 0)
  assert.equal(navigatedTo.length, 0)
})

test('点击执行失败（WinClickError → 直接报错交共用恢复，零普通重试', async () => {
  const failure = new WinClickError('渲染子窗口不可见')
  const { navigatedTo, deps: d } = deps([navSnap([MENU_RECOMMEND])], [CHAT_URL], { clickThrows: failure })
  await assert.rejects(new PageNavigator(d).navigate('recommend'), (error: unknown) => error === failure)
  assert.deepEqual(navigatedTo, [])
})

test('点击执行失败且直跳后仍未到达 → 直接报错交共用恢复，零普通重试', async () => {
  const failure = new WinClickError('[BOSS_INPUT_UNKNOWN] 输入结果未知')
  const { navigatedTo, deps: d } = deps([navSnap([MENU_RECOMMEND])], [CHAT_URL], { clickThrows: failure })
  await assert.rejects(new PageNavigator(d).navigate('recommend'), (error: unknown) => error === failure)
  assert.deepEqual(navigatedTo, [])
})

test('snapshot 抛基础设施异常（非 NavError/WinClickError）→ 原样上抛不吞，不盲跳', async () => {
  const boom = new Error('CDP WebSocket closed')
  const { navigatedTo, deps: d } = deps([navSnap([])], [CHAT_URL])
  // 覆写 snapshot 抛基础设施异常
  ;(d as unknown as { snapshot: () => Promise<never> }).snapshot = async () => {
    throw boom
  }
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('recommend'), (e: unknown) => {
    assert.equal(e, boom) // 原异常原样上抛
    return true
  })
  assert.equal(navigatedTo.length, 0) // 不走兜底
})

test('点击后 URL 未跳转（点击被拦截）→ 先直跳兜底，兜底也失败才 fail-loud', async () => {
  const { clicks, navigatedTo, deps: d } = deps([navSnap([MENU_RECOMMEND])], [CHAT_URL, CHAT_URL])
  const nav = new PageNavigator(d)
  await assert.rejects(nav.navigate('recommend'), (e: unknown) => {
    assert.ok(e instanceof PagePostconditionError)
    assert.match(e.message, /直跳/)
    return true
  })
  assert.equal(clicks.length, 1)
  assert.equal(navigatedTo.length, 1) // 点击未生效后确实尝试了直跳
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
  assert.equal(result.via, 'already')
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
  assert.equal(result.via, 'url-jump')
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
    assert.ok(e instanceof PagePostconditionError)
    assert.match(e.message, /直跳/)
    return true
  })
  assert.equal(navigatedTo.length, 1)
})

test('不在 /web/chat 区（职位详情页）点击未生效 → 直跳兜底成功（2026-09-18 扩展，此前 fail-loud）', async () => {
  const menuChat = { text: '沟通', bounds: [53, 356, 80, 32] as [number, number, number, number] }
  const JOB_URL = 'https://www.zhipin.com/job_detail/abc.html'
  const { clicks, navigatedTo, deps: d } = deps(
    [navSnap([menuChat])],
    [JOB_URL, JOB_URL],
    { navigateUrls: [CHAT_URL] },
  )
  const nav = new PageNavigator(d)
  const r = await nav.navigate('chat')
  assert.equal(r.clicked, true)
  assert.equal(r.via, 'url-jump')
  assert.equal(clicks.length, 1)
  assert.equal(navigatedTo.length, 1)
})
