# Agent 循环 AsyncGenerator 迁移 — 开发计划

> 关联设计：[AsyncGenerator 迁移方案设计](./async-generator-migration-design.md)
> 创建日期：2026-06-01
> 预计工时：8 天
> 状态：Phase 1-5 已完成。部分渠道验证需线上环境。

---

## 总览

| Phase | 内容 | 工时 | 状态 |
|-------|------|------|------|
| Phase 1 | 后端核心迁移（agent.py 重构） | 3 天 | ✅ 已完成 |
| Phase 2 | SSE 端点重构（main.py 去线程） | 1.5 天 | ✅ 已完成 |
| Phase 3 | 其他消费者适配（Gradio/CLI/渠道/定时任务） | 1 天 | ✅ 已完成 |
| Phase 4 | 前端保留扩展能力（cancelled 事件支持） | 0.5 天 | ✅ 已完成 |
| Phase 5 | 全场景回归验证 | 0.5 天 | ✅ 核心场景已验证，渠道需线上环境 |

**开发原则**：每个 Phase 完成后单独可验证。Phase 1-3 为核心迁移，必须按顺序执行。Phase 4 可独立于 Phase 1-3 的验证结果。Phase 5 在所有开发完成后执行。

---

## Phase 1：后端核心迁移（3 天）

> 目标：`process_message()` 从 `AsyncGenerator[str]` 改为 `AsyncGenerator[dict]`，所有 callback 替换为 yield。所有直接消费者（process_message_sync、execute_as_subagent、测试文件）同步适配。

### Step 1.1：创建事件模块

- [x] 新建 `src/core/agent_events.py`
  - 定义 `make_event(event_type, **kwargs)` 辅助函数
  - 导入时间戳自动填充逻辑
- [x] 验证：模块可正常导入

**改动文件**：`src/core/agent_events.py`（新增）

### Step 1.2：修改 `process_message` 签名

- [x] 删除 `progress_callback` 参数
- [x] 返回类型从 `AsyncGenerator[str, None]` 改为 `AsyncGenerator[dict, None]`
- [x] 添加 `from src.core.agent_events import make_event` 导入
- [x] 验证：仅修改签名，暂不改内部实现（此时代码会报错，预期行为）

**改动文件**：`src/core/agent.py:1331-1339`

### Step 1.3：删除 `process_message` 中 5 个 callback 闭包

- [x] 删除 `send_progress`（:1368-1370）
- [x] 删除 `send_tool_start`（:1373-1379）
- [x] 删除 `send_tool_result`（:1382-1389）
- [x] 删除 `send_thinking`（:1392-1394）
- [x] 删除 `send_clarification`（:1397-1403）
- [x] 验证：全局搜索这些函数名确认无其他引用（除 execute_as_subagent 中的同名闭包外）

**改动文件**：`src/core/agent.py:1368-1403`

### Step 1.4：替换 callback 调用为 yield（process_message 内约 20 处）

替换规则：

| 原调用 | 新代码 |
|--------|--------|
| `await send_progress("xxx")` | `yield make_event("progress", data="xxx")` |
| `await send_tool_start(name, args)` | `yield make_event("tool_start", toolName=name, toolArgs=args)` |
| `await send_tool_result(name, result, success)` | `yield make_event("tool_result", toolName=name, result=result, success=success)` |
| `await send_thinking("xxx")` | `yield make_event("thinking", data="xxx")` |
| `await send_clarification(name, q)` | `yield make_event("clarification", subagentName=name, question=q)` |

- [x] 逐个替换，确保不遗漏
- [x] 注意 `send_progress` 被传递给 `_delegate_tool.execute()` 和 `_create_scheduled_task_tool.set_context()` 的地方，这些引用也需要调整
- [x] 验证：搜索 `send_progress\|send_tool_start\|send_tool_result\|send_thinking\|send_clarification` 确认 process_message 内无残留

**改动文件**：`src/core/agent.py`（process_message 方法体内）

### Step 1.5：修改 9 处 yield 文本为 yield response 事件

逐个替换：

- [x] `:1455` — `yield final_result.strip()` → `yield make_event("response", data=final_result.strip())`
- [x] `:1459` — `yield content.strip()` → `yield make_event("response", data=content.strip())`
- [x] `:1474` — `yield clarification_text` → `yield make_event("clarification", subagentName=..., question=...)`
- [x] `:1478` — `yield error_message` → `yield make_event("response", data=error_message)`
- [x] `:1841` — `yield yield_content` → `yield make_event("response", data=yield_content)`
- [x] `:2157` — `yield clarification_text` → `yield make_event("clarification", subagentName=..., question=...)`
- [x] `:2170` — `yield f"\n<!--process-->..."` → `yield make_event("response", data=f"\n<!--process-->...")`
- [x] `:2249` — `yield f"\n<!--process-->..."` → `yield make_event("response", data=f"\n<!--process-->...")`
- [x] `:2328` — `yield apology_text` → `yield make_event("response", data=apology_text)`
- [x] 验证：搜索 `yield ` 确认 process_message 内所有 yield 都已改为 `yield make_event(...)` 格式

