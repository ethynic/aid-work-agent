# Desktop Agent protocol source

这里是 Desktop Coordinator 与服务端之间协议的语言无关唯一来源。D1 已定义完整关联链、
四种 Agent Turn outcome、版本协商和 Remote Tool Gateway invoke 契约。

版本规则：

- `protocol_version` 使用 `MAJOR.MINOR`；破坏兼容性必须升 MAJOR，向后兼容字段升 MINOR。
- 同一 MAJOR 内新增字段必须可选；禁止改变既有字段语义或复用枚举值。
- schema、example 和 `generated/` 必须由 `node scripts/generate.mjs --check` 同时校验。
- TypeScript/Python 文件是生成物，禁止手工修改。
- `examples/version-compatible.json` 与 `version-incompatible.json` 是可执行的滚动升级正反样例；无共同版本时服务端返回 HTTP 426。

D1 `agent/next` 复用现有 Agent 的真实模型循环，但通过仅供 Desktop 使用的暂停参数把 allowlist 内的服务端 tool call 在执行前交还客户端；结构化 `tool_result` 以原 `tool_call_id` 续接。默认 Web/渠道调用不传这些参数，行为不变。Gateway 事件带单调 `seq`，JSON 与 SSE 均支持 `after` 游标重放；取消仅在 `pending` 时成功，`running` 明确返回 too-late。

服务端默认隔离：`desktop_agent.enabled=false` 时 `src.main` 不导入 API 模块且不存在 `/api/desktop/v1` 路由。D1 表只位于 repeat-safe 的 `deploy/desktop_agent_d1.sql`，不接入常规更新脚本；启用前必须由运维显式执行并重启服务。
