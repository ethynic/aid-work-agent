# Agent 生成中断功能设计

> 版本: v1.0 | 创建: 2026-05-22 | 状态: 待审核

## 1. 问题分析

### 1.1 现状

当 Agent 正在处理消息时（`isProcessing = true`），用户无法发送新消息：

- **发送按钮被禁用**（`ChatInput.vue:83` — `canSend` 在 `isProcessing` 时返回 `false`）
- **输入框无阻止性禁用**，但 Enter 键触发 `handleSend()` 时因 `canSend` 为 false 直接 return
- **没有"停止"按钮**：虽然后端有完整的取消机制（Redis 标记 + SSE 中断检查），但前端只在用户切换会话/切换数字员工时才会触发 `abortStreaming()`

### 1.2 已有的取消基础设施（无需新建）

经代码调查，**取消机制的底层实现已经完整**，缺的只是 UI 入口：

| 层级 | 组件 | 实现位置 | 状态 |
|------|------|----------|------|
| 前端 SSE | `SSEManager.disconnect()` | `frontend/src/api/agent.ts:271` | ✅ 已实现 |
| 前端逻辑 | `abortStreaming()` | `frontend/src/composables/useAgent.ts:386-407` | ✅ 已实现 |
| 后端 API | `POST /api/chat/{session_id}/cancel` | `src/main.py:1463-1472` | ✅ 已实现 |
| 后端取消状态 | `SSEConnectionManager.cancel_session/is_cancelled/clear_cancelled` | `src/main.py:139-157` | ✅ 已实现（Redis 存储） |
| 后端检查点 | Agent 线程在 4 个位置检查取消状态 | `src/main.py:1150/1164/1204/1226` | ✅ 已实现 |
| 后端异常处理 | `CancelledError` 捕获 + 优雅退出 | `src/main.py:1287-1292` | ✅ 已实现 |

**结论：只需前端 UI 改动，后端零改动。**

### 1.3 用户期望行为

用户在 Agent 处理期间应该能够：

1. **点击"停止"按钮** — 立即中断当前生成
2. **中断后可以立即发送新消息** — 不需要等待超时
3. **已生成的内容保留** — 中断前 Agent 已产出的部分文本应保留在聊天记录中
4. **清晰的状态反馈** — 停止操作有视觉反馈

---

## 2. 方案设计

### 2.1 核心改动：ChatInput 组件增加停止按钮

**改动文件**: `frontend/src/components/ChatInput.vue`

**方案**: 将发送按钮在 `isProcessing` 时变为停止按钮。

```
┌──────────────────────────────────────────────┐
│  正常状态:                                    │
│  ┌──────────┐ ┌──────────────────┐ ┌──────┐  │
│  │ 📎 附件  │ │ 输入消息...       │ │ 发送 │  │
│  └──────────┘ └──────────────────┘ └──────┘  │
│                                              │
│  处理中状态:                                  │
│  ┌──────────┐ ┌──────────────────┐ ┌──────┐  │
│  │ 📎 附件  │ │ 输入消息...  ⏳   │ │ 停止 │  │
│  └──────────┘ └──────────────────┘ └──────┘  │
└──────────────────────────────────────────────┘
```

**具体实现**:

1. 新增 `emit('stop')` 事件
2. 发送按钮在 `isProcessing` 时：
   - 外观变为红色调停止按钮（方块图标 + "停止"文字）
   - 点击触发 `emit('stop')`
   - 不再 disabled
3. 输入框在 `isProcessing` 时仍可输入（让用户提前准备下一条消息）

### 2.2 ChatContainer 接收 stop 事件

**改动文件**: `frontend/src/components/ChatContainer.vue`

1. `ChatInput` 的 `@stop` 事件绑定到 `abortStreaming()`
2. `abortStreaming()` 已经从 `useAgent` 中导入

### 2.3 中断后的消息处理

**无需额外处理**。现有 `abortStreaming()` 逻辑已经：
- 调用 `sseManager.disconnect()` 断开 SSE → 触发 `onComplete()` 回调
- 通知后端 `/api/chat/{sessionId}/cancel` → Agent 线程抛出 `CancelledError` → 停止生成
- 设置 `isProcessing = false` → 恢复发送能力

**已生成的部分内容**：在 `abortStreaming()` 触发前，前端已通过 `onResponse` 回调持续更新 `messages[assistantIndex].content`，所以中断时已收到的文本自然保留在消息列表中。

### 2.4 中断后的消息标记（增强体验，可选）

