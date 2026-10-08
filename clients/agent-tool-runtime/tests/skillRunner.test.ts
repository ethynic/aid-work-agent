/**
 * skill-runner（M2）单测：
 * - frontmatter 子集解析（平铺/metadata 嵌套/flow/block 列表/未知键跳块/解析失败 fail-closed）；
 * - hash 镜像与云端共享向量一致（fixture/期望常量与仓库
 *   tests/unit/test_skill_plugin_gate.py 的 SHARED_VECTOR_* 同源抄录两侧、
 *   各自断言同一组常量，防双端算法漂移）；
 * - 命令门禁矩阵逐码（未安装/版本不符/entry 越白名单/entry∈mutable/路径逃逸/args 超限/合法通过）；
 * - spawn stub（node 代替 python 解释器）：echo JSON effect 覆盖/非零退出/挂起超时杀树/
 *   读 stdin 立即 EOF/AbortSignal 取消/单飞 busy/进度按行转发/stdout 截断保尾部；
 * - manifest 锁互斥声明（write_tools 归写 + shared_lock_capable=false 独占桌面锁）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import {
  gateSkillScriptRun,
  loadSkillDir,
  parseSkillFrontmatter,
  SKILL_ARGS_LIMITS,
  SKILL_TIMEOUT_HARD_CAP_SECONDS,
  SkillRunnerHandler,
  skillDirHash,
  discoverInstalledSkills,
} from '../src/skillRunner.js'
import { getProviderManifest, isWriteToolFor, manifestDigestFor } from '../src/providers.js'
import { ProviderBusyError } from '../src/providerManager.js'

// ---------------------------------------------------------------------------
// fixture：与云端共享的固定样例目录——内容与期望常量和仓库
// tests/unit/test_skill_plugin_gate.py 的 SHARED_VECTOR_FILES /
// SHARED_VECTOR_EXPECTED_EXEC_HASH(_NO_CACHE) 一字不动地同源抄录两侧，
// Python/TS 两侧测试各自对本地实现断言同一组常量（任一端改 hash 算法即该端
// 测试红，另一端同组向量即对账来源）。
// ---------------------------------------------------------------------------

const FIXTURE_FILES: Record<string, string> = {
  'SKILL.md':
    '---\nname: demo-skill\nversion: 1.2.3\nmetadata:\n  entry: scripts/main.js\n  mutable:\n    - references/cache.json\n---\n\n手册正文。\n',
  'scripts/main.js': "// 入口（stub）：echo JSON\nconsole.log('hello from demo-skill')\n",
  'scripts/util.js': 'export const answer = 42\n',
  'references/cache.json': '{"updated_at": "2026-01-01T00:00:00Z"}\n',
  '__pycache__/demo.cpython-312.pyc': '\x80\x04junk',
  'shots/shot-1.png': 'PNGDATA',
  '.DS_Store': 'desktop junk\n',
}

/** 无 mutable 排除的目录 hash（= 云端 compute_skill_dir_hash，共享向量全量值） */
const DEMO_DIR_HASH = 'a756571353dfe4f6aa9179d07d0568d5a8e258a5d82f96500f7440ba9efd8253'
/** mutable=['references/cache.json'] 排除的 exec hash（= 云端 compute_skill_exec_hash 共享向量排除值） */
const DEMO_EXEC_HASH = '5c53b7543e10f01c442d83d2715a581b6ed7a6a4e7c7d3f7a74bc06ec3492a57'

function buildFixture(files: Record<string, string> = FIXTURE_FILES, rootName = 'demo-skill'): string {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aidwork-sr-test-'))
  const skillDir = path.join(root, rootName)
  for (const [rel, content] of Object.entries(files)) {
    const abs = path.join(skillDir, rel)
    mkdirSync(path.dirname(abs), { recursive: true })
    writeFileSync(abs, content, 'utf8')
  }
  return root
}

// ---------------------------------------------------------------------------
// frontmatter 子集解析
// ---------------------------------------------------------------------------

