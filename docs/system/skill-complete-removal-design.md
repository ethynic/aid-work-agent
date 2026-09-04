# skill_complete 工具彻底删除 — 设计文档

> 关联开发计划：plan-skill-complete-removal.md（已完成归档，2026-09-04 清理删除）
> 登记条目：[docs/ideas.md](../../docs/ideas.md) 工具分区
> 创建日期：2026-07-14

---

## 1. 背景与动机

### 1.1 原始设计目标

`skill_complete` 工具最初的两个设计目标：

1. **标记 skill 执行完成**：与 `use_skill` 配对，给 LLM 一个显式的「这个技能我已经用完了」语义信号。
2. **压缩纯文本 skill 的中间上下文**：对于没有 `skill_execute` 脚本的纯文本指令类 skill，在 LLM 调用 `skill_complete` 时，把 skill 执行期间的中间消息（assistant 的 tool_calls、tool result）压缩成一条摘要，减少后续 token 占用。

### 1.2 实际运行中的问题

经实际运行验证，原始设计目标基本未达成，反而引入新的上下文污染：

#### 问题 A：压缩只发生在进程内存，未触达持久化层

`_compress_skill_context` 只操作 `self.memory._cache`（`ShortTermMemory` 的进程内 deque 缓存），完全没有触碰 DB：

- 持久化路径 `src/main.py:1680 MessageDB.create_batch_transactional` 写入 DB 的消息序列包含**所有 skill 中间步骤**，无任何压缩痕迹
- `src/core/agent.py:2080-2092` 会话重建分支会 `clear` cache 然后从 DB `load_history` 重建——内存里压缩掉的中间消息，下次请求全部恢复
- `ShortTermMemory._is_expired` 触发 `clear` 后再调 `get_context` 同样从 DB 重建
- Gunicorn 多 worker 模型下，每个 worker 有独立内存，cache 不跨 worker 共享

**结论**：用户感觉「压缩没起作用」的根本原因——压缩只在单次请求的尾部迭代中存在，下次请求从 DB 原样恢复。

#### 问题 B：`message_count_before` 基准点记录错误

`agent.py:2660` 在 `use_skill` 工具执行**成功之后**才记录 `msg_count`，此时本轮的 `assistant(tool_calls=[use_skill])` 已经 append 进 memory（`agent.py:2514/2517`）。压缩时 `messages[:msg_count_before]` 会把 use_skill 的 assistant + tool_result 错误地归入「skill 之前」永久保留——而这正是用户想压缩掉的 SKILL.md 全文。

#### 问题 C：压缩实现本身污染上下文

- **summary_message 用 `role: "system"`**（`skill_complete_tool.py:144-149`）：违反主流 LLM function-calling 契约（system 只能在 system_prompt 字段），中间插入 system 消息会被某些 provider 解释为额外指令，影响后续行为
- **`preserved_user_messages` 只保留 `role == "user"`**：丢掉 assistant 中间结论和 tool 中间结果，但保留 mid-skill 的 user 追问，序列被打乱成 `user → system(摘要) → user(中途追问)`，破坏 user/assistant 配对
- **并发 user 消息保留逻辑基于不成立的假设**：同一 session 同时只有一个 SSE 在跑，所谓的「并发兜底」实际保留下来的是噪音

#### 问题 D：与 v3.1 Phase 4 持久化模型冲突

v3.1 Phase 4 引入了 `create_batch_transactional` 事务批量持久化，由 `src/main.py:1650-1685` 统一收集 `tool_messages_collected` 一次性写入 DB。这套机制设计时未考虑 skill_complete 的内存压缩——压缩后的内存状态不会反映到 DB，DB 永远是全量。

### 1.3 团队已经采取的临时措施

远程 commit `7ebc3b9 暂时去除 使用 skill_complete`（gaofang, 2026-07-14）已经从 `master_agent.md` 和 `subagent_base.md` 两个 prompt 模板中去掉了「任务完成后调用 skill_complete 标记完成」的指示。这是 prompt 层的临时止血——LLM 不再被指示调用，但工具本身和所有压缩机制仍在代码中。

### 1.4 删除决策

基于上述分析：

- **目标 1（标记完成）**：可由 LLM 直接给出最终回复达成，不需要显式工具
- **目标 2（上下文压缩）**：从未真正实现（持久化层未触达），删除不损失任何已有能力
- **副作用（污染上下文）**：删除后立即消除

决定**彻底删除 skill_complete 工具及其依赖的全部代码、测试、文档**，包括：

- 工具文件 `src/tools/skill/skill_complete_tool.py`
- 数据类 `src/core/skill_session.py`（SkillSession）
- agent.py 内所有相关分支（主循环 + 子智能体执行器）
- `allowed_tools` 围栏（因 SkillSession 删除而失去生命周期管理能力）
- 配套的 prompt 模板、SKILL.md、SUBAGENT.md 中的指示文本
- 相关测试

