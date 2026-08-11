# M0.2 实施规格：结构化 operation 与 BOSS MCP

> 关联：[plan-recruiting-cli-agent-integration.md](plan-recruiting-cli-agent-integration.md) M0.2；上位契约：[recruiting-cli-agent-integration-design.md](../../design/recruiting/recruiting-cli-agent-integration-design.md) §10、§14；[first-party-cli-mcp-provider-standard.md](../../system/first-party-cli-mcp-provider-standard.md) §3-§6。
>
> 本文件是 M0.2 开发的具体实施决策，不改变上位设计已敲定的契约。

## 1. 目录与文件

```
clients/boss-resume-assistant/src/
├─ main/operations/
│  ├─ types.ts            # OperationResult / Effect / ErrorCode / OpContext / BossOperationError
│  ├─ errorMapping.ts     # 既有 executor Error → 统一错误码
│  ├─ bossContext.ts      # connect/attach/executor 依赖组装（从 7 个 command 抽取的公共段）
│  ├─ index.ts            # OPERATIONS 注册表：name → { operation, cliRenderer 元数据 }
│  ├─ bossFilter.ts       # 含 clear 子操作 bossClearFilter
│  ├─ bossGoto.ts
│  ├─ bossGreet.ts
│  ├─ bossAcceptResume.ts
│  ├─ bossRejectCurrent.ts
│  └─ bossInterviewDemo.ts
├─ mcp/
│  ├─ server.ts           # 官方 SDK McpServer + stdio，stdout 零日志
│  ├─ toolDefs.ts         # 7 个 tool 的 name/description/inputSchema/annotations（schema digest 来源）
│  └─ manifest.ts         # manifest 构建 + schema_digest 计算
└─ cli/commands/
   ├─ mcp.ts              # mcp --stdio 入口
   ├─ doctor.ts           # 只读环境检查（标准 §4 强制命令面）
   └─ version.ts          # version --json：版本 + manifest digest
clients/shared/mcp-conformance/
├─ suite.mjs              # 可复用 Provider 契约测试（标准 §11），纯 ESM JS + JSDoc
├─ suite.d.ts
└─ README.md              # 后续第一方 CLI 如何复用
```

## 2. Operation 契约

```ts
type Effect = 'none' | 'applied' | 'partial' | 'unknown'
type ErrorCode = 'INVALID_ARGUMENT' | 'CHROME_UNAVAILABLE' | 'NOT_LOGGED_IN' | 'WRONG_PAGE'
  | 'BUSY' | 'PAYWALL' | 'UI_CHANGED' | 'CANCELLED' | 'EXECUTION_UNKNOWN' | 'INTERNAL_ERROR'

interface OperationResult {
  success: boolean
  code: 'OK' | ErrorCode
  message: string            // 面向人的简短中文结果
  effect: Effect
  data: Record<string, unknown>
  retryable: boolean
  run_id: string             // crypto.randomUUID，operation 入口生成
}

interface OpContext {
  signal: AbortSignal
  progress: (p: { stage: string; current?: number; total?: number; message: string }) => void
  cdpPort?: number
}

interface BossOperation<Args> {
  name: string               // boss_greet 等
  execute(args: Args, ctx: OpContext): Promise<OperationResult>  // 永不 throw；内部捕获→结构化失败
}
```

- operation **永不 reject**（参数错误也返回 success=false + INVALID_ARGUMENT）；异常只在编程错误时抛出。
- 参数校验（zod 风格手写校验函数，不引新依赖）在 connect Chrome 之前（fail fast）。
- 每个 operation 在关键阶段发 progress（connect/attach/navigate/execute/done）；greet/accept 每处理 1 人发一次 current/total。
- `effect`：成功写动作=applied；批量部分完成=partial；写动作发出但校验失败=unknown；只读/导航/演示=none。
- `retryable`：CHROME_UNAVAILABLE/WRONG_PAGE/BUSY 可重试；写动作 unknown/partial 一律 false。

## 3. 错误映射（errorMapping.ts）

| 来源 | 判定 | code | effect |
|---|---|---|---|
| 参数校验失败 | operation 入口 | INVALID_ARGUMENT | none |
| CdpGateway.connect 失败 | ECONNREFUSED/超时 | CHROME_UNAVAILABLE | none |
| NavError 含「确认已登录」 | 消息匹配 | NOT_LOGGED_IN | none |
| greet 前置校验非推荐页 | 既有逻辑 | WRONG_PAGE | none |
| GreetError 含「付费墙」 | PAYWALL 标记 | PAYWALL | partial（greeted>0）/none |
| FilterSetError「行为单选/未提供任何筛选条件」 | | INVALID_ARGUMENT | none |
| 各 Error 含「校验失败/未减少/未生效/未关闭/未打开」等写后校验失败 | | EXECUTION_UNKNOWN | unknown |
| 其余 executor Error（结构变化、找不到元素） | | UI_CHANGED | none |
| AbortSignal 触发 | | CANCELLED | 按已完成量 none/partial |
| 单飞锁占用 | | BUSY | none |
| 兜底 | | INTERNAL_ERROR | unknown |

映射规则集中在 errorMapping.ts，消息匹配表显式列出（不散落在 operation 里）。

## 4. Executor 的增量改动（允许，非算法改写）

计划明确「加入 progress、AbortSignal」，对 executor 只做**可选参数追加**：

- `GreetDeps` / `ConsentDeps` 增加可选 `signal?: AbortSignal`、`onProgress?: (done: number) => void`：循环顶部 `if (signal?.aborted) throw new CancelledError(...)`；每完成 1 人调 onProgress。
- 新增 `src/main/operations/CancelledError.ts`（或并入 types.ts）。
- 其余 5 个 executor 为单次动作，只在入口检查一次 signal。
- **不改任何定位/点击/校验算法。**

