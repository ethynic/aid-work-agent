/**
 * 设备能力上报：providers 数组 / protocol_version / provider_manifests / 旧 provider_id 兼容；
 * 配置兼容：旧 config.json 无 providers 字段照常工作；bossCliEntry 高优先级折算。
 * M2 扩展：skills.python 配置 → skill-runner 能力 + skills 清单（name + exec_hash）上报。
 */
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
const existingEntry = fileURLToPath(new URL('./helpers/fakeProvider.js', import.meta.url))
import { deviceCapabilities, resolveProviderEntries, type RuntimeConfig } from '../src/config.js'
import { manifestDigestFor } from '../src/providers.js'

function makeConfig(overrides: Partial<RuntimeConfig> = {}): RuntimeConfig {
  return { server: 'http://127.0.0.1:1', device_id: 'dev-1', bossCliEntry: existingEntry, ...overrides }
}

// M2 fixture：与 tests/skillRunner.test.ts 同字节的 demo-skill 样例（期望 hash 同源）
const M2_FIXTURE_FILES: Record<string, string> = {
  'SKILL.md':
    '---\nname: demo-skill\nversion: 1.2.3\nmetadata:\n  entry: scripts/main.js\n  mutable:\n    - references/cache.json\n---\n\n手册正文。\n',
  'scripts/main.js': "// 入口（stub）：echo JSON\nconsole.log('hello from demo-skill')\n",
  'scripts/util.js': 'export const answer = 42\n',
  'references/cache.json': '{"updated_at": "2026-01-01T00:00:00Z"}\n',
  '__pycache__/demo.cpython-312.pyc': '\x80\x04junk',
  'shots/shot-1.png': 'PNGDATA',
  '.DS_Store': 'desktop junk\n',
}
const M2_DEMO_EXEC_HASH = '5c53b7543e10f01c442d83d2715a581b6ed7a6a4e7c7d3f7a74bc06ec3492a57'

function buildSkillsDir(): string {
  const skillsDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-caps-skills-'))
  for (const [rel, content] of Object.entries(M2_FIXTURE_FILES)) {
    const abs = path.join(skillsDir, 'demo-skill', rel)
    mkdirSync(path.dirname(abs), { recursive: true })
    writeFileSync(abs, content, 'utf8')
  }
  return skillsDir
}