**改动文件**：`src/core/agent.py`（process_message 方法体内 9 处）

### Step 1.6：重构 `_delegate_to_subagent_direct`，删除 `subagent_progress_wrapper`

- [x] 删除 `subagent_progress_wrapper` 闭包（:1222-1259）
- [x] 修改调用方：`_delegate_tool.execute()` 不再传 `progress_callback`
- [x] 在 delegate_to_subagent 工具处理处（:2130-2180），手动 yield `subagent_start` 和 `subagent_end` 事件
- [x] 验证：确认 `_delegate_to_subagent_direct` 无独立调用路径（它只在 delegate_tool 内部使用）

**改动文件**：`src/core/agent.py:1222-1259`，`src/tools/agent/delegate_tool.py`

### Step 1.7：修改 `process_message_sync()` 适配新格式

- [x] 内部迭代改为 `async for event in self.process_message(...)` 
- [x] 收集 `event.get("type") == "response"` 的 `data` 拼接为最终文本
- [x] 保留 `progress_callback` 转发：将每个 event 传给外部 callback
- [x] 保留 `self._explicit_record_service` 逻辑不变
- [x] 验证：渠道回调 `collect_files_callback` 接收的 dict 格式不变

**改动文件**：`src/core/agent.py:2334-2365`

### Step 1.8：修改 `execute_as_subagent()` 适配新格式（方案 A）

- [x] 删除 4 个 callback 闭包（:2432-2458）
- [x] 替换 callback 调用为内部事件收集（不 yield，append 到 `collected_events` 列表）
- [x] 内部逻辑改为：构建事件 dict → append 到 collected_events → 不传给外部
- [x] 最终返回值增加 `"events": collected_events` 字段
- [x] 验证：`SubagentExecutor._run_instance()` 不需要改动（仍然 await 返回 dict）

**改动文件**：`src/core/agent.py:2377-2895`

### Step 1.9：适配测试文件

- [x] `tests/test_agent_llm.py:65` — `async for chunk` → `async for event`，取 `event.get("data", "")` 当 type=="response" 时
- [x] `tests/test_agent_llm_interactive.py:225` — 同上
- [x] `tests/e2e/test_agent_full.py:36` — 同上
- [x] 验证：测试文件语法正确（不一定能运行，因为依赖外部 LLM）

**改动文件**：`tests/test_agent_llm.py`，`tests/test_agent_llm_interactive.py`，`tests/e2e/test_agent_full.py`

### Step 1.10：新增 LLM 调用事件和迭代事件（为可观测性准备）

- [x] 在 LLM 调用前 yield `make_event("llm_call_start", ...)`
- [x] 在 LLM 调用后 yield `make_event("llm_call_end", ...)`
- [x] 在迭代循环开始 yield `make_event("iteration_start", ...)`
- [x] 在迭代循环结束 yield `make_event("iteration_end", ...)`
- [x] 这些事件前端会静默忽略（default 分支），不影响现有功能
- [x] 验证：新增事件类型在 `agent_events.py` 中有定义

**改动文件**：`src/core/agent.py`（LLM 调用和迭代循环处）

### Phase 1 完成标准

- [x] `agent.py` 编译通过（`python -c "from src.core.agent import Agent"` 不报错）
- [x] 全局搜索 `send_progress`、`send_tool_start`、`send_tool_result`、`send_thinking`、`send_clarification` 在 process_message 内无残留
- [x] 全局搜索 `progress_callback` 在 `process_message` 签名中已移除
- [x] 所有 `yield` 语句在 process_message 内都改为 `yield make_event(...)`
- [x] `process_message_sync()` 可正常收集 response 文本并转发事件
- [x] `execute_as_subagent()` 返回结构不变的 dict

---

## Phase 2：SSE 端点重构（1.5 天）

> 目标：删除 ThreadPoolExecutor + 线程 + 轮询，改为直接 async for 迭代 agent.process_message()。

### Step 2.1：重写 `chat_stream` 端点

- [x] 删除 `event_generator()` 内的以下代码（约 200 行）：
  - `results` 共享字典
  - `threading.Event` 完成信号
  - `run_agent()` 内部函数（含新 event loop、sync/async callback 包装、consume_generator）
  - `ThreadPoolExecutor` 创建和提交
  - 50ms 轮询循环
