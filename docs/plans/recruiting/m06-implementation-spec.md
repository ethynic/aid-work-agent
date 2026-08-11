# M0.6 实施规格：Web 本地工具设备管理

> 关联：[plan-recruiting-cli-agent-integration.md](plan-recruiting-cli-agent-integration.md) M0.6；上位设计：[recruiting-cli-agent-integration-design.md](../../design/recruiting/recruiting-cli-agent-integration-design.md) §8.2。
>
> 后端 API 已就绪（M0.3）：`POST /api/local-tools/pairing-tickets`、`GET /api/local-tools/devices`、`POST /api/local-tools/devices/{id}/select`、`DELETE /api/local-tools/devices/{id}`。

## 1. 已勘察的前端机制

- 租户前台路由：`frontend/src/router/agentRoutes.ts` 的 `/t/:tenant_id` children（TenantLayout）。
- 页面规范：PortalLayout 子页面用 `AppHeader`（inject('toggleSidebar')），参照 `TenantSettings.vue` / `TenantUserManager.vue`。
- 菜单：普通用户可见入口在 `MenuSidebar.vue`（参照「我的定时任务」line 473 附近的普通用户菜单项）；`adminSubMenuItems` 是管理员专属，**本功能是全用户（每人配对自己电脑），放普通用户菜单区**。
- API 层：`getAuthHeader()` from `@/api/auth`（自动含 X-Tenant-Id）。
- UI 规范：Base* 组件 + 语义 token（page_patterns.md）；列表用 BaseTable；本页数据量小（个人设备通常 1-2 台），不需要分页/手机端累积加载，但普通用户会使用 → 基本手机端可用即可。
- 前端测试：Vitest + MSW（frontend/src/__tests__/）。

## 2. 交付内容

### 2.1 API 客户端 `frontend/src/api/localTools.ts`

```ts
listDevices(): Promise<Device[]>                  // GET /api/local-tools/devices
createPairingTicket(): Promise<{code, expires_at}>// POST /api/local-tools/pairing-tickets
selectDevice(id: string)                          // POST .../devices/{id}/select
revokeDevice(id: string)                          // DELETE .../devices/{id}
```

全部用 `getAuthHeader()`；统一处理 `success:false` 抛中文错误。Device 类型对齐后端 DeviceView（id/name/platform/runtime_version/online/selected/last_seen_at/status/manifest_digest）。

### 2.2 页面 `frontend/src/components/saas/LocalToolDevices.vue`

PortalLayout 子页面（AppHeader + 内容区），标题「本地工具」。内容：

1. **使用引导卡**（BaseCard）：三步说明——① 下载并启动本机 Runtime（给出命令行示例 `agent-tool-runtime pair --code <配对码>` / `agent-tool-runtime start`，带复制按钮）；② 在本页生成配对码；③ 在 Runtime 输入配对码完成绑定。说明文案注明「仅支持 Windows，需保持电脑未锁屏」。
2. **配对码区**：「生成配对码」按钮（primary）→ 生成后大号等宽字体展示 8 位码 + 5 分钟倒计时 + 「配对码只显示一次」提示 + 复制按钮。倒计时结束自动置灰提示重新生成。
3. **设备列表**（BaseTable）：序号、设备名称、平台、在线状态（BaseBadge：在线 success / 离线 neutral）、当前设备（selected → 「使用中」badge）、最近在线时间（last_seen_at 本地格式化，**不加 Z 后缀**）、版本、操作列（「选定」ghost——已选定或离线禁用；「解绑」danger-ghost + confirm）。
4. 空状态：无设备时显示引导文案。
5. 操作后刷新列表；错误用 alert（项目惯例）。

### 2.3 路由与菜单

- `agentRoutes.ts` `/t/:tenant_id` children 增加：`{ path: 'local-tools', name: 'tenant-local-tools', component: () => import('@/components/saas/LocalToolDevices.vue') }`。
- `MenuSidebar.vue` 普通用户菜单区（「我的定时任务」附近）增加「本地工具」入口，图标用桌面/显示器 SVG（stroke 风格与现有一致），显示条件与该菜单区其他项一致。demo 模式（无租户）不显示。

### 2.4 对话侧提示（最小实现）

proxy 的 DEVICE_UNAVAILABLE 引导文案已包含「本地工具」页面指引（M0.5 已实现）。本阶段把文案里的页面指引明确为「点击左侧菜单『本地工具』」——若 proxy_tool.py 现有文案不是这个说法，顺手对齐（后端一处文案改动，不重测后端逻辑）。

### 2.5 测试

- `frontend/src/__tests__/api/localTools.test.ts`：MSW mock 4 个端点，断言 URL/method/headers（含 X-Tenant-Id）与错误分支。
- `npm run build` 0 错误（强制门禁）。

## 3. 不做（本阶段）

- 不在网页调用 localhost、不暴露设备 token/CLI 路径/原始 capability payload（设计 §0 边界）。
- 不做 invocations 历史列表页（M0.7 真机后再评估）。
- demo 模式不接入。
- 手机端专门优化（基础可用即可）。
