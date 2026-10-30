import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  CEF_LOG_MAX_FILES,
  cefLineTimeMs,
  defaultCandidateRoots,
  locateDataDir,
  parseCefLog,
  parseCefLogFileNameStart,
  parseConfigCfg,
} from '../src/platform/localdb/datadir.js'

/** 真机观测行样例（账号 id 脱敏；[RISK] 为日志自带字面标记，UTF-8 + CRLF） */
const CEF_HIT_LINE =
  '20:00:26.014 I:34328 browser_process_impl(113)  [SetCurrentContext] user_id: 1688854831832237 ' +
  'cache_path:[RISK]C:\\Users\\PC\\OneDrive\\文档\\WXWork\\1688854831832237\\WXWorkCefCache'
/** 同文件存在的 qt 侧行：无数字账号段（\qtCef 结尾），必须跳过 */
const CEF_QT_LINE =
  '20:00:27.062 I:34328 browser_process_qt_impl(111)  [SetCurrentContext] user_id: 0 ' +
  'cache_path:[RISK]C:\\Users\\PC\\OneDrive\\文档\\WXWork\\qtCef'

/** 真机形态日志文件名：会话起始日期时间 + 版本（文件名即时间序） */
const cefFile = (datetime: string, version = '5.0.82.5512'): string => `${datetime}(${version}).log`

/** 造真机形态 cache_path 命中行（行首 HH:MM:SS.mmm；acct 缺省 → 无 user_id 字段） */
function hitLine(time: string, root: string, userId: string, acct?: string): string {
  const acctField = acct === undefined ? '' : `user_id: ${acct} `
  return `${time} I:34328 browser_process_impl(113)  [SetCurrentContext] ${acctField}cache_path:[RISK]${join(root, userId)}\\WXWorkCefCache`
}

/** 由文件名取会话起始 epoch（断言统一走同一条解析路径，与时区无关） */
const fileStartOf = (name: string): number => parseCefLogFileNameStart(name)!

/** 造 Config.cfg 内容（latin-1 外层 JSON；GBK 字节的 latin-1 乱码值用 \u00XX 字面构造） */
function cfgContent(entries: Record<string, string>): string {
  return JSON.stringify({ config: entries }, null, 4)
}

/** 建一个账号目录树：Data 下指定库文件 + 可选 Config.cfg */
function makeAccount(root: string, userId: string, dbs: string[], withConfig = true): string {
  const dir = join(root, userId)
  mkdirSync(join(dir, 'Data'), { recursive: true })
  for (const db of dbs) writeFileSync(join(dir, 'Data', db), '')
  if (withConfig) {
    writeFileSync(join(dir, 'Config.cfg'), cfgContent({ LoginCompanyId: '1970326113004107', HasEverLoginKey: '1' }), 'latin1')
  }
  return dir
}

test('parseCefLog：真机行形态命中（root+user_id+同 行 account_id 提取，行尾缓存目录段不混入）', () => {
  assert.deepEqual(parseCefLog(CEF_HIT_LINE), [
    {
      root: 'C:\\Users\\PC\\OneDrive\\文档\\WXWork',
      user_id: '1688854831832237',
      account_id: '1688854831832237', // 同 行 user_id 字段：账号↔目录绑定证据（评审 v6）
    },
  ])
})

test('parseCefLog：多账号多行取全部（按行序保留，含不同 root；各行 account_id 独立提取）', () => {
  const content = [
    CEF_QT_LINE,
    CEF_HIT_LINE,
    '16:43:22.793 I:49092 browser_process_impl(113)  [SetCurrentContext] user_id: 2251800650741085 ' +
      'cache_path:[RISK]D:\\OldData\\WXWork\\2251800650741085\\WXWorkCefCache',
  ].join('\r\n')
  assert.deepEqual(parseCefLog(content), [
    {
      root: 'C:\\Users\\PC\\OneDrive\\文档\\WXWork',
      user_id: '1688854831832237',
      account_id: '1688854831832237',
    },
    {
      root: 'D:\\OldData\\WXWork',
      user_id: '2251800650741085',
      account_id: '2251800650741085',
    },
  ])
})

