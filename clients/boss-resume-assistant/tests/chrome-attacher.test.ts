/**
 * ChromeAttacher 保留导出测试（原 chrome-attach.test.ts 精简）。
 * 背景：M0.1 删除 run/cliRuntime 后，probe/diagnose 等探测函数随死导出清理，
 * 本模块只保留 DEFAULT_CDP_PORT 常量（端点探测由 CdpGateway.connect 负责）。
 * 意图：DEFAULT_CDP_PORT 是 7 个保留命令与 USAGE 文档共同的默认端口契约，
 * 被意外改动会让用户惯用的 9222 调试端口全部失配。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_CDP_PORT } from '../src/main/chrome/ChromeAttacher.js'

test('DEFAULT_CDP_PORT：保持 9222 且在合法端口范围内', () => {
  assert.equal(DEFAULT_CDP_PORT, 9222)
  assert.ok(Number.isInteger(DEFAULT_CDP_PORT) && DEFAULT_CDP_PORT > 0 && DEFAULT_CDP_PORT <= 65535)
})