当前中断后，部分回复会保留在聊天记录中，但没有视觉标记表明这是一条被中断的回复。可以增加一个可选的标记：

- 在被中断的 assistant 消息末尾追加一个灰色提示 `⚠️ 生成已中断`
- 这个标记存在 `metadata` 中，前端渲染时判断显示

**这个增强目前不建议实现**，因为：
1. 后端 `CancelledError` 被捕获后不会保存部分消息到 `chat_messages` 表（`main.py:1287-1292` 只记录错误，不保存消息）
2. 前端内存中的部分消息在刷新页面后会丢失
3. 要实现完整的中断消息持久化需要改后端逻辑，投入产出比不高

**优先级**: 可选增强，Phase 2 考虑。

---

## 3. 详细实现方案

### 3.1 ChatInput.vue 改动

**新增 emit**:

```typescript
const emit = defineEmits<{
  (e: 'send', content: string): void
  (e: 'upload', file: File): void
  (e: 'remove', file_id: string): void
  (e: 'stop'): void  // 新增
}>()
```

**模板改动 — 发送/停止按钮**:

```html
<!-- Stop Button (isProcessing 时显示) -->
<button
  v-if="isProcessing"
  @click="emit('stop')"
  class="h-[46px] min-h-[44px] min-w-[44px] px-4 sm:px-5 rounded-xl font-medium transition-all flex items-center justify-center gap-2 bg-red-50 text-red-600 border border-red-200 hover:bg-red-100 hover:border-red-300"
>
  <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </svg>
  <span class="hidden sm:inline">停止</span>
</button>

<!-- Send Button (非 isProcessing 时显示) -->
<button
  v-else
  @click="handleSend"
  :disabled="!inputText.trim() && files.length === 0"
  :class="[
    'h-[46px] min-h-[44px] min-w-[44px] px-4 sm:px-5 rounded-xl font-medium transition-all flex items-center justify-center gap-2',
    inputText.trim() || files.length > 0
      ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white hover:from-primary-400 hover:to-primary-600 shadow-lg shadow-primary-500/25'
      : 'bg-gray-200 text-gray-400 cursor-not-allowed'
  ]"
>
  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
  </svg>
  <span class="hidden sm:inline">发送</span>
</button>
```

**关键变化**:
- `isProcessing` 时：显示停止按钮（始终可点击，不 disabled）
- 非 `isProcessing` 时：显示发送按钮（逻辑不变）
- 移除了发送按钮的 `disabled` 中对 `isProcessing` 的依赖（因为 `isProcessing` 时根本不渲染发送按钮）
- `canSend` computed 不再需要 `isProcessing` 检查

**输入框行为调整**:

输入框在 `isProcessing` 时**不禁用输入**（只禁用发送），用户可以提前打字。但不允许 Enter 发送（因为发送按钮已隐藏）。

```typescript
function handleEnter(e: KeyboardEvent) {
  if (isMobile.value) {
    setTimeout(autoResize, 0)
    return
  }
  // 处理中不允许 Enter 发送
  if (props.isProcessing) {
    e.preventDefault()
    return
  }
  e.preventDefault()
  handleSend()
}
```

### 3.2 ChatContainer.vue 改动

```html
<ChatInput
  :disabled="!isLoggedIn"
  :isProcessing="isProcessing"
  :files="uploadedFiles"
  @send="handleSend"
  @upload="handleUpload"
  @remove="removeFile"
  @stop="abortStreaming"
/>
```

仅需添加 `@stop="abortStreaming"`，`abortStreaming` 已从 `useAgent` 导入。

---

## 4. 用户体验流程

### 4.1 正常流程（无中断）

```
用户输入 → 点击发送 → 发送按钮变为停止按钮 + 输入框显示"处理中"
→ Agent 生成完成 → 停止按钮恢复为发送按钮
```

### 4.2 中断流程

```
用户输入 → 点击发送 → 发送按钮变为停止按钮
→ 用户点击"停止" → 调用 abortStreaming()
  → 前端: 断开 SSE 连接 + 通知后端取消
  → 后端: Agent 线程检查到取消标记 → 抛出 CancelledError → 停止生成
  → 前端: isProcessing = false → 停止按钮恢复为发送按钮
  → 已生成的部分文本保留在聊天窗口中
→ 用户可以立即输入并发送新消息
```

### 4.3 预输入流程

```
用户在 Agent 处理期间输入新消息（输入框可用）
→ Agent 完成或被停止后 → 发送按钮恢复
→ 用户点击发送或按 Enter → 新消息正常发送
```

