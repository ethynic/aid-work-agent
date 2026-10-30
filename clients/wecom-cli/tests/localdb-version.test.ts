import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { detectInstall, filetimeToEpochMs, parseProcessProbeOutput } from '../src/platform/localdb/version.js'
import { loadMatrix, lookupVersion } from '../src/platform/localdb/matrix.js'

/** 进程探测 miss 的替身：输出不含 PID= 行 */
const missExec = async () => ({ stdout: '' })

/** 进程探测抛错替身（PowerShell 缺失/超时/权限等） */
const errorExec = async () => {
  throw new Error('powershell missing')
}

/**
 * 由目标 epoch ms 反推 FILETIME 字符串（探测脚本透出 CreationDate.ToFileTime()，
 * 测试夹具按同一换算构造，保证 epoch 断言与时区无关）。
 */
const epochMsToFiletime = (epochMs: number): string => String((epochMs + 11_644_473_600_000) * 10_000)

/** 造单进程探测行（createdMs 缺省 → CREATED 段落空） */
const procLine = (pid: number, createdMs: number | null, path: string): string =>
  `PID=${pid}|CREATED=${createdMs === null ? '' : epochMsToFiletime(createdMs)}|PATH=${path}`

test('detectInstall：指定 PID 只查询该 WXWork 进程，不拿首个其他进程的版本准入', async () => {
  let command = ''
  const info = await detectInstall({ processPid: 4242, execFileFn: async (_file, args) => {
    command = args[args.indexOf('-Command') + 1]!
    return { stdout: procLine(4242, null, 'C:\\wx\\WXWork.exe') + '\nVERSION=5.0.11.6029' }
  } })
  assert.ok(command.includes("Name=''WXWork.exe'' AND ProcessId=4242"))
  assert.equal(info.pid, 4242)
  assert.equal(info.version, '5.0.11.6029')
})

test('detectInstall：进程命中 → 版本以进程 executable 为准 + 实例锚点（epoch ms），调用契约固定', async () => {
  const calls: Array<{ file: string; args: string[] }> = []
  const startedAt = 1_760_000_000_000
  const info = await detectInstall({
    execFileFn: async (file, args) => {
      calls.push({ file, args })
      return {
        stdout: [procLine(4242, startedAt, 'C:\\Program Files (x86)\\WXWork\\5.0.11.6029\\WXWork.exe'), 'VERSION=5.0.11.6029', ''].join('\r\n'),
      }
    },
    installRoot: join(tmpdir(), 'wc-localdb-version-nonexistent-root'),
    appData: join(tmpdir(), 'wc-localdb-version-nonexistent-appdata'),
  })
  // 只读 PowerShell 探测：单次调用、-NoProfile、-Command（关键参数存在即可，不绑定整串顺序）。
  // -Command 载荷钉住探针契约最小闭环：查询 WMI Win32_Process 里的 WXWork.exe（枚举全部、
  // 不截断——实例锚点需要全部进程创建时刻），输出行前缀 PID=/CREATED=/PATH=/VERSION= 与
  // parseProcessProbeOutput 解析契约一致——否则探针脚本回归在罐头输出单测下零失败逃逸。
  assert.equal(calls.length, 1)
  assert.equal(calls[0]!.file, 'powershell.exe')
  assert.ok(calls[0]!.args.includes('-NoProfile'))
  assert.ok(calls[0]!.args.includes('-Command'))
  const probeCommand = calls[0]!.args[calls[0]!.args.indexOf('-Command') + 1] ?? ''
  assert.ok(probeCommand.includes('Win32_Process'), '探测载荷应查询 Win32_Process')
  assert.ok(probeCommand.includes('WXWork.exe'), '探测载荷应过滤 WXWork.exe')
  assert.ok(!probeCommand.includes('Select-Object -First 1'), '实例锚点需全部进程，不得截断为首个')
  for (const marker of ['PID=', 'CREATED=', 'PATH=', 'VERSION=']) {
    assert.ok(probeCommand.includes(marker), `探测载荷应输出 ${marker} 段（与 parseProcessProbeOutput 契约一致）`)
  }
  assert.ok(probeCommand.includes('CreationDate'), '探测载荷应取进程 CreationDate')
  assert.equal(info.installed, true)
  assert.equal(info.exe_path, 'C:\\Program Files (x86)\\WXWork\\5.0.11.6029\\WXWork.exe')
  assert.equal(info.version, '5.0.11.6029')
  assert.equal(info.version_source, 'process')
  assert.equal(info.pid, 4242)
  assert.equal(info.process_started_at, startedAt) // FILETIME 换算回目标 epoch ms
  assert.equal(info.install_dir, 'C:\\Program Files (x86)\\WXWork\\5.0.11.6029')
  assert.equal(info.install_version, undefined)
  assert.deepEqual(info.upgrade_signals, [])
})