test('frontmatter：metadata 嵌套 entry 标量 + mutable block 列表', () => {
  const fm = parseSkillFrontmatter(FIXTURE_FILES['SKILL.md']!)
  assert.ok(fm)
  assert.equal(fm.name, 'demo-skill')
  assert.equal(fm.version, '1.2.3')
  assert.deepEqual([...fm.entry], ['scripts/main.js'])
  assert.deepEqual([...fm.mutable], ['references/cache.json'])
})

test('frontmatter：平铺键 + flow 列表 + metadata 多入口 flow 列表', () => {
  const flat = parseSkillFrontmatter('---\nname: a\nentry: scripts/run.py\nmutable: [refs/x.json, refs/y.json]\n---\n')
  assert.ok(flat)
  assert.deepEqual([...flat.entry], ['scripts/run.py'])
  assert.deepEqual([...flat.mutable], ['refs/x.json', 'refs/y.json'])

  const metaFlow = parseSkillFrontmatter(
    '---\nname: b\nversion: 2.0.0\nmetadata:\n  entry: [scripts/flow.py, scripts/uicache.py]\n---\n',
  )
  assert.ok(metaFlow)
  assert.deepEqual([...metaFlow.entry], ['scripts/flow.py', 'scripts/uicache.py'])
  assert.deepEqual([...metaFlow.mutable], [])
})

test('frontmatter：未知键（含块标量/嵌套列表/深层子块）整体跳过，已知键照常解析', () => {
  const fm = parseSkillFrontmatter(
    [
      '---',
      'name: c',
      'description: |',
      '  多行',
      '  说明文字',
      'tags:',
      '  - alpha',
      '  - beta',
      'nested:',
      '  deep:',
      '    - x: 1',
      'metadata:',
      '  entry: scripts/main.py',
      '  note: |',
      '    hello',
      '    world',
      '  mutable:',
      '    - refs/cache.json',
      '---',
    ].join('\n'),
  )
  assert.ok(fm, '含未知键的 SKILL.md 不应整体失败')
  assert.equal(fm.name, 'c')
  assert.deepEqual([...fm.entry], ['scripts/main.py'])
  assert.deepEqual([...fm.mutable], ['refs/cache.json'])
})

test('frontmatter：解析失败 fail-closed（缺定界/缺 name/裸列表项/根级缩进键/非法行）', () => {
  assert.equal(parseSkillFrontmatter('name: x\n---\n'), null, '缺起始 ---')
  assert.equal(parseSkillFrontmatter('---\nname: x\n'), null, '缺结束 ---')
  assert.equal(parseSkillFrontmatter('---\nversion: 1.0\n---\n'), null, '缺 name')
  assert.equal(parseSkillFrontmatter('---\nname: x\nmetadata:\n  entry: [broken,\n---\n'), null, 'flow 列表未闭合')
  assert.equal(parseSkillFrontmatter('---\nname: x\n- orphan\n---\n'), null, '无宿主键的列表项')
  assert.equal(parseSkillFrontmatter('---\nname: x\n  indented: 1\n---\n'), null, '根级缩进键（非 metadata 语境）')
  assert.equal(parseSkillFrontmatter('---\nname:\n---\n'), null, 'name 空值')
  assert.equal(parseSkillFrontmatter('---\nmetadata: { entry: a.py }\nname: x\n---\n'), null, 'flow map 不在子集')
})

// ---------------------------------------------------------------------------
// hash 镜像（与云端参考实现共享向量）
// ---------------------------------------------------------------------------

test('hash 镜像：与云端参考实现实算向量一致（无 mutable / 含 mutable 排除）', () => {
  const skillsDir = buildFixture()
  const skillDir = path.join(skillsDir, 'demo-skill')
  assert.equal(skillDirHash(skillDir), DEMO_DIR_HASH, '无 mutable 排除应等于 compute_skill_dir_hash 参考值')
  assert.equal(
    skillDirHash(skillDir, ['references/cache.json']),
    DEMO_EXEC_HASH,
    'mutable 排除应等于 compute_skill_exec_hash 参考值',
  )
})

