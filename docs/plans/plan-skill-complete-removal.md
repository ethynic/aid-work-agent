# skill_complete 彻底删除 — 开发计划

> 关联设计文档：[skill-complete-removal-design.md](../system/skill-complete-removal-design.md)
> 登记条目：[docs/ideas.md](../ideas.md) 工具分区
> 创建日期：2026-07-14
> 流程：三智能体（开发 → 测试 → CodeReview），按 [dev_workflow.md](../../.claude/rules/dev_workflow.md) 执行

---

## 概览

| 项 | 说明 |
|----|------|
| 目标 | 彻底删除 skill_complete 工具及其依赖的全部代码、测试、文档，附带 LLM 历史调用的静默兜底 |
| 流程 | 三智能体串行：开发 → 测试 → CodeReview → 主控者提交前验证 |
| 风险等级 | 中（涉及 agent.py 主循环 + 子智能体执行器双路径，但改动逻辑明确） |
| 估算工作量 | 开发 1-2 小时 + 测试 1 小时 + CR 1 小时 + 提交前验证 0.5 小时 |

---

## 任务依赖关系

```
T1 (agent.py 主循环改动)  ─┐
T2 (子智能体执行器改动)   ─┼→ T4 (删 skill_complete_tool.py + skill_session.py)
T3 (use_skill 改动)       ─┘
                                ↓
                          T5 (prompt/SKILL/SUBAGENT 清理)
                                ↓
                          T6 (测试清理)
                                ↓
                          T7 (文档同步)
                                ↓
                          T8 (验证 + 提交)
```

T1、T2、T3 可以并行（不同代码段），但建议串行避免合并冲突。T4 必须在 T1-T3 之后（agent.py 不再引用才能安全删文件）。

---

## Phase 1：开发智能体实现

### T1 — agent.py 主循环改动

**文件**：`src/core/agent.py`

**改动点**（按设计文档 §2.1）：

1. 删 `from src.core.skill_session import SkillSession`（L43）
2. 删 `self._active_skill_sessions: Dict[str, SkillSession] = {}`（L197）
3. 删 `from src.tools.skill.skill_complete_tool import SkillCompleteTool`（L464）
4. 删 `self._skill_complete_tool = SkillCompleteTool()`（L479）
5. 工具定义列表（L533-537）移除 `self._skill_complete_tool`
6. 工具字典（L583）移除 `"skill_complete"` 项
7. 另一处工具列表（L611）移除 `self._skill_complete_tool`
8. 删 `_compress_skill_context` 方法（L1609-1620）
9. 删 `has_active_skill_session` property（L1622-1625）
10. 删主循环兜底自动压缩（L2474-2478 整段 if）
11. 删 `_LIFECYCLE_TOOLS` 围栏（L2544-2561 整段 if）
12. 改 use_skill 分支（L2658-2669）：删创建 SkillSession 的代码块，保留 `use_skill` 工具调用本身
13. 替换 `# Handle skill_complete` 分支（L2679-2706）为 §3 兜底逻辑：
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
14. 删延迟压缩循环 + `pending_skill_compressions` 列表初始化（L3000-3001 + L2522）

**完成标准**：
- agent.py grep `skill_complete` 只剩 §3 兜底的字符串字面量
- agent.py grep `SkillSession|_active_skill_sessions|_compress_skill_context|has_active_skill_session|_skill_complete_tool` 全部为 0

### T2 — agent.py 子智能体执行器改动

**文件**：`src/core/agent.py`（子智能体执行器部分，约 L3330-3610）

**改动点**：

1. 删子智能体兜底自动压缩（L3338-3340 整段 if）
2. 改子智能体 use_skill 分支（L3455-3461）：删 SkillSession 创建，保留工具调用
3. 替换 `elif tool_name == "skill_complete"` 分支（L3464-3474）为静默兜底（同 T1 逻辑，使用 `_emit_async` 替代 `yield`）
4. 删延迟压缩循环 + `pending_skill_compressions` 列表初始化（L3605-3607 + L3357）

**完成标准**：子智能体执行器路径不再引用任何已删除符号

### T3 — use_skill 工具改动

**文件**：`src/tools/skill/use_skill_tool.py`

**改动点**：

1. `description`（L20）：删除「→ skill_complete 标记完成」流程描述
2. SKILL 模板末尾（L111）：删除「调用 skill_complete」一行

**完成标准**：grep `skill_complete` 在 use_skill_tool.py 中为 0

### T4 — 删除独立文件

**前置条件**：T1、T2、T3 完成（agent.py 和 use_skill 不再引用）

**改动**：

1. 删除 `src/tools/skill/skill_complete_tool.py`
2. 删除 `src/core/skill_session.py`

**完成标准**：两个文件物理删除

### T5 — Prompt / SKILL / SUBAGENT 清理

**文件**（按设计文档 §2.3）：

