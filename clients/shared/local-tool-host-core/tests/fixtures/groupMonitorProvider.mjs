// Test-only process. No production entry ever installs these injected operation boundaries.
import { groupMonitorFixture } from '../../../../wecom-cli/dist/tests/helpers/groupMonitorFixture.js'
import { createWecomGroupPollOperation } from '../../../../wecom-cli/dist/src/operations/groups.js'
import { OPERATIONS } from '../../../../wecom-cli/dist/src/operations/registry.js'
import { startMcpServer } from '../../../../wecom-cli/dist/src/mcp/server.js'

if (!process.env.WECOM_TEST_HOME) throw new Error('explicit synthetic fixture home required')
const fixture = await groupMonitorFixture(process.env.WECOM_TEST_HOME, false)
for (const entry of Object.values(OPERATIONS)) entry.operation = { name: 'disabled-in-test', execute() { throw new Error('unexpected tool') } }
OPERATIONS.wecom_group_poll.operation = createWecomGroupPollOperation(fixture.deps)
OPERATIONS.wecom_message_send.operation = fixture.send
await startMcpServer()