## 5. CLI 薄 renderer

7 个 command 文件改为：解析参数 → 构造 AbortController（Ctrl+C 触发 abort）→ progress 打印 `⠿ stage message` → 调 operation → 按 result 打印成功/失败文案 → 退出码（成功 0 / 参数 2 / 其余 1）。打印文案保持现有风格（⚠️ 写动作提示保留在 command 层）。

CLI 与 MCP 的能力差异（设计 §14）：

| 操作 | CLI limit | MCP schema maximum | 理由 |
|---|---|---|---|
| boss_greet | 默认 10，最大 100（现状保留） | 3（产品写动作硬上限） | §14 单次最大 3 |
| boss_accept_resume | 默认 20，最大 100（现状保留） | 1 | §14 单次最大 1 |
| boss_reject_current | 固定 1 | 固定 1（schema 无 limit 字段） | §14 |
| 其余 | — | — | 非限量写动作/只读 |

operation 层 limit 上限 100；MCP schema 用 JSON Schema `maximum` 收紧。

## 6. MCP server

- 依赖：`@modelcontextprotocol/sdk`（**精确版本锁定**，package.json 去掉 `^`）。
- `src/mcp/server.ts`：`new McpServer({ name: 'boss-recruiting', version }, { instructions })` + `StdioServerTransport`。
- **stdout 零污染**：mcp 模式下禁止任何 console.log；server 内日志只写 stderr（脱敏：不输出简历正文/坐标/CDP payload）。
- `server.registerTool(name, { description, inputSchema (zod raw shape 或 JSON Schema 按 SDK 版本 API), annotations }, handler)`；handler 调 operation，返回 `structuredContent: result` + `content: [{ type: 'text', text: JSON.stringify(result) }]`（等价 JSON text 兼容）。
- progress：handler 内 operation 的 progress → `sendNotification` progress（带 progressToken，Host 不传 token 则跳过，最终结果不受影响）。
- cancel：SDK 的 cancellation → AbortController.abort()。
- 单飞锁：进程级 `let running: string | null`；写动作与读动作都单飞（共享鼠标/页面），并发 call 立即返回 BUSY 结构化结果。
- instructions（前 512 字符内含）：仅 Windows；需已登录 BOSS 的 Chrome 以调试端口运行；需未锁屏交互桌面，操作期间勿动鼠标；boss_greet/boss_accept_resume/boss_reject_current 有外部副作用且单次上限 3/1/1；boss_interview_demo 只填不发送。
- annotations：boss_goto/boss_interview_demo → readOnlyHint 按实际语义（goto 无外部副作用 readOnly=true；interview_demo 不发送 readOnly=true）；greet/accept/reject → destructiveHint=false（非删除）但 openWorldHint=true、idempotentHint=false；filter/clear_filter → idempotentHint=true。标题全部中文。

## 7. manifest

`src/mcp/manifest.ts` 构建（version --json 与 Runtime 校验共用）：

```json
{
  "provider_id": "ai.aidwork.boss-recruiting",
  "provider_version": "<package.json version>",
  "protocol": "mcp",
  "transport": "stdio",
  "platforms": ["win32-x64"],
  "entrypoint": ["boss-recruiting.exe", "mcp", "--stdio"],
  "tools": [7 个 { name, schema_digest 用 toolDefs 的规范 JSON }],
  "schema_digest": "sha256:<对 tools 数组规范序列化的 sha256>",
  "execution_target": "local_required",
  "min_mcp_protocol_version": "2024-11-05"
}
```

digest 计算：toolDefs 的 name+description+inputSchema+annotations 按 key 排序 JSON.stringify → sha256。同一 digest 函数同时用于 `version --json` 输出和测试断言。

## 8. doctor / version

- `doctor`（只读，不写任何页面）：检查 ① win-click.ps1 存在 ② CDP 端点可连 ③ 能 attach zhipin.com 页面 ④ 页面含登录态特征（有「筛选」或侧边菜单文案，否则提示未登录/未打开 BOSS）。输出逐项 ✅/❌，任一失败退出码 1。绝不点击。
- `version --json`：输出 manifest JSON（含 schema_digest）。

## 9. 测试

新增（tests/）：
- `operations-result.test.ts`：每个 operation 的参数校验 fail-fast（不连 Chrome）、错误映射表全路径、effect/retryable 语义、run_id 存在。用 fake deps（注入 fake gateway/executor——bossContext 需支持依赖注入以便单测）。
- `operations-progress-cancel.test.ts`：greet/accept 的 onProgress 逐人回调、signal abort → CANCELLED。
- `mcp-server.test.ts`：SDK client 经 stdio 直连 dist 产物：initialize/list_tools（7 个、名称、annotations）/call_tool 成功与失败（fake 或参数错误路径）/并发 BUSY/stdout 零污染（call 期间 stdout 只出现 JSON-RPC 帧）。
- `manifest.test.ts`：digest 稳定（同输入同 digest）、manifest 字段完整。
- `mcp-conformance.test.ts`：调用 clients/shared/mcp-conformance/suite.mjs，对 `node dist/src/cli/index.js mcp --stdio` 跑契约套件（initialize/list/call/stdout 零污染/并发 BUSY/关闭后无孤儿进程）。

`clients/shared/mcp-conformance/suite.mjs` 用官方 SDK client 实现，参数化 spawn 命令，README 写明后续 CLI 复用方法。

单飞锁与「写动作不自动重放」：MCP 层不实现任何重试逻辑；测试断言同一 tool 并发第二个调用得到 BUSY。

## 10. 不做（本期）

- 自包含签名 exe 打包（对外发布阶段）。
- WSS/Redis transport。
- CLI renderer 的美化（progress 打印第 5 节从简）。
