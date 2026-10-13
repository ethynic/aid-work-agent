import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossGreetDetailOperation, type BossGreetDetailArgs } from '../src/main/operations/bossGreetDetail.js'
import { TOOL_DEFS } from '../src/mcp/toolDefs.js'
import { z } from 'zod'

test('详情页打招呼缺省/false/假true和dry-run均不能绕过显式启用门禁，零连接零输入', async () => {
  for (const value of [undefined, false, null, 0, 1, 'true', 'false', {}, []]) {
    for (const dryRun of [false, true]) {
      let connections = 0
      const operation = createBossGreetDetailOperation(async () => { connections++; throw new Error('must not connect') })
      const args = { name: '张测试', dry_run: dryRun, ...(value === undefined ? {} : { greet_in_detail: value }) }
      const result = await operation.execute(args as BossGreetDetailArgs, { signal: new AbortController().signal, progress() {} })
      assert.equal(result.success, false)
      assert.equal(result.code, 'INVALID_ARGUMENT')
      assert.equal(result.effect, 'none')
      assert.equal(result.retryable, false)
      assert.equal(connections, 0)
      assert.match(result.message, /默认关闭/)
    }
  }
})

test('MCP参数解析缺省false且严格boolean，未知camelCase启用不得生效', () => {
  const definition = TOOL_DEFS.find((tool) => tool.name === 'boss_greet_detail')!
  const flag = definition.zodShape.greet_in_detail!
  assert.equal(flag.parse(undefined), false)
  assert.equal(flag.parse(true), true)
  for (const value of ['true', 'false', 1, 0, null]) assert.equal(flag.safeParse(value).success, false)
  assert.equal(z.object(definition.zodShape).parse({ name: '张测试', greetInDetail: true }).greet_in_detail, false)
})
