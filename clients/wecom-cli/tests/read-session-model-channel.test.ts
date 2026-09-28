/**
 * M10b 双通道编排测试（模型主通道 + OCR 兜底；mock 驱动与 parseHistoryFn，
 * 绝不触达真实企微/服务端）：
 * - 未配置 AID_WECOM_SERVER_URL → 驱动一次调用 -ParseMode ocr，channel=ocr（不报错）
 * - 模型通道成功：驱动 -ParseMode none 只出 page_paths → TS 读文件转 base64（旧→新
 *   反转）→ channel=model、messages 带 side/kind/time、model_usage/billing/
 *   model_latency_ms/pages_read 透传；模型通道下 since_days 仍传驱动（早停忽略）
 * - 模型失败（unavailable）→ 驱动二次调用 -ParseMode ocr 兜底，channel=ocr +
 *   fallback_reason；驱动 navigate_required 分发后的二次调用同为 none
 * - 402 余额不足 → INSUFFICIENT_CREDIT 不降级（驱动只一次）；激活失败 config →
 *   CONFIG_MISSING 不降级
 * - 截图文件缺失 / page_paths 为空 → 降级 OCR
 * - 模型调用中用户取消 → CANCELLED，不触发 OCR 兜底
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import { ProxyError } from '../src/platform/serverProxy.js'
import { CancelledError, type OpContext } from '../src/operations/types.js'
import type { RunPowerShellDriverFn } from '../src/platform/powershell.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../src/platform/targetRef.js'
import type { ParseHistoryFn } from '../src/operations/readSession.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
function tempDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  tempDirs.push(dir)
  return dir
}
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

const TARGET = { name: '陆伟', type: 'contact', subtitle: '微信联系人' }
const ENV_MODEL = { AID_WECOM_SERVER_URL: 'https://agent.example.com' } as NodeJS.ProcessEnv

/** OCR 模式驱动返回（兜底/直连） */
const OCR_DATA = {
  navigate_required: false,
  title: '陆伟 @微信',
  parse_mode: 'ocr',
  messages: [
    { side: 'timeline', text: '08:23' },
    { side: 'peer', text: '在吗', time: '08:23' },
  ],
  page_paths: [],
  pages_read: 1,
  timing_ms: { total: 9000 },
  screenshot_paths: ['step1-precheck.png', 'page-1.png'],
}

/** 服务端 200 响应样例（M10a 契约） */
const MODEL_RESPONSE = {
  messages: [
    { side: 'timeline', kind: 'timeline', text: '08:23' },
    { time: '08:23', side: 'peer', kind: 'text', text: '在吗' },
    { time: '08:23', side: 'self', kind: 'image', text: '[图片] 二维码' },
  ],
  pages: 2,
  latency_ms: { total: 18000, per_page: [9000, 8500] },
  model_usage: { prompt_tokens: 2100, completion_tokens: 180 },
  billing: { credits_charged: 1.2 },
}

function argAfter(args: string[] | undefined, flag: string): string {
  const i = args?.indexOf(flag) ?? -1
  assert.ok(i !== -1, `驱动参数应含 ${flag}`)
  return args![i + 1]! as string
}

interface DriverCall {
  script: string
  args?: string[]
}

/**
 * mock 驱动：read-session.ps1 按 -ParseMode 分流——none 写 page-1/2.png 假截图并返回
 * page_paths（采集序新→旧），ocr 返回 OCR_DATA
 */
