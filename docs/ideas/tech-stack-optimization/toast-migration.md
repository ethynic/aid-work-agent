# vue-toastification → Element Plus Alert / Message

> **状态**：💡 灵感 | **关联**：[后台管理引入 Element Plus](admin-element-plus-migration.md)

---

## 1. 问题分析

### 1.1 当前现状

项目使用 `vue-toastification@next`（rc 版，尚未发布正式版）作为全局消息提示组件。

#### 使用规模统计

| 维度 | 数据 |
|------|------|
| 使用文件数 | **21 个**源文件 |
| toast 调用总数 | **约 182 处** |
| `toast.success` | 76 处 |
| `toast.error` | 97 处 |
| `toast.info` | 7 处 |
| `toast.warning` | 2 处 |
| 全局注册 | `main.ts`（插件 + CSS） |
| 测试 mock | 1 处（`useRpaPauseResume.test.ts`） |

#### 使用最密集的文件 Top 5

| 文件 | 调用数 | 主要操作 |
|------|--------|---------|
| `WecomPersonalRpaManager.vue` | 24 | RPA 配置增删改查 |
| `RpaBindingPanel.vue` | 18 | RPA 绑定管理 |
| `KnowledgeBase.vue` | 17 | 知识库文档管理 |
| `ReplyStyleManager.vue` | 12 | 回复风格管理 |
| `ContextCompressionManager.vue` | 12 | 上下文压缩管理 |

#### 全局配置（`main.ts`）

```ts
import Toast from 'vue-toastification'
import 'vue-toastification/dist/index.css'

createApp(App).use(Toast, {
  position: 'top-center',
  timeout: 5000,
  maxToasts: 3,
  closeOnClick: true,
  pauseOnFocusLoss: true,
  pauseOnHover: true,
  draggable: true,
  showCloseButtonOnHover: false,
  hideProgressBar: false,
  icon: true,
  rtl: false,
  transition: { enter: 'fade-enter', exit: 'fade-exit', move: 'fade-move' }
}).mount('#app')
```

### 1.2 为什么要替换

| 问题 | 说明 |
|------|------|
| `vue-toastification@rc` 是预发布版 | `next` 标签，实际包名含 `@rc`，长期未发正式版，API 不稳定 |
| 与 Element Plus 技术栈统一 | 后台管理迁 Element Plus 后，toast 也用 Element Plus 的 `ElMessage` 可减少依赖 |
| 社区活跃度低 | GitHub 更新频率下降，Vue 3 生态已有更好的替代品 |

### 1.3 为什么选 Element Plus ElMessage 而非其他

| 考量 | ElMessage | vue-sonner | 保持 vue-toastification |
|------|-----------|------------|------------------------|
| 与后台组件库统一 | ✅ 同一套依赖 | ❌ 额外依赖 | — |
| API 相似度 | ⭐⭐⭐⭐ | ⭐⭐⭐ | — |
| 打包增量 | 0（已安装 Element Plus） | +5 KB | — |
| 维护活跃度 | ✅ 大团队维护 | ✅ 活跃 | ❌ |
| 迁移工作量 | 182 处 API 替换 | 182 处 API 替换 | 0 |

**结论**：在后台管理引入 Element Plus 的前提下（见 [关联文档](admin-element-plus-migration.md)），使用 `ElMessage` 是零依赖增量的最优选择。

---

## 2. 改造方案

### 2.1 API 映射关系

`vue-toastification` → `ElMessage` 的 API 几乎一一对应：

| vue-toastification | ElMessage | 说明 |
|-------------------|-----------|------|
| `toast.success(msg)` | `ElMessage.success(msg)` | 成功消息 |
| `toast.error(msg)` | `ElMessage.error(msg)` | 错误消息 |
| `toast.info(msg)` | `ElMessage.info(msg)` | 信息消息 |
| `toast.warning(msg)` | `ElMessage.warning(msg)` | 警告消息 |
| `toast(msg, { type: 'success' })` | `ElMessage({ message: msg, type: 'success' })` | 带选项调用 |

### 2.2 全局配置迁移

**当前**（`main.ts` 插件配置）：

```ts
createApp(App).use(Toast, {
  position: 'top-center',
  timeout: 5000,
  maxToasts: 3,
  // ...
})
```

**改造后**（`main.ts` 全局设置）：

```ts
import { ElMessage } from 'element-plus'

// 全局默认配置（或在 App.vue 中通过 ElConfigProvider 设置）
// ElMessage 的默认值已接近当前配置，只需调整个别参数
```

**配置对比**：

| vue-toastification 配置 | ElMessage 对应 |
|-------------------------|---------------|
| `position: 'top-center'` | ElMessage 默认 top 居中 ✅ |
| `timeout: 5000` | `duration: 5000`（默认 3000） |
| `maxToasts: 3` | `max: 3`（默认无限制） |
| `closeOnClick: true` | 默认 ✅ |
| `pauseOnHover: true` | 默认 ✅ |
| `showCloseButtonOnHover: false` | `showClose: false` |
| `draggable: true` | 不支持拖拽（无此需求） |

### 2.3 典型替换示例

**文件：`KnowledgeBase.vue`（17 处调用）**

改造前：
```ts
import { useToast } from 'vue-toastification'
const toast = useToast()

async function uploadDocument(file: File) {
  try {
    await api.upload(file)
    toast.success('文档上传成功')
  } catch (e) {
    toast.error('文档上传失败：' + e.message)
  }
}
```