test('hash 镜像：mutable 内容变化不改变 exec_hash，非 mutable 变化必改变', () => {
  const skillsDir = buildFixture()
  const skillDir = path.join(skillsDir, 'demo-skill')
  // mutable 文件写回（自学习场景）
  writeFileSync(path.join(skillDir, 'references', 'cache.json'), '{"updated_at": "2026-10-06T00:00:00Z"}\n', 'utf8')
  assert.equal(skillDirHash(skillDir, ['references/cache.json']), DEMO_EXEC_HASH, 'mutable 变化不改 exec_hash')
  assert.notEqual(skillDirHash(skillDir), DEMO_DIR_HASH, 'computedHash 语义（无排除）应感知变化')
  // 入口脚本改动必须被 exec_hash 感知（entries ∩ mutable = ∅ 的意义）
  writeFileSync(path.join(skillDir, 'scripts', 'main.js'), "console.log('tampered')\n", 'utf8')
  assert.notEqual(skillDirHash(skillDir, ['references/cache.json']), DEMO_EXEC_HASH, '入口脚本篡改必须改变 exec_hash')
})

test('hash 镜像：排除集生效（__pycache__/shots/.pyc/.DS_Store 不参与）', () => {
  const skillsDir = buildFixture()
  const skillDir = path.join(skillsDir, 'demo-skill')
  writeFileSync(path.join(skillDir, 'shots', 'shot-2.png'), 'NEW-SHOT', 'utf8')
  writeFileSync(path.join(skillDir, '__pycache__', 'other.pyc'), 'junk2', 'utf8')
  writeFileSync(path.join(skillDir, '.DS_Store'), 'changed\n', 'utf8')
  assert.equal(skillDirHash(skillDir), DEMO_DIR_HASH, '排除集内文件变化不影响目录 hash')
})

// ---------------------------------------------------------------------------
// 目录发现
// ---------------------------------------------------------------------------

test('目录发现：discoverInstalledSkills 上报 name+exec_hash；SKILL.md 缺失目录跳过；SKILL.md 入 mutable 拒载', () => {
  const skillsDir = buildFixture()
  // broken-skill：name 可解析、无 entry —— 允许上报（执行时 entry 白名单为空自然全拒）
  mkdirSync(path.join(skillsDir, 'broken-skill'), { recursive: true })
  writeFileSync(path.join(skillsDir, 'broken-skill', 'SKILL.md'), '---\nname: broken\n---\n', 'utf8')
  // no-skill-md：无 SKILL.md，跳过
  mkdirSync(path.join(skillsDir, 'no-skill-md'), { recursive: true })
  // evil-skill：SKILL.md 声明自身 mutable（破坏信任锚），拒载
  const evilFiles: Record<string, string> = {
    'SKILL.md': '---\nname: evil-anchor\nmetadata:\n  entry: scripts/main.js\n  mutable:\n    - SKILL.md\n---\n',
    'scripts/main.js': "console.log('evil')\n",
  }
  mkdirSync(path.join(skillsDir, 'evil-skill'), { recursive: true })
  for (const [rel, content] of Object.entries(evilFiles)) {
    const abs = path.join(skillsDir, 'evil-skill', rel)
    mkdirSync(path.dirname(abs), { recursive: true })
    writeFileSync(abs, content, 'utf8')
  }

  const list = discoverInstalledSkills(skillsDir)
  // 目录名排序：broken-skill < demo-skill < evil-skill < no-skill-md；
  // 上报名取 frontmatter name（broken-skill 目录声明的 name 是 broken）
  assert.deepEqual(list.map((s) => s.name), ['broken', 'demo-skill'])
  assert.equal(list[1]!.hash, DEMO_EXEC_HASH)

  // SKILL.md ∈ mutable → 信任锚被破坏，loadSkillDir 拒载（fail-closed）
  assert.equal(loadSkillDir(path.join(skillsDir, 'evil-skill')), null)
  // 目录不存在 → 空清单
  assert.deepEqual(discoverInstalledSkills(path.join(skillsDir, 'missing-dir')), [])
})

// ---------------------------------------------------------------------------
// 命令门禁矩阵
// ---------------------------------------------------------------------------

function gate(payload: unknown, skillsDir = buildFixture()) {
  return gateSkillScriptRun(payload, skillsDir)
}

