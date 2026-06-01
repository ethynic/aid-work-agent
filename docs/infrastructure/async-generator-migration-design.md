# Agent 循环 AsyncGenerator 迁移方案设计

> 设计日期：2026-06-01
> 关联调研：[PilotDeck 智能体架构深度调研](../research/pilotdeck-agent-research.md)
> 前置条件：本迁移完成后，再实施可观测性方案
> 状态：草案

---

## 一、迁移目标

将 Agent 循环从 **Callback + 线程轮询** 模式迁移到 **AsyncGenerator 事件流** 模式，实现：

1. **统一事件通道**：所有中间状态（progress、tool_start、tool_result、thinking、clarification）和最终文本都通过 `yield` 输出，不再分两条通道
2. **消除线程边界**：Agent 运行在 FastAPI 的 async 上下文中，不再需要 ThreadPoolExecutor + 新 event loop + 共享字典
3. **类型化事件**：所有 yield 的事件都是结构化 dict，前端可以精确区分和处理
4. **前端体验升级**：从仅显示 `type=response` 升级为完整展示 Agent 工作过程（工具调用、思考过程、子智能体活动）

---

## 二、当前架构全景

### 2.1 后端事件产生

`src/core/agent.py` 的 `process_message()` 有两个输出通道：

```
通道 1: yield chunk_text            → results['chunks']  → SSE "response" 事件
通道 2: await progress_callback(dict) → results['progress'] → SSE "progress"/"tool_start"/"tool_result"/"thinking"/"clarification" 事件
```

### 2.2 线程边界

```
FastAPI 主线程 (event loop A)
  └── ThreadPoolExecutor 创建新线程
       └── asyncio.new_event_loop() (event loop B)
            └── agent.process_message()
                 ├── yield chunk → results['chunks']
                 └── callback(dict) → results['progress']

SSE generator (仍在主线程 event loop A)
  └── 每 50ms 轮询 results['progress'] 和 results['chunks']
       └── yield SSE data: 帧
```

### 2.3 前端事件消费

```
SSEManager.connect()
  └── fetch('/api/chat/stream', {method: 'POST'})
       └── ReadableStream reader
            └── parseSSELine() 按 type 分发
                 ├── type=response   → currentResponse += data → 更新消息内容
                 ├── type=progress   → addProgress('progress')  → console.log
                 ├── type=tool_start → addProgress('tool_start') → console.info
                 ├── type=tool_result→ addProgress('tool_result')→ console.info
                 ├── type=thinking   → addProgress('thinking')   → console.log
                 ├── type=clarification → addProgress('tool_start')
                 └── type=complete   → isProcessing = false
```

**当前前端只渲染 `type=response` 的内容到聊天消息气泡。** progress、tool_start、tool_result、thinking 等事件只在 debug 模式（`?debug=1`）下展示为可折叠的执行详情。

### 2.4 所有消费者清单

| 消费者 | 文件 | 入口方法 | 用到的事件类型 |
|--------|------|---------|-------------|
| Web SSE 前端 | `src/main.py:1040` | `process_message()` + ThreadPoolExecutor | 全部（response, progress, tool_*, thinking, clarification） |
| 非流式 API | `src/main.py:733` | `process_message_sync()` | 无 progress，只要最终文本 |
| Gradio 调试 UI | `gradio_app.py:167` | `process_message()` | 字符串/字典 progress（通过 queue） |
| 企业微信（同步） | `src/saas/api/channel_routes.py:199` | `process_message_sync()` | 只要 tool_result 中的下载文件 |
| 企业微信（异步） | `src/saas/api/channel_routes.py:342` | `process_message_sync()` | 只要 tool_result 中的下载文件 |
| 微信客服 | `src/saas/api/channel_routes.py:1073` | `process_message_sync()` | 只要 tool_result 中的下载文件 |
| 钉钉/飞书 | `src/saas/api/channel_routes.py:199` | `process_message_sync()` | 通过 `_process_tenant_message` 统一处理 |
| CLI | `src/main.py:1728` | `process_message()` | 只要文本（无 progress_callback） |
| 子智能体委派 | `src/core/agent.py:2140` | `delegate_tool.execute()` → `execute_as_subagent()` | progress 通过 callback 上报，yield 仅在委派结果后 |
| 定时任务执行 | `src/scheduler/executor.py:133` | `process_message_sync()` | 只要最终文本（无 progress_callback） |
| 定时任务试运行 | `src/scheduler/executor.py:238` | `process_message_sync()` | 只要最终文本（无 progress_callback） |

---

## 三、迁移后的目标架构

### 3.1 事件协议设计

定义统一的 AgentEvent 类型，替代当前的 yield chunk + callback dict：

