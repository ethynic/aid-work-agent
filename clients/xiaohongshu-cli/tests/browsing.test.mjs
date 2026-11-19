import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chromium } from 'playwright-core'
import { chromeExecutable, XiaohongshuBrowser } from '../dist/src/browser.js'
import { collectionLimit, profileTarget } from '../dist/src/browsing.js'

const executablePath = chromeExecutable()
const origin = 'https://www.xiaohongshu.com'
const ownerId = 'a'.repeat(24)
const actorId = 'c'.repeat(24)
const firstNote = '1'.repeat(24)
const secondNote = '2'.repeat(24)
const ownerUrl = origin + '/user/profile/' + ownerId
const actorUrl = origin + '/user/profile/' + actorId
const key = 'a'.repeat(64)
const identity = '<li class="user side-bar-component"><a href="/user/profile/' + actorId + '">我</a></li>'
const userInfo = '<div class="user-info"><span class="user-name">同名用户</span><span class="user-redId">小红书号：fixture</span></div>'
const card = (id, title) => `<section class="note-item"><a class="cover mask"
  href="/user/profile/${ownerId}/${id}?xsec_token=local-navigation-token" style="display:block;width:100px;height:100px">封面</a>
  <a class="title"><span>${title}</span></a><div class="like-wrapper"><span class="count">1.2万</span></div></section>`
const comment = (id, text, nested = '') => `<div class="comment-item" id="comment-${id}">
  <div class="avatar"><a href="/user/profile/${actorId}?xsec_token=private"></a></div>
  <div class="author"><span class="name">评论者</span></div><div class="content"><span class="note-text">${text}</span></div>${nested}</div>`
const fixtureTest = (name, fn) => test(name, { skip: !executablePath }, fn)

async function fixture(t, profile, note = '', chat = '') {
  const native = await chromium.launch({ executablePath, headless: true })
  const context = await native.newContext()
  const navigations = []
  await context.route(origin + '/**', async route => {
    const url = new URL(route.request().url())
    navigations.push(url.pathname)
    const body = url.pathname === '/chat' ? chat
      : url.pathname.startsWith('/explore/') ? note : profile
    await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body })
  })
  const api = new XiaohongshuBrowser(async () => context)
  t.after(async () => { await api.close(); await native.close() })
  return { api, context, navigations }
}

test('仅接受规范官网主页和有界数量，URL 查询只用于导航', () => {
  assert.equal(profileTarget(ownerUrl + '?xsec_token=private').url, ownerUrl)
  for (const invalid of [
    'http://www.xiaohongshu.com/user/profile/' + ownerId,
    'https://www.xiaohongshu.com.evil.invalid/user/profile/' + ownerId,
    'https://user@www.xiaohongshu.com/user/profile/' + ownerId,
    ownerUrl + '/note', ownerUrl + '#private', origin + '/explore/' + firstNote,
  ]) assert.throws(() => profileTarget(invalid), { code: 'XHS_INVALID_INPUT' })
  for (const invalid of [0, 101, 1.5]) assert.throws(() => collectionLimit(invalid), { code: 'XHS_INVALID_INPUT' })
})

fixtureTest('笔记卡片按稳定 ID 去重并限量，规范结果不含站点令牌', async t => {
  const profile = identity + userInfo + card(firstNote, '首篇') + card(firstNote, '首篇')
    + card(secondNote, '次篇') + '<p>没有更多了</p>'
  const { api } = await fixture(t, profile)
  const bounded = await api.listNotes(key, ownerUrl + '?xsec_token=private', 1)
  assert.equal(bounded.complete, false)
  assert.equal(bounded.truncated, true)
  assert.equal(bounded.stop_reason, 'limit')
  assert.deepEqual(bounded.notes, [{ note_id: firstNote, url: origin + '/explore/' + firstNote,
    title: '首篇', like_count: '1.2万' }])
  assert.ok(!JSON.stringify(bounded).includes('xsec_token'))
  const more = await api.listNotes(key, ownerUrl, 2)
  assert.equal(more.complete, false)
  assert.equal(more.notes.length, 2)
})

fixtureTest('明确空列表可以返回零，未能确认列表时不能称为无笔记', async t => {
  const { api, context } = await fixture(t, identity + userInfo
    + '<div class="feeds-tab-container"><div class="empty-container"><p class="empty-text">还没有发布过笔记</p></div></div>')
  assert.deepEqual((await api.listNotes(key, ownerUrl)).notes, [])
  await context.unroute(origin + '/**')
  await context.route(origin + '/**', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
    body: identity + userInfo + '<div class="feeds-loading">加载中</div>' }))
  await assert.rejects(api.listNotes(key, ownerUrl), { code: 'XHS_PAGE_UNCONFIRMED' })
})

