import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { chromium } from 'playwright-core'
import { chromeExecutable, XiaohongshuBrowser } from '../dist/src/browser.js'
import { validateFirstMessage } from '../dist/src/firstMessage.js'
import { FirstMessageStore } from '../dist/src/firstMessageStore.js'

const executablePath = chromeExecutable()
const fixtureTest = (name, fn) => test(name, { skip: !executablePath }, fn)
const origin = 'https://www.xiaohongshu.com'
const sender = 'c'.repeat(24)
const recipient = 'a'.repeat(24)
const other = 'b'.repeat(24)
const key = 'a'.repeat(64)
const profileUrl = id => origin + '/user/profile/' + id
const input = { profile_url: profileUrl(recipient), sender_id: sender, source_profile_url: profileUrl(other),
  note_id: '1'.repeat(24), comment_id: 'comment-fixture', reason: '评论表达企业客服接入需求', message: '想了解您提到的客服接入场景。' }
const hint = '对方关注或回复你之前，只能发送1条消息'

async function fixture(t, options = {}) {
  const root = options.root ?? await mkdtemp(join(tmpdir(), 'aid-xhs-first-message-'))
  const native = await chromium.launch({ executablePath, headless: true })
  const context = await native.newContext()
  let sends = 0
  let navigations = 0
  await context.exposeBinding('observeSend', () => { sends++; options.afterEnter?.() })
  await context.route(origin + '/**', async route => {
    navigations++
    const profile = `<li class="user side-bar-component"><a href="/user/profile/${options.sender ?? sender}">我</a></li>
      <div class="user-info"><span class="user-name">同名用户</span><span class="user-redId">fixture</span></div>
      <button class="xhs-user-im-btn" title="发消息" onclick="window.open('/chat?openUid=${recipient}')">发消息</button>`
    const chat = `<div class="xhs-im-chat-window__header-info"><span class="xhs-im-chat-window__header-name">同名用户</span></div>
      <div class="xhs-im-conv-item--active" data-conv-id="${options.target ?? recipient}">同名用户</div>
      ${options.skeleton ? '<div class="xhs-im-msg-skeleton">加载中</div>' : ''}
      <div class="xhs-im-msg-list">
        <div class="chat-item" data-message-id="hint-id" data-content-type="hint"><div class="chat-item__pure"><div class="xhs-im-hint__text">${options.hint ?? hint}</div></div></div>
        ${options.history ? '<div class="chat-item" data-message-id="prior"><div class="chat-item__bubble--me">既有消息</div></div>' : ''}
        <div class="xhs-im-msg-list__bottom"></div>
      </div>
      <div class="xhs-im-input-bar-editor" contenteditable="${options.disabled ? 'false' : 'true'}">${options.draft ?? ''}</div>
      <script>
        const editor = document.querySelector('.xhs-im-input-bar-editor');
        editor.addEventListener('input', () => {
          ${options.switchOnInput ? "document.querySelector('[data-conv-id]').dataset.convId='" + other + "';" : ''}
          ${options.historyOnInput ? "document.querySelector('.xhs-im-msg-list').insertAdjacentHTML('afterbegin', '<div class=chat-item data-message-id=incoming>客户刚发来消息</div>');" : ''}
          ${options.changeDraft ? "editor.textContent='人工修改草稿';" : ''}
          ${options.switchSenderOnInput ? "window.opener.document.querySelector('li.user a').href='/user/profile/" + other + "';" : ''}
        });
        editor.addEventListener('keydown', event => {
          if (event.key !== 'Enter' || event.shiftKey) return;
          event.preventDefault(); window.observeSend();
          const row = document.createElement('div'); row.className='chat-item'; row.dataset.messageId='local-uuid';
          row.dataset.storeId=${JSON.stringify(options.storeId ?? '900719925474099312345')};
          const bubble = document.createElement('div'); bubble.className='chat-item__bubble--me'; bubble.textContent=editor.innerText;
          row.appendChild(bubble);
          ${options.pending ? "const status=document.createElement('span');status.className='chat-item__status-loading';row.appendChild(status);" : ''}
          ${options.failed ? "const status=document.createElement('span');status.className='chat-item__status-btn--failed';row.appendChild(status);" : ''}
          document.querySelector('.xhs-im-msg-list').appendChild(row); editor.textContent='';
        });
      </script>`
    await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
      body: new URL(route.request().url()).pathname === '/chat' ? chat : profile })
  })
  const store = options.store ?? new FirstMessageStore(root)
  const api = new XiaohongshuBrowser(async () => context, store)
  t.after(async () => { await api.close(); await native.close(); if (!options.root) await rm(root, { recursive: true, force: true }) })
  return { api, store, root, context, native, get sends() { return sends }, get navigations() { return navigations } }
}

