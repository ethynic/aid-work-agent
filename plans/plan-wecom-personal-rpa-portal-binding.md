# 开发计划 — 企业微信个人账号 RPA 平台后台绑定管理

> **关联设计**：[docs/system/wecom-personal-rpa-portal-binding-design.md](../docs/system/wecom-personal-rpa-portal-binding-design.md)
>
> **登记位置**：`docs/ideas.md` 渠道集成 #29「企业微信个人账号 RPA 接入」条目「开发计划」列。

---

## 任务总览

| 任务 | 内容 | 依赖 | 预估 |
|------|------|------|------|
| 1 | 后端字段补齐：`list_bindings` 返回 `agent_base_url` + 最近心跳 | — | 0.5 天 |
| 2 | 共享 composable `useRpaPauseResume` | 1 | 0.5 天 |
| 3a | 平台后台 Tab：`RpaBindingPanel.vue` + 路由 + 菜单 | 2 | 1.5 天 |
| 3b | 租户后台 `WecomPersonalRpaManager.vue` 加 disable/enable | 2 | 0.5 天 |
| 4 | 状态徽章与过滤区交互打磨 | 3a | 0.5 天 |
| 5 | 平台后台前端单测（MSW mock） | 3a | 0.5 天 |
| 6 | 联调验证 + 文档登记 | 全部 | 0.5 天 |

**总预估**：4.5 天。任务 3a 与 3b 可并行或串行（共享 composable 已在任务 2 抽出，无强依赖）。

---

## 任务 1：后端字段补齐

**目标**：确认平台管理后台 `list_bindings` 返回的绑定对象包含 `agent_base_url` 字段和「最近心跳时间」字段，供前端列表展示。

### 改动文件

- `src/saas/api/wecom_personal_rpa_admin.py` — 检查 `_binding_public()` 是否输出 `agent_base_url` 和 `last_heartbeat_at`，缺失则补齐
- `src/channels/wecom_personal_rpa/db.py` — 若底层查询未返回上述字段，补充 SELECT 列
- `src/channels/wecom_personal_rpa/schemas.py` — 若 schemas 中 `BindingPublic` 模型缺字段，补齐

### 验收标准

- [ ] `GET /api/saas/wecom-personal-rpa/bindings` 返回的每个绑定对象包含 `agent_base_url` 字段（可为空字符串）
- [ ] 返回对象包含 `last_heartbeat_at` 字段（ISO 时间字符串或 null）
- [ ] 新增/补充的字段在 `_binding_public` 单测中覆盖（若现有 admin 测试文件存在）
- [ ] `pytest tests/unit/ -k rpa_admin` 全通过

### 依赖

无。

---

## 任务 2：共享 composable `useRpaPauseResume`

**目标**：抽出 pause/resume 的 API 调用 + 确认对话框 + loading 状态为可复用 composable，避免平台后台和租户后台重复实现。

### 改动文件

- `frontend/src/composables/useRpaPauseResume.ts`（新增）
- `frontend/src/api/saasRpa.ts` 或现有 RPA api 文件（新增 `pauseRpaBinding` / `resumeRpaBinding` 两个函数，封装 `POST /api/saas/wecom-personal-rpa/pause` 与 `/resume`）

### Composable 接口

```ts
const {
  pausing,           // boolean，pause 请求中
  resuming,          // boolean，resume 请求中
  pauseAccount,      // (accountId, reason?) => Promise<void>
  resumeAccount,     // (accountId) => Promise<void>
  pauseConversation, // (bindingId) => Promise<void>
  resumeConversation,// (bindingId) => Promise<void>
} = useRpaPauseResume({ onSuccess: () => refresh() })
```

### 验收标准

- [ ] composable 内置 `confirm()` 确认对话框，文案「确定禁用此账号？禁用后客户端无法收发消息」
- [ ] pause/resume 失败时 toast 报错（复用现有 toast 工具）
- [ ] loading 状态正确（防重复点击）
- [ ] onSuccess 回调在操作成功后触发列表刷新
- [ ] `frontend/src/__tests__/composables/useRpaPauseResume.test.ts` 单测覆盖（MSW mock）

### 依赖

任务 1（不需要后端字段，但建议先确认端点契约）。

---

## 任务 3a：平台后台 Tab — `RpaBindingPanel.vue`

**目标**：新增平台后台「RPA 绑定管理」页面，含绑定列表 + 过滤 + 操作按钮。

### 改动文件

- `frontend/src/components/saas/RpaBindingPanel.vue`（新增）— 主面板组件
- `frontend/src/components/saas/RpaBindingDetailModal.vue`（新增）— 详情弹框（含 agent_base_url 占位地址提示）
- `frontend/src/main.ts` — 注册路由 `/admin/rpa-bindings`
- 平台后台菜单配置文件 — 「渠道管理」分组下新增「RPA 绑定管理」入口（具体文件需现场确认，通常在 layout/menu 配置中）

### 实现要点

1. **列表**：使用 `BaseTable`，列定义见设计文档 §3.2
2. **过滤区**：状态下拉（默认排除 active）+ 租户搜索 + 账号搜索，使用 `usePageContext` composable
3. **状态徽章**：用 `BaseBadge`，颜色映射 active→success / paused→warning / invalid→danger / pending→info / needs_review→warning
4. **操作按钮**：禁用（`intent="danger-ghost"`）/ 恢复（`intent="ghost"`）/ 复核确认（`intent="ghost"`）/ 查看详情（`intent="ghost"`）
5. **详情弹框**：`agent_base_url` 字段显示，占位地址 `https://agent.example.com`，未修改时给黄色提示
6. **复用**：pause/resume 操作调用任务 2 的 `useRpaPauseResume`