test("boss-only（显式 legacy bossCliEntry）：providers=['boss-recruiting']，旧 provider_id 字段保持", () => {
  const caps = deviceCapabilities(makeConfig())
  assert.deepEqual(caps['providers'], ['boss-recruiting'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting')
  assert.equal(caps['protocol_version'], 2)
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.deepEqual(Object.keys(manifests), ['boss-recruiting'])
  assert.equal(manifests['boss-recruiting']!['provider_id'], 'ai.aidwork.boss-recruiting')
  assert.equal(manifests['boss-recruiting']!['manifest_digest'], manifestDigestFor('boss-recruiting'))
  assert.equal(manifests['boss-recruiting']!['protocol_version'], 1)
})

test('配置 weixin entry：providers 数组含 weixin，manifests 双份，旧 provider_id 仍为第一个可用 provider', () => {
  const caps = deviceCapabilities(makeConfig({ providers: { weixin: { entry: existingEntry } } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'weixin'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting', 'boss 恒为第一个可用 provider（云端兼容）')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['weixin']!['provider_id'], 'ai.aidwork.weixin')
  assert.equal(manifests['weixin']!['manifest_digest'], manifestDigestFor('weixin'))
  assert.notEqual(manifests['weixin']!['manifest_digest'], manifests['boss-recruiting']!['manifest_digest'])
})

test('配置 wecom entry：providers 数组含 wecom，manifests 含独立摘要，capabilities 上报 6 工具能力', () => {
  const caps = deviceCapabilities(makeConfig({ providers: { wecom: { entry: existingEntry } } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'wecom'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting', 'boss 恒为第一个可用 provider（云端兼容）')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['wecom']!['provider_id'], 'ai.aidwork.wecom')
  assert.equal(manifests['wecom']!['manifest_digest'], manifestDigestFor('wecom'))
  assert.notEqual(manifests['wecom']!['manifest_digest'], manifests['boss-recruiting']!['manifest_digest'])
  assert.equal(manifests['wecom']!['protocol_version'], 1)
  // M11b：wecom 工具能力按工具名上报（与 manifest 同源；粒度对齐 weixin_message_send_v2）
  const capabilities = caps['capabilities'] as string[]
  for (const tool of ['wecom_probe', 'wecom_message_send', 'wecom_send_image', 'wecom_send_file', 'wecom_read_session', 'wecom_unread_list']) {
    assert.ok(capabilities.includes(tool), `${tool} 应在 capabilities`)
  }
  // 未接入 runtime 受信面的工具不得上报
  for (const tool of ['wecom_chat_search', 'wecom_chat_select', 'wecom_watch_poll', 'wecom_add_customer']) {
    assert.ok(!capabilities.includes(tool), `${tool} 未接入不得上报`)
  }
})

test('未配置 wecom entry：capabilities 与 provider_manifests 均不含 wecom（未安装不上报）', () => {
  const caps = deviceCapabilities(makeConfig({ providers: { weixin: { entry: existingEntry } } }))
  assert.ok(!(caps['providers'] as string[]).includes('wecom'))
  assert.equal((caps['provider_manifests'] as Record<string, unknown>)['wecom'], undefined)
  const capabilities = caps['capabilities'] as string[]
  assert.ok(!capabilities.some((c) => c.startsWith('wecom_')), 'wecom 未安装不得上报任何工具能力')
})

test('配置兼容：旧 config.json 只有 bossCliEntry 时 resolveProviderEntries 正常折算', () => {
  const entries = resolveProviderEntries(makeConfig({ bossCliEntry: 'C:/boss/entry.js' }))
  assert.deepEqual(entries, { 'boss-recruiting': 'C:/boss/entry.js' })

  // bossCliEntry 优先级高于 providers['boss-recruiting'].entry
  const folded = resolveProviderEntries(makeConfig({
    bossCliEntry: 'C:/boss/high.js',
    providers: { 'boss-recruiting': { entry: 'C:/boss/low.js' }, weixin: { entry: 'C:/wx/entry.js' } },
  }))
  assert.equal(folded['boss-recruiting'], 'C:/boss/high.js')
  assert.equal(folded['weixin'], 'C:/wx/entry.js')

  // 仅 providers 配置 boss entry（无 bossCliEntry）
  const fromProviders = resolveProviderEntries(makeConfig({ bossCliEntry: undefined, providers: { 'boss-recruiting': { entry: 'C:/boss/from-providers.js' } } }))
  assert.equal(fromProviders['boss-recruiting'], 'C:/boss/from-providers.js')

  // 空 Host 没有隐式业务入口。
  assert.deepEqual(resolveProviderEntries(null), {})
})

test('entry 解析：providers 中非法条目（entry 非字符串/空）被忽略，不影响 boss', () => {
  const bogus = makeConfig({ providers: Object.assign(Object.create(null), { weixin: { entry: 42 } }) })
  const entries = resolveProviderEntries(bogus as unknown as RuntimeConfig)
  assert.equal(entries['weixin'], undefined)
  assert.ok(entries['boss-recruiting'])
})


test('D5 能力上报与实际生效 manifest 同源：默认 v1；v2Send 显式协商后协议/工具/摘要一致', async () => {
  const { setManifestOverride, weixinV2Manifest, manifestDigestOf } = await import('../src/providers.js')
  const { deviceCapabilities } = await import('../src/config.js')
  const baseConfig: Record<string, unknown> = {
    server: 'http://x', device_id: 'd',
    providers: { weixin: { entry: existingEntry } },
  }
  // 默认（未协商）：协议 1、工具集合无 v2、能力不含 v2 发送
  const def = deviceCapabilities(baseConfig as never) as { providers: string[]; capabilities: string[]; provider_manifests: Record<string, { protocol_version: number; manifest_digest: string }> }
  assert.equal(def.provider_manifests['weixin']!.protocol_version, 1)
  assert.ok(!def.capabilities.includes('weixin_message_send_v2'))
  // 显式协商 v2Send：manifest 升级 v2 变体（cli 启动注入，此处直接模拟）后，
  // 上报协议 2、摘要 = v2 变体摘要、能力含 v2 发送
  setManifestOverride('weixin', weixinV2Manifest())
  try {
    const v2cfg: Record<string, unknown> = { ...baseConfig, providers: { weixin: { entry: existingEntry, v2Send: true } } }
    const negotiated = deviceCapabilities(v2cfg as never) as typeof def
    const v2 = weixinV2Manifest()
    assert.equal(negotiated.provider_manifests['weixin']!.protocol_version, 2)
    assert.equal(negotiated.provider_manifests['weixin']!.manifest_digest, manifestDigestOf(v2))
    assert.ok(negotiated.capabilities.includes('weixin_message_send_v2'))
  } finally {
    setManifestOverride('weixin', null)
  }
  // 撤销后回归
  const reverted = deviceCapabilities(baseConfig as never) as typeof def
  assert.equal(reverted.provider_manifests['weixin']!.protocol_version, 1)
  assert.ok(!reverted.capabilities.includes('weixin_message_send_v2'))
})

// ---------------------------------------------------------------------------
// M2 skill-runner：能力真实性（skills.python 配置且存在才上报）+ skills 清单
// ---------------------------------------------------------------------------

test('M2 skills.python 配置且存在：providers 含 skill-runner（键序 boss 首位）+ manifests 摘要 + skills 清单', () => {
  const skillsDir = buildSkillsDir()
  const caps = deviceCapabilities(makeConfig({ skills: { python: process.execPath, dir: skillsDir } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'skill-runner'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting', 'boss 恒首位（云端兼容字段不受影响）')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['skill-runner']!['provider_id'], 'ai.aidwork.skill-runner')
  assert.equal(manifests['skill-runner']!['manifest_digest'], manifestDigestFor('skill-runner'))
  assert.equal(manifests['skill-runner']!['protocol_version'], 1)
  // skills 清单：name + exec_hash（云端版本门对账数据源）
  assert.deepEqual(caps['skills'], [{ name: 'demo-skill', hash: M2_DEMO_EXEC_HASH }])
})

test('M2 能力真实性：skills.python 未配置 / 文件不存在 → 不上报 skill-runner 与 skills 字段', () => {
  const skillsDir = buildSkillsDir()
  for (const cfg of [
    makeConfig(),
    makeConfig({ skills: { python: path.join(os.tmpdir(), 'no-such-python-interpreter'), dir: skillsDir } }),
    makeConfig({ skills: { dir: skillsDir } }),
  ]) {
    const caps = deviceCapabilities(cfg)
    assert.ok(!(caps['providers'] as string[]).includes('skill-runner'), '未配置/解释器缺失不得上报 skill-runner')
    assert.equal((caps['provider_manifests'] as Record<string, unknown>)['skill-runner'], undefined)
    assert.equal(caps['skills'], undefined, 'skills 清单仅随 skill-runner 能力上报')
  }
})

test('M2 skills 目录不存在：skill-runner 可用但清单为空数组（区分「无技能」与「无执行器」）', () => {
  const caps = deviceCapabilities(makeConfig({ skills: { python: process.execPath, dir: path.join(os.tmpdir(), 'aidwork-missing-skills-dir') } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'skill-runner'])
  assert.deepEqual(caps['skills'], [])
})

test('M2 键序契约：weixin+wecom+skills 并存 → boss 首位、其余字典序（skill-runner 介于其间）', () => {
  const skillsDir = buildSkillsDir()
  const caps = deviceCapabilities(makeConfig({
    providers: {
      weixin: { entry: existingEntry },
      wecom: { entry: existingEntry },
    },
    skills: { python: process.execPath, dir: skillsDir },
  }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'skill-runner', 'wecom', 'weixin'])
  const manifests = caps['provider_manifests'] as Record<string, unknown>
  assert.deepEqual(Object.keys(manifests), ['boss-recruiting', 'skill-runner', 'wecom', 'weixin'])
})

test('M2 skills 清单上报上限：超过 50 条截断（按目录名排序保留前 50）', () => {
  const skillsDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-caps-many-'))
  for (let i = 0; i <= 50; i++) {
    const dir = path.join(skillsDir, `skill-${String(i).padStart(2, '0')}`)
    mkdirSync(dir, { recursive: true })
    writeFileSync(path.join(dir, 'SKILL.md'), `---\nname: skill-${String(i).padStart(2, '0')}\n---\n`, 'utf8')
  }
  const caps = deviceCapabilities(makeConfig({ skills: { python: process.execPath, dir: skillsDir } }))
  const skills = caps['skills'] as Array<{ name: string; hash: string }>
  assert.equal(skills.length, 50, '51 条 → 截断为 50')
  assert.ok(!skills.some((s) => s.name === 'skill-50'), '排序末位被截断')
  assert.equal(skills[0]!.name, 'skill-00')
  for (const s of skills) assert.match(s.hash, /^[0-9a-f]{64}$/, '每条含 exec_hash')
})


test('空Host与缺失entry不上报业务能力，避免领取无法执行的任务', () => {
  for (const config of [null, {server: 'http://isolated.invalid', device_id: 'empty'}, makeConfig({bossCliEntry: 'C:/missing/boss.js', providers: {weixin: {entry: 'C:/missing/weixin.js'}}})]) {
    const caps = deviceCapabilities(config)
    assert.deepEqual(caps.providers, [])
    assert.deepEqual(caps.provider_manifests, {})
    assert.equal(caps.provider_id, undefined)
    assert.ok(!(caps.capabilities as string[]).includes('session_task_v1'))
  }
})
