# Desktop Agent protocol source

这里是 Desktop Coordinator 与服务端之间协议的语言无关唯一来源。Phase B 仅建立
`agent-turn` 首个 envelope 和 payload 占位引用；D1 在真实消费者出现时扩展 payload。

版本规则：

- `protocol_version` 使用 `MAJOR.MINOR`；破坏兼容性必须升 MAJOR，向后兼容字段升 MINOR。
- 同一 MAJOR 内新增字段必须可选；禁止改变既有字段语义或复用枚举值。
- schema、example 和 `generated/` 必须由 `node scripts/generate.mjs --check` 同时校验。
- TypeScript/Python 文件是生成物，禁止手工修改。
