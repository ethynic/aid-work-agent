import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { validateJobConfig, loadJobConfigFile } from '../src/cli/jobConfig.js'

// ===== validateJobConfig（纯对象校验）=====

test('岗位配置：合法配置完整解析', () => {
  const cfg = validateJobConfig({
    name: '高级前端工程师',
    hardRules: { city: ['杭州'], minYears: 3, requiredSkills: ['Vue'], excludeKeywords: ['外包'] },
    preferences: '3 年以上经验优先',
    actionLimits: { session: 50, day: 200 },
  })
  assert.equal(cfg.name, '高级前端工程师')
  assert.deepEqual(cfg.hardRules.city, ['杭州'])
  assert.equal(cfg.hardRules.minYears, 3)
  assert.equal(cfg.preferences, '3 年以上经验优先')
  assert.equal(cfg.actionLimitSession, 50)
  assert.equal(cfg.actionLimitDay, 200)
})

test('岗位配置：最小配置（仅 name）通过，可选项为空', () => {
  const cfg = validateJobConfig({ name: '  岗位A  ' })
  assert.equal(cfg.name, '岗位A')
  assert.deepEqual(cfg.hardRules, {})
  assert.equal(cfg.preferences, null)
  assert.equal(cfg.actionLimitSession, null)
  assert.equal(cfg.actionLimitDay, null)
})

test('岗位配置 fail-loud：缺 name / name 为空', () => {
  assert.throws(() => validateJobConfig({}), /name 必填/)
  assert.throws(() => validateJobConfig({ name: '  ' }), /name 必填/)
  assert.throws(() => validateJobConfig({ name: 123 }), /name 必填/)
})

test('岗位配置 fail-loud：非法硬规则键（键白名单）', () => {
  assert.throws(() => validateJobConfig({ name: 'A', hardRules: { salary: 100 } }), /未知键 "salary"/)
})

test('岗位配置 fail-loud：硬规则类型错误', () => {
  assert.throws(() => validateJobConfig({ name: 'A', hardRules: { city: '杭州' } }), /hardRules\.city 必须是非空字符串数组/)
  assert.throws(() => validateJobConfig({ name: 'A', hardRules: { city: [''] } }), /hardRules\.city 必须是非空字符串数组/)
  assert.throws(() => validateJobConfig({ name: 'A', hardRules: { minYears: -1 } }), /hardRules\.minYears 必须是正整数/)
  assert.throws(() => validateJobConfig({ name: 'A', hardRules: { requiredSkills: 'Vue' } }), /requiredSkills 必须是非空字符串数组/)
})

test('岗位配置 fail-loud：动作上限非法 / 未知键', () => {
  assert.throws(() => validateJobConfig({ name: 'A', actionLimits: { session: 0 } }), /actionLimits\.session 必须是正整数/)
  assert.throws(() => validateJobConfig({ name: 'A', actionLimits: { week: 10 } }), /未知键 "week"/)
})

test('岗位配置 fail-loud：preferences 非字符串', () => {
  assert.throws(() => validateJobConfig({ name: 'A', preferences: ['x'] }), /preferences 必须是字符串/)
})

// ===== loadJobConfigFile（YAML/JSON 文件加载）=====

function withTempDir(fn: (dir: string) => void): void {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jobcfg-'))
  try {
    fn(dir)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

test('岗位配置：YAML 文件加载', () => {
  withTempDir((dir) => {
    const file = path.join(dir, 'job.yaml')
    fs.writeFileSync(
      file,
      ['name: 后端工程师', 'hardRules:', '  city: [北京]', '  minYears: 5', 'actionLimits:', '  session: 30', ''].join('\n'),
    )
    const cfg = loadJobConfigFile(file)
    assert.equal(cfg.name, '后端工程师')
    assert.deepEqual(cfg.hardRules.city, ['北京'])
    assert.equal(cfg.hardRules.minYears, 5)
    assert.equal(cfg.actionLimitSession, 30)
  })
})

test('岗位配置：JSON 文件加载', () => {
  withTempDir((dir) => {
    const file = path.join(dir, 'job.json')
    fs.writeFileSync(file, JSON.stringify({ name: '测试岗', hardRules: { excludeKeywords: ['外包'] } }))
    const cfg = loadJobConfigFile(file)
    assert.equal(cfg.name, '测试岗')
    assert.deepEqual(cfg.hardRules.excludeKeywords, ['外包'])
  })
})

test('岗位配置 fail-loud：文件不存在 / 解析失败 / 格式不支持', () => {
  withTempDir((dir) => {
    assert.throws(() => loadJobConfigFile(path.join(dir, 'nope.yaml')), /不存在/)

    const bad = path.join(dir, 'bad.json')
    fs.writeFileSync(bad, '{invalid json')
    assert.throws(() => loadJobConfigFile(bad), /解析失败/)

    const txt = path.join(dir, 'job.txt')
    fs.writeFileSync(txt, 'name: A')
    assert.throws(() => loadJobConfigFile(txt), /不支持的配置文件格式/)
  })
})