fixtureTest('评论通过来源主页 cover 进入，父评论与已展开回复分别归属并规范作者链接', async t => {
  const profile = identity + userInfo + card(firstNote, '首篇') + `<script>
    document.querySelector('a.cover').onclick = event => {
      event.preventDefault(); location.href='/explore/${firstNote}?xsec_token=local-only';
    };
  </script>`
  const note = identity + '<div class="note-scroller"><div class="comments-container">'
    + comment('parent', '采购需求', comment('reply', '补充回复'))
    + comment('parent', '采购需求') + '<p class="end-container"> - THE END - </p></div></div>'
  const { api, navigations } = await fixture(t, profile, note)
  const result = await api.listComments(key, ownerUrl, firstNote, 20)
  assert.equal(result.complete, true)
  assert.equal(result.comments.length, 2)
  assert.equal(result.comments[0].text, '采购需求')
  assert.equal(result.comments[1].text, '补充回复')
  assert.equal(result.comments[0].author.profile_url, actorUrl)
  assert.deepEqual(navigations, ['/user/profile/' + ownerId, '/explore/' + firstNote])
  assert.ok(!JSON.stringify(result).includes('xsec_token'))
})

fixtureTest('零评论需要可见的明确空文案，未读完标截断', async t => {
  const profile = identity + userInfo + card(firstNote, '首篇') + `<script>
    document.querySelector('a.cover').onclick = event => {
      event.preventDefault(); location.href='/explore/${firstNote}';
    };
  </script>`
  const note = identity + '<div class="note-scroller"><div class="comments-container"><p class="no-comments">暂无评论</p></div></div>'
  const { api, context } = await fixture(t, profile, note)
  const empty = await api.listComments(key, ownerUrl, firstNote, 20)
  assert.equal(empty.complete, true)
  assert.deepEqual(empty.comments, [])
  await context.unroute(origin + '/**')
  await context.route(origin + '/**', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
    body: route.request().url().includes('/explore/') ? identity + '<div class="note-scroller"><div class="comments-container">'
      + comment('one', '- THE END -') + '</div></div>' : profile }))
  const partial = await api.listComments(key, ownerUrl, firstNote, 1)
  assert.equal(partial.complete, false)
  assert.equal(partial.stop_reason, 'limit')
})

fixtureTest('会话以活动收件人 ID 核对，同昵称错目标拒绝，编辑区保持空白', async t => {
  const profile = identity + userInfo + `<button class="xhs-user-im-btn" title="发消息"
    onclick="window.open('/chat?openUid=${ownerId}')">发消息</button>`
  const chat = `<div class="xhs-im-chat-window__header-info"><span class="xhs-im-chat-window__header-name">同名用户</span></div>
    <div class="xhs-im-conv-item--active" data-conv-id="${ownerId}">同名用户</div>
    <div class="xhs-im-input-bar-editor" contenteditable="true"></div>`
  const { api, context } = await fixture(t, profile, '', chat)
  const ready = await api.openConversation(key, ownerUrl)
  assert.equal(ready.target_id, ownerId)
  assert.equal(ready.sender_id, actorId)
  assert.equal(ready.editable, true)
  const chatPage = context.pages().find(page => page.url().includes('/chat?'))
  assert.equal(await chatPage.locator('[contenteditable]').innerText(), '')
  await context.unroute(origin + '/**')
  await context.route(origin + '/**', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
    body: route.request().url().includes('/chat?') ? chat.replace('data-conv-id="' + ownerId + '"',
      'data-conv-id="' + actorId + '"') : profile }))
  await assert.rejects(api.openConversation(key, ownerUrl), { code: 'XHS_TARGET_MISMATCH' })
})

fixtureTest('未登录及人工验证分别报告，已取消请求不产生导航', async t => {
  const { api, context, navigations } = await fixture(t, '<div class="login-modal">登录</div>')
  await assert.rejects(api.listNotes(key, ownerUrl), { code: 'XHS_LOGIN_REQUIRED' })
  await context.unroute(origin + '/**')
  await context.route(origin + '/**', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '<p>安全验证</p>' }))
  await assert.rejects(api.listNotes(key, ownerUrl), { code: 'XHS_VERIFICATION_REQUIRED' })
  const controller = new AbortController()
  controller.abort()
  const before = navigations.length
  await assert.rejects(api.listNotes(key, ownerUrl, undefined, controller.signal), { code: 'XHS_CANCELLED' })
  assert.equal(navigations.length, before)
})