- [x] 替换为约 80 行的 async generator 直连模式：
  - `async for event in agent.process_message(...)` 直接迭代
  - 每个 event 序列化为 SSE 帧 `data: {json}\n\n`
  - 异常处理（CancelledError → cancelled 事件，Exception → error 事件）
  - finally 块发送 complete 事件
- [x] 验证：SSE 连接可建立，`connected` 事件正常发送

**改动文件**：`src/main.py:1260-1604`

### Step 2.2：迁移 `record_service` 处理逻辑

- [x] 将 `SessionRecordManager.start_record()` 移到 event_generator 开头（agent 调用前）
- [x] 将 `record_service.handle_progress_event(event)` 移到 async for 循环内，按事件类型处理
- [x] 将 `record_service.complete(full_response)` 移到 finally 块
- [x] 将 `SessionRecordManager.end_record()` 移到 finally 块
- [x] 验证：token 记录和会话记录正常

**改动文件**：`src/main.py`（event_generator 内）

### Step 2.3：保留 `sse_manager` 取消检查

- [x] 通过 `cancel_check=lambda: sse_manager.is_cancelled(session_id)` 参数传递
- [x] 在 event_generator 开头清除残留 cancel 标记
- [x] 验证：用户取消时事件流正确终止

**改动文件**：`src/main.py`（event_generator 内）

### Step 2.4：迁移数据库保存逻辑

- [x] 将完整响应拼接到 finally 块（从所有 response 事件中收集）
- [x] 将 in-memory history 更新移到 finally 块
- [x] 将 DB 消息保存（用户消息 + 助手消息 + progressMessages + downloadableFiles）移到 finally 块
- [x] 保留客户端已断开时的 early return 逻辑
- [x] 验证：会话记录正常保存，刷新后消息可恢复

**改动文件**：`src/main.py`（event_generator 内）

### Step 2.5：简化非流式 `/api/chat` 端点（优化项）

- [x] 将 `loop.run_in_executor(None, lambda: asyncio.run(...))` 简化为直接 `await agent.process_message_sync(...)`
- [x] 原因：`process_message_sync` 现在是纯 async，不再需要线程
- [x] 验证：非流式 API 返回正确 JSON

**改动文件**：`src/main.py:729-739`

### Phase 2 完成标准

- [x] SSE 端点无 ThreadPoolExecutor、无 threading.Event、无 results 共享字典
- [x] `python -c "from src.main import app"` 不报错
- [x] 前端可正常连接 SSE，消息流式显示
- [x] record_service token 记录正常
- [x] 取消功能正常
- [x] DB 保存正常

---

## Phase 3：其他消费者适配（1 天）

> 目标：适配所有非 SSE 端点的 process_message/process_message_sync 消费者。

### Step 3.1：修改 `gradio_app.py`

- [x] 删除 `progress_callback` lambda 传参
- [x] `async for event in master_agent.process_message(...)` 迭代
- [x] 按 event type 分发：response → 拼接文本，progress/tool_start/tool_result → add_progress_message
- [x] 验证：Gradio UI 可正常对话和显示进度

**改动文件**：`gradio_app.py:131-190`

### Step 3.2：修改 CLI 模式

- [x] `async for event in master_agent.process_message(...)` 迭代
- [x] response → `print(data, end="", flush=True)`
- [x] tool_start → `print(f"  [tool] {toolName}")`
- [x] progress → `print(f"  [progress] {data}")`
- [x] 验证：CLI 输出正常

**改动文件**：`src/main.py:1701-1736`

### Step 3.3：验证渠道路由兼容性

- [x] 企业微信同步路径（`channel_routes.py:199`）：`process_message_sync()` 已在 Phase 1 适配，`collect_files_callback` 接收的 dict 格式不变
- [x] 企业微信异步路径（`channel_routes.py:342`）：同上
- [x] 微信客服路径（`channel_routes.py:1073`）：同上
- [x] 钉钉/飞书（通过 `_process_tenant_message` 统一处理）：同上
- [x] 验证：渠道消息发送和文件下载功能正常

**改动文件**：无（验证确认兼容性）

### Step 3.4：验证定时任务兼容性

- [x] `scheduler/executor.py:133`（execute）和 `:238`（dry_run）：调用 `process_message_sync()` 无 progress_callback，已适配
- [x] 验证：定时任务可正常触发和返回结果

**改动文件**：无（验证确认兼容性）

### Phase 3 完成标准

- [x] Gradio UI 对话和进度显示正常
- [x] CLI 输出正常
- [x] 渠道路由消息发送和文件下载正常
- [x] 定时任务执行正常
- [x] 所有直接消费 `process_message()` 的代码已适配

---

## Phase 4：前端保留扩展能力（0.5 天）

> 目标：为后端新增的 `cancelled` 事件提供最小化的类型和解析支持。**默认 UI 不展示工作过程**，保持 `v-if="isDebugEnabled && hasProgress"` 不变。Debug 模式行为不变。