---

## 2. 删除范围详表

### 2.1 核心代码删除

| 文件 / 位置 | 处理 | 说明 |
|------------|------|------|
| `src/tools/skill/skill_complete_tool.py` | **整文件删除** | SkillCompleteTool / SkillCompleteInput / _compress_skill_context 实现 |
| `src/core/skill_session.py` | **整文件删除** | SkillSession 数据类，唯一用途是配合压缩 |
| `src/core/agent.py:43` | 删 import | `from src.core.skill_session import SkillSession` |
| `src/core/agent.py:197` | 删字段 | `self._active_skill_sessions: Dict[str, SkillSession] = {}` |
| `src/core/agent.py:464` | 删 import | `from src.tools.skill.skill_complete_tool import SkillCompleteTool` |
| `src/core/agent.py:479` | 删实例化 | `self._skill_complete_tool = SkillCompleteTool()` |
| `src/core/agent.py:533-537` | 删工具定义 | 工具列表中的 `self._skill_complete_tool` |
| `src/core/agent.py:583` | 删字典项 | `"skill_complete": self._skill_complete_tool` |
| `src/core/agent.py:611` | 删工具列表项 | 另一处工具列表的 `self._skill_complete_tool` |
| `src/core/agent.py:1609-1620` | 删方法 | `_compress_skill_context` |
| `src/core/agent.py:1622-1625` | 删 property | `has_active_skill_session`（无外部调用方） |
| `src/core/agent.py:2474-2478` | 删兜底 | 主循环「无工具调用时自动压缩」整段 if |
| `src/core/agent.py:2544-2561` | 删围栏 | `_LIFECYCLE_TOOLS` + `allowed_tools` 权限检查整段 |
| `src/core/agent.py:2658-2669` | 改 use_skill | 删除创建 SkillSession 的代码块，保留 use_skill 工具调用本身 |
| `src/core/agent.py:2679-2706` | 删分支 | `# Handle skill_complete - compress skill context` 整个分支，替换为兜底（见 §3） |
| `src/core/agent.py:3000-3001` | 删延迟压缩 | `for comp_session_id...` 循环 + 上方 `pending_skill_compressions` 列表初始化 |
| `src/core/agent.py:3338-3340` | 删兜底 | 子智能体执行器「无工具调用时自动压缩」 |
| `src/core/agent.py:3455-3461` | 改 use_skill | 子智能体侧的 SkillSession 创建 |
| `src/core/agent.py:3464-3474` | 删分支 | 子智能体侧的 `elif tool_name == "skill_complete"` |
| `src/core/agent.py:3605-3607` | 删延迟压缩 | 子智能体侧的 `pending_skill_compressions` 循环 |

### 2.2 围栏（`_LIFECYCLE_TOOLS`）的删除理由

`allowed_tools` 围栏原本依赖 `skill_complete` 来重置 skill 的「活跃」状态——LLM 调用 complete 时从 `_active_skill_sessions` 移除该 skill，围栏解除。

删除 complete 后，活跃状态失去显式重置机制：

- `_active_skill_sessions` 是 `Agent` 实例字段，`master_agent` 是单例 → 一旦加入永不移除
- 跨会话污染：用户 A 在会话 1 用了 `complaint-core`（allowed_tools 限定），用户 B 在会话 2 不调任何 skill 但围栏仍生效，限制用户 B 的工具集

考虑过的三个替代方案：

| 方案 | 评估 |
|------|------|
| 改为 `Dict[session_id, Set[skill_name]]` 按 session 隔离 | 改动面大，且 turn 内何时清除 active 状态仍需定义 |
| 全局 set，turn 结束统一 clear | 多用户并发时互相清空，不可接受 |
| **直接删围栏** | **采用**。围栏原本就是配合 complete 的生命周期管理，删 complete 后围栏价值有限，依赖 prompt 约束 skill 期间的工具使用即可 |

### 2.3 Prompt / SKILL / SUBAGENT 文档清理

| 文件 | 改动 |
|------|------|
| `src/prompts/templates/master_agent.md:23` | 删「任务完成后调用 skill_complete」一句（远程 7ebc3b9 已改） |
| `src/prompts/templates/subagent_base.md:20` | 删同上（远程 7ebc3b9 已改） |
| `src/prompts/templates/master_agent.md:82` | 工具名清单中的 `skill_complete` 删除 |
| `src/prompts/templates/subagent_base.md:87` | 同上 |
| `src/tools/skill/use_skill_tool.py:20` | 删 description 中「→ skill_complete 标记完成」流程描述 |
| `src/tools/skill/use_skill_tool.py:111` | 删 SKILL 模板中「调用 skill_complete」一行 |
| `src/skills/contract-approval-1.0.0/SKILL.md:105` | 删「调用 skill_complete 标记完成」 |
| `src/core/skill_registry.py:339` | 删自动生成指南末尾的 skill_complete 说明 |
| `subagents/complaint-handling/SUBAGENT.md:142,145` | 删「使用流程」和「每次处理后必须调用 skill_complete」两处 |