test('parseCefLog：行内无 user_id 字段仍命中（account_id 缺省，不做伪造绑定）', () => {
  const noAcct = 'cache_path:[RISK]' + join('C:\\R', '1688854831832237') + '\\WXWorkCefCache'
  assert.deepEqual(parseCefLog(noAcct), [{ root: 'C:\\R', user_id: '1688854831832237' }])
})

test('parseCefLog：行内 user_id 字段与路径账号段不一致——两者都照实输出（绑定由消费侧判定）', () => {
  const mismatched =
    '20:00:26.014 I:34328 browser_process_impl(113)  [SetCurrentContext] user_id: 1111222233334444 ' +
    'cache_path:[RISK]C:\\R\\1688854831832237\\WXWorkCefCache'
  assert.deepEqual(parseCefLog(mismatched), [
    { root: 'C:\\R', user_id: '1688854831832237', account_id: '1111222233334444' },
  ])
})

test('parseCefLog：[RISK] 标记缺失 / 无数字账号段(qtCef) / 畸形 / 空内容 一律跳过', () => {
  // 同一路径但缺 [RISK] 字面标记：不是既有日志格式，跳过（防把任意文本当命中）
  const noRisk =
    '20:00:26.014 I:34328 browser_process_impl(113)  [SetCurrentContext] user_id: 1688854831832237 ' +
    'cache_path:C:\\Users\\PC\\OneDrive\\文档\\WXWork\\1688854831832237\\WXWorkCefCache'
  assert.deepEqual(parseCefLog(noRisk), [])
  assert.deepEqual(parseCefLog(CEF_QT_LINE), [])
  assert.deepEqual(parseCefLog('cache_path:[RISK]'), [])
  assert.deepEqual(parseCefLog(''), [])
  assert.deepEqual(parseCefLog('random log noise\r\nwithout marker\n'), [])
})

test('parseCefLog：传入文件起始时刻 → 行时间戳合成为 line_time_ms；无行时间戳/未传 → 缺省', () => {
  const fileStart = fileStartOf(cefFile('2026-10-15_00-00-26'))
  // 真机行（行首 20:00:26.014，晚于文件起始时刻 00:00:26 → 同日 20:00）
  const hits = parseCefLog(CEF_HIT_LINE, fileStart)
  assert.equal(hits.length, 1)
  assert.equal(hits[0]!.line_time_ms, new Date(2026, 9, 15, 20, 0, 26, 14).getTime())
  // 无行时间戳的命中行：命中本身保留，但无法定时间 → line_time_ms 缺省
  const undated = parseCefLog('cache_path:[RISK]C:\\R\\1688854831832237\\WXWorkCefCache', fileStart)
  assert.equal(undated.length, 1)
  assert.equal(undated[0]!.line_time_ms, undefined)
  // 未传 fileStartMs（纯解析）→ 一律不带 line_time_ms（既有解析契约不变）
  assert.equal(parseCefLog(CEF_HIT_LINE)[0]!.line_time_ms, undefined)
})

test('parseCefLogFileNameStart：真机形态/紧凑形态命中，伪日期（2026-02-31、13 月、25 时）与非日期名 → null', () => {
  const real = parseCefLogFileNameStart('2026-10-14_16-43-22(5.0.81.5503).log')
  assert.equal(real, new Date(2026, 9, 14, 16, 43, 22, 0).getTime())
  const compact = parseCefLogFileNameStart('20261014083015(5.0.82).log')
  assert.equal(compact, new Date(2026, 9, 14, 8, 30, 15, 0).getTime())
  // 伪日期：Date 构造会把 2-31 翻滚进 3 月——回读校验必须拒绝（该文件命中不作证据）
  assert.equal(parseCefLogFileNameStart('2026-02-31_00-00-00(x).log'), null)
  assert.equal(parseCefLogFileNameStart('2026-13-01_00-00-00(x).log'), null)
  assert.equal(parseCefLogFileNameStart('2026-10-14_25-00-00(x).log'), null)
  assert.equal(parseCefLogFileNameStart('session-export.log'), null)
  assert.equal(parseCefLogFileNameStart('2026-10-14.log'), null)
})

