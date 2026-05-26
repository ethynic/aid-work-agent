# 对话体验优化设计 — 执行详情参数化 + 正在输入提示

> 日期：2026-05-26
> 状态：设计中

## 背景

当前 AI 回复区域有两部分内容：
1. **执行详情面板**（展开/收起）— 显示工具调用、思考过程等
2. **"生成中..." 脉冲文字** — AI 回复生成时的提示

存在以下体验问题：

1. **执行详情面板始终显示**，即使普通用户不关心技术细节，也会看到工具调用记录，界面显得嘈杂
2. **等待反馈不够明确**，用户发送消息后，在 AI 处理但尚未返回任何内容时，只有弹跳圆点（在 MessageList 中），一旦助手消息占位创建后只剩小字"生成中..."，用户感知不到系统在工作

## 需求

1. **执行详情面板参数化控制**：通过 URL 参数决定是否显示执行详情面板，默认不显示
2. **执行过程输出到前端 console**：所有阶段信息完整打印到浏览器控制台，方便调试
3. **"正在输入"提示**：参考飞书/钉钉的"对方正在输入..."风格，在 AI 处理期间显示明确的提示，结果出来后自动消失

## 方案设计

### 一、执行详情面板参数化

#### 控制方式

通过 URL query 参数 `debug` 控制：

| URL | 行为 |
|-----|------|
| `http://host/chat` | 不显示执行详情面板 |
| `http://host/chat?debug=1` | 显示执行详情面板（展开/收起） |
| `http://host/t/xxx` | 不显示执行详情面板 |
| `http://host/t/xxx?debug=1` | 显示执行详情面板 |

#### 实现思路

1. **新增 composable** `useDebugMode()`：
   - 读取 `route.query.debug`
   - 返回 `isDebugEnabled: ComputedRef<boolean>`
   - 全局单例，避免重复解析

2. **MessageItem.vue** 改动：
   - 引入 `useDebugMode()`
   - 执行详情区块（当前第 67-103 行）外层增加 `v-if="isDebugEnabled"` 守卫
   - `isDebugEnabled` 为 false 时，整个执行详情区块不渲染

3. **进度数据仍然正常收集**：`useAgent.ts` 中的 `addProgress()` 逻辑不变，数据照常写入 `message.progressMessages`，只是 UI 层不渲染

#### 判断参数

选择 URL query 而非其他方式的理由：

| 方案 | 优点 | 缺点 |
|------|------|------|
| URL query `?debug=1` | 无状态、可分享链接、无需后端配合 | 切换需刷新页面 |
| localStorage 配置项 | 持久化、无需刷新 | 隐藏入口、普通用户可能误开 |
| 用户角色/权限 | 自动化 | 增加后端复杂度，不适合调试场景 |

**URL query 最适合调试场景**：开发者需要时加上参数即可，普通用户不会看到。

### 二、执行过程输出到前端 Console

#### 实现思路

在 `useAgent.ts` 的 `addProgress()` 函数中增加 `console.log` 输出：

```typescript
function addProgress(
  content: string,
  type: ProgressMessage['type'],
  toolName?: string,
  toolArgs?: object,
  result?: any
) {
  const newMsg: ProgressMessage = { type, content, timestamp: Date.now(), toolName, toolArgs, result }
  progressMessages.value.push(newMsg)

  // 输出到前端 console，方便调试
  const label = `[Agent ${type}]`
  const timestamp = new Date().toLocaleTimeString()

  if (type === 'error') {
    console.error(`${timestamp} ${label}`, content, { toolName, toolArgs, result })
  } else if (type === 'tool_result' || type === 'tool_start') {
    console.info(`${timestamp} ${label}`, content, { toolName, toolArgs, result })
  } else {
    console.log(`${timestamp} ${label}`, content)
  }

  // 同时更新 AI 消息占位中的 progressMessages
  const lastMsg = messages.value[messages.value.length - 1]
  if (lastMsg && lastMsg.role === 'assistant' && lastMsg.progressMessages) {
    lastMsg.progressMessages.push(newMsg)
  }
}
```

#### Console 输出格式

```
14:30:15 [Agent progress] 🚀 正在发送请求...
14:30:16 [Agent thinking] 🤔 正在分析问题... {toolName: undefined, ...}
14:30:17 [Agent tool_start] 🔧 需要调用工具【网络搜索】 {toolName: "web_search", toolArgs: {keyword: "..."}, result: undefined}
14:30:18 [Agent tool_result] ✅ 网络搜索完成 {toolName: "web_search", toolArgs: undefined, result: {...}}
14:30:19 [Agent complete] ✅ 任务完成
```

