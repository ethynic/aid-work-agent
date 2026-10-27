import test from 'node:test'
import assert from 'node:assert/strict'
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import {
  loadMatrix,
  localDbStateDir,
  lookupVersion,
  readLocalState,
  writeLocalState,
  STATE_FILE_NAME,
} from '../src/platform/localdb/matrix.js'

/** status='validated' 条目的必需字段全集（与 version-matrix.json 实际字段一致，缺一即降级） */
const FULL_VALIDATED_ENTRY = {
  status: 'validated',
  algorithm: 'wxaes128cbc-nonlegacy',
  page_size: 4096,
  db_magic: 'b3df88da44b8102f',
  key_form: 'raw16-in-private-rw',
  scan_strategy: 'dual-md5-sha256-oracle',
  data_dir_pattern: '{documents}/WXWork/{user_id}',
  schema_profile: 'wecom-5.0.x',
}

test('loadMatrix：显式路径/已解析对象均可加载（schema_version=1）；entries 缺失归一为空表', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wc-localdb-matrix-'))
  try {
    const file = join(dir, 'matrix.json')
    writeFileSync(
      file,
      JSON.stringify({ schema_version: 1, updated_at: '2026-10-14', entries: { '1.2.3.4': FULL_VALIDATED_ENTRY } }),
    )
    const fromFile = loadMatrix(file)
    assert.equal(fromFile?.schema_version, 1)
    assert.equal(fromFile?.updated_at, '2026-10-14')
    assert.equal(fromFile?.entries['1.2.3.4']?.status, 'validated')

    const fromObject = loadMatrix({ schema_version: 1, entries: { '1.2.3.4': FULL_VALIDATED_ENTRY } })
    assert.equal(fromObject?.entries['1.2.3.4']?.status, 'validated')

    // entries 缺失/非法 → 合法矩阵但空表（与文件损坏 null 语义分开）
    assert.deepEqual(loadMatrix({ schema_version: 1 }), { schema_version: 1, entries: {} })
    assert.deepEqual(loadMatrix({ schema_version: 1, entries: 'no' }), { schema_version: 1, entries: {} })
    // 非对象形态 → null
    assert.equal(loadMatrix(42), null)
    assert.equal(loadMatrix([1, 2]), null)
    assert.equal(loadMatrix(null), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('loadMatrix：schema_version 必须 === 1（模块常量 SUPPORTED_SCHEMA_VERSION），缺失/99 → null（调用方按全 unknown 处理）', () => {
  // 更高版本（未来格式，本模块不认）→ null，绝不按旧语义解析
  assert.equal(loadMatrix({ schema_version: 99, entries: { '5.0.11.6029': { status: 'validated' } } }), null)
  // 缺 schema_version（无法确认格式）→ null
  assert.equal(loadMatrix({ entries: { '5.0.11.6029': { status: 'validated' } } }), null)
  assert.equal(loadMatrix({}), null)
  assert.equal(loadMatrix({ schema_version: '1' }), null) // 字符串形式不认
  // 文件路径注入同样受门槛约束
  const dir = mkdtempSync(join(tmpdir(), 'wc-localdb-matrix-schema-'))
  try {
    const file = join(dir, 'matrix.json')
    writeFileSync(file, JSON.stringify({ schema_version: 99, entries: {} }))
    assert.equal(loadMatrix(file), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('loadMatrix：默认路径可加载随代码发布的矩阵（仓库/dist 任一候选位置，validated 条目可解析）', () => {
  const matrix = loadMatrix()
  assert.ok(matrix, '默认矩阵应可从 dist/src 或 src/platform/localdb 的 version-matrix.json 加载')
  assert.equal(matrix.schema_version, 1)
  assert.equal(matrix.entries['5.0.11.6029']?.status, 'validated')
})

test('已验证的小版本复用既有算法，但不放行相邻未验证版本', () => {
  const matrix = loadMatrix()
  const current = lookupVersion(matrix, '5.0.13.6005')
  const previous = lookupVersion(matrix, '5.0.12.6018')
  assert.equal(current.status, 'validated')
  assert.ok(current.entry && previous.entry)
  for (const field of ['algorithm', 'page_size', 'key_form', 'scan_strategy', 'schema_profile'] as const) {
    assert.equal(current.entry[field], previous.entry[field], `${field} 应复用已经真机复核的方案`)
  }
  assert.equal(lookupVersion(matrix, '5.0.13.6006').status, 'unknown')
  assert.equal(lookupVersion(matrix, '5.0.13').status, 'unknown')
})

test('发行布局冒烟：临时目录只含 dist/src 树 + drivers（无 src）——dist 处的编译模块 loadMatrix 默认路径可加载；缺矩阵资产则 null', async () => {
  // 复刻发行包布局：打包器整拷 dist/src + drivers，不含源码 src。
  // 仓库内的默认路径测试覆盖不了发行场景，这里把编译后的 matrix.js 与矩阵资产放进隔离 stage
  // 再动态 import，从 stage 的 dist 位置验证默认路径解析。
  const stage = mkdtempSync(join(tmpdir(), 'wc-localdb-release-'))
  try {
    const stageLocaldb = join(stage, 'dist', 'src', 'platform', 'localdb')
    mkdirSync(stageLocaldb, { recursive: true })
    mkdirSync(join(stage, 'drivers'), { recursive: true }) // 顶层资产仅 drivers（与打包器一致）
    assert.ok(!existsSync(join(stage, 'src')), '发行布局模拟前提：stage 内不得存在 src 树')

    // 编译产物原样入位（运行本测试时的 dist/src/platform/localdb/matrix.js）
    const compiledMatrixJs = fileURLToPath(new URL('../src/platform/localdb/matrix.js', import.meta.url))
    // 正向素材取 dist 中的矩阵文件——scripts/copy-matrix.mjs 的产物（npm test 链先 build:main，必然存在）。
    // 不取仓库源码 src：否则只验证加载路径，脚本失效时本测试仍绿，冒不住发行包丢资产的回归。
    const matrixAssetSrc = fileURLToPath(new URL('../src/platform/localdb/version-matrix.json', import.meta.url))
    copyFileSync(compiledMatrixJs, join(stageLocaldb, 'matrix.js'))

    // 负向：stage 内只有编译模块、无矩阵资产 → 两处候选路径都缺失 → null（全 unknown 兜底）
    const modWithoutAsset = await import(pathToFileURL(join(stageLocaldb, 'matrix.js')).href)
    assert.equal(modWithoutAsset.loadMatrix(), null, '发行布局缺矩阵资产时应落 null，不得读到仓库 src')

    // 正向：构建资产复制到位（scripts/copy-matrix.mjs 的产物位置）→ 默认路径命中 dist 资产
    copyFileSync(matrixAssetSrc, join(stageLocaldb, 'version-matrix.json'))
    const mod = await import(pathToFileURL(join(stageLocaldb, 'matrix.js')).href)
    const matrix = mod.loadMatrix()
    assert.ok(matrix, '发行布局内 loadMatrix 默认路径应命中 dist/src/platform/localdb/version-matrix.json')
    assert.equal(matrix.schema_version, 1)
    assert.equal(matrix.entries['5.0.11.6029']?.status, 'validated')
  } finally {
    rmSync(stage, { recursive: true, force: true })
  }
})

test('loadMatrix：文件缺失/JSON 损坏 → null 不抛出', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wc-localdb-matrix-broken-'))
  try {
    assert.equal(loadMatrix(join(dir, 'missing.json')), null)
    const broken = join(dir, 'broken.json')
    writeFileSync(broken, '{oops')
    assert.equal(loadMatrix(broken), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('lookupVersion：validated 命中（schema_version=1）；broken/stale 条目透传条目自身 status', () => {
  const matrix = loadMatrix({
    schema_version: 1,
    entries: {
      '5.0.11.6029': FULL_VALIDATED_ENTRY,
      '4.0.0.1': { status: 'broken', notes: '指纹漂移' },
      '4.0.0.2': { status: 'stale' },
    },
  })
  assert.equal(lookupVersion(matrix, '5.0.11.6029').status, 'validated')
  assert.equal(lookupVersion(matrix, '4.0.0.1').status, 'broken')
  assert.equal(lookupVersion(matrix, '4.0.0.2').status, 'stale')
  // 命中条目原样返回（字段透传给指纹预检/分派）
  const hit = lookupVersion(matrix, '4.0.0.1')
  assert.equal(hit.entry?.notes, '指纹漂移')
})

test('lookupVersion：validated 条目缺必需字段或类型/值非法 → 加载即降级 unknown（保守禁扫），条目保留供诊断', () => {
  // 只有 status=validated 的残缺条目：加载后 status 必须降级为 unknown
  const matrix = loadMatrix({ schema_version: 1, entries: { '5.0.11.6029': { status: 'validated' } } })
  assert.ok(matrix, '残缺条目不否定矩阵本身')
  const r = lookupVersion(matrix, '5.0.11.6029')
  assert.equal(r.status, 'unknown')
  assert.ok(r.entry, '降级条目保留供诊断')
  assert.equal(r.entry?.status, 'unknown')
  // 缺部分字段同样降级
  const partial = loadMatrix({ schema_version: 1, entries: { '5.0.11.6030': { ...FULL_VALIDATED_ENTRY, db_magic: undefined } } })
  assert.equal(lookupVersion(partial, '5.0.11.6030').status, 'unknown')
  // 字段存在但类型/值非法同样降级（代表性组合：空串字符串字段 + 非正整数 page_size，不穷举）
  const invalid = loadMatrix({
    schema_version: 1,
    entries: { '5.0.11.6031': { ...FULL_VALIDATED_ENTRY, db_magic: '', page_size: 0 } },
  })
  const r2 = lookupVersion(invalid, '5.0.11.6031')
  assert.equal(r2.status, 'unknown')
  assert.ok(r2.entry, '类型/值非法的降级条目同样保留供诊断')
  // 字段齐备且取值合法的 validated 条目不受影响
  const full = loadMatrix({ schema_version: 1, entries: { '5.0.11.6029': FULL_VALIDATED_ENTRY } })
  assert.equal(lookupVersion(full, '5.0.11.6029').status, 'validated')
})

test('lookupVersion：未命中/矩阵缺失 → {entry:null, status:unknown}；严格精确匹配无前缀', () => {
  const matrix = loadMatrix({ schema_version: 1, entries: { '5.0.11.6029': { status: 'validated' } } })
  assert.deepEqual(lookupVersion(matrix, '5.0.11.6030'), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(matrix, '5.0.11'), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(matrix, ' 5.0.11.6029'), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(matrix, ''), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(matrix, null), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(null, '5.0.11.6029'), { entry: null, status: 'unknown' })
  // 条目 status 字段异常 → 归并 unknown（条目仍返回）
  const weird = loadMatrix({ schema_version: 1, entries: { '9.9.9.9': { status: 'wat' } } })
  const r = lookupVersion(weird, '9.9.9.9')
  assert.equal(r.status, 'unknown')
  assert.ok(r.entry)
})

test('lookupVersion：原型键（__proto__/toString 等）不算条目 → {entry:null, status:unknown}', () => {
  const matrix = loadMatrix({ schema_version: 1, entries: { '5.0.11.6029': { status: 'validated' } } })
  assert.deepEqual(lookupVersion(matrix, '__proto__'), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(matrix, 'toString'), { entry: null, status: 'unknown' })
  assert.deepEqual(lookupVersion(matrix, 'constructor'), { entry: null, status: 'unknown' })
  // JSON 文本里的 "__proto__" 自有键：跳过存储（不改写 entries 原型），同表兄弟条目不受影响
  // （兄弟条目用 stale——validated 需要必需字段齐备，与本用例关注点无关）
  const fromJson = loadMatrix(
    JSON.parse('{"schema_version":1,"entries":{"__proto__":{"status":"validated"},"1.2.3.4":{"status":"stale"}}}'),
  )
  assert.deepEqual(lookupVersion(fromJson, '__proto__'), { entry: null, status: 'unknown' })
  assert.equal(lookupVersion(fromJson, '1.2.3.4').status, 'stale')
})

test('localDbStateDir：LOCALAPPDATA 拼接为 AidWorkAgent/wecom-cli/localdb；显式 null → null', () => {
  assert.equal(localDbStateDir('C:\\la'), join('C:\\la', 'AidWorkAgent', 'wecom-cli', 'localdb'))
  assert.equal(localDbStateDir(null), null)
})

test('readLocalState/writeLocalState：原子写入回读一致、附加字段保留、覆盖写、无临时文件残留', () => {
  const root = mkdtempSync(join(tmpdir(), 'wc-localdb-state-'))
  try {
    const dir = join(root, 'nested', 'localdb') // 目录不存在 → 自动创建
    assert.equal(
      writeLocalState(
        { mode: 'db', kind: undefined, reason: 'ok', version: '5.0.11.6029', checked_at: '2026-10-14T00:00:00Z', custom: 'x' },
        dir,
      ),
      true,
    )
    const state = readLocalState(dir)
    assert.equal(state?.mode, 'db')
    assert.equal(state?.version, '5.0.11.6029')
    assert.equal(state?.checked_at, '2026-10-14T00:00:00Z')
    assert.equal(state?.custom, 'x')
    // 原子写入：目录内只剩正式状态文件，无 .tmp 残留
    assert.deepEqual(readdirSync(dir), [STATE_FILE_NAME])
    // 覆盖写（rename 覆盖已有文件）
    assert.equal(writeLocalState({ mode: 'ui', kind: 'compat', reason: '版本不在矩阵' }, dir), true)
    const second = readLocalState(dir)
    assert.equal(second?.mode, 'ui')
    assert.equal(second?.kind, 'compat')
    assert.equal(second?.version, undefined)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('readLocalState：mode 契约——非法值拒绝，缺失视为损坏（null，统一 unset 语义）；unset 是合法值', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wc-localdb-state-mode-'))
  try {
    const write = (raw: unknown) => writeFileSync(join(dir, STATE_FILE_NAME), JSON.stringify(raw))
    // 合法 mode 全集 {db, ui, unset}（ui 必须带合法 kind，缺省 kind 的规则见 kind 契约用例）
    for (const mode of ['db', 'ui', 'unset'] as const) {
      write(mode === 'ui' ? { mode, kind: 'compat', reason: 'x' } : { mode, reason: 'x' })
      assert.equal(readLocalState(dir)?.mode, mode, `mode=${mode} 应合法`)
    }
    // 非法/缺失/非字符串 → null（调用方按 unset 处理，不猜）
    write({ mode: 'nonsense' })
    assert.equal(readLocalState(dir), null)
    write({ reason: '缺 mode' })
    assert.equal(readLocalState(dir), null)
    write({ mode: 42 })
    assert.equal(readLocalState(dir), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('readLocalState：kind 契约——mode=ui 必须带合法 kind（缺省/null/非法 → null）；非 ui 模式 kind 归一丢弃', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wc-localdb-state-kind-'))
  try {
    const write = (raw: unknown) => writeFileSync(join(dir, STATE_FILE_NAME), JSON.stringify(raw))
    write({ mode: 'ui', kind: 'transient', reason: 'x' })
    assert.equal(readLocalState(dir)?.kind, 'transient')
    // ui 态缺省 kind 没有兼容语义 → 整体视为损坏（原「null 视同缺省放行」规则翻转，不部分采纳）
    write({ mode: 'ui', reason: '缺 kind' })
    assert.equal(readLocalState(dir), null)
    write({ mode: 'ui', kind: null })
    assert.equal(readLocalState(dir), null)
    // kind 非法：哪怕 mode 合法也整体视为损坏（不部分采纳）
    write({ mode: 'ui', kind: 'nonsense' })
    assert.equal(readLocalState(dir), null)
    // 非 ui 模式：kind 仅 ui 模式有意义，带值 → 归一为缺省（丢弃），整体仍有效
    write({ mode: 'db', kind: 'compat' })
    const normalized = readLocalState(dir)
    assert.equal(normalized?.mode, 'db')
    assert.equal(normalized?.kind, undefined)
    assert.equal(normalized === null || 'kind' in normalized, false, '归一后不得残留 kind 键')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('readLocalState：文件缺失/JSON 损坏/非对象 → null（视为 unset，不抛出）', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wc-localdb-state-corrupt-'))
  try {
    assert.equal(readLocalState(dir), null) // 文件不存在
    writeFileSync(join(dir, STATE_FILE_NAME), '{broken')
    assert.equal(readLocalState(dir), null)
    writeFileSync(join(dir, STATE_FILE_NAME), '"just a string"')
    assert.equal(readLocalState(dir), null)
    writeFileSync(join(dir, STATE_FILE_NAME), '[1,2]')
    assert.equal(readLocalState(dir), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('readLocalState/writeLocalState：目录不可定位（null）→ 读取 null / 写入 false', () => {
  assert.equal(readLocalState(null), null)
  assert.equal(writeLocalState({ mode: 'db' }, null), false)
})