test('cefLineTimeMs：同日合成 + 跨午夜（行时刻早于文件起始 → 日期 +1 天）+ 畸形行时刻 → null', () => {
  // 同日：文件 00:00:26 起，行 20:00:26.014 → 当日
  const sameDay = fileStartOf(cefFile('2026-10-15_00-00-26'))
  assert.equal(cefLineTimeMs(CEF_HIT_LINE, sameDay), new Date(2026, 9, 15, 20, 0, 26, 14).getTime())
  // 跨午夜：文件 23:59:00 起，行 00:01:30.250 → 次日
  const crossMidnight = fileStartOf(cefFile('2026-10-14_23-59-00'))
  assert.equal(cefLineTimeMs('00:01:30.250 I:1 x cache_path:none', crossMidnight), new Date(2026, 9, 15, 0, 1, 30, 250).getTime())
  // 无时间戳 / 字段越界（25 时、61 分）→ 无法定时间
  assert.equal(cefLineTimeMs('no time here cache_path:none', crossMidnight), null)
  assert.equal(cefLineTimeMs('25:00:00.000 I:1 x', crossMidnight), null)
  assert.equal(cefLineTimeMs('00:61:00.000 I:1 x', crossMidnight), null)
})

test('parseConfigCfg：latin-1 外层 + GBK 乱码值——登录键存在性与 ASCII 安全值并存，乱码值不致命', () => {
  // 真机结构：{"config": {…}}；kRedPoint* 实测为 GBK 字节的 latin-1 乱码（GBK best-effort 也会失败）
  const content = cfgContent({
    DbKeyVerKey: '2',
    LoginCompanyId: '1970326113004107',
    HasEverLoginKey: '1',
    kRedPointSmartSheetRecallData: '\u00c8\u00cb\u00b2\u00e2\u00ca\u00d4\u00c2\u00d2\u00c2\u00eb',
  })
  const summary = parseConfigCfg(content)
  assert.equal(summary.parsed, true)
  assert.equal(summary.total_keys, 4)
  assert.deepEqual(summary.login_keys_present.sort(), ['DbKeyVerKey', 'HasEverLoginKey', 'LoginCompanyId'])
  assert.equal(summary.ascii_values['LoginCompanyId'], '1970326113004107')
  assert.equal(summary.ascii_values['HasEverLoginKey'], '1')
  assert.equal(summary.ascii_values['kRedPointSmartSheetRecallData'], undefined)
})

test('parseConfigCfg：外层非 JSON / 非对象 / 空 config → parsed=false 空结果，不抛出', () => {
  for (const bad of ['not json at all', '{"config": "GBK\u00d2\u00ec\xb4\xf8\\"', '[]', '{"config": null}', '']) {
    const summary = parseConfigCfg(bad)
    assert.equal(summary.parsed, false)
    assert.deepEqual(summary.login_keys_present, [])
    assert.deepEqual(summary.ascii_values, {})
  }
})

test('parseConfigCfg：扁平形态兜底（无 config 包裹）+ 超长非 ASCII 值排除', () => {
  const flat = JSON.stringify({ LoginCompanyId: '123', HasEverLoginKey: '0' })
  const summary = parseConfigCfg(flat)
  assert.equal(summary.parsed, true)
  assert.equal(summary.ascii_values['LoginCompanyId'], '123')
  // 超过 ASCII 值上限的 GBK 乱码长值：不进 ascii_values
  const longMojibake = '\u00c8'.repeat(200)
  const summary2 = parseConfigCfg(JSON.stringify({ kLong: longMojibake }))
  assert.equal(summary2.ascii_values['kLong'], undefined)
  assert.deepEqual(summary2.login_keys_present, [])
})