function makeMockDriver(opts: {
  calls: DriverCall[]
  writeFiles?: boolean
}): RunPowerShellDriverFn {
  return async (driverOpts) => {
    const call: DriverCall = { script: driverOpts.script, args: driverOpts.args }
    opts.calls.push(call)
    const base = driverOpts.script.replace(/^.*[\\/]/, '')
    if (base !== 'read-session.ps1') throw new Error(`unexpected driver ${base}`)
    const dir = argAfter(driverOpts.args, '-ArtifactDir')
    const parseMode = argAfter(driverOpts.args, '-ParseMode')
    if (parseMode === 'none') {
      const paths: string[] = []
      if (opts.writeFiles !== false) {
        for (let i = 1; i <= 2; i++) {
          const p = join(dir, `page-${i}.png`)
          writeFileSync(p, Buffer.from(`png-${i}`)) // page-1=最新底部屏（采集序新→旧）
          paths.push(p)
        }
      } else {
        // 文件缺失场景：返回路径但不落盘
        paths.push(join(dir, 'page-1.png'), join(dir, 'page-2.png'))
      }
      return {
        navigate_required: false,
        title: '陆伟 @微信',
        parse_mode: 'none',
        page_paths: paths,
        pages_read: 2,
        timing_ms: { scroll: 2000, total: 4000 },
        screenshot_paths: ['step1-precheck.png', ...paths],
      }
    }
    return { ...OCR_DATA }
  }
}

function makeOp(
  runDriverFn: RunPowerShellDriverFn,
  extra: {
    env?: NodeJS.ProcessEnv
    parseHistoryFn?: ParseHistoryFn
    /** 分发路径用：真实签发/验证（共享 keyDir），内部 chatSelect 依赖 ref 内坐标 */
    verifyRefFn?: VerifyTargetRefFn
    createRefFn?: CreateTargetRefFn
  } = {},
) {
  return createWecomReadSessionOperation({
    runDriverFn,
    verifyRefFn: extra.verifyRefFn ?? (() => TARGET),
    createRefFn: extra.createRefFn,
    artifactDirFn: () => tempDir('wecom-read-model-test-'),
    env: extra.env ?? {},
    parseHistoryFn: extra.parseHistoryFn,
  })
}

test('双通道：未配置 SERVER_URL → 驱动一次调用 -ParseMode ocr，channel=ocr（不报错，链路与 M9 一致）', async () => {
  const calls: DriverCall[] = []
  const op = makeOp(makeMockDriver({ calls }), { env: {} as NodeJS.ProcessEnv })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.channel, 'ocr')
  assert.equal(r.data.fallback_reason, undefined)
  assert.equal(r.data.pages_read, 1)
  const messages = r.data.messages as Array<{ side: string; text: string }>
  assert.deepEqual(messages.map((m) => m.text), ['08:23', '在吗'])
  assert.equal(calls.length, 1)
  assert.equal(argAfter(calls[0]!.args, '-ParseMode'), 'ocr')
  assert.match(r.message, /OCR 通道/)
})

test('双通道：模型通道成功 → channel=model，messages 带 kind/time，计费/延迟透传，images 反转为旧→新', async () => {
  const calls: DriverCall[] = []
  const seen: { images?: string[]; sessionTitle?: string } = {}
  const parseHistoryFn: ParseHistoryFn = async (params) => {
    seen.images = params.images
    seen.sessionTitle = params.sessionTitle
    return MODEL_RESPONSE
  }
  const op = makeOp(makeMockDriver({ calls }), { env: ENV_MODEL, parseHistoryFn })
  const r = await op.execute({ target_ref: 'ref', max_pages: 2, since_days: 7 }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.channel, 'model')
  assert.equal(r.data.pages_read, 2) // 服务端 ok pages
  // 驱动只跑一轮 none（无本地 OCR）
  assert.equal(calls.length, 1)
  assert.equal(argAfter(calls[0]!.args, '-ParseMode'), 'none')
  assert.equal(argAfter(calls[0]!.args, '-MaxPages'), '2')
  assert.ok(calls[0]!.args!.includes('-SinceDays'), 'since_days 原样传驱动（none 模式忽略早停）')
  // images = page-2（旧）→ page-1（新）反转；内容为假截图 base64
  assert.deepEqual(seen.images, [
    Buffer.from('png-2').toString('base64'),
    Buffer.from('png-1').toString('base64'),
  ])
  assert.equal(seen.sessionTitle, '陆伟 @微信')
  // messages 透传（side/kind/time）
  const messages = r.data.messages as Array<{ side: string; kind: string; text: string; time?: string }>
  assert.deepEqual(
    messages.map((m) => [m.side, m.kind, m.text, m.time]),
    [
      ['timeline', 'timeline', '08:23', undefined],
      ['peer', 'text', '在吗', '08:23'],
      ['self', 'image', '[图片] 二维码', '08:23'],
    ],
  )
  // 计费/计量/延迟透传
  assert.deepEqual(r.data.model_usage, { prompt_tokens: 2100, completion_tokens: 180 })
  assert.deepEqual(r.data.billing, { credits_charged: 1.2 })
  assert.deepEqual(r.data.model_latency_ms, { total: 18000, per_page: [9000, 8500] })
  assert.equal((r.data.timing_ms as Record<string, number>).total, 4000) // 驱动 timing（截图阶段）
  assert.match(r.message, /模型通道/)
  assert.match(r.message, /1\.2 积分/)
})