```python
# src/core/agent_events.py

from typing import TypedDict, Optional, Any, List


class AgentEvent(TypedDict, total=False):
    """Agent 循环产生的所有事件统一格式"""
    type: str           # 事件类型（必填）
    timestamp: float    # Unix 时间戳（自动填充）

    # --- 生命周期事件 ---
    # type: "connected"
    session_id: str
    agent_type: str

    # type: "progress"
    data: str

    # type: "response"  — 文本内容，前端拼接到消息气泡
    # data: str

    # type: "tool_start"
    toolName: str
    toolArgs: dict

    # type: "tool_result"
    toolName: str
    result: Any
    success: bool

    # type: "thinking"
    # data: str

    # type: "clarification"
    subagentName: str
    question: str

    # type: "busy"（注意：此事件在 main.py SSE 端点中直接 yield，不在 agent.py 中产生）
    instance_id: str
    message: str
    is_same_user: bool

    # type: "error"
    # data: str

    # type: "complete"
    # data: str  (可选，成功时的最终状态描述)

    # type: "cancelled"

    # --- 新增：子智能体事件 ---
    # type: "subagent_start"
    subagent_id: str
    subagent_name: str
    task: str

    # type: "subagent_end"
    subagent_id: str
    success: bool
    summary: str

    # --- 新增：LLM 调用事件（为可观测性准备） ---
    # type: "llm_call_start"
    model: str
    iteration: int

    # type: "llm_call_end"
    model: str
    iteration: int
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int

    # --- 新增：迭代事件 ---
    # type: "iteration_start"
    # iteration: int

    # type: "iteration_end"
    # iteration: int
```

**关键设计决策**：

1. **与现有 SSE 事件格式 100% 兼容**：`type`、`data`、`toolName`、`toolArgs`、`result`、`success`、`subagentName`、`question` 等字段名完全保持不变，前端零改动
2. **新增事件类型是追加**：`subagent_start/end`、`llm_call_start/end`、`iteration_start/end`、`cancelled` 是新增，前端可以选择性处理
3. **`response` 事件不变**：前端仍然只把 `type=response` 的内容拼接到消息气泡，这是核心渲染逻辑

### 3.2 Agent.process_message 新签名

```python
async def process_message(
    self,
    user_input: str,
    session_id: str,
    user: Optional[User] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> AsyncGenerator[dict, None]:
    """
    处理用户消息，yield AgentEvent dict。

    事件类型：
    - response:    文本内容，前端拼接到消息气泡
    - progress:    进度文本
    - tool_start:  工具开始执行
    - tool_result: 工具执行完成
    - thinking:    思考过程
    - clarification: 子智能体需要补充信息
    - complete:    处理完成
    - error:       错误
    - cancelled:   用户取消
    """
```

**变化**：
- 删除 `progress_callback` 参数
- 返回类型从 `AsyncGenerator[str, None]` 改为 `AsyncGenerator[dict, None]`
- 所有 yield 的都是 dict，不再 yield 纯字符串

### 3.3 新的 SSE 端点

```python
# src/main.py

@app.post("/api/chat/stream")
async def chat_stream(http_request: Request, request: ChatRequest):
    """SSE 流式对话 — 直接 async for 迭代，无需线程"""

    async def event_generator():
        # 1. 发送 connected 事件
        yield f"data: {json.dumps({...})}\n\n"

        # 2. 直接在 FastAPI event loop 中迭代 agent
        try:
            async for event in agent.process_message(
                user_input=full_message,
                session_id=session_id,
                user=agent_user,
                attachments=attachments,
                cancel_check=lambda: sse_manager.is_cancelled(session_id),
            ):
                # 3. 每个 event 直接序列化为 SSE 帧
                event['timestamp'] = int(time.time() * 1000)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                # 4. 处理 record_service 需要的特殊逻辑
                if event.get('type') == 'tool_result':
                    record_service.handle_progress_event(event)

        except asyncio.CancelledError:
            yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"
        finally:
            # 5. 发送 complete 事件
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
            # 6. 清理和保存

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={...}
    )
```

**消除了**：
- `ThreadPoolExecutor` 创建线程
- `asyncio.new_event_loop()` 创建新 event loop
- `results` 共享字典
- `threading.Event` 完成信号
- `sync_progress_callback` / `async_progress_callback` 回调包装
- `consume_generator()` 辅助函数
- 50ms 轮询循环

**保留了**：
- SSE 帧格式 `data: {json}\n\n`（前端零改动）
- `record_service` 处理逻辑（从 callback 内移到 event_generator 循环内）
- `sse_manager` 取消检查（从 callback 内移到 `cancel_check` 参数）
- 数据库保存逻辑（移到 event_generator 的 finally 块）

### 3.4 前端变更

前端分为两部分：**零改动部分** 和 **体验升级部分**。

#### 零改动部分（SSE 消费层）

`frontend/src/api/agent.ts` 的 `SSEManager.parseSSELine()` 和 `frontend/src/types/index.ts` 的 `MessageStreamEvent` 类型**无需任何修改**。

