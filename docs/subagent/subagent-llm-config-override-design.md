# 子智能体 LLM 配置扩展：按 Provider 覆盖 MODEL_CODE 设计文档

> 创建日期: 2026-07-27
> 状态: ✅ 已完成开发（三智能体流程通过 + 25 单测全绿 + 前端 build 通过 + 启动安全验证）

## 1. 问题背景

### 1.1 现状

系统通过 `.env` 中的 `LLM_PROVIDER`（deepseek / qwen / zhipu）+ `{PROVIDER}_MODEL_CODE` + `{PROVIDER}_BASE_URL` 控制全局 LLM 调用。子智能体已能在 `SUBAGENT.md` frontmatter 中通过 `llm_provider` 字段覆盖**主 provider**（参见 `src/core/agent.py:209-214`）。

但存在两个限制：

1. **无法覆盖 model_code**：子智能体无法指定「调用 DeepSeek 时用 `deepseek-v4-pro` 而不是全局默认的 `deepseek-v4-flash`」
2. **无法覆盖 failover 链上备用 provider 的 model_code**：主 provider 失败时切换到备用 provider，但备用 provider 仍用全局默认 model，子智能体无法干预

### 1.2 业务诉求

旅游顾问（`subagents/travel-consultant/`）需要 `deepseek-v4-pro` 才能完成报价计算（成本考虑，`.env` 默认是 `deepseek-v4-flash`）。同时希望 failover 到 qwen 时也能用指定的 `qwen3.7-plus`，而不是 qwen 的全局默认 model。

### 1.3 目标

- `SUBAGENT.md` 和数据库 `subagent_definitions` 表都支持指定主 provider + 各 provider 的 model_code
- 前端 `/portal/agent-definitions` 配置页面同步暴露 `llm_provider` + `llm_model_codes`
- Failover 链上每个 provider 用子智能体配置的 model_code（如果配置了），否则用全局默认

## 2. 设计方案

### 2.1 数据格式约定

**SUBAGENT.md frontmatter（平铺，贴合 .env 习惯）**：

```yaml
llm_provider: deepseek
deepseek_model_code: deepseek-v4-pro
qwen_model_code: qwen3.7-plus
```

- `llm_provider`：主 provider 名（字符串）
- `{provider}_model_code`：各 provider 的 model 覆盖（可选，未配置则用全局默认）

**数据库 `subagent_definitions.llm_provider`** 改为 JSONB：

```json
{
  "provider": "deepseek",
  "model_codes": {
    "deepseek": "deepseek-v4-pro",
    "qwen": "qwen3.7-plus"
  }
}
```

兼容旧字符串格式：`"deepseek"` 解析为 `{"provider": "deepseek"}`（`extract_llm_config` 函数处理）。

**SubagentConfig（Python 对象）** 字段拆分：

```python
llm_provider: Optional[str]            # 主 provider 名
llm_model_codes: Optional[Dict[str, str]]  # 各 provider 的 model_code 覆盖
```

DB CRUD 层负责 JSONB <-> 两个字段的拆装。

### 2.2 关键约束

**用户明确指定的约束**：

1. **不允许只指定 `llm_model_codes` 而不带 `llm_provider`**：必须同时指定。如果 `llm_provider` 为空，子智能体直接使用全局 `llm_gateway` 单例（与现状一致），不创建专用 gateway，自然也不消费 `llm_model_codes`。这是为了避免无意义的配置。
2. **不需要数据迁移**：现有数据都为空，直接 `ALTER` 字段类型为 JSONB，使用 `jsonb_build_object` 处理可能的旧字符串值。

### 2.3 Failover 兼容

子智能体的 `model_codes` 同时作用于主 provider 和 failover 链上的备用 provider。每个 provider 用 `model_codes.get(provider_name)` 覆盖，未配置则用全局默认。Failover 链本身仍用全局 `settings.llm.failover.providers`，子智能体只覆盖 model_code，不自定义 failover 链。

### 2.4 行为矩阵

| `llm_provider` | `llm_model_codes` | 行为 |
|----------------|-------------------|------|
| 空 | 空 | 使用全局 `llm_gateway` 单例（与现状一致） |
| 指定 | 空 | 用全局默认 model，主 provider 用指定值 |
| 指定 | 指定 | 完整覆盖：主 provider + failover 链上每个 provider 用配置的 model_code |

