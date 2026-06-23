---
关联想法: wecom_kf 转人工工具优化（schema + 渠道隔离）
关联设计: docs/channel/wecom-kf/transfer_to_human_optimization.md
关联设计(上游): docs/channel/wecom-kf/wecom_kf_design.md
状态: 📋 待开发
创建日期: 2026-06-23
---

# 微信客服转人工工具优化 — 开发计划

> 反向关联：实现细节见 [transfer_to_human_optimization.md](./transfer_to_human_optimization.md)。

## 任务分解

### 阶段 1：Schema 与渠道隔离（核心）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 1.1 | 重写 `TransferToHumanInput.reason` 为必填，加 Field 描述 | `src/tools/transfer_to_human.py` | 0.1d | ⏳ |
| 1.2 | 重写 `description`（描述何时转人工 + 调用后无需回复），清空 `usage_guide`（渠道由工具段兜底，不再用提示词约束 LLM） | `src/tools/transfer_to_human.py` | 0.1d | ⏳ |
| 1.3 | 重写 `execute`：先判断 `get_kf_context()`，非微信渠道返回友好提示 | `src/tools/transfer_to_human.py` | 0.2d | ⏳ |
| 1.4 | 移除关键词校验逻辑（`human_transfer_keywords` 不再约束 LLM 路径） | `src/tools/transfer_to_human.py` | 0.05d | ⏳ |
| 1.5 | 保留 servicer 列表校验，调整错误文案 | `src/tools/transfer_to_human.py` | 0.05d | ⏳ |
| 1.6 | 会话 metadata 新增 `transfer_source=agent` 字段 | `src/tools/transfer_to_human.py` | 0.05d | ⏳ |

### 阶段 2：allow_agent_transfer 开关（增强）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 2.1 | 在工具 execute 中读取 `kf_config.allow_agent_transfer`，默认 true | `src/tools/transfer_to_human.py` | 0.1d | ⏳ |
| 2.2 | 文档说明该字段，前端客服配置页（后续迭代）暂不动 | `wecom_kf_design.md` | 0.05d | ⏳ |

### 阶段 3：测试

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 3.1 | 单元测试：微信渠道成功转接（含 metadata 字段断言） | `tests/unit/tools/test_transfer_to_human.py` | 0.2d | ⏳ |
| 3.2 | 单元测试：非微信渠道调用返回友好提示 | 同上 | 0.1d | ⏳ |
| 3.3 | 单元测试：servicer 列表为空时返回失败 | 同上 | 0.1d | ⏳ |
| 3.4 | 单元测试：allow_agent_transfer=false 时返回失败 | 同上 | 0.1d | ⏳ |
| 3.5 | 单元测试：reason 未传时 LLM schema 层校验失败（pydantic） | 同上 | 0.05d | ⏳ |

### 阶段 4：文档同步

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 4.1 | 更新 `wecom_kf_design.md` 第 7.1 节为最新实现 | `docs/channel/wecom-kf/wecom_kf_design.md` | 0.1d | ⏳ |
| 4.2 | 在 `docs/ideas.md` 登记并更新状态 | `docs/ideas.md` | 0.05d | ⏳ |
| 4.3 | 开发完成后将条目移至 `docs/ideas_finished.md` | `docs/ideas_finished.md` | 0.05d | ⏳ |

## 总预计

约 **1.5 人天**。

## 风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| LLM 在非微信渠道仍然尝试调用 | description 已明确告知，但仍可能误判 | 工具返回的 hint 会引导 LLM 用文字回复用户 |
| 移除关键词校验后租户失控 | Agent 可能更频繁主动转人工 | 通过 `allow_agent_transfer` 开关兜底 |
| `reason` 改必填后存量调用失败 | LLM schema 校验会在调用前提示补全 | 影响范围仅为 LLM 生成，无存量代码调用 |