---

## 5. 涉及文件

| 文件 | 改动类型 | 改动范围 |
|------|----------|----------|
| `frontend/src/components/ChatInput.vue` | 修改 | 按钮 UI + emit + Enter 键处理 |
| `frontend/src/components/ChatContainer.vue` | 修改 | 添加 `@stop` 事件绑定 |
| `frontend/src/composables/useAgent.ts` | 修改 | 区分取消和完成的状态显示 |
| `src/main.py` | 修改 | 新 SSE 请求清除残留标记 + 传入 cancel_check |
| `src/core/agent.py` | 修改 | Agent 循环中加入 cancel_check 检查 |

### 5.1 Bug 修复：取消标记竞态条件

**问题**：用户停止当前请求后立即发送新请求时，新请求会立即被取消并返回 "Cancelled by user"。

**根因**：
```
T1: 用户点停止 → abortStreaming() → POST /cancel → Redis 设置取消标记
T2: 用户发新消息 → POST /chat/stream → 新 Agent 线程启动
T3: 新线程检查 is_cancelled() → True（旧标记还在）→ 立即退出 "Cancelled by user"
T4: 旧线程 finally → clear_cancelled()（太晚了，新请求已死）
```

**修复**：在 `event_generator()` 开始时主动清除残留的取消标记（`src/main.py:1119`），确保新请求不受旧取消标记影响。

旧线程的 `finally` 中 `clear_cancelled()` 保留不变，双重清除无害。

### 5.2 Bug 修复：取消后后端仍保存消息

**问题**：用户点停止后刷新页面，发现被取消的对话仍然有完整回复。

**根因**：消息保存逻辑在 Agent 线程中执行。Agent 跑完后立即保存消息到 DB，此时 `POST /cancel` 可能还没到达后端。即使 SSE 连接已断开，Agent 线程仍继续执行保存。

**修复**：将消息保存从 Agent 线程移到 SSE generator 中 `yield complete` 之后。核心原理：
- `yield complete` 成功 → 客户端在线 → 继续执行保存
- `yield complete` 抛 `BrokenPipeError` → 客户端已断开 → `return` 退出 generator → 不保存

Agent 线程只把数据放入 `results['pending_save']`，不做 DB 写入。SSE generator 在确认客户端收到 complete 事件后才执行 DB 保存。

### 5.3 Bug 修复：取消显示为"任务完成"

**问题**：用户主动停止时，执行详情显示 `✅ 任务完成` 而非取消状态。

**根因**：`SSEManager.disconnect()` 触发 `AbortError`，被 `catch` 后调用 `onComplete()`（而非 `onError()`），`onComplete` 无条件显示"任务完成"。

**修复**：`useAgent.ts` 新增 `_cancelledByUser` 标记。`abortStreaming()` 在 `disconnect()` 前设置标记，`onComplete` 检查后显示 `⚠️ 已停止`。

---

## 6. 测试验证

### 手动测试场景

1. **停止按钮显示**：发送消息后确认发送按钮变为停止按钮（红色、方块图标）
2. **中断功能**：Agent 处理期间点击停止，确认：
   - Agent 立即停止生成
   - 已生成的部分文本保留
   - 停止按钮恢复为发送按钮
   - 可以立即发送新消息
3. **预输入**：Agent 处理期间在输入框输入文字，停止后按 Enter 发送
4. **快速操作**：发送 → 立即停止 → 立即再发送 → 确认无异常
5. **移动端**：确认移动端 UI 也正确显示停止按钮

---

## 7. 风险与注意事项

1. **中断时机不可控**：后端只在 4 个检查点检测取消（progress 回调、chunk 消费），如果 Agent 正在执行一个耗时的工具调用（如浏览器操作），取消会在该工具调用完成后的下一个检查点生效。这是可接受的延迟。

2. **并发安全**：`abortStreaming()` 是前端单线程操作，不存在并发问题。后端 Redis 取消标记有 300s TTL 自动过期。

3. **部分消息不持久化**：中断后前端显示的部分回复不会被保存到数据库，刷新页面后消失。这是当前设计有意为之（`CancelledError` 分支不保存消息）。如果需要持久化中断时的部分回复，需要在后端 `main.py:1287-1292` 中增加保存逻辑。

4. **Enter 键行为**：处理中按 Enter 不再发送消息，但不阻止用户在输入框中换行（Shift+Enter 仍然可用）。移动端 Enter 仍然是换行。