test('defaultCandidateRoots：默认回退序列（Documents 未重定向 → OneDrive 中文/英文形态，env 缺失跳过）', () => {
  assert.deepEqual(defaultCandidateRoots('C:\\Users\\PC', 'C:\\Users\\PC\\OneDrive'), [
    { root: 'C:\\Users\\PC\\Documents\\WXWork', source: 'documents' },
    { root: 'C:\\Users\\PC\\OneDrive\\文档\\WXWork', source: 'onedrive-documents' },
    { root: 'C:\\Users\\PC\\OneDrive\\Documents\\WXWork', source: 'onedrive-documents' },
  ])
  // env 缺失：对应候选跳过，不拼 undefined 路径
  assert.deepEqual(defaultCandidateRoots(undefined, 'C:\\OD'), [
    { root: 'C:\\OD\\文档\\WXWork', source: 'onedrive-documents' },
    { root: 'C:\\OD\\Documents\\WXWork', source: 'onedrive-documents' },
  ])
  assert.deepEqual(defaultCandidateRoots('C:\\Users\\PC', undefined), [
    { root: 'C:\\Users\\PC\\Documents\\WXWork', source: 'documents' },
  ])
})

test('locateDataDir：全 miss（候选根/CEF 目录不存在）→ 全 null/空数组，不抛出', async () => {
  const info = await locateDataDir({
    appData: join(tmpdir(), 'wc-localdb-datadir-nonexistent-appdata'),
    candidateRoots: [{ root: join(tmpdir(), 'wc-localdb-datadir-nonexistent-root'), source: 'documents' }],
  })
  assert.equal(info.source, null)
  assert.equal(info.root, null)
  assert.equal(info.user_id, null)
  assert.equal(info.dir, null)
  assert.equal(info.db_paths, null)
  assert.deepEqual(info.accounts, [])
  assert.deepEqual(info.identity, {
    config_found: false,
    login_company_id: null,
    cross_check: null,
    identity_confirmed: false,
    reason: null,
    process_pid: null,
  })
})

test('locateDataDir：候选根依序回退（documents 前置 miss → onedrive-documents 命中）', async () => {
  const rootDocs = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-docs-'))
  const rootOneDrive = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-onedrive-'))
  try {
    // documents 根存在但无含 message.db 的账号目录（不命中）；onedrive 根有一个完整账号（命中）
    mkdirSync(join(rootDocs, 'not-numeric'), { recursive: true })
    makeAccount(rootOneDrive, '1688854831832237', ['message.db', 'session.db', 'user.db', 'message_lookup.db'])
    const info = await locateDataDir({
      cefLogDir: null, // 禁用 CEF 层（本用例只测候选根回退；真机 CEF 日志会抢占命中）
      candidateRoots: [
        { root: rootDocs, source: 'documents' },
        { root: rootOneDrive, source: 'onedrive-documents' },
      ],
    })
    assert.equal(info.source, 'onedrive-documents')
    assert.equal(info.root, rootOneDrive)
    assert.equal(info.user_id, '1688854831832237')
    assert.equal(info.dir, join(rootOneDrive, '1688854831832237'))
    assert.equal(info.db_paths?.message, join(rootOneDrive, '1688854831832237', 'Data', 'message.db'))
    assert.equal(info.db_paths?.session, join(rootOneDrive, '1688854831832237', 'Data', 'session.db'))
    assert.equal(info.db_paths?.user, join(rootOneDrive, '1688854831832237', 'Data', 'user.db'))
    assert.equal(info.db_paths?.message_lookup, join(rootOneDrive, '1688854831832237', 'Data', 'message_lookup.db'))
  } finally {
    rmSync(rootDocs, { recursive: true, force: true })
    rmSync(rootOneDrive, { recursive: true, force: true })
  }
})

