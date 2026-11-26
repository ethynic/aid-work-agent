import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { createWeixinFriendAddOperation } from '../src/operations/friendAdd.js'
import { getOperationEntry } from '../src/operations/registry.js'
import { CancelledError, CodedOperationError } from '../src/operations/types.js'

const executable = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
const ctx = { signal: new AbortController().signal, progress() {} }

test('friend add rejects ambiguous targets and unsafe verification text before running any driver', async () => {
  const op = createWeixinFriendAddOperation(async () => assert.fail('driver must not start'))
  for (const args of [
    { identifier: 'wx_user_a,wx_user_b' },
    { identifier: 'wx_user_a', verifyMessage: 'a\nb' },
    { identifier: 'wx_user_a', verifyMessage: ' ' },
    { identifier: 'wx_user_a', verifyMessage: 'a'.repeat(61) },
  ]) {
    const result = await op.execute(args, ctx)
    assert.equal(result.code, 'INVALID_ARGUMENT')
    assert.equal(result.effect, 'none')
  }
})

test('the signed MCP tool validates the snake-case verification argument before any desktop operation', async () => {
  const operation = getOperationEntry('weixin_friend_add').operation
  const signal = AbortSignal.abort()
  const result = await operation.execute({ identifier: 'wx_user_a', verify_message: 'line\nbreak' }, { ...ctx, signal })
  assert.equal(result.code, 'INVALID_ARGUMENT')
  assert.equal(result.effect, 'none')
})

test('friend add sends one trimmed search identifier and exact message over private child stdin', async () => {
  const verifyMessage = ' 你好，来自团队🤝 '
  const progress: string[] = []
  const op = createWeixinFriendAddOperation(async (input) => {
    assert.equal(input.args, undefined)
    assert.equal(input.signal, ctx.signal)
    assert.deepEqual(JSON.parse(input.stdin!), { identifier: '+8613800000000', verify_message: verifyMessage })
    return { status: 'request_sent' }
  })
  const result = await op.execute({ identifier: '  +8613800000000  ', verifyMessage }, {
    ...ctx,
    progress: ({ message }) => progress.push(message),
  })
  assert.equal(result.success, true)
  assert.equal(result.effect, 'applied')
  assert.deepEqual(result.data, { status: 'request_sent' })
  assert.ok(!JSON.stringify({ progress, result }).includes(verifyMessage))
  assert.ok(!JSON.stringify({ progress, result }).includes('13800000000'))
})

test('already-friend and pending responses avoid duplicate requests and preserve the prefilled message option', async () => {
  for (const status of ['already_friend', 'already_requested']) {
    const op = createWeixinFriendAddOperation(async ({ stdin }) => {
      assert.deepEqual(JSON.parse(stdin!), { identifier: 'wx_user_a' })
      return { status }
    })
    const result = await op.execute({ identifier: 'wx_user_a' }, ctx)
    assert.equal(result.success, true)
    assert.equal(result.effect, 'none')
    assert.deepEqual(result.data, { status })
  }
})

test('friend add preserves explicit proxy refusals and prevents running a possibly submitted write again', async () => {
  const errors = [
    [new CodedOperationError('INSUFFICIENT_CREDIT', '余额不足'), 'INSUFFICIENT_CREDIT', 'none'],
    [new CodedOperationError('RESULT_TIMEOUT', '超时'), 'EXECUTION_UNKNOWN', 'unknown'],
    [new CancelledError(), 'CANCELLED', 'unknown'],
    [new CodedOperationError('EXECUTION_UNKNOWN', '提交后未核验'), 'EXECUTION_UNKNOWN', 'unknown'],
  ] as const
  for (const [error, code, effect] of errors) {
    const op = createWeixinFriendAddOperation(async () => { throw error })
    const result = await op.execute({ identifier: 'wx_user_a' }, ctx)
    assert.equal(result.code, code)
    assert.equal(result.effect, effect)
    assert.equal(result.retryable, false)
  }
})

test('friend add CLI rejects duplicated or missing flags instead of submitting to a guessed target', () => {
  for (const extra of [['--identifier', 'wx_user_b'], ['--verify-message'], ['wx_user_b'], ['--typo']]) {
    const command = spawnSync(process.execPath, [
      executable, 'add', '--domain', 'friend', '--identifier', 'wx_user_a', '--json', ...extra,
    ], { encoding: 'utf8' })
    assert.equal(command.status, 2, command.stderr)
    assert.equal(JSON.parse(command.stdout).code, 'INVALID_ARGUMENT')
  }
})