原因：后端 yield 的事件 JSON 格式完全兼容现有的 10 种事件类型：

```typescript
// 现有类型，完全不需要改
export type MessageStreamEvent =
  | { type: 'connected'; session_id: string; timestamp: number }
  | { type: 'progress'; data: string; timestamp: number }
  | { type: 'response'; data: string; timestamp: number }
  | { type: 'complete'; timestamp: number }
  | { type: 'error'; data: string; timestamp: number }
  | { type: 'tool_start'; toolName: string; toolArgs: object; timestamp: number }
  | { type: 'tool_result'; toolName: string; result: any; success: boolean; timestamp: number }
  | { type: 'thinking'; data: string; timestamp: number }
  | { type: 'clarification'; subagentName: string; question: string; timestamp: number }
  | { type: 'busy'; ... }
```

新增的事件类型（`subagent_start`、`llm_call_end`、`iteration_start`、`cancelled`）在 `parseSSELine` 的 switch 中会落入 default 分支被静默忽略——不影响现有功能。

#### 前端扩展部分（保留能力，默认不展示）

前端需要为后端新增的事件类型提供最小化的类型支持和解析支持，**但默认 UI 不展示工作过程**，保持 `v-if="isDebugEnabled && hasProgress"` 不变。

**设计原则**：
1. 默认模式下不展示任何 Agent 工作过程信息，只渲染 `type=response` 的内容
2. Debug 模式（`?debug=1`）下保持原有的执行详情展示
3. 代码层面保留新增事件类型的解析能力，方便未来随时开启

**1. `MessageStreamEvent` 类型扩展**（仅追加 `cancelled`，其余暂不加）：

后端当前实际新增的事件只有 `cancelled`（用户取消时产生），其余（`subagent_start`、`subagent_end`、`llm_call_end`、`iteration_start`）尚未使用。

```typescript
export type MessageStreamEvent =
  // ... 现有 10 种保持不变 ...
  | { type: 'cancelled'; timestamp: number }
```

**2. `SSEManager.parseSSELine()` 扩展**（追加 case，不触发 UI 回调）：

```typescript
case 'cancelled':
  // 保留扩展能力，暂不触发 UI 回调
  break
```

**3. `MessageItem.vue` — 不改动**：

保持 `v-if="isDebugEnabled && hasProgress"` 条件不变，默认模式下不展示工作过程。

**4. `useAgent.ts` — 不改动**：

不新增回调处理。现有的 `addProgress()` 已足够处理当前事件类型。

**5. 未来扩展开启方式**：

当需要开启默认模式展示时，只需：
- 修改 `MessageItem.vue` 的 `v-if` 条件
- 在 `useAgent.ts` 中添加新回调（如 `onCancelled`）
- 在 `SSEManager.connect()` 中暴露新回调参数

---

## 四、详细改动清单

### 4.1 `src/core/agent_events.py` — 新增

定义 `AgentEvent` 类型（如上 §3.1）和辅助函数：

```python
import time
from typing import Dict, Any

def make_event(event_type: str, **kwargs) -> Dict[str, Any]:
    """创建标准 Agent 事件"""
    event = {"type": event_type, "timestamp": int(time.time() * 1000)}
    event.update(kwargs)
    return event
```

### 4.2 `src/core/agent.py` — 重构

#### 改动 1：修改签名

```python
# 前：
async def process_message(
    self, user_input, session_id, user=None, attachments=None,
    progress_callback=None, cancel_check=None,
) -> AsyncGenerator[str, None]:

# 后：
async def process_message(
    self, user_input, session_id, user=None, attachments=None,
    cancel_check=None,
) -> AsyncGenerator[dict, None]:
```

#### 改动 2：删除 callback 闭包（process_message 和 execute_as_subagent 两处）

`process_message` 中删除 5 个闭包（`agent.py:1368-1403`）：
- `send_progress`、`send_tool_start`、`send_tool_result`、`send_thinking`、`send_clarification`

`execute_as_subagent` 中删除 4 个闭包（`agent.py:2432-2458`）：
- `send_progress`、`send_tool_start`、`send_tool_result`、`send_thinking`
- 注意：`execute_as_subagent` 没有 `send_clarification` 闭包

还需处理 `_delegate_to_subagent_direct` 中的 `subagent_progress_wrapper`（`agent.py:1222-1259`），该包装器将 dict 事件扁平化为字符串再传给外层 callback。迁移后可删除此包装器，因为事件流直接 yield 不需要中间转换。

#### 改动 3：替换所有 callback 调用为 yield

全文替换规则（约 30 处）：