test('locateDataDir：多账号枚举（CEF 命中排第一；非数字目录跳过；无 Data 库账号 has_data_db=false 照实列出）', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-multi-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-cef-'))
  try {
    const dirFull = makeAccount(root, '1688854831832237', ['message.db', 'session.db', 'user.db', 'message_lookup.db'])
    const dirPartial = makeAccount(root, '1688852699308616', ['session.db', 'user.db']) // 真机形态：历史账号无主库
    const dirBare = makeAccount(root, '2251800650741085', []) // 数字目录但无 Data 库
    mkdirSync(join(root, 'qtCef'), { recursive: true }) // 非数字目录跳过
    mkdirSync(join(root, 'Global'), { recursive: true })
    // CEF 日志：旧文件（10-14 16:43）绑定 2251 历史账号，新文件（10-15 00:00）绑定当前 1688 账号。
    const oldFile = cefFile('2026-10-14_16-43-22', '5.0.81.5503')
    const newFile = cefFile('2026-10-15_00-00-26')
    writeFileSync(join(cefDir, oldFile), CEF_QT_LINE + '\r\n' + hitLine('16:43:22.793', root, '2251800650741085', '2251800650741085') + '\r\n')
    writeFileSync(join(cefDir, newFile), hitLine('00:00:26.500', root, '1688854831832237', '1688854831832237') + '\r\n')
    utimesSync(dirPartial, new Date('2026-10-14T00:00:00Z'), new Date('2026-10-14T00:00:00Z'))
    utimesSync(dirFull, new Date('2026-10-15T00:00:00Z'), new Date('2026-10-15T00:00:00Z'))

    const info = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [] })
    assert.equal(info.source, 'cef-log')
    assert.equal(info.root, root)
    assert.equal(info.user_id, '1688854831832237') // 最新文件（字典序大）的最后命中
    // CEF 命中账号排第一；其余按 id 升序（mtime 仅在无 CEF 依据时参与 selected 选择）
    assert.deepEqual(
      info.accounts.map((a) => a.user_id),
      ['1688854831832237', '1688852699308616', '2251800650741085'],
    )
    assert.equal(info.accounts[0]!.has_data_db, true)
    assert.equal(info.accounts[1]!.has_data_db, false) // 有 Data 目录但无 message.db
    assert.equal(info.accounts[2]!.has_data_db, false)
    assert.ok(info.accounts[0]!.dir_mtime_ms, '目录 mtime 应输出')
    assert.equal(info.accounts[0]!.login_company_id, '1970326113004107')
    assert.equal(info.accounts[0]!.has_ever_login, '1')
    assert.equal(info.dir, dirFull)
    assert.ok(existsSync(info.accounts[1]!.dir))
    assert.ok(!existsSync(join(root, 'qtCef', 'Data')))
    assert.equal(info.identity.identity_confirmed, false) // CEF 只能定位候选，不能证明进程归属
    assert.equal(info.identity.reason, 'active-account-unavailable')
    // 无 message.db 的账号目录不干扰 dirPartial 存在性断言
    assert.equal(dirBare === info.accounts[2]!.dir, true)
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})

test('locateDataDir：快速重启或同 PID 新上下文无账号，旧绑定不能确认身份', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-old-context-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-old-cef-'))
  try {
    makeAccount(root, '111', ['message.db'])
    const file = cefFile('2026-10-15_08-00-00')
    writeFileSync(join(cefDir, file), [
      hitLine('08:59:00.000', root, '111', '111'),
      hitLine('09:00:10.000', root, '111'),
    ].join('\n'))
    const info = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [] })
    assert.equal(info.user_id, '111')
    assert.equal(info.identity.identity_confirmed, false)
    assert.equal(info.identity.reason, 'active-account-unavailable')
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})

test('locateDataDir：交叠日志以行事件时间选候选，文件较晚创建不能覆盖较新事件', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-overlap-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-overlap-cef-'))
  try {
    makeAccount(root, '111', ['message.db'])
    makeAccount(root, '222', ['message.db'])
    writeFileSync(join(cefDir, cefFile('2026-10-15_10-00-00')), hitLine('12:00:00.000', root, '222', '222'))
    writeFileSync(join(cefDir, cefFile('2026-10-15_11-00-00')), hitLine('11:00:05.000', root, '111', '111'))
    const info = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [] })
    assert.equal(info.user_id, '222')
    assert.equal(info.identity.identity_confirmed, false)
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})

