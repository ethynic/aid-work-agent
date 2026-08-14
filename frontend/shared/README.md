# Shared frontend

Web 与 Desktop 经过验证后共同使用的纯前端模块。Phase B 不提前搬迁业务实现。

约束：

- 不依赖 `web`、`desktop`、Electron 或 Node 原生模块。
- 共享行为通过 `shared/testing/contractHarness.ts` 在两个 renderer 的消费者侧复用。
- 不知道是否共用的代码继续留在原消费者目录。