| 原 callback 调用 | 新 yield |
|-----------------|---------|
| `await send_progress("xxx")` | `yield make_event("progress", data="xxx")` |
| `await send_tool_start(name, args)` | `yield make_event("tool_start", toolName=name, toolArgs=args)` |
| `await send_tool_result(name, result, success)` | `yield make_event("tool_result", toolName=name, result=result, success=success)` |
| `await send_thinking("xxx")` | `yield make_event("thinking", data="xxx")` |
| `await send_clarification(name, q)` | `yield make_event("clarification", subagentName=name, question=q)` |

#### 改动 4：修改 yield 文本为 yield response 事件

实际代码中有 9 处 yield（`agent.py:1455, 1459, 1474, 1478, 1841, 2157, 2170, 2249, 2328`）：

```python
# 前（9 处 yield 文本）：
yield final_result.strip()          # :1455 重新委派成功
yield content.strip()               # :1459 dict 结果提取内容
yield clarification_text            # :1474 重新委派需要补充信息
yield error_message                 # :1478 重新委派失败
yield yield_content                 # :1841 最终响应（无工具调用）
yield clarification_text            # :2157 子智能体需要补充信息
yield f"\n<!--process-->..."        # :2170 子智能体生成内容
yield f"\n<!--process-->..."        # :2249 content_generate 工具成功
yield apology_text                  # :2328 最大迭代次数

# 后：
yield make_event("response", data=final_result.strip())
yield make_event("response", data=content.strip())
yield make_event("clarification", ...)  # :1474 改为结构化事件
yield make_event("response", data=error_message)  # :1478 错误也作为响应
yield make_event("response", data=yield_content)
yield make_event("clarification", ...)  # :2157 改为结构化事件
yield make_event("response", data=f"\n<!--process-->...")
yield make_event("response", data=f"\n<!--process-->...")
yield make_event("response", data=apology_text)
```

#### 改动 5：新增 LLM 调用事件（为可观测性准备）

```python
# LLM 调用前
yield make_event("llm_call_start", model=model_name, iteration=iteration)

response = await self.llm.chat_with_tools(...)

# LLM 调用后
yield make_event("llm_call_end",
    model=model_name,
    iteration=iteration,
    prompt_tokens=response.get("usage", {}).get("prompt_tokens", 0),
    completion_tokens=response.get("usage", {}).get("completion_tokens", 0),
    duration_ms=int((time.time() - llm_call_start) * 1000),
)
```

#### 改动 6：新增迭代事件

```python
while iteration < max_iterations:
    iteration += 1
    yield make_event("iteration_start", iteration=iteration)
    # ... 循环体 ...
    # 循环末尾（在 append tool results 之后）
    yield make_event("iteration_end", iteration=iteration)
```

#### 改动 7：子智能体委派事件

**重要发现**：当前代码中，子智能体执行期间（`self._delegate_tool.execute()` 是 `await` 阻塞调用）不会 yield 任何事件。进度完全通过 `progress_callback` 链上报。迁移后需要改为事件流，但 `delegate_tool.execute()` 内部调用 `SubagentExecutor`，后者调用 `execute_as_subagent()`。所以事件流需要从 `execute_as_subagent` → `SubagentExecutor` → `delegate_tool` → `process_message` 逐层穿透。

```python
# delegate_to_subagent 工具处理（agent.py:2140-2180）
yield make_event("subagent_start",
    subagent_id=subagent_id,
    subagent_name=subagent_name,
    task=task_description,
)

# 注意：当前是 await 阻塞调用，需要改为 async for 事件迭代
delegation_result = {}
async for event in self._delegate_tool.execute_stream(...):
    if event.get("type") in ("response", "progress", "tool_start", "tool_result", "thinking"):
        yield event  # 透传子智能体事件到上层
    elif event.get("type") == "complete":
        delegation_result = event.get("result", {})

yield make_event("subagent_end",
    subagent_id=subagent_id,
    success=delegation_result.get("success", True),
    summary=delegation_result.get("summary", ""),
)
```

**注意**：这要求 `delegate_tool.execute()` 也支持返回 AsyncGenerator，或者新增 `execute_stream()` 方法。这是一个**更深的改动链**，需要在实现时同步修改 `SubagentTool`、`SubagentExecutor` 和 `execute_as_subagent` 的事件传递。

#### 改动 8：修改 `process_message_sync()`

```python
async def process_message_sync(
    self, user_input, session_id, user=None, attachments=None,
    record_service=None, progress_callback=None
) -> str:
    self._explicit_record_service = record_service
    response_parts = []
    async for event in self.process_message(
        user_input, session_id, user, attachments, cancel_check=None,
    ):
        if event.get("type") == "response":
            response_parts.append(event.get("data", ""))
        # 向 channel 的 progress_callback 转发事件
        if progress_callback:
            if isinstance(progress_callback, Callable):
                await progress_callback(event)
    return "".join(response_parts)
```

#### 改动 9：修改 `execute_as_subagent()`

