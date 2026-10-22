/** Deterministic business guards for rebuilding a connection location, never external writes. */
import type { BossSession } from '../operations/bossContext.js'
import { CancelledError, CodedOperationError, type OpContext } from '../operations/types.js'
import { setTimeout as delay } from 'node:timers/promises'
import { membershipAccountKey } from './MembershipInspector.js'
import { FilterSetter, FILTER_SNAPSHOT_OPTIONS } from './FilterSetter.js'
import { JobSwitcher, JobSwitchError, FILTER_BUTTON_PATTERN } from './JobSwitcher.js'
import { PageNavigator, NavError, isBossPage } from './PageNavigator.js'
import { type BrowserPageContext, resolveBrowserTarget, updateBrowserTarget } from './browserTargetBinding.js'

export async function captureBrowserPageContext(session: BossSession, prior?: BrowserPageContext): Promise<BrowserPageContext | undefined> {
  const raw = new URL(await session.getUrl())
  const entry = `${raw.origin}${raw.pathname}`
  if (raw.origin !== 'https://www.zhipin.com' || !['/web/chat/recommend', '/web/chat/index'].includes(raw.pathname)) return
  const snapshot = await session.snapshot()
  const account = membershipAccountKey(snapshot)
  const accountRef = account ?? prior?.account_ref
  const value: BrowserPageContext = { entry_url: entry, ...(accountRef ? { account_ref: accountRef } : {}) }
  if (raw.pathname === '/web/chat/recommend') {
    try { value.job_name = new JobSwitcher({ snapshot: session.snapshot, click: session.click }).locateJobBox(snapshot).name ?? prior?.job_name } catch { value.job_name = prior?.job_name }
    const toolbar = snapshot.strings.filter(s => FILTER_BUTTON_PATTERN.test(s.trim()))
    if (toolbar.length === 1 && toolbar[0]!.trim() === '筛选') value.filters = { clear: true }
    else if (prior && (!account || !prior.account_ref || prior.account_ref === account)
      && prior.job_name === value.job_name) value.filters = prior.filters
  }
  return value
}

export async function restoreBrowserPageContext(session: BossSession, saved: BrowserPageContext, ctx: OpContext): Promise<void> {
  const sleep = async (ms: number) => {
    try { await delay(ms, undefined, { signal: ctx.signal }) }
    catch (error) { if (ctx.signal.aborted) throw new CancelledError(); throw error }
  }
  const check = async () => {
    if (ctx.signal.aborted) throw new CancelledError()
    const url = await session.getUrl()
    if (/\/(?:web\/user\/)?login(?:[/?#]|$)|passport\.zhipin\.com/.test(url)) {
      throw new CodedOperationError('NOT_LOGGED_IN', 'BOSS 页面要求登录，请完成原账号登录后在原会话接续')
    }
    const snapshot = await session.snapshot()
    const account = membershipAccountKey(snapshot)
    if (account && saved.account_ref && account !== saved.account_ref) {
      throw new CodedOperationError('UI_CHANGED', '检测到 BOSS 账号已变化，请恢复原账号后接续')
    }
    return { url, snapshot }
  }
  // Creating/attaching a target is not a page-ready signal. Wait locally for
  // the existing toolbar, and return from a post-login landing page through
  // the ordinary navigator before restoring the original job/filter values.
  const target = saved.entry_url.endsWith('/recommend') ? 'recommend' : 'chat'
  const deadline = Date.now() + 15000
  let ready = false
  while (Date.now() < deadline) {
    const current = await check()
    if (isBossPage(current.url, target)) {
      try {
        if (target === 'recommend') new JobSwitcher({ snapshot: session.snapshot, click: session.click }).locateJobBox(current.snapshot)
        ready = true
        break
      } catch (error) { if (!(error instanceof JobSwitchError)) throw error }
    } else if (isBossPage(current.url, target === 'recommend' ? 'chat' : 'recommend')) {
      try {
        await new PageNavigator({ snapshot: session.snapshot, click: session.click, getUrl: session.getUrl,
          pageNavigate: session.pageNavigate, signal: ctx.signal, sleep }).navigate(target)
        continue
      } catch (error) {
        await check() // Prefer an actual login dependency over a navigation failure.
        if (!(error instanceof NavError)) throw error
      }
    }
    await sleep(500)
  }
  if (!ready) throw new CodedOperationError('UI_CHANGED', '原业务页面尚未就绪，未取得原职位及筛选栏；已读资料保留，请页面加载完成后在原会话接续')
  if (saved.entry_url.endsWith('/recommend')) {
    const switcher = new JobSwitcher({ snapshot: session.snapshot, click: session.click, signal: ctx.signal })
    if (!saved.job_name) throw new CodedOperationError('UI_CHANGED', '缺少原职位，无法恢复原推荐列表；请在原页面恢复职位后接续')
    if (switcher.locateJobBox(await session.snapshot()).name !== saved.job_name) await switcher.selectJob({ jobName: saved.job_name })
    await check()
    if (!saved.filters) throw new CodedOperationError('UI_CHANGED', '原页面已重建，但缺少原筛选值；请恢复筛选后在原会话接续')
    const setter = new FilterSetter({ snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS), click: session.click,
      mouseWheel: session.mouseWheel, drag: session.drag, signal: ctx.signal })
    if ('clear' in saved.filters) await setter.clear()
    else {
      // A historical avatar hash is auxiliary identity evidence, not a fresh
      // membership hint. The setter must observe current VIP capability itself.
      const spec = { ...saved.filters.spec }
      delete spec.membership_account_ref
      delete spec.membership_hint
      const applied = await setter.apply(spec)
      if (!applied.applied || applied.skippedFilters.length) throw new CodedOperationError('MEMBERSHIP_REQUIRED', '原筛选条件未完整恢复，当前会员条件不可用；已读资料保留，请恢复原条件后接续')
      if (applied.substitutions.length) throw new CodedOperationError('UI_CHANGED', '原筛选选项已变化，未按替代条件继续读取；已读资料保留，请核对原条件后接续')
    }
    await check()
  }
}

/** Freeze effective filter values rather than replaying aliases or VIP substitutions. */
export async function rememberFilterResult(name: string, result: import('../operations/types.js').OperationResult, port: number): Promise<void> {
  if (!result.success || !['boss_filter', 'boss_clear_filter'].includes(name) || result.data.applied !== true) return
  const ref = result.data.browser_target_ref
  if (typeof ref !== 'string') return
  const binding = await resolveBrowserTarget(ref, port)
  if (!binding.page_context) return
  if (name === 'boss_filter' && result.data.account_ref && binding.page_context.account_ref
    && result.data.account_ref !== binding.page_context.account_ref) return
  const filters: BrowserPageContext['filters'] = name === 'boss_clear_filter' ? { clear: true } : { spec: {
    filters: result.data.applied_rows as Array<{ label: string; options: string[] }>,
    ...(result.data.age ? { age: result.data.age as { min: number; max: number | null } } : {}),
    first_degree_only: result.data.first_degree_only === true,
  } }
  await updateBrowserTarget(ref, port, binding.target_id, b => ({ ...b, page_context: { ...b.page_context!, filters } }))
}