## 3. 变更内容

### 3.1 数据模型层

**文件**: `src/models/subagent.py`

- 新增 `llm_model_codes: Optional[Dict[str, str]]` 字段（位于 `llm_provider` 之后）
- 新增模块级辅助函数：
  - `extract_llm_config(raw) -> tuple[Optional[str], Optional[Dict[str, str]]]`：DB JSONB → (provider, model_codes)，兼容 None / 旧字符串 / 新 dict 三种格式
  - `pack_llm_config(provider, model_codes) -> Optional[Dict[str, Any]]`：(provider, model_codes) → JSONB dict，两者都为 None 时返回 None

### 3.2 SUBAGENT.md 解析与序列化

**文件**: `src/subagents/loader.py`

- `parse_subagent_md`：从 frontmatter 收集所有 `{provider}_model_code` 字段（非空字符串），构造 `llm_model_codes` dict
- `serialize_to_subagent_md`：把 `llm_model_codes` dict 展平为 `{provider}_model_code` 字段写回

### 3.3 DB 层

**文件**: `src/db/subagent_definition_db.py`

- `create` 方法：参数 `llm_provider` + `llm_model_codes` 独立传入，内部用 `pack_llm_config` 组装为 JSONB 写入 `llm_provider` 列
- `update` 方法：通过 `has_llm_override = "llm_provider" in kwargs or "llm_model_codes" in kwargs` 检测任一字段被传入，触发整体 JSONB 重新打包。语义为「整体覆盖」--两个字段一起提交。

### 3.4 DB → SubagentConfig 转换（两处）

**文件**: `src/subagents/registry.py:404` + `src/subagents/factory.py:238`

把 `llm_provider=row.get("llm_provider")` 改为通过 `extract_llm_config` 拆分两个字段。

### 3.5 服务层

**文件**: `src/services/subagent_definition_service.py`

- `create_definition` 新增 `llm_model_codes` 参数，透传给 DB
- `_serialize` 在循环后用 `extract_llm_config` 拆分 JSONB，把 `llm_provider` 和 `llm_model_codes` 作为两个独立字段返回给前端

### 3.6 API 层

**文件**: `src/api/agent_definitions.py`

- `CreateDefinitionRequest` + `UpdateDefinitionRequest` 新增 `llm_model_codes: Optional[Dict[str, str]] = None`
- `update_definition` 端点改用 `body.model_dump(exclude_unset=True)` 而非 `exclude_none=True`，以便区分「未传入字段」和「显式传 null 清空」。这对 `llm_provider` / `llm_model_codes` 至关重要：用户需要能通过传 null 清空已有 LLM 配置。

### 3.7 LLM Gateway 改造（核心）

**文件**: `src/llm/gateway.py`

- `_build_provider(provider_name, api_key, model: Optional[str] = None)`：新增 `model` 参数，覆盖 settings 默认值
- `LLMGateway.__init__(provider_name=None, model_codes: Optional[Dict[str, str]] = None)`：新增 `model_codes` 参数，存为 `self._model_codes`，传给 `FailoverGateway`
- `_call_with_pool` / `_stream_with_pool`：调用 `_build_provider` 时传入 `self._model_codes.get(self.provider_name)` 作为 model 覆盖
- `get_model_name()`：优先返回 `self._model_codes.get(provider_name)`，未配置时返回全局默认。该方法被 `record_service.set_model()` 等多处使用，确保会话记录 / trace / 监控中 model 字段与实际调用一致。

### 3.8 FailoverGateway 改造

**文件**: `src/llm/failover.py`

- `_build_provider` 签名同步加 `model: Optional[str] = None`
- `FailoverGateway.__init__(primary_name, fallback_names, model_codes: Optional[Dict[str, str]] = None)`：新增 `model_codes` 参数，存为 `self._model_codes`
- `_call_slot` / `_stream_slot`：调用 `_build_provider` 时传入 `self._model_codes.get(slot.provider_name)` 作为该 slot 的 model 覆盖
- `get_model_name()`：同 LLMGateway，优先返回主 provider 的 model_code 覆盖

