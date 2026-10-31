// Synthetic encrypted databases only. Production operations, real stdio MCP, no desktop actions.
import { groupMonitorFixture } from '../../../../wecom-cli/dist/tests/helpers/groupMonitorFixture.js'
import { createWecomReadSessionOperation } from '../../../../wecom-cli/dist/src/operations/readSession.js'
import { OPERATIONS } from '../../../../wecom-cli/dist/src/operations/registry.js'
import { startMcpServer } from '../../../../wecom-cli/dist/src/mcp/server.js'

if (!process.env.WECOM_TEST_HOME) throw new Error('explicit synthetic fixture home required')
const fixture = await groupMonitorFixture(process.env.WECOM_TEST_HOME, false)
for (const entry of Object.values(OPERATIONS)) entry.operation = { name: 'disabled-in-test', execute() { throw new Error('unexpected tool') } }
OPERATIONS.wecom_read_session.operation = createWecomReadSessionOperation({ localdb: fixture.deps })
await startMcpServer()
