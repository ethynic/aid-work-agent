# Desktop Agent protocol source

> **Frozen legacy D1（2026-09-22）**：该协议尚未发布，现只用于 DC0 调用方/删除审计和旧测试识别，不再增加 `agent/next`、outcome 或客户端推进 Run 的正式能力。新 Desktop 直接使用统一 Agent API，Runtime 使用 Device API；目标协议与迁移计划见 `docs/plans/plan-desktop-client-p1.md` 和 `docs/plans/plan-desktop-client-p1-implementation.md`。新协议可用并完成依赖迁移后删除本目录，不建立兼容层。

本目录曾是 Desktop Coordinator 与服务端之间的 D1 协议源，保留完整关联链、四种 Agent Turn
outcome、版本协商和 Remote Tool Gateway invoke 契约，仅供识别旧调用方和验证安全删除。
它不是新 Agent API/Device API 的协议源，任何生成命令通过也不表示当前 P0/P1 契约已验收。

以下版本规则只描述 frozen D1 的历史校验方式：

- `protocol_version` 使用 `MAJOR.MINOR`；破坏兼容性必须升 MAJOR，向后兼容字段升 MINOR。
- 同一 MAJOR 内新增字段必须可选；禁止改变既有字段语义或复用枚举值。
- schema、example 和 `generated/` 必须由 `node scripts/generate.mjs --check` 同时校验。
- TypeScript/Python 文件是生成物，禁止手工修改。
- `examples/version-compatible.json` 与 `version-incompatible.json` 是可执行的滚动升级正反样例；无共同版本时服务端返回 HTTP 426。

历史 D1 `agent/next` 曾计划通过暂停参数把 allowlist 内的服务端 tool call 交还客户端；该行为不得
启用或迁入新链路。新 Desktop 不能发送这种 outcome/tool_result 来推进云端 Run。

服务端默认隔离：`desktop_agent.enabled=false` 时 `src.main` 不导入 API 模块且不存在 `/api/desktop/v1` 路由。D1 表只位于 repeat-safe 的 `deploy/desktop_agent_d1.sql`，不接入常规更新脚本；启用前必须由运维显式执行并重启服务。
