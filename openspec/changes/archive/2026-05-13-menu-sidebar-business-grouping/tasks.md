## 1. 准备：理解现有代码与数据流

- [x] 1.1 阅读 `MenuSidebar.vue` 中业务菜单相关代码（`currentBusinessPages`、`currentSubagentName`、模板渲染逻辑）
- [x] 1.2 确认 `SubagentListItem` 类型中 `display_name` 字段的可用性（检查 `useSubagentList.ts`）
- [x] 1.3 确认 `availableSubagents` 中各数字员工的 `business_pages` 分布情况

## 2. 重构业务菜单渲染逻辑

- [x] 2.1 新增 `groupedBusinessPages` computed：将 `filteredAvailableSubagents` 按数字员工分组，过滤掉无 `business_pages` 的分组
- [x] 2.2 新增 `expandedSubagentId` computed：根据 `currentSubagent`（路由参数）决定哪个分组展开；非会话页面为 `null`（全部折叠）
- [x] 2.3 新增/复用展开状态变量（如 `expandedGroups: Set<string>`），初始值由 `expandedSubagentId` 决定
- [x] 2.4 监听 `currentSubagent` 变化，重置 `expandedGroups` 为路由驱动状态
- [x] 2.5 修改模板：由"单组平铺"改为"多组折叠"结构，每组包含数字员工名称标签和菜单列表
- [x] 2.6 数字员工名称优先使用 `display_name`，其次 `name`

## 3. 验证与边界处理

- [x] 3.1 验证单数字员工场景：仍显示一个分组，默认展开
- [x] 3.2 验证多数字员工 + 会话页面场景：当前数字员工分组展开，其余折叠
- [x] 3.3 验证非会话页面场景：所有分组折叠
- [x] 3.4 验证路由切换时展开状态重置行为
- [x] 3.5 验证无 `business_pages` 的数字员工不显示分组
- [x] 3.6 运行 `cd frontend && npm run build` 确保无编译错误