与 `process_message` 同样模式——删除 4 个 callback 闭包（`agent.py:2432-2458`），改为 yield 事件。但 `execute_as_subagent` 当前返回 dict 而非 generator。

**有两个选择**：

**方案 A（推荐）：保持返回 dict，内部收集事件**
子智能体的调用者（`SubagentExecutor._run_instance`）不消费事件流，只等待最终结果。

```python
async def execute_as_subagent(...) -> Dict[str, Any]:
    collected_events = []

    async for event in self.process_message(...):
        collected_events.append(event)
        # subagent 不需要 yield 出去

    # 从收集的事件中提取最终文本
    response_text = "".join(
        e.get("data", "") for e in collected_events if e.get("type") == "response"
    )
    return {"result": response_text, "events": collected_events, ...}
```

**方案 B：改为 AsyncGenerator（支持子智能体事件透传）**
如果要实现改动 7 中的子智能体事件透传，需要将 `execute_as_subagent` 也改为 AsyncGenerator。这会影响 `SubagentExecutor._run_instance` 的调用方式。

**建议先用方案 A**，子智能体事件透传（`subagent_start/end`）在 `process_message` 的 delegate_to_subagent 工具处理处手动 yield，不需要真正穿透 `execute_as_subagent`。

### 4.3 `src/main.py` — 重构 SSE 端点

#### 改动 1：`chat_stream` 端点

删除 `ThreadPoolExecutor` + `run_agent` + `event_generator` 的 200+ 行代码，替换为约 80 行的 async generator 直连模式（如上 §3.3）。

#### 改动 2：非流式 `/api/chat` 端点

无需改动，它调用的是 `process_message_sync()`，内部会适配。

#### 改动 3：CLI 模式

```python
# 前：
async for chunk in agent.process_message(...):
    print(chunk, end="", flush=True)

# 后：
async for event in agent.process_message(...):
    if event.get("type") == "response":
        print(event.get("data", ""), end="", flush=True)
    elif event.get("type") == "tool_start":
        print(f"  [tool] {event.get('toolName')}")
    elif event.get("type") == "progress":
        print(f"  [progress] {event.get('data')}")
```

### 4.4 `gradio_app.py` — 适配

```python
# 前（gradio_app.py:167）：
async for chunk in master_agent.process_message(
    ..., progress_callback=lambda msg: progress_callback(session_id, msg)
):
    response_chunks.append(chunk)

# 后：
async for event in master_agent.process_message(...):
    if event.get("type") == "response":
        response_chunks.append(event.get("data", ""))
    elif event.get("type") == "tool_start":
        add_progress_message(session_id, f"🔧 执行工具: {event.get('toolName')}")
    elif event.get("type") == "tool_result":
        add_progress_message(session_id, f"📤 工具完成: {event.get('toolName')}")
    elif event.get("type") == "progress":
        add_progress_message(session_id, event.get("data", ""))
```

注意：Gradio 的 `progress_callback` 当前通过 lambda 传给 `process_message`，迁移后删除该 lambda，改为在迭代中直接处理事件。

### 4.5 `src/saas/api/channel_routes.py` — 适配

渠道路由使用 `process_message_sync()`，该方法的适配在 §4.2 改动 8 中处理。渠道回调 `collect_files_callback` 接收的事件格式不变（`{"type": "tool_result", "toolName": ..., "result": ..., "success": ...}`），无需改动。

**注意**：当前渠道只有一个统一入口 `_process_tenant_message`（支持 wecom/dingtalk/feishu），以及企业微信的异步路径 `_process_tenant_wecom_background` 和微信客服路径。钉钉和飞书**不单独有路由**，都走 `_process_tenant_message`。

### 4.6 `src/core/subagent_executor.py` — 适配

`SubagentExecutor`（`src/subagents/executor.py`）内部调用 `Agent` 实例的 `execute_as_subagent()`，而非 `process_message()`（注意：设计文档原文此处描述有误，实际调用的是 `execute_as_subagent`）。

如果采用方案 A（保持 `execute_as_subagent` 返回 dict），则 `SubagentExecutor` **无需改动**——它调用 `execute_as_subagent()` 的返回值结构不变。

如果采用方案 B（改为 AsyncGenerator），则需修改 `_run_instance` 方法（`executor.py:319`）：

```python
# 前：
result = await asyncio.wait_for(
    instance.execute_as_subagent(...),
    timeout=timeout
)

# 后（方案 B）：
collected_events = []
async for event in instance.execute_as_subagent(...):
    collected_events.append(event)
    if progress_callback:
        await progress_callback(event)
```

### 4.7 `src/scheduler/executor.py` — 无需改动

定时任务执行器（`execute()` 和 `dry_run()`）调用 `process_message_sync()`，不传 `progress_callback`，只取最终文本。因为 `process_message_sync()` 的适配在 §4.2 改动 8 中处理，此处无需改动。

### 4.8 `_delegate_to_subagent_direct` — 删除 `subagent_progress_wrapper`