改造后：
```ts
import { ElMessage } from 'element-plus'
// 不再需要 useToast() 实例

async function uploadDocument(file: File) {
  try {
    await api.upload(file)
    ElMessage.success('文档上传成功')
  } catch (e) {
    ElMessage.error('文档上传失败：' + e.message)
  }
}
```

### 2.4 批量替换策略

由于 182 处调用分散在 21 个文件中，建议使用以下策略：

#### Step 1：全局替换 import

```ts
// 替换前
import { useToast } from 'vue-toastification'
const toast = useToast()

// 替换后
import { ElMessage } from 'element-plus'
// 删除 const toast = useToast()
```

#### Step 2：替换所有 toast 调用

```
查找：toast.success\( → 替换为 ElMessage.success(
查找：toast.error\(   → 替换为 ElMessage.error(
查找：toast.info\(    → 替换为 ElMessage.info(
查找：toast.warning\( → 替换为 ElMessage.warning(
```

**注意**：`toast` 后面有 `.`，需要精确替换以避免误伤包含 "toast" 的变量名。

#### Step 3：替换 CSS 导入

在 `main.ts` 中：
```diff
- import Toast from 'vue-toastification'
- import 'vue-toastification/dist/index.css'
- createApp(App).use(Toast, { ... })
+ // toast 已迁移到 Element Plus ElMessage
```

### 2.5 无法直接替换的情况

部分调用使用了 `vue-toastification` 的高级功能，需要额外处理：

| 功能 | vue-toastification 写法 | ElMessage 替代方案 |
|------|------------------------|-------------------|
| 自定义超时时间 | `toast.success(msg, { timeout: 2000 })` | `ElMessage({ message: msg, type: 'success', duration: 2000 })` |
| Toast 内容为 HTML/VNode | `toast.info(h('div', ...))` | `ElMessage({ message: h('div', ...), type: 'info' })` 或 `dangerouslyUseHTMLString` |
| 带操作按钮 | `toast.info(msg, { onClick: fn })` | `ElMessage({ message: msg, showClose: true })` 或 `ElNotification` |
| 不自动关闭 | `toast.success(msg, { timeout: false })` | `ElMessage({ message: msg, type: 'success', duration: 0 })` |
| 关闭回调 | `toast.success(msg, { onClose: fn })` | `ElMessage({ message: msg, type: 'success', onClose: fn })` |

**建议**：如果某些复杂场景 `ElMessage` 不满足，可使用 `ElNotification`（通知，支持更多交互）作为升级替代。

### 2.6 测试文件同步

```diff
// frontend/src/__tests__/composables/useRpaPauseResume.test.ts

- vi.mock('vue-toastification', () => ({
-   useToast: () => toastMock,
- }))

+ // 改为 mock Element Plus
+ vi.mock('element-plus', async () => {
+   const actual = await vi.importActual('element-plus')
+   return {
+     ...actual,
+     ElMessage: toastMock,
+   }
+ })
```

---

## 3. 风险与注意事项

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 批量替换遗漏 | 中 | 替换后全局搜索 `useToast`、`from 'vue-toastification'` 确认完全清除 |
| ElMessage 样式与当前 toast 不一致 | 低 | Element Plus 的 Message 样式成熟，差异在可接受范围 |
| 拖拽功能丢失（原 `draggable: true`） | 低 | 企业后台场景几乎不需要拖拽 toast |
| 聊天界面也使用 toast | ⚠️ | `ChatContainer.vue`（1 处 toast.error）、`SettingsDialog.vue`（6 处）——需要在聊天界面也使用 ElMessage，或聊天界面保留独立轻量提示 |

### 3.1 聊天界面的 Toast

`ChatContainer.vue` 仅有 1 处 `toast.error`，`SettingsDialog.vue` 有 6 处。如果不想在聊天界面引入 Element Plus（聊天界面保持纯 Tailwind），可以选择：
- 聊天界面也使用 ElMessage（Element Plus 按需导入体积小，影响不大）
- 或聊天界面独立封装 `useToast` composable，内部用 DOM 操作实现简单 toast

**推荐**：统一使用 ElMessage，0 额外依赖，API 一致。

### 3.2 i18n 兼容性

ElMessage 内置支持国际化，如果项目未来需要多语言，这是一个额外收益。

---

## 4. 实施步骤

| 步骤 | 内容 | 工作量 |
|------|------|-------|
| Step 1 | 确认 Element Plus 已正确安装和按需导入 | 前提条件 |
| Step 2 | 移除 `vue-toastification` 全局注册（`main.ts`） | 即时 |
| Step 3 | 配置 ElMessage 全局默认参数（duration、max 等） | 0.5 小时 |
| Step 4 | 逐个文件替换 `import { useToast }` → `import { ElMessage }` | 2 小时 |
| Step 5 | 批量替换 `toast.success/error/info/warning` → `ElMessage.success/error/info/warning` | 1 小时 |
| Step 6 | 审查高级用法（自定义 timeout、HTML 消息等）并适配 | 1 小时 |
| Step 7 | 更新测试 mock | 0.5 小时 |
| Step 8 | 运行 `npm run build` + 手动验收所有 toast 出现场景 | 1 小时 |
| Step 9 | 从 `package.json` 移除 `vue-toastification` 依赖 | 即时 |

**总计**：约 1 人天