### 2.4 测试清理

| 文件 | 处理 |
|------|------|
| `tests/test_skill_system.py` | 删 `TestSkillSession`（L25）、`test_compress_skill_context_integration`（L272）、`TestAgentSkillCompleteTool`（L344）三段 |
| `tests/unit/test_skill_session.py` | **整文件删除** |
| `tests/unit/test_content_generate.py:51-61` | 删 `TestAgentSkillCompleteTool` 类 |
| `tests/unit/test_delegation_tool_tenant_filter.py:39,49` | 删 mock 中的 `_skill_complete_tool` 行 |
| `scripts/test_ppt_skill_agent.py:226-230` | 删 skill_complete 调用检查 |

### 2.5 历史数据（不清理）

| 数据源 | 处理 | 理由 |
|--------|------|------|
| `chat_messages` 表中残留的 skill_complete tool_calls | **不清理** | 数据量大、清理成本高、§3 的代码兜底足以应对 |
| `channel_messages` 表 | **不清理** | 同上 |

---

## 3. 兜底机制：LLM 仍调用 skill_complete 时静默吞掉

### 3.1 触发场景分析

即使清理了 prompt、SKILL.md、SUBAGENT.md，LLM 仍可能在以下路径尝试调用：

| 路径 | 风险等级 | 说明 |
|------|---------|------|
| 历史会话上下文残留 | **高** | DB 里存量会话的 `chat_messages` 含历史上 LLM 调用 skill_complete 的 assistant(tool_calls)，下次该会话发消息时进入 LLM 上下文，模型可能复用 |
| 渠道会话历史 | **高** | `channel_messages` 表同理 |
| 模型训练记忆 | 中 | Qwen / Zhipu 训练数据可能含类似工具规范，模型自发产出。但 function-calling 强约束于 tools 参数，不在 tools 列表时基本不生成 |
| 浏览器 system prompt 缓存 | 低 | server 端 prompt 即时读模板，无缓存 |
| 用户消息注入 | 极低 | 不在 tools 列表时模型无法生成合法 tool_call |

### 3.2 兜底实现

**位置**：`src/core/agent.py` 主循环（`# Handle skill_complete` 原分支位置，约 L2679）和子智能体执行器（原 `elif tool_name == "skill_complete"` 位置，约 L3464）。

**逻辑**：

```python
# skill_complete 已废弃（2026-07-14 移除）
# 仍可能被历史会话上下文或模型记忆触发，静默吞掉避免污染
if tool_name == "skill_complete":
    tool_results.append({
        "tool_call_id": tool_id,
        "content": {"success": True, "message": "skill_complete 已废弃，无需调用"}
    })
    yield make_event("tool_result", toolName=tool_name,
                     result={"success": True, "message": "skill_complete 已废弃"},
                     success=True)
    continue
```

子智能体执行器中对应 elif 分支保留同样的静默处理。

### 3.3 为什么静默 success 而不是 error

- **报错路径**：LLM 收到 error tool_result → 尝试修复（重试、改参数、解释错误）→ 多绕几轮才放弃，浪费 token，污染对话历史
- **静默 success**：LLM 看到「完成了」→ 直接进入最终回复阶段 → 干净利落

---

## 4. 保留的内容

明确**不删除**的组件：

| 组件 | 保留理由 |
|------|---------|
| `skill_execute` 工具 | 用户确认保留，独立功能，与 complete 无关 |
| `use_skill` 工具 | 加载 SKILL.md 的入口，完全保留 |
| `SkillRegistry` / `SkillLoader` | skill 加载机制无关 complete |
| `skill_execute` 调用链 | 包括版本校验、plan 集成、tlog 记录等全部保留 |
| skill 内部的脚本执行能力 | 由 `skill_execute` 提供，不受影响 |

---

## 5. 影响评估

### 5.1 对用户可见行为的影响

| 场景 | 删除前 | 删除后 |
|------|--------|--------|
| 调用纯文本指令类 skill | LLM 调 use_skill → 执行 → 调 skill_complete（实际未压缩） | LLM 调 use_skill → 执行 → 直接给最终回复 |
| 调用脚本类 skill | LLM 调 use_skill → skill_execute → skill_complete | LLM 调 use_skill → skill_execute → 直接给最终回复 |
| 历史会话上下文 | skill_complete tool_calls 残留 | 兜底静默吞掉，无报错 |
| token 消耗 | skill_complete 工具定义占 tool list 一项 + LLM 偶发调用浪费 | 节省 tool 定义 token，无 LLM 调用浪费 |

