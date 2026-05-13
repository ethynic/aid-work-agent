## Why

当前业务菜单的显示逻辑完全依赖当前会话关联的数字员工（subagent）。在非对话页面（如"全部历史会话"）没有当前数字员工时，业务菜单完全隐藏。这导致两个问题：(1) 用户难以理解"为什么有时能看到菜单、有时看不到"；(2) 在非对话页面无法访问任何业务功能，用户产生"菜单丢失"的困惑。

## What Changes

- `MenuSidebar.vue` 中业务菜单由"单组平铺"改为"按数字员工分组展示"
- 多数字员工时，默认所有分组折叠；当前会话关联的数字员工分组自动展开
- 单数字员工时，仍显示为一个可折叠分组（保留上下文标识）
- 非会话页面（无当前数字员工）所有分组默认折叠
- 路由变化时重置展开状态，始终保证"当前数字员工展开、其余折叠"
- 数字员工名称优先使用 `display_name`，其次 `name`
- 无业务菜单的数字员工不显示分组

## Capabilities

### New Capabilities

无。本变更纯为前端 UI 交互优化，不引入新的系统能力或 API。

### Modified Capabilities

无。现有业务菜单的数据结构和路由定义保持不变。

## Impact

- **前端**: `frontend/src/components/MenuSidebar.vue`（主要改动）
- **前端**: `frontend/src/composables/useSubagentList.ts`（确认 `display_name` 字段可用性）
- **无后端变更**，无 API 变更
- **无数据库变更**
