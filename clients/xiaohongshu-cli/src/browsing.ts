import type { BrowserContext, Page } from 'playwright-core'
import { BrowserFailure } from './errors.js'

const ORIGIN = 'https://www.xiaohongshu.com'
const ID = /^[0-9a-f]{24}$/i
const SIGNED_IN = 'li.user.side-bar-component a[href^="/user/profile/"]'
const LOGIN = '.login-modal, .side-bar-component.login-btn button'
const NOTES = 'section.note-item'
const COMMENTS = '.comments-container .comment-item[id^="comment-"]'
const MAX_ROUNDS = 8
const PAGE_TIMEOUT = 8000

export interface ProfileTarget {
  id: string
  url: string
  navigationUrl: string
}

export function profileTarget(value: string): ProfileTarget {
  if (value.length > 2048) throw new BrowserFailure('XHS_INVALID_INPUT', '小红书主页链接过长')
  let url: URL
  try { url = new URL(value) } catch {
    throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的小红书主页链接')
  }
  const match = /^\/user\/profile\/([0-9a-f]{24})$/i.exec(url.pathname)
  if (url.origin !== ORIGIN || url.username || url.password || url.hash || !match) {
    throw new BrowserFailure('XHS_INVALID_INPUT', '仅支持小红书官网的用户主页链接')
  }
  return { id: match[1].toLowerCase(), url: ORIGIN + '/user/profile/' + match[1].toLowerCase(), navigationUrl: url.href }
}

export function collectionLimit(limit: number | undefined): number {
  if (limit === undefined) return 20
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new BrowserFailure('XHS_INVALID_INPUT', '数量上限须为 1 到 100 的整数')
  }
  return limit
}

export function noteId(value: string): string {
  if (!ID.test(value)) throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的笔记 ID')
  return value.toLowerCase()
}

export interface Note {
  note_id: string
  url: string
  title: string
  like_count: string
}
export interface Comment {
  comment_id: string
  text: string
  author: { nickname: string; profile_url: string }
}
interface CollectionState {
  schema_version: 1
  profile_url: string
  complete: boolean
  truncated: boolean
  stop_reason: 'end' | 'limit' | 'load_limit'
}

function assertTarget(page: Page, expected: ProfileTarget): void {
  const actual = new URL(page.url())
  if (actual.origin !== ORIGIN || actual.pathname.toLowerCase() !== '/user/profile/' + expected.id) {
    throw new BrowserFailure('XHS_TARGET_MISMATCH', '页面与指定小红书用户不一致')
  }
}

async function assertSignedIn(page: Page, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  const signedIn = page.locator(SIGNED_IN).filter({ visible: true }).first()
  const verification = page.getByText(/^(请完成安全验证|安全验证|请完成验证|访问受限)$/).filter({ visible: true })
  if (!await signedIn.isVisible() && await verification.count()) {
    throw new BrowserFailure('XHS_VERIFICATION_REQUIRED', '小红书要求人工验证，请在浏览器中处理')
  }
  const login = page.locator(LOGIN).filter({ visible: true }).first()
  await signedIn.or(login).first().waitFor({ timeout: PAGE_TIMEOUT })
  signal?.throwIfAborted()
  if (await login.isVisible()) throw new BrowserFailure('XHS_LOGIN_REQUIRED', '请先在小红书连接器扫码登录')
  if (!await signedIn.isVisible()) throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '无法确认小红书登录状态')
}

async function openProfile(page: Page, target: ProfileTarget, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  await page.bringToFront()
  const response = await page.goto(target.navigationUrl, { waitUntil: 'domcontentloaded', timeout: 15000 })
  signal?.throwIfAborted()
  if (!response?.ok()) throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '小红书主页暂时不可用')
  assertTarget(page, target)
  await assertSignedIn(page, signal)
  await page.locator('.user-info').filter({ visible: true }).first().waitFor({ timeout: PAGE_TIMEOUT })
}

function collectionState(target: ProfileTarget, stop: CollectionState['stop_reason']): CollectionState {
  return { schema_version: 1, profile_url: target.url,
    complete: stop === 'end', truncated: stop !== 'end', stop_reason: stop }
}

async function pauseForLoad(page: Page, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  await page.waitForTimeout(500)
  signal?.throwIfAborted()
}