### 3.9 Agent 消费层

**文件**: `src/core/agent.py:208-220`

```python
if subagent_config and getattr(subagent_config, 'llm_provider', None):
    from src.llm.gateway import LLMGateway
    self.llm = LLMGateway(
        provider_name=subagent_config.llm_provider,
        model_codes=getattr(subagent_config, 'llm_model_codes', None),
    )
    logger.info(f"Agent using override LLM: provider={subagent_config.llm_provider}, "
                f"model_codes={getattr(subagent_config, 'llm_model_codes', None)}")
```

### 3.10 SQL 迁移

**文件**: `deploy/init-postgres.sql` + `deploy/db_update.sql`

- `init-postgres.sql`：`subagent_definitions.llm_provider` 字段类型从 `TEXT` 改为 `JSONB`
- `db_update.sql` 末尾追加：

```sql
-- 2026-7-27，subagent_definitions.llm_provider 改为 JSONB，存 {provider, model_codes}
-- 现有数据都为空，无需迁移；若历史存在字符串值，转为 {"provider": "..."}
ALTER TABLE subagent_definitions ALTER COLUMN llm_provider TYPE JSONB USING
  CASE WHEN llm_provider IS NULL OR llm_provider = '' THEN NULL
  ELSE jsonb_build_object('provider', llm_provider)
  END;
```

空字符串判断防止历史脏数据被转为 `{"provider": ""}` 这种无意义 JSONB。

### 3.11 前端类型层

**文件**: `frontend/src/api/agentDefinitions.ts`

- `AgentDefinition` 接口新增 `llm_model_codes: Record<string, string> | null`
- `createDefinition` 入参新增 `llm_model_codes?: Record<string, string>`

### 3.12 前端 UI

**文件**: `frontend/src/components/AgentDefinitionManager.vue`

- `populateForm`：在 form.value 中初始化 `llm_provider`（字符串）和 `llm_model_codes` 对象（含 deepseek / qwen / zhipu 三个键）
- `saveDefinition`：保存前过滤空字符串，构造 payload。若 `llm_provider` 为空则把 `llm_model_codes` 也置 null（强制执行 §2.2 约束1）
- 模板：在「回复风格」和「业务页面编辑器」之间新增「LLM 提供者」BaseSelect + 「各 Provider Model 覆盖」3 个 BaseInput

## 4. 关键约束

### 4.1 不允许单独指定 model_codes

如果 `llm_provider` 为空但 `llm_model_codes` 非空，子智能体不会创建专用 `LLMGateway`（直接用全局 `llm_gateway` 单例），自然也不消费 `model_codes`。前端在 `saveDefinition` 时强制把 `llm_model_codes` 同步置 null。

### 4.2 整体覆盖语义

`update` 端点的 `llm_provider` + `llm_model_codes` 两个字段一起提交、一起覆盖。例如只想把 `llm_provider` 从 deepseek 改成 qwen，必须同时显式传 `llm_model_codes`（即使为 null，表示清空所有 model_code 覆盖）。这是「整体覆盖」语义，与字段级别的「部分更新」不同。

### 4.3 failover 链不变

子智能体的 `model_codes` 只覆盖 failover 链上每个 provider 的 model_code，不改变 failover 链本身的顺序与组成。Failover 链仍来自全局 `settings.llm.failover.providers`。

### 4.4 未配置时回退全局默认

`llm_provider` 为空 → 使用全局 `llm_gateway` 单例（与现状一致）。`llm_provider` 指定但 `llm_model_codes` 中某 provider 未配置 → 该 provider 用全局默认 model。