`agent.py:1222-1259` 的 `subagent_progress_wrapper` 是一个特殊闭包，将子智能体的 dict 事件扁平化为字符串再传给外层 callback。迁移后：

- `process_message` 直接 yield 事件，不再需要 callback 包装
- `_delegate_to_subagent_direct` 方法需重构：不再接收 `progress_callback`，改为 yield 事件或被 `process_message` 的 delegate 逻辑统一处理
- 如果该方法有独立调用路径，需确认其调用者也能处理事件流

### 4.9 测试文件适配

以下测试文件直接消费 `process_message()` 的输出，需要适配新的 dict 格式：

| 测试文件 | 行号 | 改动 |
|---------|------|------|
| `tests/test_agent_llm.py` | :65 | `async for chunk` → `async for event`，取 `event.get("data", "")` |
| `tests/test_agent_llm_interactive.py` | :225 | 同上 |
| `tests/e2e/test_agent_full.py` | :36 | 同上 |
| `tests/test_pdf_skill.py` | :82 | 使用 `process_message_sync()`，内部适配后无需改动 |

### 4.10 前端 — 保留扩展能力（默认不展示）

前端仅做最小化适配：为后端新增的 `cancelled` 事件提供类型支持和解析支持。**默认 UI 不展示工作过程**，保持 `v-if="isDebugEnabled && hasProgress"` 不变。

#### 4.10.1 `frontend/src/types/index.ts`

仅追加 `cancelled` 事件类型（后端当前实际使用的唯一新事件）：

```typescript
export type MessageStreamEvent =
  // ... 现有 10 种保持不变 ...
  | { type: 'cancelled'; timestamp: number }
```

其余新事件（`subagent_start`、`subagent_end`、`llm_call_end`、`iteration_start`）等后端实际使用时再追加。

#### 4.10.2 `frontend/src/api/agent.ts`

`SSEManager.parseSSELine()` 追加 `cancelled` case（静默处理，不触发 UI 回调）：

```typescript
case 'cancelled':
  break  // 保留扩展能力，暂不触发 UI 回调
```

#### 4.10.3 不改动的文件

| 文件 | 原因 |
|------|------|
| `frontend/src/components/MessageItem.vue` | 保持 `v-if="isDebugEnabled && hasProgress"` 不变，默认不展示 |
| `frontend/src/composables/useAgent.ts` | 不新增回调处理，现有的 `addProgress()` 已足够 |
| `frontend/src/composables/useDebugMode.ts` | 不变 |

#### 4.10.4 未来扩展开启方式

当需要开启默认模式展示时：
1. 修改 `MessageItem.vue` 的 `v-if` 条件（去掉 `isDebugEnabled` 守卫）
2. 在 `useAgent.ts` 中添加新回调（如 `onCancelled`）
3. 在 `SSEManager.connect()` 中暴露新回调参数
4. 在 `types/index.ts` 中追加实际使用的新事件类型

---

## 五、不改动清单

以下文件/模块**无需任何改动**：

| 文件/模块 | 原因 |
|-----------|------|
| `src/llm/gateway.py` | `chat_with_tools()` 保持返回 dict，不参与迁移 |
| `src/llm/failover.py` | 同上 |
| `src/tools/**` | 工具只被 `tool_executor.execute()` 调用，不感知 agent 事件流 |
| `src/channels/**` | 渠道适配器接收 `UnifiedResponse`，不消费 agent 事件流 |
| `src/scheduler/executor.py` | 使用 `process_message_sync()`，内部适配后无需改动 |
| `src/knowledge/**` | 知识库被工具调用，不感知 agent 事件流 |
| `src/skills/**` | 技能被 agent 调用，不感知 agent 事件流 |
| `src/db/**` | 数据库层不参与 |
| `frontend/src/components/ui/**` | 基础组件不参与 |
| `frontend/src/variants/**` | 样式变体不参与 |
| `deploy/**` | 无数据库 schema 变更 |

---

## 六、迁移步骤与验证

### Phase 1：后端核心迁移（3 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 1.1 | 创建 `src/core/agent_events.py`（make_event 函数） | 单元测试 |
| 1.2 | 修改 `process_message` 签名，删除 `progress_callback` 参数 | 编译通过 |
| 1.3 | 删除 `process_message` 中 5 个 callback 闭包（:1368-1403） | — |
| 1.4 | 删除 `execute_as_subagent` 中 4 个 callback 闭包（:2432-2458） | — |
| 1.5 | 全文替换 callback 调用为 yield（约 30 处） | — |
| 1.6 | 修改所有 9 处 yield 文本为 `yield make_event("response", data=...)` | — |
| 1.7 | 重构 `_delegate_to_subagent_direct`，删除 `subagent_progress_wrapper`（:1222-1259） | 子智能体委派兼容 |
| 1.8 | 修改 `process_message_sync()` 适配新格式 | 渠道回调兼容 |
| 1.9 | 修改 `execute_as_subagent()` 适配新格式（方案 A：内部收集事件） | 子智能体运行正常 |
| 1.10 | 适配 3 个测试文件（`test_agent_llm.py` 等） | 测试编译通过 |