test('门禁：payload 非法逐项拒绝（INVALID_SKILL_PAYLOAD）', () => {
  const skillsDir = buildFixture()
  const base = { skill: 'demo-skill', entry: 'scripts/main.js', exec_hash: DEMO_EXEC_HASH }
  const cases: Array<[unknown, string]> = [
    [null, 'null'],
    ['string', '字符串'],
    [{ ...base, skill: '' }, 'skill 空'],
    [{ ...base, entry: '' }, 'entry 空'],
    [{ ...base, exec_hash: 42 }, 'exec_hash 非字符串'],
    [{ ...base, args: 'seq' }, 'args 非数组'],
    [{ ...base, args: ['ok', 3] }, 'args 元素非字符串'],
    [{ ...base, timeout_seconds: 0 }, 'timeout_seconds 0'],
    [{ ...base, timeout_seconds: -5 }, 'timeout_seconds 负数'],
  ]
  for (const [payload, label] of cases) {
    const r = gateSkillScriptRun(payload, skillsDir)
    assert.ok(!('ok' in r), `${label} 应拒绝`)
    assert.equal((r as { code: string }).code, 'INVALID_SKILL_PAYLOAD', `${label} 码`)
    assert.equal((r as { effect: string }).effect, 'none')
  }
})

test('门禁：技能未安装 → SKILL_NOT_INSTALLED（可重试）', () => {
  const r = gate({ skill: 'not-installed', entry: 'scripts/main.js', exec_hash: 'x' })
  assert.ok(!('ok' in r))
  assert.equal((r as { code: string }).code, 'SKILL_NOT_INSTALLED')
  assert.equal((r as { retryable: boolean }).retryable, true)
})

test('门禁：exec_hash 对账失败 → SKILL_VERSION_MISMATCH（可重试）', () => {
  const r = gate({ skill: 'demo-skill', entry: 'scripts/main.js', exec_hash: 'deadbeef' })
  assert.ok(!('ok' in r))
  assert.equal((r as { code: string }).code, 'SKILL_VERSION_MISMATCH')
  assert.equal((r as { retryable: boolean }).retryable, true)
})

test('门禁：entry 不在本地已验证 SKILL.md 白名单 → SKILL_ENTRY_NOT_ALLOWED（附合法入口清单）', () => {
  const r = gate({ skill: 'demo-skill', entry: 'scripts/util.js', exec_hash: DEMO_EXEC_HASH })
  assert.ok(!('ok' in r))
  assert.equal((r as { code: string }).code, 'SKILL_ENTRY_NOT_ALLOWED')
  const msg = (r as { message: string }).message
  assert.ok(msg.includes('scripts/main.js'), '文案应列出合法入口供 LLM 自纠')
  // 甚至不存在的路径也在此拒绝（先白名单后路径）
  const r2 = gate({ skill: 'demo-skill', entry: 'nope/other.py', exec_hash: DEMO_EXEC_HASH })
  assert.equal((r2 as { code: string }).code, 'SKILL_ENTRY_NOT_ALLOWED')
})

test('门禁：entry ∈ metadata.mutable → SKILL_GATE_REJECTED（entries ∩ mutable = ∅ 安全闭环）', () => {
  const files: Record<string, string> = {
    ...FIXTURE_FILES,
    'SKILL.md':
      '---\nname: overlap-skill\nmetadata:\n  entry: [references/cache.json, scripts/main.js]\n  mutable:\n    - references/cache.json\n---\n',
  }
  const skillsDir = buildFixture(files, 'overlap-skill')
  const info = loadSkillDir(path.join(skillsDir, 'overlap-skill'))!
  const r = gateSkillScriptRun(
    { skill: 'overlap-skill', entry: 'references/cache.json', exec_hash: info.execHash },
    skillsDir,
  )
  assert.ok(!('ok' in r))
  assert.equal((r as { code: string }).code, 'SKILL_GATE_REJECTED')
  // 白名单内非 mutable 入口仍可用
  const ok = gateSkillScriptRun({ skill: 'overlap-skill', entry: 'scripts/main.js', exec_hash: info.execHash }, skillsDir)
  assert.ok('ok' in ok, '非 mutable 入口应放行')
})

