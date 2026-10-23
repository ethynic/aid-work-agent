/**
 * Phase 7：诊断导出 home 定位——installation-v2/current.json 指针跟随 + legacy 路径兼容。
 * 复盘背景：2026-10-20 采集器固定读 legacy 目录，在 installation-v2 代次运行正常的
 * 机器上误报 homeExists=false。本套只验证只读定位逻辑（不写指针、不建安装结构）。
 */
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { resolvePrimaryRuntimeHome, resolveRuntimeHomeCandidates } from '../src/diagnostics.js'

/** 造一个 installation-v2 指针现场（对齐 runtimeInstallation 写入的真实形状；不创建 home 目录本身） */
function writePointer(appData: string, userDataDir: string, home: string): void {
  const root = path.join(appData, userDataDir, 'installation-v2')
  mkdirSync(root, { recursive: true })
  writeFileSync(
    path.join(root, 'current.json'),
    JSON.stringify({ version: 2, id: randomUUID(), home, priorHome: null, initialized: true }),
    'utf8',
  )
}

function makeAppData(): string {
  return mkdtempSync(path.join(os.tmpdir(), 'aidwork-diag-home-'))
}

test('指针跟随：installation-v2 current.json 指向的 home 存在 → 实际 home 取指针（修复 homeExists=false 误报）', () => {
  const appData = makeAppData()
  try {
    const home = path.join(appData, 'aidwork-runtime-app-acceptance', 'installation-v2', 'generations', randomUUID(), 'connections', randomUUID())
    mkdirSync(home, { recursive: true })
    writePointer(appData, 'aidwork-runtime-app-acceptance', home)
    // legacy 目录不存在——旧采集器在此场景误报 homeExists=false
    const candidates = resolveRuntimeHomeCandidates(appData)
    const acceptance = candidates.find((c) => c.profile === 'acceptance')!
    assert.equal(acceptance.source, 'installation-v2-pointer')
    assert.equal(acceptance.home, home)
    assert.equal(acceptance.homeExists, true)
    assert.equal(acceptance.legacyHomeExists, false)
    const primary = resolvePrimaryRuntimeHome(appData)
    assert.ok(primary)
    assert.equal(primary.home, home)
    assert.equal(primary.homeExists, true)
  } finally {
    rmSync(appData, { recursive: true, force: true })
  }
})

test('legacy 兼容：无指针时回退固定路径（旧安装行为不变）', () => {
  const appData = makeAppData()
  try {
    mkdirSync(path.join(appData, 'aidwork-tool-runtime'), { recursive: true })
    const candidates = resolveRuntimeHomeCandidates(appData)
    assert.equal(candidates.find((c) => c.profile === 'acceptance')!.source, 'legacy-path')
    const production = candidates.find((c) => c.profile === 'production')!
    assert.equal(production.source, 'legacy-path')
    assert.equal(production.home, path.join(appData, 'aidwork-tool-runtime'))
    assert.equal(production.homeExists, true)
    const primary = resolvePrimaryRuntimeHome(appData)!
    assert.equal(primary.profile, 'production')
    assert.equal(primary.home, path.join(appData, 'aidwork-tool-runtime'))
  } finally {
    rmSync(appData, { recursive: true, force: true })
  }
})

test('指针存在但指向目录缺失 → 回退 legacy，指针值仍如实上报', () => {
  const appData = makeAppData()
  try {
    const missing = path.join(appData, 'aidwork-runtime-app', 'installation-v2', 'generations', randomUUID(), 'connections', randomUUID())
    writePointer(appData, 'aidwork-runtime-app', missing) // home 目录不存在
    const candidates = resolveRuntimeHomeCandidates(appData)
    const production = candidates.find((c) => c.profile === 'production')!
    assert.equal(production.source, 'legacy-path')
    assert.equal(production.pointerHome, missing)
    assert.equal(production.homeExists, false)
  } finally {
    rmSync(appData, { recursive: true, force: true })
  }
})

test('指针损坏（非法 JSON / 形状非法 / 指向 generations 之外）→ 记录 pointerError 并回退 legacy，不抛出', () => {
  const appData = makeAppData()
  try {
    mkdirSync(path.join(appData, 'aidwork-tool-runtime-acceptance'), { recursive: true })
    const root = path.join(appData, 'aidwork-runtime-app-acceptance', 'installation-v2')
    mkdirSync(root, { recursive: true })
    writeFileSync(path.join(root, 'current.json'), '{not-json', 'utf8')
    let candidates = resolveRuntimeHomeCandidates(appData)
    let acceptance = candidates.find((c) => c.profile === 'acceptance')!
    assert.equal(acceptance.pointerError, 'pointer-corrupt-json')
    assert.equal(acceptance.source, 'legacy-path')

    writeFileSync(path.join(root, 'current.json'), JSON.stringify({ version: 1, home: 'C:\\tmp' }), 'utf8')
    candidates = resolveRuntimeHomeCandidates(appData)
    acceptance = candidates.find((c) => c.profile === 'acceptance')!
    assert.equal(acceptance.pointerError, 'pointer-invalid-shape')

    writeFileSync(
      path.join(root, 'current.json'),
      JSON.stringify({ version: 2, home: path.join(appData, 'outside-home') }),
      'utf8',
    )
    candidates = resolveRuntimeHomeCandidates(appData)
    acceptance = candidates.find((c) => c.profile === 'acceptance')!
    assert.equal(acceptance.pointerError, 'pointer-home-outside-generations')
    assert.equal(acceptance.source, 'legacy-path')
    assert.equal(acceptance.homeExists, true, 'legacy 存在则 homeExists 仍为真')
  } finally {
    rmSync(appData, { recursive: true, force: true })
  }
})

test('全新机器（无任何 home）→ homeExists=false 是真实事实，primary 为 null', () => {
  const appData = makeAppData()
  try {
    const candidates = resolveRuntimeHomeCandidates(appData)
    assert.ok(candidates.every((c) => !c.homeExists))
    assert.ok(candidates.every((c) => c.pointerHome === null && c.pointerError === null))
    assert.equal(resolvePrimaryRuntimeHome(appData), null)
  } finally {
    rmSync(appData, { recursive: true, force: true })
  }
})