### Phase 2：SSE 端点重构（1.5 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 2.1 | 重写 `chat_stream` 端点，去掉 ThreadPoolExecutor | SSE 连接正常 |
| 2.2 | 保留 `record_service` 处理逻辑 | token 记录正常 |
| 2.3 | 保留 `sse_manager` 取消检查 | 取消功能正常 |
| 2.4 | 保留数据库保存逻辑（移到 finally 块） | 会话记录正常 |

### Phase 3：其他消费者适配（1 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 3.1 | 修改 `gradio_app.py` 适配新格式（删除 progress_callback lambda） | Gradio UI 正常 |
| 3.2 | 修改 CLI 模式适配新格式 | CLI 输出正常 |
| 3.3 | 验证渠道路由（企业微信/钉钉/飞书）兼容性 | 渠道消息正常 |
| 3.4 | 验证定时任务执行器（`scheduler/executor.py`）兼容性 | 定时任务正常 |

### Phase 4：前端体验升级（2 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 4.1 | 扩展 `MessageStreamEvent` 类型 | TypeScript 编译通过 |
| 4.2 | 扩展 `SSEManager.parseSSELine()` | 新事件类型解析正确 |
| 4.3 | 扩展 `useAgent.ts` 回调处理 | console.log 显示完整工作过程 |
| 4.4 | 升级 `MessageItem.vue` 工作过程可视化 | 正常模式下展示工具调用步骤 |
| 4.5 | 运行 `npm run build` | 构建通过 |

### Phase 5：回归验证（0.5 天）

| 场景 | 验证点 |
|------|--------|
| Web SSE 对话 | 消息正常流式显示、工具调用进度显示、完成事件正确 |
| 工具调用 | 各类工具（搜索、邮件、知识库）调用和结果展示正确 |
| 子智能体委派 | 委派和结果回传正常 |
| 用户取消 | 取消请求后事件流正确终止 |
| 非流式 API | `/api/chat` 返回正确 JSON |
| 企业微信渠道 | 消息发送和文件下载正常 |
| 钉钉/飞书渠道 | 消息发送正常 |
| 微信客服渠道 | 消息发送和文件下载正常 |
| 定时任务 | 定时执行和试运行正常 |
| Gradio UI | 流式对话和进度显示正常 |
| CLI 模式 | 输出正常 |
| 单元/集成测试 | `test_agent_llm.py` 等测试通过 |

---

## 七、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| SSE 事件时序变化导致前端渲染异常 | 低 | 高 | 前端事件格式 100% 兼容，仅新增类型 |
| 去掉线程后 LLM 长时间调用阻塞 FastAPI event loop | 中 | 高 | FastAPI 的 `StreamingResponse` 是 async generator，`await self.llm.chat_with_tools()` 会正确释放 event loop；如果仍有问题，可以用 `asyncio.to_thread()` 包装单个 LLM 调用（而非整个 agent 循环） |
| 渠道路由兼容性问题 | 低 | 中 | `process_message_sync()` 的适配保证渠道只收到文本和文件事件 |
| Gunicorn 多 worker 下的事件流行为 | 低 | 中 | 每个请求独立 async generator，无共享状态 |

### 关于 LLM 调用阻塞问题

**关键分析**：当前架构用线程是为了"不阻塞 FastAPI event loop"。但 `await self.llm.chat_with_tools()` 本身就是 async 的——它内部用的是 `aiohttp` 或 `httpx` 做 HTTP 请求，await 时会释放 event loop。所以**不需要线程**。

如果某些极端情况下 LLM 调用确实是同步阻塞的（如某个 provider 的 SDK 使用同步 HTTP），可以在该调用点单独用 `asyncio.to_thread()` 包装，而不是把整个 agent 循环放到线程里。

---

## 八、总工时评估

| 阶段 | 工时 | 说明 |
|------|------|------|
| Phase 1: 后端核心 | 3 天 | agent.py 约 30 处替换 + 9 处 yield 改造 + sync/subagent 适配 + _delegate_to_subagent_direct 重构 |
| Phase 2: SSE 端点 | 1.5 天 | main.py 重构，删 200+ 行写 80 行 |
| Phase 3: 其他消费者 | 1 天 | Gradio、CLI、渠道、定时任务验证 + 测试文件适配 |
| Phase 4: 前端升级 | 2 天 | 类型扩展 + 可视化升级 |
| Phase 5: 回归验证 | 0.5 天 | 全场景测试 |
| **合计** | **8 天** | |

---

## 九、迁移完成后的收益