### 验收标准

- [ ] 平台管理员登录后能在「渠道管理」下看到「RPA 绑定管理」入口
- [ ] 列表正确展示所有租户的绑定（跨租户可见）
- [ ] 状态过滤、租户搜索、账号搜索均生效
- [ ] 点击「禁用」弹出确认框，确认后状态变 paused
- [ ] 点击「恢复」状态变 active（或 online）
- [ ] 详情弹框中 agent_base_url 占位地址正确显示，未修改时有黄色警告
- [ ] 使用平台管理员账号访问；租户管理员账号访问 `/admin/*` 被 403 拒绝
- [ ] 遵循 [list-page-convention.md](../.claude/rules/list-page-convention.md) 规范（序号列、操作列、分页器固定底部）

### 依赖

任务 2。

---

## 任务 3b：租户后台 `WecomPersonalRpaManager.vue` 加 disable/enable

**目标**：在租户管理员视角的 RPA 管理页面，为账号列表行追加「禁用/恢复」操作按钮，复用任务 2 的 composable。

### 改动文件

- `frontend/src/components/saas/WecomPersonalRpaManager.vue` — 账号列表行操作列追加「禁用/恢复」按钮（根据当前 status 动态显示其中一个）
- 复用 `frontend/src/composables/useRpaPauseResume.ts`（任务 2 产出）

### 实现要点

1. 行操作列在「查看」按钮旁追加：
   - status=active/online → 显示「禁用」按钮（`intent="danger-ghost"` `size="sm"`）
   - status=paused → 显示「恢复」按钮（`intent="ghost"` `size="sm"`）
2. 调用 `useRpaPauseResume` 的 `pauseAccount` / `resumeAccount`
3. 操作成功后刷新账号列表

### 验收标准

- [ ] 租户管理员在 `/t/{tenant_id}/...` 的 RPA 管理页能看到每行的「禁用/恢复」按钮
- [ ] 按钮根据 status 正确切换显示
- [ ] 禁用/恢复操作生效，列表状态实时更新
- [ ] 租户管理员只能操作自己租户的账号（后端 tenant 隔离已就绪，前端无需额外校验，但需验证）
- [ ] 不引入与 `RpaBindingPanel` 的重复代码（共享 composable）

### 依赖

任务 2。可与任务 3a 并行或串行。

---

## 任务 4：状态徽章与过滤区交互打磨

**目标**：统一状态徽章颜色映射，完善过滤区默认行为。

### 改动文件

- `frontend/src/components/saas/RpaBindingPanel.vue` — 状态徽章映射常量
- 可选：抽出 `frontend/src/api/enums.ts` 中新增 `RpaBindingStatus` 枚举（若项目中尚未定义）

### 验收标准

- [ ] 五种状态（active/paused/pending/invalid/needs_review）徽章颜色统一
- [ ] 过滤区默认显示「需关注」（除 active 外的全部）
- [ ] 切换状态过滤后列表正确刷新
- [ ] 空状态有友好提示（使用 `.empty-state` 类）

### 依赖

任务 3a。

---

## 任务 5：平台后台前端单测

**目标**：为 `RpaBindingPanel` 和 `useRpaPauseResume` 补充前端单测。

### 改动文件

- `frontend/src/__tests__/composables/useRpaPauseResume.test.ts`（任务 2 已建，此处补全用例）
- `frontend/src/__tests__/components/RpaBindingPanel.test.ts`（新增）
- `frontend/src/__tests__/mocks/handlers.ts` — 补充 `/api/saas/wecom-personal-rpa/bindings`、`/pause`、`/resume` 的 MSW handler

### 验收标准

- [ ] pause/resume 成功路径覆盖
- [ ] pause/resume 失败路径（接口 500）覆盖，toast 报错
- [ ] 确认对话框取消时不发请求
- [ ] 状态过滤、搜索生效
- [ ] `npm test` 全通过

### 依赖

任务 3a。

---

## 任务 6：联调验证 + 文档登记

**目标**：端到端联调，确认平台后台与租户后台两边禁用/恢复都生效，并更新 ideas.md 状态。

### 改动文件

- `docs/ideas.md` — #29 条目状态更新为 🔧 部分完成，说明补充「平台后台绑定管理 Tab + 租户后台禁用/恢复按钮」已交付
- 本开发计划文档 — 各任务勾选完成状态

### 验收标准

- [ ] 平台管理员禁用某租户的账号 → 该租户管理员在租户后台看到状态变 paused
- [ ] 租户管理员禁用某账号 → 平台后台列表同步变 paused
- [ ] 审计日志（`rpa_audit_log`）有对应 pause/resume 记录，含 operator_user_id 和 reason
- [ ] ideas.md #29 条目更新状态
- [ ] 本开发计划所有任务勾选完成

### 依赖

任务 1~5 全部完成。

---

## 风险与注意事项

1. **跨租户可见性**：平台后台列表跨租户展示所有绑定，确保仅 `platform_admin` 角色可访问（路由守卫 + 后端 `require_platform_admin`）。
2. **审计完整性**：pause/resume 必须落审计日志，reason 字段前端要引导填写（弹框中提供输入框，可选）。
3. **占位地址提示**：`agent_base_url` 字段保存时若仍为 `https://agent.example.com`，前端只警告不阻断（向后兼容）。
4. **不删除**：首版严格不提供删除按钮，避免审计链断裂。