test('detectInstall：多进程 → pid/exe_path 取首个进程，process_started_at 取全部创建时刻最小值（earliest=实例启动）', async () => {
  const t1 = 1_760_000_000_000
  const t2 = t1 + 60_000 // 子进程晚一分钟
  const t3 = t1 + 120_000
  const info = await detectInstall({
    execFileFn: async () => ({
      stdout: [
        procLine(100, t2, 'C:\\wx\\WXWork.exe'),
        procLine(101, t1, 'C:\\wx\\WXWork.exe'), // 最早创建（乱序出现，验证取 min 而非首行）
        procLine(102, t3, 'C:\\wx\\WXWork.exe'),
        'VERSION=5.0.11.6029',
        '',
      ].join('\n'),
    }),
    installRoot: join(tmpdir(), 'wc-localdb-version-multiproc-root'),
    appData: join(tmpdir(), 'wc-localdb-version-multiproc-appdata'),
  })
  assert.equal(info.installed, true)
  assert.equal(info.pid, 100) // PID/路径仍取首个进程对象（与 keyscan 共用同一选择）
  assert.equal(info.exe_path, 'C:\\wx\\WXWork.exe')
  assert.equal(info.process_started_at, t1) // earliest：任一进程都属于本实例，实例起点=最早者
})