1. **可观测性零障碍**：Trace 上下文自动传播，span 嵌套自然表达，无需任何补丁
2. **代码量减少**：`main.py` SSE 端点从 ~250 行减少到 ~80 行，`agent.py` 删除 callback 闭包相关代码
3. **前端体验提升**：正常模式下可视化 Agent 工作过程，不再需要 debug 模式
4. **延迟降低**：事件零延迟（消除 50ms 轮询）
5. **后续功能简化**：三层压缩、审计、Hook 等功能实现都因统一事件流而更简单

---

## 十、审查发现与修正记录

> 审查日期：2026-06-01
> 基于实际代码对照设计文档的全面审查

### 10.1 遗漏的消费者（已补充到 §2.4）

| 消费者 | 文件 | 说明 |
|--------|------|------|
| 定时任务执行 | `src/scheduler/executor.py:133` | 调用 `process_message_sync()`，无 progress_callback |
| 定时任务试运行 | `src/scheduler/executor.py:238` | 同上 |
| 测试文件（3个） | `tests/test_agent_llm.py:65` 等 | 直接消费 `process_message()` 的 yield 输出 |

### 10.2 与实际代码不符的描述（已修正）

| 原描述 | 实际代码 | 修正 |
|--------|---------|------|
| 非流式 API 在 `main.py:661` | 实际在 `main.py:733` | §2.4 已更正行号 |
| yield 约 6 处 | 实际有 9 处（:1455, :1459, :1474, :1478, :1841, :2157, :2170, :2249, :2328） | §4.2 改动 4 已补充完整列表 |
| SubagentExecutor 调用 `process_message()` | 实际调用 `execute_as_subagent()`（`executor.py:319`） | §4.6 已修正描述 |
| 删除 5 个 callback 闭包 | `process_message` 有 5 个，`execute_as_subagent` 还有 4 个独立的闭包 | §4.2 改动 2 已补充 |
| 渠道路由行号 | 钉钉/飞书没有独立路由，都走 `_process_tenant_message` | §2.4 和 §4.5 已修正 |
| `process_message_sync` 不传 cancel_check | 确认：内部调用 `process_message` 时不传 `cancel_check` | §4.2 改动 8 保持一致 |

### 10.3 新增的设计风险

#### 风险 1：`subagent_progress_wrapper` 扁平化逻辑丢失

`_delegate_to_subagent_direct`（:1222-1259）中的 `subagent_progress_wrapper` 将子智能体的结构化 dict 事件扁平化为字符串。迁移后这个逻辑会消失。

**影响**：如果外层消费者依赖扁平化的字符串格式，可能出问题。但迁移后所有消费者都处理结构化 dict，所以影响为低。

#### 风险 2：子智能体事件透传深度

设计文档 §4.2 改动 7 提出 yield `subagent_start/end` 事件，但当前 `delegate_tool.execute()` 是一个阻塞 await 调用。如果要实现子智能体执行期间的事件透传（tool_start、thinking 等），需要修改整条调用链：
- `SubagentTool.execute()` → `SubagentExecutor.delegate()` → `Agent.execute_as_subagent()` → `Agent.process_message()`

**建议**：Phase 1 只在外层 yield `subagent_start/end`，不透传子智能体内部事件。后续作为独立优化实现。

#### 风险 3：`record_service` 的集成点变化

当前 `record_service.handle_progress_event(event)` 在 `sync_progress_callback`（main.py:1317）中调用，即在每个 progress 事件产生时立即处理。迁移后，这个调用移到 `event_generator` 的 `async for` 循环内，时序基本不变，但需要确认 `record_service` 是否依赖线程上下文（当前它在主线程 event loop 中被调用，迁移后仍然在主线程 event loop 中，所以无影响）。

#### 风险 4：非流式 `/api/chat` 端点的 `run_in_executor`

当前非流式端点（main.py:733）使用 `loop.run_in_executor(None, lambda: asyncio.run(agent.process_message_sync(...)))`。迁移后 `process_message_sync` 内部迭代 `process_message()`（AsyncGenerator），`asyncio.run()` 会创建新 event loop 并在其中运行。这种方式仍然有效，但可以考虑简化为直接 `await agent.process_message_sync(...)`（因为 `process_message` 现在是纯 async 的，不再有线程问题）。

### 10.4 建议补充的改动项

| 项目 | 说明 | 是否阻塞迁移 |
|------|------|-------------|
| `src/scheduler/executor.py` 适配验证 | 虽然无需改动，但应纳入回归验证 | 否 |
| 3 个测试文件适配 | `test_agent_llm.py` 等 | 是（编译错误） |
| `_delegate_to_subagent_direct` 重构 | 删除 `subagent_progress_wrapper` | 是 |
| 非 `/api/chat` 端点简化 | 从 `run_in_executor` 改为直接 `await` | 否（优化项） |
