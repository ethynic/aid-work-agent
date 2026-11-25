# 前端 Vitest 存量失败用例基线（2026-11-24）

> 背景：2026-11-24 全站防重复提交改造（66 个 .vue）完成后跑全量 vitest，发现 13 个失败用例。经 HEAD 基线对照确认**全部为存量失败，与本次改动无关**。
>
> **2026-11-24 整改闭环**：13 例已全部修复（均为测试侧过期，零实现改动），全量 vitest 恢复全绿。各条目的根因与修复记录保留如下备查；基线本身已清空。

## 整改结果（2026-11-24）

| # | 测试文件 | 例数 | 根因（均测试侧过期） | 修复 |
|---|---------|------|---------------------|------|
| 1 | `router/routes.test.ts` | 1 | 08257890 有意删除 portal dashboard 路由（首页由系统健康页接管），内联快照未同步 | 快照移除 `"dashboard"` |
| 2 | `composables/useAgentAsk.test.ts` | 1 | 0b47761a（11-23）waiting parked 行权威查询降频至 30s（runnerEvents.test.ts 已同步，本文件漏了），测试仍按 1s 等待下一轮快照 | `advanceTimersByTimeAsync(1000)` → `30000` |
| 3 | `components/TenantAgentCustomizeDialog.test.ts` | 1 | 4f48d14f 删除简介描述字段，`留空则使用默认描述` 占位符不存在；另 `/api/saas/tenants/me`（LLM 编辑开关预查）缺 MSW handler | 删过期断言（改验问候按钮文案）；handlers.ts 补 `GET /api/saas/tenants/me` |
| 4 | `notifications/chatNavigation.independent.test.ts` | 10 | fd17ad37（11-19）MenuSidebar 新增 `hiddenMenus`/`privateCloud` 依赖，测试 mock 的 `useTenantAuth` 未同步（解析为 undefined → `hiddenMenus.value.has` TypeError） | mock 补 `hiddenMenus: ref(new Set())` + `privateCloud: ref(false)` |

- 修复涉及文件：上述 4 个测试文件 + `__tests__/mocks/handlers.ts`，共 5 个，全部为测试装配/断言过期，无生产代码改动。
- 验证：4 个文件 35 例全过；全量 132 文件 1157 用例（1134 通过 / 23 跳过，0 失败，见下方「已知环境抖动」）。
- 基线中的例数勘误：#4 实际为 10 例（原记录 11 例系计数口径误差），总数 12+1=13 与原结论一致。

## 已知环境抖动（非基线，勿与本整改混淆）

`AttachmentPreviewPanel.office.test.ts` 的 beforeAll 动态 import `pptx-preview`（vite-node 下真实耗时约 26s，逼近 30s 超时线）：在 Windows 盘慢速挂载（/mnt/d）宿主机全量并发跑时偶发超时、隔离运行必过；容器内磁盘不受影响。属环境抖动，不改动测试语义。

## 原始记录（整改前存档）

### 基线对照证据

| 运行 | 结果 |
|------|------|
| 含防重复提交改动 | 132 文件：127 通过 / 5 失败；1157 用例：1121 通过 / **13 失败** / 23 跳过 |
| HEAD 基线（`git stash push -- frontend` 后运行，`stash pop` 恢复） | 同样 4 个测试文件失败，共 **12 用例**；叠加 TenantAgentCustomizeDialog 1 例（单独对照，HEAD 版组件同样失败）= 13，失败集合与含改动运行完全一致 |

### 复现命令（整改前）

```bash
cd frontend
npx vitest run \
  web/__tests__/router/routes.test.ts \
  web/__tests__/composables/useAgentAsk.test.ts \
  web/__tests__/components/TenantAgentCustomizeDialog.test.ts \
  web/__tests__/notifications/chatNavigation.independent.test.ts
```