| 文件 | 行号 | 改动 |
|------|------|------|
| `src/prompts/templates/master_agent.md` | L82 | 删工具名清单中的 `skill_complete`（L23 已由远程 7ebc3b9 改） |
| `src/prompts/templates/subagent_base.md` | L87 | 删工具名清单中的 `skill_complete`（L20 已由远程 7ebc3b9 改） |
| `src/skills/contract-approval-1.0.0/SKILL.md` | L105 | 删「调用 skill_complete 标记完成」 |
| `src/core/skill_registry.py` | L339 | 删自动生成指南末尾的 skill_complete 说明 |
| `subagents/complaint-handling/SUBAGENT.md` | L142, L145 | 删使用流程和强制调用 skill_complete 两条 |

**完成标准**：grep `skill_complete` 在 `src/prompts/`、`src/skills/`、`subagents/`、`src/core/skill_registry.py` 中为 0

### T6 — 测试清理

**文件**（按设计文档 §2.4）：

1. `tests/test_skill_system.py`：删 `TestSkillSession`（L25-46）、`test_compress_skill_context_integration`（L272-...）、`TestAgentSkillCompleteTool`（L344-...）三段
2. `tests/unit/test_skill_session.py`：**整文件删除**
3. `tests/unit/test_content_generate.py`：删 `TestAgentSkillCompleteTool`（L51-61）
4. `tests/unit/test_delegation_tool_tenant_filter.py`：删 mock 中 `_skill_complete_tool` 相关行（L39, L49）
5. `scripts/test_ppt_skill_agent.py`：删 skill_complete 调用检查（L226-230）

**完成标准**：grep `skill_complete\|SkillSession\|SkillCompleteTool` 在 `tests/` 和 `scripts/` 中为 0

### T7 — 文档同步

**文件**（按设计文档 §7）：

| 文档 | 同步动作 |
|------|---------|
| `docs/system/master-subagent/design_master_subagent.md` | 删 `_active_skill_sessions`、`SkillSession`、`_handle_use_skill` 创建 session、skill_complete 排队延迟压缩 等字段和流程描述 |
| `docs/memory/memory_design.md` | 标记 `_compress_skill_context` / SkillCompleteTool 段落为已废弃 |
| `docs/infrastructure/memory/memory_design.md` | 同上 |
| `docs/infrastructure/memory/context_compression_design.md` | 删「Skill 完成后压缩技能段」横向压缩描述 |
| `docs/system/digital-employee/tool-messages-persistence-design.md` | 检查并清理 skill_complete 引用 |
| `docs/tools/http_api_adapter_design.md` | 删「Skill 完成（skill_complete）」流程 |
| `docs/system/prompt/prompt_system_refactor_design.md` | 删 P2.3 中 use_skill → skill_execute → skill_complete 描述 |
| `docs/subagent/complaint-handling/complaint-agent-design.md` | 删流程描述 |
| `docs/subagent/content_generate_universal_design.md` | 删 skill_complete 工具描述 |

**完成标准**：设计文档与代码现状一致

### 开发智能体交接报告

开发完成后必须报告：
- 改动文件清单
- 自测命令和结果：`./scripts/dev_test.sh tests/unit/test_skill_system.py tests/unit/test_content_generate.py tests/unit/test_delegation_tool_tenant_filter.py -p no:cacheprovider -q`
- grep 终检结果
- 已知风险点
- **不提交、不 push**

---

## Phase 2：测试智能体独立验证

**启动时机**：开发智能体完成且自测通过。

### 验证内容

1. **跑新改动相关测试**：
   ```
   ./scripts/dev_test.sh tests/unit/test_skill_system.py -p no:cacheprovider -q -v
   ./scripts/dev_test.sh tests/unit/test_content_generate.py -p no:cacheprovider -q -v
   ./scripts/dev_test.sh tests/unit/test_delegation_tool_tenant_filter.py -p no:cacheprovider -q -v
   ```

2. **回归测试**：跑 skill / agent / tools 相关模块全量测试，确认没破坏既有功能：
   ```
   ./scripts/dev_test.sh tests/unit/ -k "skill or agent or tool" -p no:cacheprovider -q
   ```

3. **启动安全检查**（关键，避免服务器挂）：
   - 容器环境：`docker exec aid-agent-api python -c "from src.core.agent import Agent; Agent(is_master=True); print('ok')"`
   - 宿主机环境：`python -c "from src.core.agent import Agent; Agent(is_master=True); print('ok')"`
   - 先用 `docker ps | grep aid-agent-api` 探测环境

4. **import 链检查**：
   ```
   docker exec aid-agent-api python -c "from src.core.agent import get_master_agent; a = get_master_agent(); print('tools:', [t['name'] for t in a._get_tools()])"
   ```
   确认 tool list 中没有 `skill_complete`