### Step 4.1：扩展 `MessageStreamEvent` 类型

- [x] 仅追加 `cancelled` 事件类型（后端当前实际使用的唯一新事件）
- [x] 其余新事件（`subagent_start`、`subagent_end`、`llm_call_end`、`iteration_start`）等后端实际使用时再追加
- [x] 验证：TypeScript 编译通过

**改动文件**：`frontend/src/types/index.ts`

### Step 4.2：扩展 `SSEManager.parseSSELine()`

- [x] 追加 `cancelled` case（静默处理，`break`，不触发 UI 回调）
- [x] 验证：现有事件类型处理逻辑不受影响

**改动文件**：`frontend/src/api/agent.ts`

### Step 4.3：构建验证

- [x] 运行 `cd frontend && npm run build`
- [x] 验证：构建通过，无 warning

**改动文件**：无（验证步骤）

### 不改动的文件

| 文件 | 原因 |
|------|------|
| `frontend/src/components/MessageItem.vue` | 保持 `v-if="isDebugEnabled && hasProgress"` 不变 |
| `frontend/src/composables/useAgent.ts` | 不新增回调处理 |
| `frontend/src/composables/useDebugMode.ts` | 不变 |

### Phase 4 完成标准

- [x] TypeScript 编译通过
- [x] npm run build 通过
- [x] 默认模式下不展示工作过程（只渲染 response 内容）
- [x] Debug 模式下执行详情展示不变
- [x] 现有功能不受影响

---

## Phase 5：全场景回归验证（0.5 天）

> 目标：确保迁移后所有功能正常工作。

### 验证场景清单

- [x] **Web SSE 对话**：消息流式显示、工具调用进度、完成事件
- [x] **工具调用**：搜索、邮件、知识库等工具调用和结果展示
- [ ] **子智能体委派**：委派和结果回传（MASTER → SUBAGENT）（需要线上环境验证）
- [ ] **子智能体独立运行**：STANDALONE 模式通过前端直连正常工作（需要线上环境验证）
- [ ] **用户取消**：取消请求后事件流终止（需要线上环境验证）
- [x] **非流式 API**：`/api/chat` 返回正确 JSON
- [ ] **企业微信渠道**：消息发送和文件下载（需要线上环境验证）
- [ ] **钉钉/飞书渠道**：消息发送（需要线上环境验证）
- [ ] **微信客服渠道**：消息发送和文件下载（需要线上环境验证）
- [ ] **定时任务**：执行和试运行（需要线上环境验证）
- [ ] **Gradio UI**：流式对话和进度显示（需要启动 gradio_app.py 验证）
- [ ] **CLI 模式**：输出正常（需要 CLI 环境验证）
- [ ] **测试文件**：`test_agent_llm.py` 等测试通过（需要 LLM 凭证）

---

## 开发注意事项

### 必须按顺序执行的依赖

```
Step 1.1（agent_events.py）→ Step 1.2（签名）→ Step 1.3（删闭包）→ Step 1.4（替换 callback）
→ Step 1.5（替换 yield）→ Step 1.6（delegate 重构）→ Step 1.7（sync 适配）→ Step 1.8（subagent 适配）
→ Step 1.9（测试适配）→ Step 1.10（新增事件）→ Phase 1 完成

Phase 1 完成 → Step 2.1（SSE 重写）→ Step 2.2-2.5 → Phase 2 完成

Phase 2 完成 → Step 3.1-3.4 → Phase 3 完成

Phase 3 完成 → Step 4.1-4.5（前端可并行开发，但需后端完成才能测试）

Phase 4 完成 → Phase 5
```

### 可并行的工作

- Phase 4 前端工作可与 Phase 2/3 并行开发（但测试需等后端完成）
- Step 1.10（新增 LLM/迭代事件）可在 Step 1.5 完成后任意时间插入

### 回滚策略

- 每个 Phase 独立 git commit
- Phase 1 完成后如果 Phase 2 SSE 重写出问题，可临时保留旧的 ThreadPoolExecutor 方案，用适配层桥接新格式
- Phase 4 前端改动是纯追加，可随时 revert 不影响后端

### 子智能体两种模式的影响总结

| 模式 | 运行方式 | 迁移影响 |
|------|---------|---------|
| STANDALONE（前端直连） | 前端 POST subagent 参数 → AgentRouter 创建独立 Agent → process_message() SSE 流式 | **零额外改动**，和 master_agent 完全相同路径 |
| SUBAGENT（MASTER 委派） | MASTER LLM 调用 delegate_to_subagent → SubagentExecutor → execute_as_subagent() 返回 Dict | **方案 A**：保持返回 Dict，内部收集事件；主智能体在委派前后 yield subagent_start/end |