test('detectInstall：进程命中但缺 VERSION 行 → version 落空（version_source=null），pid/锚点保留', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-version-nover-'))
  const startedAt = 1_760_000_000_000
  try {
    mkdirSync(join(root, '4.1.5.7'), { recursive: true })
    writeFileSync(join(root, '4.1.5.7', 'WXWork.exe'), '')
    const info = await detectInstall({
      execFileFn: async () => ({ stdout: procLine(777, startedAt, 'C:\\wx\\WXWork.exe') + '\n' }),
      installRoot: root,
      appData: join(root, 'no-appdata'),
    })
    assert.equal(info.installed, true)
    assert.equal(info.exe_path, 'C:\\wx\\WXWork.exe')
    assert.equal(info.version, null)
    assert.equal(info.version_source, null)
    assert.equal(info.pid, 777)
    assert.equal(info.process_started_at, startedAt)
    assert.equal(info.install_version, undefined) // 进程命中时不做安装目录探测
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('detectInstall：进程行 CREATED 段落空 → 锚点 null（不可得时身份核验保守 unconfirmed）', async () => {
  const info = await detectInstall({
    execFileFn: async () => ({ stdout: 'PID=777|CREATED=|PATH=C:\\wx\\WXWork.exe\n' }),
    installRoot: join(tmpdir(), 'wc-localdb-version-nocreated-root'),
    appData: join(tmpdir(), 'wc-localdb-version-nocreated-appdata'),
  })
  assert.equal(info.installed, true)
  assert.equal(info.pid, 777)
  assert.equal(info.process_started_at, null)
})

test('detectInstall：进程查询失败 + 安装目录残留 5.0.11.6029 → version 必须 null、version_source 非 process、矩阵 validated 不得命中', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-residual-'))
  try {
    mkdirSync(join(root, '5.0.11.6029'), { recursive: true })
    writeFileSync(join(root, '5.0.11.6029', 'WXWork.exe'), '')
    const info = await detectInstall({ execFileFn: errorExec, installRoot: root, appData: join(root, 'no-appdata') })
    // 安装目录版本只落诊断字段，绝不冒充运行版本
    assert.equal(info.version, null)
    assert.notEqual(info.version_source, 'process')
    assert.equal(info.version_source, null)
    assert.equal(info.pid, null)
    assert.equal(info.process_started_at, null) // 查询失败 → 锚点不可得
    assert.equal(info.install_version, '5.0.11.6029')
    assert.equal(info.install_dir, join(root, '5.0.11.6029'))
    // 矩阵对 5.0.11.6029 判 validated 的前提：条目 7 必需字段齐备且取值合法
    // （残缺条目会被加载校验降级 unknown，前提即不成立）——先显式钉住前提，再验证安全网。
    const matrix = loadMatrix({
      schema_version: 1,
      entries: {
        '5.0.11.6029': {
          status: 'validated',
          algorithm: 'wxaes128cbc-nonlegacy',
          page_size: 4096,
          db_magic: 'b3df88da44b8102f',
          key_form: 'raw16-in-private-rw',
          scan_strategy: 'dual-md5-sha256-oracle',
          data_dir_pattern: '{documents}/WXWork/{user_id}',
          schema_profile: 'wecom-5.0.x',
        },
      },
    })
    assert.equal(lookupVersion(matrix, '5.0.11.6029').status, 'validated')
    // detectInstall 的 version=null 传入 lookupVersion → 未命中（null 不做字符串匹配），安全网不得放行
    assert.equal(lookupVersion(matrix, info.version).status, 'unknown')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('detectInstall：无版本子目录 → 回退根目录直置 WXWork.exe（version 落空、无 install_version）', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-flat-'))
  try {
    writeFileSync(join(root, 'WXWork.exe'), '')
    const info = await detectInstall({ execFileFn: missExec, installRoot: root, appData: join(root, 'no-appdata') })
    assert.equal(info.installed, true)
    assert.equal(info.exe_path, join(root, 'WXWork.exe'))
    assert.equal(info.version, null)
    assert.equal(info.version_source, null)
    assert.equal(info.pid, null)
    assert.equal(info.process_started_at, null)
    assert.equal(info.install_version, undefined)
    assert.equal(info.install_dir, root)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('detectInstall：进程与安装目录全 miss → installed=false 其余落空', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-allmiss-'))
  try {
    const info = await detectInstall({ execFileFn: missExec, installRoot: root, appData: join(root, 'no-appdata') })
    assert.equal(info.installed, false)
    assert.equal(info.exe_path, null)
    assert.equal(info.version, null)
    assert.equal(info.version_source, null)
    assert.equal(info.pid, null)
    assert.equal(info.process_started_at, null)
    assert.equal(info.install_dir, null)
    assert.equal(info.install_version, undefined)
    assert.deepEqual(info.upgrade_signals, [])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('detectInstall：WXWorkUpgrader 存在 → upgrader_dir；upgrade 目录近期 mtime → upgrade_dir_active', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-signals-'))
  try {
    mkdirSync(join(root, 'WXWorkUpgrader'), { recursive: true })
    const appData = join(root, 'appdata')
    const upgradeDir = join(appData, 'Tencent', 'WXWork', 'upgrade')
    mkdirSync(upgradeDir, { recursive: true })
    const info = await detectInstall({ execFileFn: missExec, installRoot: root, appData })
    assert.deepEqual([...info.upgrade_signals].sort(), ['upgrade_dir_active', 'upgrader_dir'])

    // upgrade 目录 mtime 窗口外（陈年残留）不算活动信号
    const stale = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000)
    utimesSync(upgradeDir, stale, stale)
    const info2 = await detectInstall({ execFileFn: missExec, installRoot: root, appData })
    assert.deepEqual(info2.upgrade_signals, ['upgrader_dir'])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('filetimeToEpochMs：FILETIME（100ns 自 1601-01-01 UTC）→ epoch ms；非数字/负值结果 → null', () => {
  const epoch = 1_760_000_000_000
  assert.equal(filetimeToEpochMs(epochMsToFiletime(epoch)), epoch)
  // Windows FILETIME 纪元起点 1601-01-01 附近：换算结果 ≤ 0 视为无效（真实进程创建时间不可能）
  assert.equal(filetimeToEpochMs('0'), null)
  assert.equal(filetimeToEpochMs('abc'), null)
  assert.equal(filetimeToEpochMs(''), null)
  assert.equal(filetimeToEpochMs('133xxx'), null)
})

test('parseProcessProbeOutput：标准多进程输出/BOM/噪声行/CREATED 非法/空输出/仅 VERSION 行', () => {
  const t1 = 1_760_000_000_000
  const t2 = 1_760_000_600_000
  // 标准两进程 + VERSION：earliest=t1
  assert.deepEqual(
    parseProcessProbeOutput([procLine(100, t2, 'C:\\a b\\WXWork.exe'), procLine(101, t1, 'C:\\a b\\WXWork.exe'), 'VERSION=5.0.11.6029'].join('\r\n')),
    { pid: 100, exe_path: 'C:\\a b\\WXWork.exe', version: '5.0.11.6029', process_started_at: t1 },
  )
  // BOM + 噪声行 + 路径含空格与 = 号
  assert.deepEqual(
    parseProcessProbeOutput(`\uFEFFsome noise\n${procLine(99, t1, 'C:\\x y\\WX=Work.exe')}\n`),
    { pid: 99, exe_path: 'C:\\x y\\WX=Work.exe', version: null, process_started_at: t1 },
  )
  // CREATED 非法（含字母）→ 锚点落 null；PID 非数字 → pid null；仅 VERSION 行可独立取版本
  assert.deepEqual(parseProcessProbeOutput('PID=12abc|CREATED=133abc|PATH=\nVERSION=1.2.3.4'), {
    pid: null,
    exe_path: null,
    version: '1.2.3.4',
    process_started_at: null,
  })
  // 全段落空的 PID 行 + 空输出
  assert.deepEqual(parseProcessProbeOutput('PID=|CREATED=|PATH='), {
    pid: null,
    exe_path: null,
    version: null,
    process_started_at: null,
  })
  assert.deepEqual(parseProcessProbeOutput(''), { pid: null, exe_path: null, version: null, process_started_at: null })
})