async function notesEnded(page: Page): Promise<boolean> {
  return await page.locator('.feeds-tab-container .empty-container .empty-text')
    .filter({ hasText: /^(暂时没有笔记|还没有发布过笔记)$/ }).filter({ visible: true }).count() > 0
}

async function readNotes(page: Page, target: ProfileTarget): Promise<Note[]> {
  const cards = await page.locator(NOTES).evaluateAll(nodes => nodes.map(node => ({
    href: node.querySelector('a.cover')?.getAttribute('href') || '',
    title: node.querySelector('a.title span')?.textContent?.trim() || '',
    like: node.querySelector('.like-wrapper .count')?.textContent?.trim() || '',
  })))
  const notes: Note[] = []
  for (const card of cards) {
    let url: URL
    try { url = new URL(card.href, ORIGIN) } catch { continue }
    if (url.origin !== ORIGIN) continue
    const match = /^\/user\/profile\/([0-9a-f]{24})\/([0-9a-f]{24})$/i.exec(url.pathname)
    if (!match || match[1].toLowerCase() !== target.id) continue
    notes.push({ note_id: match[2].toLowerCase(), url: ORIGIN + '/explore/' + match[2].toLowerCase(),
      title: card.title, like_count: card.like })
  }
  return notes
}

export async function listNotes(page: Page, target: ProfileTarget, limit: number, signal?: AbortSignal) {
  await openProfile(page, target, signal)
  const notes = new Map<string, Note>()
  for (let round = 0; round < MAX_ROUNDS; round++) {
    await assertSignedIn(page, signal)
    for (const note of await readNotes(page, target)) notes.set(note.note_id, note)
    const ended = await notesEnded(page)
    if (notes.size >= limit) {
      return { ...collectionState(target, ended && notes.size <= limit ? 'end' : 'limit'),
        notes: Array.from(notes.values()).slice(0, limit) }
    }
    if (ended) {
      return { ...collectionState(target, 'end'), notes: Array.from(notes.values()) }
    }
    await page.evaluate(() => window.scrollBy(0, Math.max(600, innerHeight)))
    await pauseForLoad(page, signal)
  }
  if (!notes.size) throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '未确认主页笔记列表，请检查浏览器')
  return { ...collectionState(target, 'load_limit'), notes: Array.from(notes.values()) }
}

async function openNote(page: Page, target: ProfileTarget, id: string, signal?: AbortSignal) {
  await openProfile(page, target, signal)
  for (let round = 0; round < MAX_ROUNDS; round++) {
    await assertSignedIn(page, signal)
    const covers = page.locator('section.note-item a.cover')
    const hrefs = await covers.evaluateAll(nodes => nodes.map(node => node.getAttribute('href') || ''))
    for (let index = 0; index < hrefs.length; index++) {
      const url = new URL(hrefs[index], ORIGIN)
      if (url.origin === ORIGIN && url.pathname.toLowerCase() === '/user/profile/' + target.id + '/' + id) {
        signal?.throwIfAborted()
        await covers.nth(index).click({ timeout: PAGE_TIMEOUT })
        await page.waitForURL(url => url.origin === ORIGIN && url.pathname.toLowerCase() === '/explore/' + id,
          { timeout: PAGE_TIMEOUT })
        signal?.throwIfAborted()
        await page.locator('.comments-container').first().waitFor({ timeout: PAGE_TIMEOUT })
        return
      }
    }
    if (await notesEnded(page)) throw new BrowserFailure('XHS_NOTE_NOT_FOUND', '指定主页没有找到这篇笔记')
    await page.evaluate(() => window.scrollBy(0, Math.max(600, innerHeight)))
    await pauseForLoad(page, signal)
  }
  throw new BrowserFailure('XHS_NOTE_UNCONFIRMED', '尚未在指定主页找到这篇笔记，请检查浏览器')
}