### 三、"正在输入"提示优化

#### 当前行为分析

| 阶段 | 当前提示 | 位置 |
|------|---------|------|
| 消息发送后，助手占位未创建 | 三个弹跳圆点 | MessageList（消息之间） |
| 助手占位已创建，内容为空 | "生成中..." 小字 | MessageItem（气泡内） |
| 助手占位已创建，内容不为空 | 无 | — |

**问题**：助手占位创建非常快（几乎立即），弹跳圆点很快消失。之后在 AI 思考/工具调用期间（可能持续数秒到十几秒），只有小字"生成中..."，用户感知弱。

#### 设计方案

参考飞书/钉钉的"对方正在输入中..."风格，将提示显示在 **AI 回复白框内部**：

```
┌─────────────────────────────────────┐
│  [用户消息气泡]                      │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ ● 对方正在输入中...            │  │  ← AI 白框内，无内容时显示
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │  [输入框]                [发送]│  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

AI 返回内容后，提示自然被实际内容替换。

#### 具体行为

| 状态 | 提示内容 | 说明 |
|------|---------|------|
| AI 正在思考/处理 | `对方正在输入中...` | thinking / working 阶段 |
| AI 返回内容后 | 提示消失，显示实际内容 | onResponse 首次触发后 |
| AI 完成/出错 | 提示消失 | complete / error 事件 |

#### 显示位置

放在 **MessageItem.vue** 的 AI 白框（`.message-ai-content`）内部。

当 `showInputHint` 为 true（assistant + isProcessing + content 为空 + inputHintState 非 idle）时，白框内显示提示文字；内容到达后，提示被 `v-else` 切换为实际内容。

#### 状态追踪

`inputHintState`（在 `useAgent.ts` 中）：

```typescript
type InputHintState = 'idle' | 'thinking' | 'working' | 'responding'
```

状态转换逻辑：

```
sendMessage() 调用 → thinking
onThinking 事件 → thinking（保持）
onToolStart 事件 → working
onToolResult 事件 → working（保持）
onResponse 首次触发 → responding（隐藏提示）
onComplete/onError → idle
```

#### 样式

```html
<div v-if="showInputHint" class="flex items-center gap-2">
  <span class="inline-block w-1.5 h-1.5 rounded-full bg-primary-400 animate-pulse"></span>
  <span class="text-xs text-muted">对方正在输入中...</span>
</div>
```

- 左侧呼吸动画小圆点（`animate-pulse`）
- 文字较小（`text-xs`），颜色淡灰（`text-muted`）

#### 与现有"生成中..."的关系

- **移除** MessageItem.vue 中的"生成中..."文字（被白框内"对方正在输入中..."替代）
- **保留** MessageList.vue 中的弹跳圆点（用于助手占位尚未创建的极短窗口）

### 四、MessageItem.vue 中"生成中..."的清理

当前 MessageItem.vue 第 105-109 行：

```html
<div v-if="isProcessing" class="flex items-center gap-2 mt-2">
  <span class="text-xs text-primary-400 animate-pulse">生成中...</span>
</div>
```

此段**移除**，由 MessageList 底部的"正在输入"提示条替代。

## 涉及文件

| 文件 | 改动 |
|------|------|
| `frontend/src/composables/useAgent.ts` | ① `addProgress()` 增加 console 输出 ② 新增 `inputHintState` 状态及状态转换逻辑 ③ 导出 `inputHintState` |
| `frontend/src/components/MessageItem.vue` | ① 执行详情区块增加 `v-if="isDebugEnabled"` 守卫 ② 移除"生成中..."提示 ③ AI 白框内新增"对方正在输入中..."提示 |
| `frontend/src/components/MessageList.vue` | 传递 `inputHintState` 给 MessageItem |
| `frontend/src/components/ChatContainer.vue` | 传递 `inputHintState` 给 MessageList |
| `frontend/src/composables/useDebugMode.ts` | **新建**，解析 URL query 参数 |
| `frontend/src/types/index.ts` | 新增 `InputHintState` 类型定义 |

## 不涉及的文件

- 后端无任何改动
- 数据库无改动
- `useAgent.ts` 中 `addProgress()` 的数据收集逻辑不变，只是增加 console 输出

## 风险评估

| 风险 | 等级 | 缓解 |
|------|------|------|
| console 输出在生产环境暴露敏感信息 | 低 | toolArgs 和 result 中不含密码/token 等敏感信息，且 console 仅本地可见 |
| URL debug 参数被普通用户误用 | 无 | 只是多显示执行详情面板，不影响功能 |
| inputHintState 状态转换边界情况 | 低 | 最终都会被 onComplete/onError 重置为 idle |
