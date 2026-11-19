import type { BrowserContext, Locator, Page } from 'playwright-core'
import { assertConversationTarget, noteId, openNativeConversation, profileTarget, readSenderId } from './browsing.js'
import { BrowserFailure } from './errors.js'
import { FirstMessageStore, type FirstMessageRecord } from './firstMessageStore.js'

const FIRST_MESSAGE_HINT = '对方关注或回复你之前，只能发送1条消息'
const WAIT_MS = 8000

export interface FirstMessageInput {
  profile_url: string
  sender_id: string
  source_profile_url: string
  note_id: string
  comment_id: string
  reason: string
  message: string
}

export interface FirstMessageOutcome {
  success: boolean
  code: string
  message: string
  effect: 'none' | 'unknown' | 'applied'
  retryable: false
  data: FirstMessageRecord
}

export function validateFirstMessage(input: FirstMessageInput): FirstMessageInput {
  const target = profileTarget(input.profile_url)
  const source = profileTarget(input.source_profile_url)
  const sender = profileTarget('https://www.xiaohongshu.com/user/profile/' + input.sender_id).id
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(input.comment_id)
    || !input.reason.trim() || input.reason.length > 2000
    || !input.message.trim() || input.message.length > 2000 || input.message.includes('\0')) {
    throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的评论来源、联系理由和私信正文')
  }
  if (sender === target.id) throw new BrowserFailure('XHS_INVALID_INPUT', '首联收件人必须是另一位用户')
  return { ...input, profile_url: target.navigationUrl, source_profile_url: source.url,
    sender_id: sender, note_id: noteId(input.note_id), message: input.message.replace(/\r\n/g, '\n').trim() }
}

function outcome(data: FirstMessageRecord, code: string, message: string,
  effect: FirstMessageOutcome['effect'] = 'none'): FirstMessageOutcome {
  return { success: effect === 'applied', code, message, effect, retryable: false, data }
}

async function assertSender(page: Page, expected: string): Promise<void> {
  if (await readSenderId(page) !== expected) {
    throw new BrowserFailure('XHS_SENDER_MISMATCH', '当前小红书账号与指定发送账号不一致')
  }
}

function editorFor(chat: Page, targetId: string): Locator {
  return chat.locator('body:has(.xhs-im-conv-item--active[data-conv-id="' + targetId + '"])'
    + ' .xhs-im-input-bar-editor[contenteditable="true"]').first()
}

async function assertFirstContact(chat: Page, targetId: string, draft = ''): Promise<Locator> {
  await chat.locator('.xhs-im-msg-skeleton').waitFor({ state: 'hidden', timeout: WAIT_MS })
  const list = chat.locator('.xhs-im-msg-list').first()
  await list.waitFor({ timeout: WAIT_MS })
  await list.locator('.xhs-im-msg-list__bottom').waitFor({ state: 'attached', timeout: WAIT_MS })
  const hints = list.locator('.xhs-im-hint__text').filter({ visible: true })
  await hints.first().waitFor({ timeout: WAIT_MS })
  if (await hints.count() !== 1 || (await hints.innerText()).trim() !== FIRST_MESSAGE_HINT) {
    throw new BrowserFailure('XHS_FIRST_CONTACT_UNCONFIRMED', '网页没有确认可发送首条私信，请人工处理')
  }
  const rows = list.locator(':scope > .chat-item')
  if (await rows.count() !== 1 || await rows.locator('.chat-item__pure .xhs-im-hint__text').count() !== 1) {
    throw new BrowserFailure('XHS_EXISTING_CONVERSATION', '会话已有消息，请由人工接手')
  }
  await assertConversationTarget(chat, targetId)
  const editor = editorFor(chat, targetId)
  if (!await editor.isVisible() || !await editor.isEditable()) {
    throw new BrowserFailure('XHS_MESSAGE_BLOCKED', '网页当前不允许编辑私信')
  }
  if ((await editor.innerText()).replace(/\r\n/g, '\n') !== draft) {
    throw new BrowserFailure(draft ? 'XHS_DRAFT_CHANGED' : 'XHS_EXISTING_DRAFT', '会话草稿与预期不一致，请由人工处理')
  }
  return editor
}

