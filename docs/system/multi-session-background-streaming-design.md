# 多会话后台流式设计

> 关联开发计划：[plan-multi-session-background-streaming.md](../plans/plan-multi-session-background-streaming.md)
> 登记：[ideas.md](../../ideas.md) 前端分区 #43

## 背景与问题

旧聊天前端（`frontend/src/composables/useAgent.ts`）全局只有**一份**流式状态（`messages` / `isProcessing` / `sseManager`）和**一条** SSE 连接，所有会话共享。因此：

- 切换到历史会话 / 新建会话 / 切换数字员工时，必须弹 confirm 让用户「终止当前会话」（`abortStreaming()`），否则旧会话的流数据会串到新会话消息列表
- 正在进行的 Agent 任务被强制中断，切回后看不到已生成内容

## 目标

1. 切换历史会话、新建会话、切换数字员工均**不再弹中断确认**，旧会话在后台继续流式生成
2. 切回正在进行的会话时，显示其实时进度与已生成内容
3. 会话列表 UI：进行中的会话标题旁显示**旋转 loading**；后台完成且未查看的会话显示**完成小点**，点开后消失

## 方案：per-session 流式状态池

把全局单份流式状态重构为按 session_id 隔离的状态池。每个会话拥有独立的 `SSEManager` 连接与消息缓冲，切换会话只是改变「当前查看的 sessionId」，后台会话的连接保持存活继续收流。

### 核心结构（useAgent.ts）

```ts
interface SessionStreamState {
  sseManager: SSEManager                    // 每会话独立连接
  messages: Ref<ChatMessage[]>              // 该会话的实时消息（含流式累积）
  progressMessages: Ref<ProgressMessage[]>
  isProcessing: Ref<boolean>
  currentResponse: Ref<string>
  inputHintState: Ref<InputHintState>
  hasUnreadCompletion: Ref<boolean>         // 后台完成未查看 → 完成小点
  dbLoaded: boolean                         // 是否已从 DB 加载过历史，避免重复请求
  cancelledByUser: boolean
}
const sessionStreams = shallowReactive(new Map<string, SessionStreamState>())
const sessionId = ref<string>(...)          // 当前查看的会话
```

**关键技术点：必须用 `shallowReactive` 而非 `reactive`**。`reactive` 会深层包装状态对象并在属性访问时 unwrap 内部 ref，导致 `state.messages.value` 失效；`shallowReactive` 只追踪 Map 的增删（供会话列表指示器响应式），值保持原样，内部 ref 的响应性不受影响。

### 视图代理（消费组件零改动）

导出的 `messages` / `progressMessages` / `isProcessing` / `currentResponse` / `inputHintState` 改为指向「当前查看会话状态」的 computed 代理：

```ts
const messages = computed({
  get: () => getStreamState(sessionId.value).messages.value,
  set: (v) => { getStreamState(sessionId.value).messages.value = v }
})
```

`ChatContainer.vue` / `MessageList.vue` / `ChatInput.vue` 等消费方无需感知状态池。

### 关键行为

| 操作 | 行为 |
|------|------|
| `sendMessage(content, subagent, overrideSessionId, instanceId)` | 目标会话独立 guard（仅该会话 `isProcessing` 才拒绝）；所有 SSE 回调写入该会话自己的 state；流结束（完成/出错）时若该会话非当前查看 → 置 `hasUnreadCompletion` |
| `switchSession(sid)` | 不中断任何连接；`sessionId.value = sid`；清除该会话完成小点；内存已有状态直接复用（computed 代理自动显示实时内容），否则从 DB 加载历史写入该会话 state |
| `abortStreaming(sid?)` | 只中断指定会话（默认当前查看会话），后端 cancel 用该会话 sid |
| 删除会话 | `removeStreamState(sid)` 断开连接并从状态池移除 |
| 登出/重新登录 | `clearSessionCache()`（无参）断开全部连接并清空状态池 |

**删除 `onUnmounted(() => sseManager.disconnect())`**：旧代码在组件卸载时断开全局连接，这是后台流式被 SPA 导航杀死的关键。改造后流在页面存活期内持续，离开聊天页去业务页也不中断。

### DB 加载竞态防护

`switchSession` 中 `await getSessionMessages()` 期间，用户可能已在该会话发出消息（流式已开始）。DB 返回后若直接覆盖 `messages` 会丢失实时消息，因此覆盖前检查 `state.isProcessing.value || state.messages.value.length > 0`，是则仅标记 `dbLoaded` 不覆盖。

## UI 指示器（MenuSidebar.vue 会话列表项）

- `isSessionRunning(sid)` → 标题右侧旋转 spinner（`animate-spin`，primary 色）
- 否则 `hasSessionUnreadCompletion(sid)` → 实心小圆点（`bg-success-500`）
- 两函数读取 `sessionStreams` 中 ref 的 `.value`，模板渲染时自动追踪，无需额外事件

同时删除三处中断确认：`handleSelectSession`、`handleNewSession`（MenuSidebar）、`handleSubagentChange`（ChatContainer）。

## 后端兼容性

无需改动。`/api/chat/stream`（`src/main.py`）按 session_id 独立管理状态（`sse_manager.get_or_create_session`），内部锁仅保护 map 读写，多会话并发流式天然支持；LLM 并发由 `KeyPool` 信号量控制。

## 行为边界

- **页面刷新/关闭浏览器**：SSE 连接随页面断开（HTTP 连接级限制），后端已落库的消息不丢，未落库的尾部增量丢失——与旧行为一致，不劣化
- **同一会话重复发消息**：仍被该会话 `isProcessing` 阻塞（输入框禁用逻辑不变）
- **后台并发数**：不设上限（正常用户同时进行 ≤ 2-3 个会话）

## 测试

`frontend/src/__tests__/composables/useAgentMultiSession.test.ts`（7 用例）：

1. 后台会话 SSE 事件不串到当前查看会话
2. 切回正在进行的会话显示实时累积内容
3. 后台完成 → 完成小点；查看后消失
4. 当前查看的会话完成时不标记小点
5. 某会话进行中不阻塞其他会话发消息；同会话重复发送被拒绝
6. `abortStreaming` 只停当前查看会话，后端 cancel 用对应 sid
7. 连续切换会话不中断进行中的流
