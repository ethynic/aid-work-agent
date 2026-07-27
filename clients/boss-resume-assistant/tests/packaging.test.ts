import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import path from 'node:path'

/** 依赖扫描：禁止 Playwright / Puppeteer / Selenium（设计文档 §6.3 要求） */
test('package.json 直接依赖无浏览器自动化框架', () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.resolve('package.json'), 'utf8'),
  ) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> }

  const all = { ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) }
  const keys = Object.keys(all).join(' ').toLowerCase()
  const forbidden = ['playwright', 'puppeteer', 'selenium']
  for (const fw of forbidden) {
    assert.ok(!keys.includes(fw), `forbidden dependency: ${fw}`)
  }
})

test('package.json 直接依赖无 CDP 高级封装库（用原生 WebSocket）', () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.resolve('package.json'), 'utf8'),
  ) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> }

  const all = { ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) }
  const keys = Object.keys(all).join(' ').toLowerCase()
  // chrome-remote-interface 等会隐式管理 execution context，禁用
  const forbidden = ['chrome-remote-interface', 'chrome-launcher']
  for (const fw of forbidden) {
    assert.ok(!keys.includes(fw), `forbidden dependency: ${fw}`)
  }
})

test('better-sqlite3 为运行时依赖', () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.resolve('package.json'), 'utf8'),
  ) as { dependencies?: Record<string, string> }
  assert.ok(pkg.dependencies && pkg.dependencies['better-sqlite3'], 'better-sqlite3 must be a dependency')
})

test('main 入口指向 dist/src/main/main.js', () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.resolve('package.json'), 'utf8'),
  ) as { main?: string }
  assert.equal(pkg.main, 'dist/src/main/main.js')
})

test('electron-builder.yml 排除 tests 与 sourcemap', () => {
  const yml = fs.readFileSync(path.resolve('electron-builder.yml'), 'utf8')
  assert.ok(yml.includes('"!**/tests/**"'), 'electron-builder should exclude tests')
  assert.ok(yml.includes('"!**/*.map"'), 'electron-builder should exclude sourcemaps')
  assert.ok(yml.includes('better-sqlite3'), 'electron-builder should include better-sqlite3')
})