test('双通道：模型通道 navigate_required → search+select 分发后二次调用仍为 none → 模型成功', async () => {
  const keyDir = tempDir('wecom-read-model-key-')
  const calls: DriverCall[] = []
  let firstNone = true
  const runDriverFn: RunPowerShellDriverFn = async (driverOpts) => {
    calls.push({ script: driverOpts.script, args: driverOpts.args })
    const base = driverOpts.script.replace(/^.*[\\/]/, '')
    if (base === 'chat-search.ps1') {
      return {
        query: '陆伟',
        items: [{ name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 200, y: 60, probability: 0.9 }],
        best_index: 0,
        overlay: { x: 100, y: 50, w: 400, h: 542 },
        jev: { used: false, latency_ms: 0 },
        timing_ms: {},
      }
    }
    if (base === 'chat-select.ps1') {
      return { target: { name: '陆伟' }, title: '陆伟 @微信', clicked: { x: 1, y: 2 }, timing_ms: {}, screenshot_paths: [] }
    }
    // read-session.ps1
    const dir = argAfter(driverOpts.args, '-ArtifactDir')
    const parseMode = argAfter(driverOpts.args, '-ParseMode')
    if (parseMode === 'none') {
      if (firstNone) {
        firstNone = false
        return {
          navigate_required: true,
          reason: '标题归一化规则未命中（当前标题「张三」≠ 目标「陆伟」）',
          title: '张三',
          timing_ms: {},
          screenshot_paths: [],
        }
      }
      const paths: string[] = []
      for (let i = 1; i <= 2; i++) {
        const p = join(dir, `page-${i}.png`)
        writeFileSync(p, Buffer.from(`png-${i}`))
        paths.push(p)
      }
      return {
        navigate_required: false,
        title: '陆伟 @微信',
        parse_mode: 'none',
        page_paths: paths,
        pages_read: 2,
        timing_ms: {},
        screenshot_paths: paths,
      }
    }
    return { ...OCR_DATA }
  }
  const op = makeOp(runDriverFn, {
    env: ENV_MODEL,
    parseHistoryFn: async () => MODEL_RESPONSE,
    // 分发链内部 chatSelect 依赖签发 ref 内的 overlay 坐标：用真实签发/验证（共享 keyDir）
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir }),
    createRefFn: (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  })
  const ref = createTargetRef('陆伟', 'contact', '微信联系人', { keyDir })
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, true, r.message)
  assert.equal(r.data.channel, 'model')
  assert.equal(r.data.navigated, true)
  const readCalls = calls.filter((c) => c.script.endsWith('read-session.ps1'))
  assert.equal(readCalls.length, 2)
  assert.equal(argAfter(readCalls[0]!.args, '-ParseMode'), 'none')
  assert.equal(argAfter(readCalls[1]!.args, '-ParseMode'), 'none', '分发后的二次读取同为 none（模型通道）')
})

