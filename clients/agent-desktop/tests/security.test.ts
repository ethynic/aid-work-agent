import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'
import {
  createSecureWebPreferences,
  createContentSecurityPolicy,
  isAllowedAgentRoute,
  isAllowedMainFrameNavigation,
  isTrustedIpcSender,
  mapSchemeRequest,
  normalizeApiBaseUrl,
  resolveInsideRoot,
} from '../electron/security.js'

test('仅允许 Agent 与租户路由，平台 Portal 始终拒绝', () => {
  for (const route of ['/', '/chat/general', '/knowledge-base', '/t/acme', '/t/acme/chat/general']) {
    assert.equal(isAllowedAgentRoute(route), true, route)
  }
  for (const route of ['/portal', '/portal/', '/Portal', '/portal/login', '/%70ortal', '/%2570ortal', '/portal%2flogin', '/portal%252flogin', '/subagents', '/t', '/unknown']) {
    assert.equal(isAllowedAgentRoute(route), false, route)
  }
})

test('API base URL 仅允许 HTTPS 与本机开发 HTTP', () => {
  assert.equal(normalizeApiBaseUrl('https://api.example.com/api/'), 'https://api.example.com/api')
  assert.equal(normalizeApiBaseUrl('http://localhost:8000'), 'http://localhost:8000')
  assert.equal(normalizeApiBaseUrl('http://127.0.0.1:8000'), 'http://127.0.0.1:8000')
  for (const url of [
    'http://api.example.com',
    'http://localhost.evil.example',
    'ftp://api.example.com',
    'https://u:p@api.example.com',
    'https://u%40example.com@api.example.com',
    'https://api.example.com?a=1',
    'https://api.example.com#fragment',
  ]) {
    assert.throws(() => normalizeApiBaseUrl(url), url)
  }
})

test('scheme 映射对合法 history 路由回退且拒绝 Portal/traversal', () => {
  assert.deepEqual(mapSchemeRequest('aidagent://app/t/acme/chat'), { kind: 'history-fallback', relativePath: 'index.html' })
  assert.deepEqual(mapSchemeRequest('aidagent://app/assets/app-123.js'), { kind: 'file', relativePath: 'assets/app-123.js' })
  for (const url of [
    'aidagent://app/portal',
    'aidagent://app/subagents',
    'aidagent://app/%70ortal',
    'aidagent://app/portal%2flogin',
    'aidagent://app/Portal',
    'aidagent://app/%2570ortal',
    'aidagent://app/portal/../chat/general',
    'aidagent://app/../chat/general',
    'aidagent://app/%2e%2e/secret',
    'aidagent://app/t/acme/%252e%252e/secret',
    'aidagent://app/t/acme/%2e%2e/secret',
    'aidagent://evil/t/acme',
    'aidagent://app.evil/t/acme',
    'aidagent://evil@app/t/acme',
    'aidagent://app:443/t/acme',
    'https://app/t/acme',
    'aidagent://app/t/acme?next=/portal',
    'aidagent://app/t/acme#portal',
  ]) {
    assert.equal(mapSchemeRequest(url).kind, 'reject', url)
  }
})

test('CSP 只允许精确 API origin，不给任意 HTTPS 主机开放连接', () => {
  const policy = createContentSecurityPolicy('https://api.example.com/api')
  assert.match(policy, /connect-src 'self' https:\/\/api\.example\.com/)
  assert.doesNotMatch(policy, /connect-src[^;]*\shttps:(?:\s|;|$)/)
  assert.match(policy, /object-src 'none'/)
  assert.match(policy, /frame-src 'none'/)
})

test('scheme 文件解析不能逃逸 renderer 根目录', () => {
  const root = path.resolve('dist/renderer')
  assert.equal(resolveInsideRoot(root, 'index.html'), path.join(root, 'index.html'))
  for (const relativePath of ['../secret.txt', '..\\secret.txt', path.resolve(root, '..', 'secret.txt')]) {
    assert.throws(() => resolveInsideRoot(root, relativePath), relativePath)
  }
})

test('IPC only accepts the current Desktop main window main frame', () => {
  assert.equal(isTrustedIpcSender('aidagent://app/t/acme/chat', true, true), true)
  assert.equal(isTrustedIpcSender('aidagent://app/portal', true, true), false)
  assert.equal(isTrustedIpcSender('https://api.example.com', true, true), false)
  assert.equal(isTrustedIpcSender('aidagent://app/t/acme/chat', false, true), false)
  assert.equal(isTrustedIpcSender('aidagent://app/t/acme/chat', true, false), false)
})

test('main-frame navigation only accepts Agent history routes', () => {
  assert.equal(isAllowedMainFrameNavigation('aidagent://app/t/acme/chat'), true)
  assert.equal(isAllowedMainFrameNavigation('aidagent://app/assets/app.js'), false)
  assert.equal(isAllowedMainFrameNavigation('https://example.com'), false)
})

test('Electron renderer 安全配置固定开启隔离与 Web Security', () => {
  assert.deepEqual(createSecureWebPreferences('C:/preload.js'), {
    preload: 'C:/preload.js',
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
  })
})