test('门禁：路径逃逸/绝对路径 → INVALID_ENTRY_PATH（即使声明在白名单内）', () => {
  const files: Record<string, string> = {
    ...FIXTURE_FILES,
    'SKILL.md':
      '---\nname: escape-skill\nmetadata:\n  entry: [../evil.js, /etc/passwd, scripts/main.js]\n  mutable:\n    - references/cache.json\n---\n',
  }
  const skillsDir = buildFixture(files, 'escape-skill')
  const info = loadSkillDir(path.join(skillsDir, 'escape-skill'))!
  for (const entry of ['../evil.js', '/etc/passwd']) {
    const r = gateSkillScriptRun({ skill: 'escape-skill', entry, exec_hash: info.execHash }, skillsDir)
    assert.ok(!('ok' in r), `${entry} 应拒绝`)
    assert.equal((r as { code: string }).code, 'INVALID_ENTRY_PATH', `${entry} 码`)
  }
})

test('门禁：args 数量/单长/总长超限 → SKILL_ARGS_LIMIT_EXCEEDED', () => {
  const base = { skill: 'demo-skill', entry: 'scripts/main.js', exec_hash: DEMO_EXEC_HASH }
  const tooMany = gate({ ...base, args: Array.from({ length: SKILL_ARGS_LIMITS.maxArgs + 1 }, () => 'a') })
  assert.equal((tooMany as { code: string }).code, 'SKILL_ARGS_LIMIT_EXCEEDED')

  const tooLong = gate({ ...base, args: ['x'.repeat(SKILL_ARGS_LIMITS.maxArgChars + 1)] })
  assert.equal((tooLong as { code: string }).code, 'SKILL_ARGS_LIMIT_EXCEEDED')

  const totalOver = gate({ ...base, args: Array.from({ length: 9 }, () => 'x'.repeat(SKILL_ARGS_LIMITS.maxArgChars)) })
  assert.equal((totalOver as { code: string }).code, 'SKILL_ARGS_LIMIT_EXCEEDED')

  // 边界值（恰好上限）应通过
  const edge = gate({
    ...base,
    args: Array.from({ length: 8 }, () => 'x'.repeat(SKILL_ARGS_LIMITS.maxArgChars)),
  })
  assert.ok('ok' in edge, '8×500=4000 恰好在总长上限内')
})

test('门禁：合法通过 + timeout 截顶（云端下发 9999 → 硬顶 1800；缺省 900）', () => {
  const r = gate({ skill: 'demo-skill', entry: 'scripts/main.js', exec_hash: DEMO_EXEC_HASH, args: ['seq', 'verify'], timeout_seconds: 9999 })
  assert.ok('ok' in r)
  assert.equal(r.entry, 'scripts/main.js')
  assert.deepEqual(r.args, ['seq', 'verify'])
  assert.equal(r.timeoutSeconds, SKILL_TIMEOUT_HARD_CAP_SECONDS)
  assert.equal(r.timeoutSeconds, 1800)

  const def = gate({ skill: 'demo-skill', entry: 'scripts/main.js', exec_hash: DEMO_EXEC_HASH })
  assert.ok('ok' in def)
  assert.equal(def.timeoutSeconds, 900)
  assert.deepEqual(def.args, [])
})

// ---------------------------------------------------------------------------
// manifest 锁互斥声明（providers.test.ts 仅授权改计数一行，故断言置此）
// ---------------------------------------------------------------------------

test('manifest 声明：skill-runner 单工具面、归写集合、独占桌面锁（与三 CLI 同互斥域）', () => {
  const m = getProviderManifest('skill-runner')!
  assert.ok(m, 'TRUSTED_MANIFESTS 应含 skill-runner')
  assert.equal(m.provider_id, 'ai.aidwork.skill-runner')
  assert.deepEqual([...m.tools], ['skill_script_run'])
  assert.equal(m.execution_target, 'local_required')
  assert.equal(m.protocol_version, 1, 'v1 形态：v2 invocation 被 PROTOCOL_NOT_SUPPORTED 拒绝')
  assert.equal(m.shared_lock_capable, false, '独占桌面锁 = 与三 CLI 同互斥域（不并发控制桌面）')
  assert.equal(isWriteToolFor(m, 'skill_script_run'), true, 'RPA 键鼠注入按写对待：锁屏前置 + 崩溃 EXECUTION_UNKNOWN')
  assert.match(manifestDigestFor('skill-runner'), /^[0-9a-f]{64}$/)
  assert.notEqual(manifestDigestFor('skill-runner'), manifestDigestFor('boss-recruiting'), 'digest 独立，三 CLI digest 不受影响')
})