**结论**：用户感知不到功能损失（压缩本来就未生效），副作用（污染）消除。

### 5.2 对系统性能的影响

- **正面**：tool list 减少 1 项，每轮 LLM 请求节省 token；减少 `_active_skill_sessions` 字典操作和 `_compress_skill_context` 调用开销
- **负面**：无

### 5.3 对后续工作的 paving

- `docs/memory/memory_design.md` 和 `docs/infrastructure/memory/memory_design.md` 中关于 `_compress_skill_context` 的设计段落需标记为废弃
- `docs/infrastructure/memory/context_compression_design.md` 中「Skill 完成后压缩技能段」描述需更新
- `docs/system/master-subagent/design_master_subagent.md` 中 `_active_skill_sessions` / `SkillSession` 相关字段描述需删除

---

## 6. 风险与回滚

### 6.1 风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| 删除时漏改某个引用点导致 import error | 中 | 三智能体流程 + 提交前 import 终检 `from src.core.agent import Agent` |
| 历史会话里 skill_complete 残留触发 LLM 调用 | 高 | §3 兜底机制覆盖 |
| SUBAGENT.md / SKILL.md 中遗漏 skill_complete 指示文本 | 中 | grep 全量扫描确认 |
| 子智能体执行器路径漏改 | 中 | 设计文档明确列出两处（主循环 + 子智能体），CR 重点检查 |
| `allowed_tools` 围栏删除后，skill 期间工具使用失控 | 低 | prompt 约束 + skill 设计本身限制工具使用范围；原本围栏就因 SkillSession 失效而不可靠 |

### 6.2 回滚方案

改动均通过单次 commit 完成，回滚即 `git revert <commit>`。

注意：远程已有 `7ebc3b9 暂时去除 使用 skill_complete`（prompt 层临时止血），本次删除是在此基础上的彻底清理。回滚到本次删除前不会影响 7ebc3b9 的临时止血；回滚到 7ebc3b9 之前才会恢复完整 skill_complete 机制。

### 6.3 验证标准

- 单测：相关测试文件全部通过
- 启动安全：`from src.core.agent import Agent; Agent(...)` 不报错（容器环境 `docker exec aid-agent-api python -c "..."`）
- import 链：`from src.core.agent import get_master_agent` 拉起的整套环境不包含 skill_complete_tool / skill_session 模块
- grep 终检：`grep -ri "skill_complete\|SkillSession\|_compress_skill_context\|_active_skill_sessions\|has_active_skill_session" src/` 在源代码中应只剩 §3 兜底的字符串字面量

---

## 7. 文档同步清单

完成开发后需同步更新的既有文档：

| 文档 | 同步内容 |
|------|---------|
| `docs/system/master-subagent/design_master_subagent.md` | 删除 `_active_skill_sessions` / `SkillSession` / `_handle_use_skill` 创建 session / skill_complete 排队延迟压缩 相关字段和流程描述 |
| `docs/memory/memory_design.md` | 标记 `_compress_skill_context` / SkillCompleteTool 段落为已废弃 |
| `docs/infrastructure/memory/memory_design.md` | 同上（这是 memory_design.md 的另一份副本） |
| `docs/infrastructure/memory/context_compression_design.md` | 删除「Skill 完成后压缩技能段」横向压缩描述 |
| `docs/system/digital-employee/tool-messages-persistence-design.md` | 检查是否引用 skill_complete 压缩机制 |
| `docs/tools/http_api_adapter_design.md` | 删除「Skill 完成（skill_complete）」流程描述 |
| `docs/system/prompt/prompt_system_refactor_design.md` | 删除 P2.3 中关于 use_skill → skill_execute → skill_complete 的描述 |
| `docs/subagent/complaint-handling/complaint-agent-design.md` | 删除 use_skill → ... → skill_complete 流程描述 |
| `docs/subagent/content_generate_universal_design.md` | 删除 skill_complete 静态工具描述 |
| `docs/ideas.md` | 在工具分区登记本次删除条目 |
| `docs/ideas_finished.md` | 完成后迁移条目到此 |

---

## 8. 不在本次范围

明确**不做**的事情：

- 不清理 DB 中历史的 skill_complete tool_calls 残留
- 不重构 `skill_execute` 工具
- 不引入新的 skill 生命周期管理机制（如按 session_id 隔离的 active skill set）
- 不修复 v3.1 Phase 4 持久化模型与内存压缩的冲突（这是更大的议题，超出本次范围）
- 不实现真正的上下文压缩能力（如基于 token 阈值的自动摘要），这是 `docs/infrastructure/memory/context_compression_design.md` 的独立主题