test('双通道：模型失败（网络/超时/502）→ 驱动二次调用 -ParseMode ocr 兜底，channel=ocr + fallback_reason', async () => {
  const calls: DriverCall[] = []
  const op = makeOp(makeMockDriver({ calls }), {
    env: ENV_MODEL,
    parseHistoryFn: async () => {
      throw new ProxyError('unavailable', '服务端请求超时（>150s）')
    },
  })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.channel, 'ocr')
  assert.equal(r.data.fallback_reason, '服务端请求超时（>150s）')
  assert.equal(r.data.pages_read, 1)
  assert.deepEqual(
    (r.data.messages as Array<{ text: string }>).map((m) => m.text),
    ['08:23', '在吗'],
  )
  // 驱动两次：先 none 截图，后 ocr 兜底
  assert.equal(calls.length, 2)
  assert.equal(argAfter(calls[0]!.args, '-ParseMode'), 'none')
  assert.equal(argAfter(calls[1]!.args, '-ParseMode'), 'ocr')
  assert.match(r.message, /降级/)
})

test('双通道：402 余额不足 → INSUFFICIENT_CREDIT 不降级（驱动只一次，无 OCR 兜底）', async () => {
  const calls: DriverCall[] = []
  const op = makeOp(makeMockDriver({ calls }), {
    env: ENV_MODEL,
    parseHistoryFn: async () => {
      throw new ProxyError('insufficient_credit', '服务端积分余额不足（402：NO_CREDIT）——模型通道按次计积分，请充值后重试')
    },
  })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INSUFFICIENT_CREDIT')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.match(r.message, /积分/)
  assert.equal(calls.length, 1, '余额不足不得降级 OCR')
  assert.equal(argAfter(calls[0]!.args, '-ParseMode'), 'none')
})

test('双通道：激活失败（config）→ CONFIG_MISSING 不降级', async () => {
  const calls: DriverCall[] = []
  const op = makeOp(makeMockDriver({ calls }), {
    env: ENV_MODEL,
    parseHistoryFn: async () => {
      throw new ProxyError('config', 'token 失效且重新激活失败：激活失败：ACTIVATION_CODE_USED')
    },
  })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CONFIG_MISSING')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /模型通道不可用/)
  assert.equal(calls.length, 1, '激活失败不得降级 OCR')
})

test('双通道：截图文件缺失 → 降级 OCR（驱动二次调用 ocr）', async () => {
  const calls: DriverCall[] = []
  const parseCalls: unknown[] = []
  const op = makeOp(makeMockDriver({ calls, writeFiles: false }), {
    env: ENV_MODEL,
    parseHistoryFn: async (params) => {
      parseCalls.push(params)
      return MODEL_RESPONSE
    },
  })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.channel, 'ocr')
  assert.match(String(r.data.fallback_reason), /page-1/)
  assert.equal(parseCalls.length, 0, '文件读不到不得调服务端')
  assert.equal(calls.length, 2)
  assert.equal(argAfter(calls[1]!.args, '-ParseMode'), 'ocr')
})

test('双通道：驱动未返回 page_paths（空）→ 降级 OCR', async () => {
  const calls: DriverCall[] = []
  let noneCalls = 0
  const runDriverFn: RunPowerShellDriverFn = async (driverOpts) => {
    calls.push({ script: driverOpts.script, args: driverOpts.args })
    const parseMode = argAfter(driverOpts.args, '-ParseMode')
    if (parseMode === 'none') {
      noneCalls++
      return { navigate_required: false, title: '陆伟 @微信', parse_mode: 'none', page_paths: [], pages_read: 0, timing_ms: {}, screenshot_paths: [] }
    }
    return { ...OCR_DATA }
  }
  const op = makeOp(runDriverFn, { env: ENV_MODEL, parseHistoryFn: async () => MODEL_RESPONSE })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.channel, 'ocr')
  assert.equal(noneCalls, 1)
  assert.equal(calls.length, 2)
  assert.equal(argAfter(calls[1]!.args, '-ParseMode'), 'ocr')
})

test('双通道：模型调用中用户取消 → CANCELLED，不触发 OCR 兜底', async () => {
  const calls: DriverCall[] = []
  const op = makeOp(makeMockDriver({ calls }), {
    env: ENV_MODEL,
    parseHistoryFn: async () => {
      throw new CancelledError()
    },
  })
  const r = await op.execute({ target_ref: 'ref' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'none')
  assert.equal(calls.length, 1, '取消不得触发 OCR 兜底重跑')
})