async function readComments(page: Page): Promise<Comment[]> {
  const entries = await page.locator(COMMENTS).evaluateAll(nodes => nodes.map(node => {
    // 回复节点也可能嵌套在父评论中，按最近 comment-item 核对字段归属。
    const own = (selector: string) => Array.from(node.querySelectorAll(selector))
      .find(element => element.closest('.comment-item') === node)
    return {
      id: node.id.slice('comment-'.length),
      text: own('.content .note-text')?.textContent?.trim() || '',
      href: own('.avatar a[href^="/user/profile/"]')?.getAttribute('href') || '',
      nickname: own('.author .name')?.textContent?.trim() || '',
    }
  }))
  const comments: Comment[] = []
  for (const entry of entries) {
    if (!entry.id || !entry.text || !entry.href) continue
    try {
      const author = profileTarget(new URL(entry.href, ORIGIN).href)
      comments.push({ comment_id: entry.id, text: entry.text,
        author: { nickname: entry.nickname, profile_url: author.url } })
    } catch { /* 无法定位作者的条目不伪造身份。 */ }
  }
  return comments
}

export async function listComments(page: Page, target: ProfileTarget, id: string, limit: number, signal?: AbortSignal) {
  await openNote(page, target, id, signal)
  const comments = new Map<string, Comment>()
  for (let round = 0; round < MAX_ROUNDS; round++) {
    await assertSignedIn(page, signal)
    for (const comment of await readComments(page)) comments.set(comment.comment_id, comment)
    const more = page.locator('.comments-container .show-more').filter({ visible: true })
    const ended = !await more.count() && (
      await page.locator('.comments-container .end-container')
        .filter({ hasText: /^\s*- THE END -\s*$/ }).filter({ visible: true }).count() > 0
      || await page.locator('.comments-container .no-comments')
        .filter({ hasText: /^(暂时没有评论|暂无评论|快来评论吧)$/ }).filter({ visible: true }).count() > 0)
    const stop = ended ? 'end' : comments.size >= limit ? 'limit' : undefined
    if (stop) {
      return { ...collectionState(target, comments.size > limit ? 'limit' : stop),
        note_id: id, comments: Array.from(comments.values()).slice(0, limit) }
    }
    if (await more.count()) {
      signal?.throwIfAborted()
      await more.first().click({ timeout: PAGE_TIMEOUT })
    } else {
      await page.locator('.note-scroller').first().evaluate(element => {
        element.scrollBy(0, Math.max(600, element.clientHeight))
      })
    }
    await pauseForLoad(page, signal)
  }
  if (!comments.size) throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '未确认评论区内容，请检查浏览器')
  return { ...collectionState(target, 'load_limit'), note_id: id, comments: Array.from(comments.values()).slice(0, limit) }
}

export async function openConversation(page: Page, context: BrowserContext, target: ProfileTarget, signal?: AbortSignal) {
  await openProfile(page, target, signal)
  const nickname = (await page.locator('.user-info .user-name').first().innerText()).trim()
  const redId = (await page.locator('.user-info .user-redId').first().innerText()).trim()
  const button = page.locator('button.xhs-user-im-btn[title="发消息"]')
  await button.waitFor({ timeout: PAGE_TIMEOUT })
  signal?.throwIfAborted()
  const [chat] = await Promise.all([
    page.waitForEvent('popup', { timeout: PAGE_TIMEOUT }),
    button.click({ timeout: PAGE_TIMEOUT }),
  ])
  await chat.waitForURL(url => url.origin === ORIGIN && url.pathname === '/chat'
    && url.searchParams.get('openUid')?.toLowerCase() === target.id, { timeout: PAGE_TIMEOUT })
  await chat.locator('.xhs-im-chat-window__header-info').first().waitFor({ timeout: PAGE_TIMEOUT })
  await chat.waitForFunction(() => Boolean(document.querySelector('.xhs-im-chat-window__header-name')?.textContent?.trim()),
    undefined, { timeout: PAGE_TIMEOUT })
  signal?.throwIfAborted()
  const activeConversation = chat.locator('.xhs-im-conv-item--active[data-conv-id]').filter({ visible: true })
  if (await activeConversation.count() !== 1
    || (await activeConversation.getAttribute('data-conv-id'))?.toLowerCase() !== target.id) {
    throw new BrowserFailure('XHS_TARGET_MISMATCH', '会话目标与主页用户不一致')
  }
  signal?.throwIfAborted()
  if (!context.pages().includes(chat)) throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '会话窗口已关闭')
  const editor = chat.locator('.xhs-im-input-bar-editor[contenteditable="true"]').first()
  const editable = await editor.isVisible() && await editor.isEditable()
  await chat.bringToFront()
  return { schema_version: 1, status: 'conversation_opened', target_id: target.id,
    profile_url: target.url, nickname, red_id: redId, editable }
}