// ---------------------------------------------------------------------------
// 执行（stub 解释器 = node；fixture entry 为 node 脚本）
// ---------------------------------------------------------------------------

interface Scenario {
  skillsDir: string
  execHash: string
  handler: SkillRunnerHandler
}

/** 场景 fixture：SKILL.md 声明 scripts/main.js 入口，脚本内容由调用方给定 */
function buildScenario(scriptContent: string, name = 'stub-skill', mutable: string[] = []): Scenario {
  const skillMd =
    mutable.length > 0
      ? `---\nname: ${name}\nmetadata:\n  entry: scripts/main.js\n  mutable:\n${mutable.map((m) => `    - ${m}`).join('\n')}\n---\n`
      : `---\nname: ${name}\nmetadata:\n  entry: scripts/main.js\n---\n`
  const skillsDir = buildFixture({ ...FIXTURE_FILES, 'SKILL.md': skillMd, 'scripts/main.js': scriptContent }, name)
  const info = loadSkillDir(path.join(skillsDir, name))!
  return {
    skillsDir,
    execHash: info.execHash,
    handler: new SkillRunnerHandler({ skillsDir, pythonPath: process.execPath }),
  }
}

const HANG_JS = "console.log('started')\nsetInterval(function () {}, 1000)\n"

test('执行：合法通过（exec_hash 对账后 spawn），exit 0 → success + effect applied + stdout 回传', async () => {
  const s = buildScenario("console.log('hello from demo-skill')")
  const progress: string[] = []
  const r = await s.handler.callTool(
    'skill_script_run',
    { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash, args: ['--flag'] },
    { onProgress: (p) => progress.push(p.message ?? '') },
  )
  assert.equal(r['success'], true)
  assert.equal(r['effect'], 'applied')
  const data = r['data'] as { stdout: string; exit_code: number; duration_ms: number }
  assert.equal(data.exit_code, 0)
  assert.ok(data.stdout.includes('hello from demo-skill'))
  assert.ok(typeof data.duration_ms === 'number' && data.duration_ms >= 0)
  assert.ok(progress.includes('hello from demo-skill'), 'stdout 行应转发进度')
})

test('执行：stdout 尾部 JSON {"effect":"none"} 覆盖 effect（合法枚举才生效）', async () => {
  const s = buildScenario("console.log('done')\nconsole.log(JSON.stringify({ effect: 'none' }))")
  const r = await s.handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash })
  assert.equal(r['success'], true)
  assert.equal(r['effect'], 'none')

  const invalid = buildScenario("console.log('x')\nconsole.log(JSON.stringify({ effect: 'partial' }))")
  const ri = await invalid.handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: invalid.execHash })
  assert.equal(ri['effect'], 'applied', '非法枚举不覆盖，保持默认 applied')

  const plain = buildScenario('console.log("not json")')
  const rp = await plain.handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: plain.execHash })
  assert.equal(rp['effect'], 'applied')
})

test('执行：非零退出 → success:false + effect unknown + exit_code 回传（禁自动重试）', async () => {
  const s = buildScenario("console.error('boom')\nprocess.exit(3)")
  const r = await s.handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash })
  assert.equal(r['success'], false)
  assert.equal(r['code'], 'SKILL_EXIT_NONZERO')
  assert.equal(r['effect'], 'unknown')
  assert.equal(r['retryable'], false)
  const data = r['data'] as { exit_code: number | null; stderr: string }
  assert.equal(data.exit_code, 3)
  assert.ok(data.stderr.includes('boom'))
})

test('执行：挂起脚本超时 → SKILL_TIMEOUT + 进程树终止（timeout_seconds=1）', async () => {
  const s = buildScenario(HANG_JS)
  const r = await s.handler.callTool(
    'skill_script_run',
    { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash, timeout_seconds: 1 },
  )
  assert.equal(r['success'], false)
  assert.equal(r['code'], 'SKILL_TIMEOUT')
  assert.equal(r['effect'], 'unknown')
  const data = r['data'] as { duration_ms: number }
  assert.ok(data.duration_ms >= 900, `超时路径应等待 ≥900ms（实际 ${data.duration_ms}）`)
})