test('locateDataDir：主窗口进程直接证据不受日志缺失、冲突、mtime 或旧 Config 影响', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-live-account-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-live-cef-'))
  try {
    const dir = makeAccount(root, '111', ['message.db'], false)
    makeAccount(root, '222', ['message.db'])
    const activeAccount = { pid: 42, message_db: join(dir, 'Data', 'message.db') }
    // 无日志、无配置也不阻塞更强的当前句柄证据。
    const first = await locateDataDir({ cefLogDir: null, candidateRoots: [], activeAccount })
    assert.equal(first.source, 'process-handle')
    assert.equal(first.user_id, '111')
    assert.equal(first.identity.identity_confirmed, true)
    assert.equal(first.identity.process_pid, 42)
    writeFileSync(join(cefDir, cefFile('2026-10-15_11-00-00')), hitLine('11:00:05.000', root, '222', '222'))
    utimesSync(dir, new Date('2020-01-01'), new Date('2020-01-01'))
    const second = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [], activeAccount })
    assert.equal(second.dir, first.dir)
    assert.equal(second.identity.identity_confirmed, true)
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})

test('locateDataDir：同 PID 新证据换账号；没有新证据时不能复用上次确认', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-live-switch-'))
  try {
    for (const id of ['111', '222']) {
      const dir = makeAccount(root, id, ['message.db'])
      const info = await locateDataDir({ cefLogDir: null, candidateRoots: [],
        activeAccount: { pid: 42, message_db: join(dir, 'Data', 'message.db') } })
      assert.equal(info.user_id, id)
      assert.equal(info.identity.identity_confirmed, true)
    }
    const withoutProof = await locateDataDir({ cefLogDir: null, candidateRoots: [{ root, source: 'documents' }] })
    assert.equal(withoutProof.identity.identity_confirmed, false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('locateDataDir：CEF 引用目录不在盘 → 视为 miss 继续回退链命中候选根', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-fallback-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-cef2-'))
  try {
    makeAccount(root, '1688854831832237', ['message.db'])
    writeFileSync(
      join(cefDir, cefFile('2026-10-14_20-00-26')),
      'cache_path:[RISK]Z:\\Gone\\WXWork\\1688854831832237\\WXWorkCefCache\r\n',
    )
    const info = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [{ root, source: 'onedrive-documents' }] })
    assert.equal(info.source, 'onedrive-documents') // CEF 层 miss（Z:\Gone 不在盘），回退命中
    assert.equal(info.root, root)
    assert.equal(info.user_id, '1688854831832237')
    // 无 CEF 依据 → selected 按目录 mtime 新→旧；单账号场景仍是它
    assert.equal(info.accounts.length, 1)
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})

test('locateDataDir：Config.cfg 缺失 → identity=config-missing；库文件缺失逐项落 null', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-nocfg-'))
  try {
    makeAccount(root, '1688852699308616', ['message.db']) // 合格账号（根命中判据：有 Data\message.db）
    utimesSync(join(root, '1688852699308616'), new Date('2020-01-01T00:00:00Z'), new Date('2020-01-01T00:00:00Z'))
    // 被选中账号：无 Config.cfg、无主库（真机历史账号形态：仅 session/user 残留库），mtime 更新（保持创建时刻）
    makeAccount(root, '1688854831832237', ['session.db', 'user.db'], false)
    const info = await locateDataDir({ cefLogDir: null, candidateRoots: [{ root, source: 'documents' }] })
    assert.equal(info.user_id, '1688854831832237')
    assert.deepEqual(info.db_paths, {
      message: null,
      session: join(root, '1688854831832237', 'Data', 'session.db'),
      user: join(root, '1688854831832237', 'Data', 'user.db'),
      message_lookup: null,
    })
    assert.deepEqual(info.identity, {
      config_found: false,
      login_company_id: null,
      cross_check: 'config-missing',
      identity_confirmed: false,
      reason: 'active-account-unavailable',
      process_pid: null,
    })
    const selectedAccount = info.accounts.find((a) => a.user_id === '1688854831832237')
    assert.equal(selectedAccount?.config_found, false)
    assert.equal(selectedAccount?.login_company_id, undefined)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('locateDataDir：Config.cfg 存在值等于目录 user_id 的键 → cross_check=id-present-in-config', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-idkey-'))
  try {
    const dir = makeAccount(root, '1688854831832237', ['message.db'])
    writeFileSync(
      join(dir, 'Config.cfg'),
      cfgContent({ LoginCompanyId: '1970326113004107', UserIdKey: '1688854831832237' }),
      'latin1',
    )
    const info = await locateDataDir({ cefLogDir: null, candidateRoots: [{ root, source: 'documents' }] })
    assert.equal(info.identity.cross_check, 'id-present-in-config')
    assert.equal(info.identity.login_company_id, '1970326113004107')
    assert.equal(info.identity.config_found, true)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('locateDataDir：无 CEF 依据时 selected 按目录 mtime 新→旧（最近活跃启发式）；身份不确认（评审 v6）', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-mtime-'))
  try {
    const dirOld = makeAccount(root, '1688852699308616', ['message.db'])
    const dirNew = makeAccount(root, '1688854831832237', ['message.db'])
    utimesSync(dirOld, new Date('2026-10-01T00:00:00Z'), new Date('2026-10-01T00:00:00Z'))
    utimesSync(dirNew, new Date('2026-10-15T00:00:00Z'), new Date('2026-10-15T00:00:00Z'))
    const info = await locateDataDir({ cefLogDir: null, candidateRoots: [{ root, source: 'onedrive-documents' }] })
    assert.equal(info.user_id, '1688854831832237') // mtime 更新的账号排第一
    assert.equal(info.accounts[0]!.dir, dirNew)
    // 非 CEF 来源无法证明目录属于当前登录进程（多账号残留/同 PID 切号防误判）
    assert.equal(info.identity.identity_confirmed, false)
    assert.equal(info.identity.cross_check, 'no-id-key')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('locateDataDir：大 CEF 日志（>2MB）头尾窗口策略——启动行在头、切号新行在尾均可命中', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-biglog-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-bigcef-'))
  try {
    const dirTail = makeAccount(root, '1688854831832237', ['message.db'])
    makeAccount(root, '1688852699308616', ['message.db'])
    // >2MB 填充文件：头部一行命中 1688 账号（启动初始化形态），尾部一行命中 1688 账号（切号追加形态）
    const filler = 'x'.repeat(64) + '\r\n'
    const tailLine = hitLine('00:01:01.000', root, '1688854831832237', '1688854831832237') + '\r\n'
    writeFileSync(
      join(cefDir, cefFile('2026-10-15_00-00-26')),
      hitLine('00:00:26.500', root, '1688852699308616', '1688852699308616') + '\r\n' + filler.repeat(34000) + tailLine,
    )
    const info = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [{ root, source: 'onedrive-documents' }] })
    assert.equal(info.source, 'cef-log')
    assert.equal(info.user_id, '1688854831832237')
    assert.equal(info.dir, dirTail)
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})

test('locateDataDir：CEF 日志文件数超上限——只扫最新 N 个，上限外旧文件的命中不参与', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-cap-'))
  const cefDir = mkdtempSync(join(tmpdir(), 'wc-localdb-datadir-capcef-'))
  try {
    makeAccount(root, '1688854831832237', ['message.db'])
    // 共 CEF_LOG_MAX_FILES + 1 个文件：唯一命中排在最旧（最新 N 个窗口之外）→ CEF 层 miss，回退候选根
    writeFileSync(
      join(cefDir, cefFile('2026-09-01_00-00-00', '5.0.81.5503')),
      'cache_path:[RISK]' + join(root, '1688854831832237') + '\\WXWorkCefCache\r\n',
    )
    for (let i = 2; i <= CEF_LOG_MAX_FILES + 1; i++) {
      writeFileSync(join(cefDir, `2026-10-${String(i).padStart(2, '0')}_00-00-00(5.0.81.5503).log`), 'noise\r\n')
    }
    const info = await locateDataDir({ cefLogDir: cefDir, candidateRoots: [{ root, source: 'documents' }] })
    assert.equal(info.source, 'documents') // 唯一 CEF 命中被扫描窗口截断 → 回退候选根命中
    assert.equal(info.user_id, '1688854831832237')
  } finally {
    rmSync(root, { recursive: true, force: true })
    rmSync(cefDir, { recursive: true, force: true })
  }
})