5. **代码自读**：检查明显 bug（漏改 import、参数签名不匹配、变量未定义等）

### 测试智能体交接报告

- 测试命令 + 通过/失败数
- 修复了什么（如有）
- 未修的问题及原因
- 最终结论：是否测试全绿 + 启动安全
- **不提交、不 push**

---

## Phase 3：CodeReview 智能体独立审查

**启动时机**：测试智能体确认全绿 + 启动安全后。

### 审查重点

1. **P0 正确性**：
   - agent.py 主循环 + 子智能体执行器两条路径都改干净了吗？
   - §3 兜底逻辑两处都加了吗？逻辑一致吗？
   - 是否有遗留的 `from src.core.skill_session import` 或 `from src.tools.skill.skill_complete_tool import`
   - `pending_skill_compressions` 列表的初始化和消费是否成对删除

2. **P0 启动安全**：
   - import 是否破坏 main.py 启动链路
   - 是否有循环 import
   - `Agent.__init__` 中删除字段后，其他方法是否还引用

3. **P1 资源/安全**：
   - 删除是否完整（无半成品）
   - 兜底分支是否能正确处理历史 DB 残留

4. **P2 测试质量**：
   - 测试删除是否彻底
   - 是否有被注释或弱化的断言

### CR 智能体交接报告

- 按严重度排序的发现（P0/P1/P2/nit）
- 标 FIXED 或 REPORTED-ONLY
- 最终结论：是否可安全提交
- **不提交、不 push**

---

## Phase 4：主控者提交前验证

**执行者**：主控者（接收用户指令的 agent）

### 步骤

1. **import 终检**（自己跑一遍，不依赖智能体报告）：
   - 探测环境：`docker ps | grep aid-agent-api`
   - 容器：`docker exec aid-agent-api python -c "from src.core.agent import Agent, get_master_agent; a = get_master_agent(); tools = [t['name'] for t in a._get_tools()]; assert 'skill_complete' not in tools; print('ok')"`
   - 宿主机：去掉 `docker exec aid-agent-api` 前缀

2. **grep 终检**：
   ```
   grep -ri "skill_complete\|SkillSession\|_compress_skill_context\|_active_skill_sessions\|has_active_skill_session\|_skill_complete_tool" src/
   ```
   预期：src/ 下只剩 §3 兜底的字符串字面量 `"skill_complete"`（在 agent.py 两处 if 判断中），其他全部为 0

3. **fetch 最新远程**：`git fetch origin`
   - 若有新提交，先合并解决冲突

4. **暂存相关文件**（排除本地在途的无关改动）：
   - 本次改动文件：`src/core/agent.py`、`src/tools/skill/skill_complete_tool.py`（删除）、`src/core/skill_session.py`（删除）、`src/tools/skill/use_skill_tool.py`、`src/prompts/templates/master_agent.md`、`src/prompts/templates/subagent_base.md`、`src/skills/contract-approval-1.0.0/SKILL.md`、`src/core/skill_registry.py`、`subagents/complaint-handling/SUBAGENT.md`、相关 tests/、相关 docs/
   - **排除**：`frontend/`、`clients/agent-desktop/`、`docs/tools/browser/`、`docs/system/desktop-agent-client-*.md` 等本地在途工作

5. **提交**：中文 commit message，说明改动 + 测试结果 + 流程通过情况

6. **推送**：`git push origin master`

---

## 验收标准

| 项 | 标准 |
|----|------|
| 单测 | 相关测试 100% 通过 |
| 启动 | `Agent(is_master=True)` 构造成功，tool list 无 `skill_complete` |
| grep | src/ 下 skill_complete 仅剩兜底字符串字面量 |
| 文档 | ideas.md 登记完成，相关设计文档同步完成 |
| 流程 | 三智能体流程通过，无 P0/P1 未修问题 |

---

## 不在本次范围（明确不做）

- 不清理 DB 中历史的 skill_complete tool_calls 残留
- 不重构 `skill_execute` 工具
- 不引入新的 skill 生命周期管理机制
- 不修复 v3.1 Phase 4 持久化模型与内存压缩的冲突
- 不实现真正的上下文压缩能力
- 不动用户本地在途的 frontend / desktop / browser 工作（已在 stash 中保护）

---

## 风险记录

| 风险 | 缓解措施 | 负责方 |
|------|---------|--------|
| 漏改某个 import 导致启动失败 | 三智能体 + 主控者双重 import 终检 | 主控者 |
| 子智能体路径漏改 | CR 重点检查两条路径 | CR 智能体 |
| SUBAGENT.md / SKILL.md 遗漏 skill_complete 指示 | grep 全量扫描 | 开发 + CR |
| `allowed_tools` 围栏删除后 skill 工具失控 | prompt 约束足够，原围栏本就不可靠 | 接受 |
| 提交时混入本地在途改动 | 主控者按文件清单精确暂存 | 主控者 |