test('执行：读 stdin 脚本立即 EOF 快速失败（stdin ignore，不挂到超时）', async () => {
  const stdinJs = [
    "var chunks = []",
    "process.stdin.on('data', function (c) { chunks.push(c) })",
    "process.stdin.on('end', function () {",
    "  if (chunks.length === 0) { console.error('EOF without input'); process.exit(2) }",
    "  process.stdout.write(chunks.join(''))",
    "})",
    "process.stdin.on('error', function () { console.error('stdin error'); process.exit(2) })",
  ].join('\n')
  const s = buildScenario(stdinJs)
  const r = await s.handler.callTool(
    'skill_script_run',
    { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash, timeout_seconds: 30 },
  )
  assert.equal(r['code'], 'SKILL_EXIT_NONZERO', '应因 EOF 立即失败，而非挂到超时')
  const data = r['data'] as { duration_ms: number }
  assert.ok(data.duration_ms < 10_000, `EOF 失败应快速（实际 ${data.duration_ms}ms）`)
})

test('执行：AbortSignal 取消 → SKILL_ABORTED + 进程树终止', async () => {
  const s = buildScenario(HANG_JS)
  const ac = new AbortController()
  const pending = s.handler.callTool(
    'skill_script_run',
    { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash, timeout_seconds: 60 },
    { signal: ac.signal },
  )
  setTimeout(() => ac.abort(), 150)
  const r = await pending
  assert.equal(r['success'], false)
  assert.equal(r['code'], 'SKILL_ABORTED')
  assert.equal(r['effect'], 'unknown')
})

test('执行：单飞 busy——并发第二调用抛 ProviderBusyError', async () => {
  const s = buildScenario(HANG_JS)
  const ac = new AbortController()
  const first = s.handler.callTool(
    'skill_script_run',
    { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash, timeout_seconds: 60 },
    { signal: ac.signal },
  )
  await new Promise((resolve) => setTimeout(resolve, 100))
  await assert.rejects(
    () => s.handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash }),
    (err: unknown) => err instanceof ProviderBusyError,
  )
  ac.abort()
  await first
})

test('执行：stdout 截断保尾部（超出上限丢弃头部）', async () => {
  const s = buildScenario("process.stdout.write('A'.repeat(250) + 'B'.repeat(50))")
  const handler = new SkillRunnerHandler({ skillsDir: s.skillsDir, pythonPath: process.execPath, stdoutLimitChars: 100 })
  const r = await handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash })
  const data = r['data'] as { stdout: string }
  assert.equal(data.stdout, 'A'.repeat(50) + 'B'.repeat(50), '截断应保留尾部 100 字符')
})

test('执行：门禁失败经 callTool 返回结构化结果（不 throw），非法工具名拒绝', async () => {
  const s = buildScenario("console.log('x')")
  const bad = await s.handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/other.js', exec_hash: s.execHash })
  assert.equal(bad['success'], false)
  assert.equal(bad['code'], 'SKILL_ENTRY_NOT_ALLOWED')
  assert.equal(bad['effect'], 'none')

  const wrongTool = await s.handler.callTool('boss_greet', {})
  assert.equal(wrongTool['code'], 'TOOL_NOT_ALLOWED')
})

test('执行：解释器不存在 → SKILL_SPAWN_FAILED（effect none，进程未起）', async () => {
  const s = buildScenario("console.log('x')")
  const handler = new SkillRunnerHandler({ skillsDir: s.skillsDir, pythonPath: path.join(os.tmpdir(), 'no-such-python-bin') })
  const r = await handler.callTool('skill_script_run', { skill: 'stub-skill', entry: 'scripts/main.js', exec_hash: s.execHash })
  assert.equal(r['success'], false)
  assert.equal(r['code'], 'SKILL_SPAWN_FAILED')
  assert.equal(r['effect'], 'none')
})
