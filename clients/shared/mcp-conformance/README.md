# 第一方 CLI / MCP Provider 统一契约测试套件

对任何第一方 CLI 的 stdio MCP server 跑标准契约检查（标准 `docs/system/first-party-cli-mcp-provider-standard.md` §11）：

1. `initialize`：serverInfo + instructions 存在（instructions 应在前 512 字符内含关键前提与写动作上限）
2. `list_tools`：tool 非空、名称集合匹配、每个 tool 有 object 型 inputSchema
3. `call_tool` 结构化结果契约：`success/code/message/effect/data/retryable/run_id` 字段完整，`effect ∈ none|applied|partial|unknown`
4. `call_tool` 参数非法失败路径：`isError` 或结构化 `INVALID_ARGUMENT`，不得挂起/崩溃
5. stdout 零污染：原始 spawn 逐行断言 stdout 只有 JSON-RPC 帧（日志只能走 stderr）
6. 关闭后无孤儿进程：stdin 关闭后 Provider 进程自行退出
7. 并发单飞 BUSY（可选）：悬挂端口让第一个 call 卡住，第二个并发 call 必须快速返回 BUSY 结构化结果
8. 取消后锁释放（可选）：取消卡住的 call 后，新 call 不得再返回 BUSY

套件用官方 `@modelcontextprotocol/sdk` client 实现，纯 ESM、零测试框架依赖。

## 复用方法（后续第一方 CLI）

以被测 CLI 包内的测试文件为 SDK 解析基点（`requireBase`），动态 import 本套件：

```ts
import { pathToFileURL } from 'node:url'

const suiteUrl = new URL('../../../shared/mcp-conformance/suite.mjs', import.meta.url)
const { runConformance } = await import(suiteUrl.href)

const report = await runConformance({
  requireBase: import.meta.url,                 // 用被测包的 node_modules 解析 SDK
  spawn: { command: process.execPath, args: ['dist/src/cli/index.js', 'mcp', '--stdio'] },
  expectTools: ['my_tool_a', 'my_tool_b'],
  callProbe: { name: 'my_tool_a', arguments: {} },          // 无环境时快速返回结构化失败
  invalidProbe: { name: 'my_tool_a', arguments: { bad: 1 } },
  busyProbe: {                                              // 可选：并发/取消检查
    makeSpawn: (port) => ({ command: process.execPath, args: ['dist/src/cli/index.js', 'mcp', '--stdio', '--cdp-port', String(port)] }),
    tool: { name: 'my_tool_a', arguments: {} },
  },
})
assert.equal(report.failed, 0)
```

约束：`callProbe` 必须在无目标环境（如未启动 Chrome）时也快速返回（结构化失败即可），否则套件会超时。

## CLI 模式

```bash
node suite.mjs \
  --require-base "C:/repos/aid-work-agent/clients/boss-resume-assistant/package.json" \
  --expect-tools boss_filter,boss_clear_filter,boss_goto,boss_greet,boss_accept_resume,boss_reject_current,boss_interview_demo \
  --call 'boss_goto:{"target":"chat"}' \
  --invalid 'boss_goto:{"target":"nope"}' \
  --busy-port-flag --cdp-port \
  -- node dist/src/cli/index.js mcp --stdio
```

`--require-base`：SDK（`@modelcontextprotocol/sdk`）的模块解析基点，指向装了 SDK 的包内任一文件（如 package.json）；缺省用套件自身位置。
`--busy-port-flag`：在 spawn 参数后追加该 flag + 悬挂端口号，用于并发/取消检查。

参考实现：`clients/boss-resume-assistant/tests/mcp-conformance.test.ts`。