## 5. 改动范围

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/models/subagent.py` | 修改 | 新增 `llm_model_codes` 字段 + `extract_llm_config` / `pack_llm_config` 辅助函数 |
| `src/subagents/loader.py` | 修改 | 解析 + 序列化 |
| `src/subagents/registry.py` | 修改 | DB->Config 转换 |
| `src/subagents/factory.py` | 修改 | DB->Config 转换 |
| `src/db/subagent_definition_db.py` | 修改 | CRUD 改 JSONB |
| `src/services/subagent_definition_service.py` | 修改 | 透传参数 + `_serialize` 拆分 |
| `src/api/agent_definitions.py` | 修改 | Pydantic 模型 + 端点改 `exclude_unset` |
| `src/llm/gateway.py` | 修改 | `_build_provider` 加 model 参数 + `LLMGateway.__init__` 加 model_codes + `get_model_name` |
| `src/llm/failover.py` | 修改 | `FailoverGateway.__init__` 加 model_codes + `_call_slot`/`_stream_slot` 传 model 覆盖 + `get_model_name` |
| `src/core/agent.py` | 修改 | Agent 构造时传 model_codes |
| `deploy/init-postgres.sql` | 修改 | 字段类型改 JSONB |
| `deploy/db_update.sql` | 修改 | 追加 ALTER 迁移 |
| `frontend/src/api/agentDefinitions.ts` | 修改 | 类型定义 |
| `frontend/src/components/AgentDefinitionManager.vue` | 修改 | 表单 UI |
| `tests/test_phase2_e2e.py` | 修改 | 补充断言 |
| `tests/unit/test_subagent_llm_config.py` | 新增 | 25 个单元测试 |

## 6. 决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| DB 存储方式 | A. 拆两个字段 B. JSONB 单字段 | B | 紧凑、向后兼容、未来加 `zhipu_model_code` 无需改表结构 |
| SUBAGENT.md 格式 | A. 嵌套 `model_codes: {deepseek: ...}` B. 平铺 `{provider}_model_code` | B | 与 `.env` 风格一致，便于运维理解 |
| SubagentConfig 字段 | A. JSONB dict B. 两个独立字段 | B | 便于 Python 代码消费，避免每次 `extract` |
| model_codes 作用范围 | A. 仅主 provider B. 主 provider + failover 链 | B | 用户明确要求，子智能体也要有 failover 机制 |
| 数据迁移 | A. 写迁移脚本 B. 直接 ALTER，无迁移 | B | 现有数据都为空，无需迁移 |
| API 更新语义 | A. `exclude_none=True` B. `exclude_unset=True` | B | 区分「未传入字段」和「显式传 null 清空」 |
| Failover 链来源 | A. 子智能体自定义 B. 全局 settings | B | 子智能体只覆盖 model_code，failover 链保持系统级统一 |
| 单独 model_codes 不带 provider | A. 允许 B. 禁止 | B | 避免无意义配置（无 provider 时 model_codes 不被消费） |

## 7. 测试覆盖

**单元测试** `tests/unit/test_subagent_llm_config.py`（25 个用例）：

- `TestExtractPackLlmConfig`：10 个，覆盖 `extract_llm_config` / `pack_llm_config` 辅助函数（None / 旧字符串 / 新 dict / 异常输入 / roundtrip）
- `TestSubagentMdParsing`：5 个，覆盖 SUBAGENT.md frontmatter 解析与序列化往返
- `TestLLMGatewayModelCodes`：3 个，覆盖 `LLMGateway` 构造 + `_build_provider` model 覆盖 + `get_model_name`
- `TestGetModelNameUsesOverride`：1 个，覆盖 `get_model_name` 优先返回 model_codes
- `TestUpdateDefinitionRequestClearing`：3 个，覆盖 API 清空语义（显式 null / 未传入 / 正常更新）

**Phase 2 e2e** `tests/test_phase2_e2e.py`：

- 创建子智能体时传 `llm_model_codes={"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"}`
- 断言 DB 读回 + `load_from_db()` 后 `config.llm_model_codes == {...}`

**回归测试**：

- `tests/unit/test_llm_failover.py`：42 个通过，确认 failover 改造不破坏既有行为
- `tests/unit/ -k subagent`：90 个通过

## 8. 关联文档

- [LLM Failover 设计文档](../infrastructure/llm-failover-design.md) §7「子智能体 model_codes 覆盖」--本文档的 failover 部分扩展
- [Prompt 全生命周期管理设计](../infrastructure/prompt-lifecycle-design.md)--`subagent_definitions` 表的另一个主要改造方
- [数字员工管理](../system/digital-employee/digital-employee-management.md)--子智能体整体管理规范