async function confirmedMessage(chat: Page, targetId: string, message: string, previous: string[], signal?: AbortSignal) {
  const until = Date.now() + WAIT_MS
  while (Date.now() < until) {
    signal?.throwIfAborted()
    await assertConversationTarget(chat, targetId)
    const match = await chat.locator('.xhs-im-msg-list > .chat-item[data-message-id]').evaluateAll((rows, args) => {
      for (const row of rows) {
        const id = row.getAttribute('data-message-id') || ''
        const store = row.getAttribute('data-store-id') || ''
        const bubble = row.querySelector<HTMLElement>('.chat-item__bubble--me')
        if (!id || args.previous.includes(id) || !/^[1-9]\d*$/.test(store) || !bubble
          || bubble.innerText.replace(/\r\n/g, '\n') !== args.message
          || row.querySelector('.chat-item__status-loading, .chat-item__status-btn--failed')) continue
        return { message_id: id, store_id: store }
      }
      return null
    }, { previous, message })
    if (match) return match
    await chat.waitForTimeout(200)
  }
  return undefined
}

export async function sendFirstMessage(page: Page, context: BrowserContext, profileKey: string,
  input: FirstMessageInput, store: FirstMessageStore, signal?: AbortSignal): Promise<FirstMessageOutcome> {
  const target = profileTarget(input.profile_url)
  const recordId = store.recordId(profileKey, input.sender_id, target.id)
  const record: FirstMessageRecord = { schema_version: 1, record_id: recordId, status: 'not_sent',
    expected_sender_id: input.sender_id, sender_id: null, target_id: target.id,
    profile_url: target.url, source_profile_url: input.source_profile_url,
    note_id: input.note_id, comment_id: input.comment_id, reason: input.reason, message: input.message,
    created_at: new Date().toISOString() }
  let reserved = false
  try {
    signal?.throwIfAborted()
    const prior = await store.find(recordId)
    if (prior) return outcome(prior, 'XHS_CONTACT_ALREADY_ATTEMPTED', '已有首联记录，请人工核对，未再次发送')
    const native = await openNativeConversation(page, context, target, signal)
    const chat = native.chat
    record.sender_id = native.data.sender_id
    record.target_nickname = native.data.nickname
    record.target_red_id = native.data.red_id
    await assertSender(page, input.sender_id)
    await assertFirstContact(chat, target.id)
    record.checked_at = new Date().toISOString()
    signal?.throwIfAborted()
    const previous = await chat.locator('.xhs-im-msg-list > .chat-item[data-message-id]')
      .evaluateAll(rows => rows.map(row => row.getAttribute('data-message-id') || ''))
    record.status = 'unknown'
    record.reserved_at = new Date().toISOString()
    reserved = await store.reserve(record)
    if (!reserved) return outcome(record, 'XHS_CONTACT_ALREADY_ATTEMPTED', '已有首联占用，请人工核对，未再次发送')
    signal?.throwIfAborted()
    // 预留落盘后再检查用户是否已切换目标或输入草稿。
    await assertSender(page, input.sender_id)
    const editor = await assertFirstContact(chat, target.id)
    await editor.click({ timeout: WAIT_MS })
    signal?.throwIfAborted()
    await chat.keyboard.insertText(input.message)
    signal?.throwIfAborted()
    await assertSender(page, input.sender_id)
    await assertFirstContact(chat, target.id, input.message)
    signal?.throwIfAborted()
    // Enter 为本次唯一发送动作，定位器同时要求当前活动收件人。
    await editorFor(chat, target.id).press('Enter', { timeout: WAIT_MS })
    const sent = await confirmedMessage(chat, target.id, input.message, previous, signal)
    if (!sent) throw new BrowserFailure('XHS_SEND_UNCONFIRMED', '发送结果尚未确认，请人工核对')
    Object.assign(record, sent, { status: 'sent', sent_at: new Date().toISOString(), local_receipt: 'saved' })
    try { await store.finish(record) }
    catch {
      record.local_receipt = 'unconfirmed'
      return outcome(record, 'XHS_RECEIPT_UNCONFIRMED', '私信已确认发送，本机终态记录未确认，请人工保留回执', 'applied')
    }
    return outcome(record, 'OK', '首条私信已确认发送，请由人工接手', 'applied')
  } catch (error) {
    if (reserved) {
      record.status = 'unknown'
      try { await store.finish(record) } catch { /* 原预留持续阻止再次发送。 */ }
      return outcome(record, 'XHS_SEND_UNCONFIRMED', '首联结果待人工核对，禁止自动重发', 'unknown')
    }
    record.status = 'not_sent'
    const failure = error instanceof BrowserFailure ? error
      : new BrowserFailure(signal?.aborted ? 'XHS_CANCELLED' : 'XHS_CONTACT_UNCONFIRMED',
        signal?.aborted ? '操作已取消' : '首联条件或本机记录无法确认，请人工检查')
    return outcome(record, failure.code, failure.message)
  }
}