test('首联入参限定官网和来源，正文按网页发送语义归一化', () => {
  assert.equal(validateFirstMessage({ ...input, message: ' \r\n正文\r\n第二行 \r\n' }).message, '正文\n第二行')
  for (const invalid of [
    { profile_url: 'https://example.invalid/' }, { source_profile_url: 'file:///local' },
    { sender_id: '../escape' }, { sender_id: recipient }, { note_id: 'wrong' },
    { comment_id: '../escape' }, { reason: ' ' }, { message: ' ' }, { message: 'x'.repeat(2001) },
  ]) assert.throws(() => validateFirstMessage({ ...input, ...invalid }), { code: 'XHS_INVALID_INPUT' })
})

fixtureTest('只发送一次并以服务端正数编号确认，回执保留来源且重启不重放', async t => {
  const root = await mkdtemp(join(tmpdir(), 'aid-xhs-restart-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const first = await fixture(t, { root })
  const result = await first.api.sendFirstMessage(key, { ...input, message: '  ' + input.message + '\n' })
  assert.equal(result.success, true)
  assert.equal(result.effect, 'applied')
  assert.equal(result.retryable, false)
  assert.equal(result.data.status, 'sent')
  assert.equal(result.data.sender_id, sender)
  assert.equal(result.data.target_id, recipient)
  assert.equal(result.data.message, input.message)
  assert.equal(result.data.store_id, '900719925474099312345')
  assert.equal(result.data.source_profile_url, input.source_profile_url)
  assert.equal(first.sends, 1)
  const reserved = JSON.parse(await readFile(join(root, result.data.record_id + '.json'), 'utf8'))
  assert.equal(reserved.status, 'unknown')
  const receipt = await new FirstMessageStore(root).find(result.data.record_id)
  assert.equal(receipt.status, 'sent')
  assert.equal(receipt.local_receipt, 'saved')
  await first.api.close(); await first.native.close()
  const restarted = await fixture(t, { root })
  const blocked = await restarted.api.sendFirstMessage(key, input)
  assert.equal(blocked.code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
  assert.equal(blocked.effect, 'none')
  assert.equal(blocked.data.status, 'sent')
  assert.equal(restarted.sends, 0)
  assert.equal(restarted.navigations, 0)
})

fixtureTest('发送者、同名错会话、已有历史、陌生限制和草稿在预留前拒绝', async t => {
  for (const [options, code] of [
    [{ sender: other }, 'XHS_SENDER_MISMATCH'],
    [{ target: other }, 'XHS_TARGET_MISMATCH'],
    [{ history: true }, 'XHS_EXISTING_CONVERSATION'],
    [{ hint: '对方设置了隐私，无法发送消息' }, 'XHS_FIRST_CONTACT_UNCONFIRMED'],
    [{ draft: '人工草稿' }, 'XHS_EXISTING_DRAFT'],
    [{ disabled: true }, 'XHS_MESSAGE_BLOCKED'],
  ]) {
    const f = await fixture(t, options)
    const result = await f.api.sendFirstMessage(key, input)
    assert.equal(result.code, code)
    assert.equal(result.effect, 'none')
    assert.equal(result.data.status, 'not_sent')
    assert.equal(f.sends, 0)
    assert.deepEqual(await readdir(f.root), [])
  }
})

fixtureTest('输入后切换双方账号、出现新消息或人工改稿都不能触发 Enter', async t => {
  for (const options of [{ switchOnInput: true }, { switchSenderOnInput: true }, { historyOnInput: true }, { changeDraft: true }]) {
    const f = await fixture(t, options)
    const result = await f.api.sendFirstMessage(key, input)
    assert.equal(result.effect, 'unknown')
    assert.equal(result.retryable, false)
    assert.equal(f.sends, 0)
    const repeated = await f.api.sendFirstMessage(key, input)
    assert.equal(repeated.code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
    assert.equal(f.sends, 0)
  }
})

fixtureTest('仅客户端 UUID、零存储号和未成功状态不能称为发送确认且不重放', async t => {
  for (const options of [{ storeId: '' }, { storeId: '0' }, { pending: true }, { failed: true }]) {
    const f = await fixture(t, options)
    const result = await f.api.sendFirstMessage(key, input)
    assert.equal(result.effect, 'unknown')
    assert.equal(result.success, false)
    assert.equal(result.retryable, false)
    assert.equal(result.data.status, 'unknown')
    assert.equal(f.sends, 1)
    const blocked = await f.api.sendFirstMessage(key, input)
    assert.equal(blocked.code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
    assert.equal(f.sends, 1)
    assert.equal((await new FirstMessageStore(f.root).find(result.data.record_id)).status, 'unknown')
  }
})

fixtureTest('Enter 后取消保留未知占用，不因取消自动重发', async t => {
  const controller = new AbortController()
  const f = await fixture(t, { afterEnter: () => controller.abort() })
  const result = await f.api.sendFirstMessage(key, input, controller.signal)
  assert.equal(result.effect, 'unknown')
  assert.equal(result.retryable, false)
  assert.equal(f.sends, 1)
  assert.equal((await f.api.sendFirstMessage(key, input)).code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
  assert.equal(f.sends, 1)
  await f.api.close(); await f.native.close()
  const restarted = await fixture(t, { root: f.root })
  assert.equal((await restarted.api.sendFirstMessage(key, input)).code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
  assert.equal(restarted.sends, 0)
  assert.equal(restarted.navigations, 0)
})

fixtureTest('历史仍在加载时不发送，预留后取消也不能触发 Enter', async t => {
  const loading = await fixture(t, { skeleton: true })
  const unresolved = await loading.api.sendFirstMessage(key, input)
  assert.equal(unresolved.effect, 'none')
  assert.equal(unresolved.data.status, 'not_sent')
  assert.equal(loading.sends, 0)
  assert.deepEqual(await readdir(loading.root), [])
  const controller = new AbortController()
  const f = await fixture(t)
  const reserve = f.store.reserve.bind(f.store)
  f.store.reserve = async record => {
    const result = await reserve(record)
    controller.abort()
    return result
  }
  const cancelled = await f.api.sendFirstMessage(key, input, controller.signal)
  assert.equal(cancelled.effect, 'unknown')
  assert.equal(f.sends, 0)
  assert.equal((await f.api.sendFirstMessage(key, input)).code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
  assert.equal(f.sends, 0)
})

fixtureTest('站点已确认而本机终态写失败时如实回报已发送，预留仍阻重', async t => {
  const root = await mkdtemp(join(tmpdir(), 'aid-xhs-receipt-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const store = new FirstMessageStore(root)
  store.finish = async () => { throw new Error('fixture disk failure') }
  const f = await fixture(t, { root, store })
  const result = await f.api.sendFirstMessage(key, input)
  assert.equal(result.effect, 'applied')
  assert.equal(result.success, true)
  assert.equal(result.code, 'XHS_RECEIPT_UNCONFIRMED')
  assert.equal(result.data.local_receipt, 'unconfirmed')
  assert.equal(result.data.status, 'sent')
  assert.equal(f.sends, 1)
  assert.equal((await new FirstMessageStore(root).find(result.data.record_id)).status, 'unknown')
  assert.equal((await f.api.sendFirstMessage(key, input)).code, 'XHS_CONTACT_ALREADY_ATTEMPTED')
  assert.equal(f.sends, 1)
})

test('独占预留跨实例防并发，账号和连接 Profile 分别隔离', async () => {
  const root = await mkdtemp(join(tmpdir(), 'aid-xhs-reserve-'))
  try {
    const first = new FirstMessageStore(root)
    const second = new FirstMessageStore(root)
    const recordId = first.recordId(key, sender, recipient)
    assert.notEqual(recordId, first.recordId('b'.repeat(64), sender, recipient))
    assert.notEqual(recordId, first.recordId(key, other, recipient))
    const record = { schema_version: 1, record_id: recordId, status: 'unknown' }
    const outcomes = await Promise.all([first.reserve(record), second.reserve(record)])
    assert.deepEqual(outcomes.sort(), [false, true])
    assert.equal((await second.find(recordId)).status, 'unknown')
  } finally { await rm(root, { recursive: true, force: true }) }
})
