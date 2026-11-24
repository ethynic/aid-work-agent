# 前端 Vitest 存量失败用例基线（2026-11-24）

> 背景：2026-11-24 全站防重复提交改造（66 个 .vue）完成后跑全量 vitest，发现 13 个失败用例。经 HEAD 基线对照确认**全部为存量失败，与本次改动无关**。本文档登记失败清单、错误签名与初步根因分析，供后续整改，防止长期遗留掩盖真实回归。

## 基线对照证据

| 运行 | 结果 |
|------|------|
| 含防重复提交改动 | 132 文件：127 通过 / 5 失败；1157 用例：1121 通过 / **13 失败** / 23 跳过 |
| HEAD 基线（`git stash push -- frontend` 后运行，`stash pop` 恢复） | 同样 4 个测试文件失败，共 **12 用例**；叠加 TenantAgentCustomizeDialog 1 例（单独对照，HEAD 版组件同样失败）= 13，失败集合与含改动运行完全一致 |

结论：防重复提交改动零测试回归；13 例均为存量失败。

## 失败清单与初步分析

### 1. `web/__tests__/router/routes.test.ts`（1 例）

- 用例：`entry route responsibilities > keeps the Web Portal and tenant nested path contracts`
- 错误签名：Snapshot mismatch —— 快照期望 portal 路由含 `"dashboard"`，实际路由表已无该项（快照过期或路由被有意移除后未更新快照）
- 初步结论：路由变更后未重生成快照的陈旧快照问题，非行为回归
- 整改建议：确认 dashboard 路由移除是否有意；若有意则 `vitest -u` 更新快照，否则恢复路由

### 2. `web/__tests__/composables/useAgentAsk.test.ts`（1 例）

- 用例：`useAgent structured ask submission (WP4 提交链路) > a rejected conflicting control and a live wait clear the resume-pending conflict for resubmission`
- 错误签名：`AssertionError: Expected controlled external request: GET/runners/runner-A: expected undefined to be defined`（测试自建的 `network.pending` 中找不到该请求，useAgentAsk.test.ts:235）
- 初步结论：`useAgent` 的 Runner 恢复/冲突清除链路与测试编写时的行为契约已漂移（请求时机、URL 或路径分支变化），需对照 WP4 设计确认哪边是正确语义
- 整改建议：读 `useAgentAsk` 提交链路现状，判定改测试或改实现；涉及 Runner 恢复语义，不宜直接 -u

### 3. `web/__tests__/components/TenantAgentCustomizeDialog.test.ts`（1 例）

- 用例：`关闭时重置 form：重新打开不显示上一个员工的数据`
- 错误签名：`Cannot call element on an empty DOMWrapper`（首个输入框断言处，弹框内容未渲染）；伴随 MSW 告警 `GET /api/saas/tenants/me` 无匹配 handler
- 初步结论：测试环境缺 `/api/saas/tenants/me` mock，组件打开前置请求未被拦截导致弹框渲染链路中断（或组件新增了该前置依赖）。其余 2 例（回退确认框）通过，说明组件本身可挂载
- 整改建议：在 `__tests__/mocks/handlers.ts` 补该端点 handler 后复测

### 4. `web/__tests__/notifications/chatNavigation.independent.test.ts`（11 例，单一根因）

- 用例：`independent actual chat/sidebar notification navigation` 组下全部 11 例
- 错误签名（统一）：`TypeError: Cannot read properties of undefined (reading 'value')` → `isMenuHidden`（`web/components/MenuSidebar.vue:911`：`hiddenMenus.value.has(key)`），经模板计算属性（MenuSidebar.vue:1146-1147）在渲染期触发
- 初步结论：`hiddenMenus`（TENANT_HIDDEN_MENUS 菜单隐藏开关，经 `/saas/auth/me` 下发）依赖的初始化来源（auth store / composable）在该测试文件的环境装配下为 undefined。2026-11-19 落地的菜单隐藏开关（fd17ad37）引入该依赖时未同步此测试的 mock 装配
- 整改建议：在该测试文件（或其 mount helper）补 `hiddenMenus` 依赖的 mock/默认值；核对 MenuSidebar 对 `hiddenMenus` 缺省路径是否需要防御（生产经 `/saas/auth/me` 恒有值，测试环境裸装配暴露未设默认）

## 影响与优先级

- 全量 vitest 恒红，会掩盖真实回归；任何「全量跑绿」类验收都被这 13 例阻塞
- 建议优先级：#4（11 例单一根因，改一处全绿）> #3（补一个 mock）> #1（快照更新）> #2（需语义判定，工作量最大）
- 整改完成后更新本文档并从基线中移除对应条目

## 复现命令

```bash
cd frontend
npx vitest run \
  web/__tests__/router/routes.test.ts \
  web/__tests__/composables/useAgentAsk.test.ts \
  web/__tests__/components/TenantAgentCustomizeDialog.test.ts \
  web/__tests__/notifications/chatNavigation.independent.test.ts
```
