# 企业微信个人账号 RPA — 平台后台绑定管理 Tab 设计

> **关联文档**：[docs/ideas.md §渠道集成 #29 企业微信个人账号 RPA 接入](../ideas.md)
>
> 本文档登记在 `docs/ideas.md`「渠道集成 / #29」条目的「设计文档」列。

---

## 1. 背景

企业微信个人账号 RPA 接入已完成服务端契约层、客户端视觉定位方案、以及租户管理员视角的 `WecomPersonalRpaManager.vue`（注册/轮换密钥/查看账号）。但平台管理员（运维侧）目前缺少一个集中查看「租户 ↔ 企微账号 ↔ 客户端」绑定全貌、并对异常绑定做快速处置（禁用/恢复）的入口。

本设计新增平台后台「RPA 绑定管理」Tab，复用已有 `/api/saas/wecom-personal-rpa/*` 端点，补齐平台侧运维视角。

### 1.1 目标用户

- **平台管理员**（`role=platform_admin`）：负责所有租户的 RPA 绑定监控与异常处置。
- **租户管理员**（`role=tenant_admin`）：在租户前台 `/t/{tenant_id}/...` 管理自己租户的 RPA 配置（已有，本设计同步增强禁用/恢复能力）。

### 1.2 非目标

- 不重新设计 RPA 协议或客户端。
- 不新增「删除绑定」能力（见 §5.1）。
- 不改变租户前台已有注册流程。

---

## 2. 现状盘点

### 2.1 后端（已就绪）

| 模块 | 路径 | 状态 |
|------|------|------|
| 绑定契约 | `src/channels/wecom_personal_rpa/schemas.py` + `db.py` + `deploy/init-postgres.sql` | ✅ |
| 平台管理 API | `src/saas/api/wecom_personal_rpa_admin.py` | ✅ 已有 `list_bindings` / `pause` / `resume` / `confirm` |
| 租户前台 API | `src/saas/api/wecom_personal_rpa_routes.py` | ✅ callback / config / ws / download_file |
| 渠道适配 | `src/channels/wecom_personal_rpa/adapter.py` | ✅ |

已有可复用端点（平台管理后台前缀 `/api/saas/wecom-personal-rpa`，模块文件 `src/saas/api/wecom_personal_rpa_admin.py`）：

- `GET /bindings?status=&account_id=` — 列出当前租户的绑定（租户隔离；platform_admin 通过 X-Tenant-Id 代管理时列出目标租户），支持按状态过滤（pending/active/paused/invalid/needs_review）
- `GET /all_bindings` — **跨租户**列表（仅 platform_admin，用于平台运维视角），JOIN 携带 client_id / agent_base_url / last_heartbeat_at（本次新增）
- `PATCH /clients/{client_id}/agent_base_url` — 修改客户端的服务端地址（仅 platform_admin；本次新增）
- `GET /clients` / `GET /clients/{id}/accounts` — 客户端与其下账号
- `POST /bindings/{id}/confirm` — 复核确认
- `POST /pause` / `POST /resume` — 三级（account/conversation/tenant）幂等暂停/恢复

### 2.2 前端

| 组件 | 路径 | 状态 |
|------|------|------|
| 租户管理员视角 | `frontend/src/components/saas/WecomPersonalRpaManager.vue` | ✅ 注册/轮换/查看/账号级 pause/resume（已有）；binding/tenant 级 pause/resume 走内联实现，未抽到共享 composable |
| 平台管理员视角 | — | ❌ 缺失，本设计新增 |

### 2.3 关联文档

- 协议：[wecom-personal-rpa-protocol.md](wecom-personal-rpa-protocol.md)
- 主设计：[wecom-personal-rpa-design.md](wecom-personal-rpa-design.md)
- 视觉定位设计：[wecom-personal-rpa-vision-design.md](wecom-personal-rpa-vision-design.md)
- 视觉定位突破总结：[wecom-personal-rpa-vision-breakthrough.md](wecom-personal-rpa-vision-breakthrough.md)
- 客户端操作手册：[clients/wecom-personal-rpa/docs/操作手册.md](../../clients/wecom-personal-rpa/docs/操作手册.md)
- 客户端 EXE 构建与部署：[clients/wecom-personal-rpa/docs/操作手册.md](../../clients/wecom-personal-rpa/docs/操作手册.md)

---

## 3. 方案设计

### 3.1 信息架构

平台后台新增一级入口「RPA 绑定管理」（位于「渠道管理」分组下），下含两个子 Tab：

```
平台后台
└── 渠道管理
    └── RPA 绑定管理
        ├── Tab 1: 绑定列表（默认）
        │   └── 全租户所有绑定，支持状态/租户/账号过滤
        └── Tab 2: 客户端总览
            └── 所有已注册客户端 + 其下账号 + 在线状态
```

### 3.2 绑定列表（Tab 1）

**列定义**：

| 列 | 说明 |
|----|------|
| 租户 | 租户名 + tenant_id（点击可跳转到租户前台） |
| 企微账号 | external_user_id / 昵称 |
| 客户端 | client_id（机器名） |
| agent_base_url | 客户端回填的生产服务地址（见 §3.3） |
| 状态 | active / paused / pending / invalid / needs_review |
| 最近心跳 | 最后一次 ws 心跳时间 |
| 操作 | 禁用 / 恢复 / 复核确认 / 查看详情 |

**过滤区**：状态下拉（默认显示除 active 外的全部需关注项）+ 租户搜索 + 账号搜索。

### 3.3 agent_base_url 字段说明与默认值

**字段用途**：客户端注册时回填的服务端生产地址，供运维排查「客户端连不上服务端」类问题时快速定位。

**默认值策略**：

- 新增 RPA 配置或租户管理员注册客户端时，UI 上 `agent_base_url` 输入框使用**占位地址** `https://agent.example.com`，旁边标注「请改为实际生产地址」。
- **禁止**使用 `http://localhost:8000` 作为默认值 —— 会让运维误以为服务端跑在本机，掩盖真实部署问题。
- 字段非必填（保留向后兼容），但保存时若仍为占位地址，前端给出黄色提示「未修改占位地址，生产环境请填写真实 URL」。

ASCII mockup（绑定详情/编辑弹框内字段示意）：

```
┌─ 绑定详情 ────────────────────────────────────────┐
│ 租户:        [demo-tenant                  ]      │
│ 企微账号:    [wm_abc123 / 张三              ]     │
│ 客户端:      [client_xxxx (销售部-PC01)    ]      │
│ agent_base_url:                                 │
│   [https://agent.example.com          ]  ← 占位  │
│   ⚠ 请改为实际生产地址（勿用 localhost）          │
│ 状态:        [active ▾]                          │
│                                                   │
│            [取消]  [保存]                         │
└───────────────────────────────────────────────────┘
```

### 3.4 前端改动点

#### 3.4.1 新增 `RpaBindingPanel.vue`（平台后台）

位置：`frontend/src/components/saas/RpaBindingPanel.vue`

包含：
- 绑定列表（BaseTable + 状态徽章）
- 过滤区（状态/租户/账号）
- 操作按钮：禁用/恢复/复核确认
- 详情弹框（含 agent_base_url 字段，使用占位地址）

#### 3.4.2 新增路由与菜单项

- `frontend/src/main.ts` 注册 `/admin/rpa-bindings` 路由
- 平台后台菜单「渠道管理」分组下新增「RPA 绑定管理」入口

#### 3.4.3 同步增强 `WecomPersonalRpaManager.vue`（租户管理员）

**决策点（用户已确认）**：现有 `frontend/src/components/saas/WecomPersonalRpaManager.vue` 同步加上 disable/enable 按钮。

- 后端端点已就绪（`/api/saas/wecom-personal-rpa/pause` + `/resume`，支持 `scope=account`/`conversation`/`tenant`）
- 前端零成本受益：在账号列表行操作列追加「禁用/恢复」按钮
- **避免重复代码**：抽出一个共享的 `useRpaPauseResume` composable（`frontend/src/composables/useRpaPauseResume.ts`），封装 pause/resume API 调用 + 确认对话框 + loading 状态，供 `RpaBindingPanel` 和 `WecomPersonalRpaManager` 复用

### 3.5 关键流程

#### 3.5.1 平台管理员禁用某账号

```
平台管理员 → 选中绑定行 → 点「禁用」
  → confirm('确定禁用此账号？禁用后客户端无法收发消息')
  → POST /api/saas/wecom-personal-rpa/pause
       { scope: "account", account_id: "...", reason: "..." }
  → 后端 set_account_status(paused) + 写审计
  → 前端刷新列表，状态变 paused
```

#### 3.5.2 平台管理员恢复

```
POST /api/saas/wecom-personal-rpa/resume
  { scope: "account", account_id: "..." }
→ 后端 set_account_status(online) + 写审计
→ 前端刷新
```

#### 3.5.3 租户管理员禁用（新增能力）

与平台管理员调用同一端点（`TenantContextMiddleware` 自动注入 `tenant_id`，后端已按 tenant 隔离），区别仅在前端入口位于 `/t/{tenant_id}/...`。

---

## 4. 安全考虑

1. **权限隔离**：平台后台路由（`/admin/*`）走 `require_platform_admin`；租户前台路由走 `require_tenant_admin`，后端 `_check_client_ownership` 已就绪。
2. **审计可追溯**：pause/resume 必须落 `rpa_audit_log`（`category=pause_resume`），含 `operator_user_id`、`reason`、`affected_ids`。
3. **脱敏**：客户端密钥永不返回前端（已有 `_client_summary` 脱敏函数）。
4. **幂等**：pause/resume 对已 paused / 已 online 状态幂等，不重复写审计。

---

## 5. 关键决策记录

### 5.1 决策 1：首版不暴露删除 API

**决策**：首版**不提供**「删除绑定」能力，仅提供禁用（pause）。

**理由**：
- 绑定记录是审计链的一部分（历史消息溯源、合规追溯依赖绑定关系）。
- 物理删除会破坏外键完整性（`chat_messages` / `chat_sessions` 通过 `account_id` / `binding_id` 关联）。
- 禁用（pause）已能满足「停止某账号收发消息」的运维诉求，无需冒删除风险。

**后续**：若将来有强需求，再单独设计「软删除 + 保留审计快照」方案。

### 5.2 决策 2：agent_base_url 占位地址用 `https://agent.example.com`

**决策**：UI 默认值/占位符使用 `https://agent.example.com`，**不用** `http://localhost:8000`。

**理由**：
- `localhost` 会让运维误以为服务端跑在本机，掩盖真实部署问题。
- `example.com` 是 RFC 2606 保留域名，明确表达「这是占位，必须替换」的语义。
- 保存时若仍为占位值，前端给黄色警告（不阻断）。

### 5.3 决策 3：租户后台同步加禁用/恢复按钮

**决策**：现有 `WecomPersonalRpaManager.vue` 同步增强 disable/enable 按钮。

**理由**：
- 后端端点（pause/resume）已就绪且按 tenant 隔离，前端零后端成本。
- 租户管理员经常需要自助暂停某账号（员工离职、设备丢失等场景），不应每次都联系平台管理员。
- 抽出共享 composable `useRpaPauseResume`，避免与 `RpaBindingPanel` 重复实现。

---

## 6. 开发计划

| 任务 | 内容 | 依赖 | 预估 |
|------|------|------|------|
| 1 | 后端字段补齐：确认 `list_bindings` 返回 `agent_base_url` + 最近心跳时间 | — | 0.5 天 |
| 2 | 共享 composable `useRpaPauseResume` | 任务 1 | 0.5 天 |
| 3a | 平台后台 Tab：`RpaBindingPanel.vue` + 路由 + 菜单 | 任务 2 | 1.5 天 |
| 3b | 租户后台 `WecomPersonalRpaManager.vue` 加 disable/enable 按钮（复用 3a 的 composable） | 任务 2 | 0.5 天（可与 3a 并行或串行） |
| 4 | 状态徽章与过滤区交互打磨 | 任务 3a | 0.5 天 |
| 5 | 平台后台前端单测（MSW mock pause/resume） | 任务 3a | 0.5 天 |
| 6 | 联调验证 + 文档登记 | 全部 | 0.5 天 |

**总预估**：约 4.5 天。详细可执行计划见 [plans/plan-wecom-personal-rpa-portal-binding.md](../../plans/plan-wecom-personal-rpa-portal-binding.md)。

---

## 7. 关联文档

| 类型 | 文档 |
|------|------|
| 主设计 | [wecom-personal-rpa-design.md](wecom-personal-rpa-design.md) |
| 协议 | [wecom-personal-rpa-protocol.md](wecom-personal-rpa-protocol.md) |
| 视觉定位 | [wecom-personal-rpa-vision-design.md](wecom-personal-rpa-vision-design.md) |
| 视觉突破总结 | [wecom-personal-rpa-vision-breakthrough.md](wecom-personal-rpa-vision-breakthrough.md) |
| 客户端操作手册 | [clients/wecom-personal-rpa/docs/操作手册.md](../../clients/wecom-personal-rpa/docs/操作手册.md) |
| 客户端 EXE 构建与部署 | [clients/wecom-personal-rpa/docs/操作手册.md](../../clients/wecom-personal-rpa/docs/操作手册.md) |
| 开发计划 | [plans/plan-wecom-personal-rpa-portal-binding.md](../../plans/plan-wecom-personal-rpa-portal-binding.md) |

### 7.1 与服务端拉取会话存档模式的关系（2026-07-03）

本设计的「绑定管理 Tab」管理的是**租户 ↔ 客户端实例 ↔ 企微账号**的运维绑定关系（停用/恢复/审计），与服务端拉取会话存档模式（`listen_mode` 字段）是**两个不同维度**：

| 维度 | 管理对象 | 入口 | 数据模型 |
|------|---------|------|---------|
| 绑定管理 Tab（本文档） | 客户端实例 + 企微账号 + 监控白名单 | 平台后台 `RpaBindingPanel.vue` | `wecom_rpa_clients` / `wecom_rpa_accounts` / `wecom_rpa_conversation_bindings` |
| 服务端拉取模式（listen_mode） | 渠道配置的 5 个凭证 + 拉取模式开关 | 租户前台 `ChannelConfig.vue` | `tenant_channel_configs.config` JSON |

两者互不影响：
- 第一期 listen_mode 永远 'server'，绑定管理 Tab 的所有运维动作（停用/恢复/审计）对 server 模式同样生效
- 服务端拉取的消息仍按 `monitor_user_names` / `monitor_user_ids` 白名单过滤（绑定管理 Tab 管理）
- `wecom_rpa_clients.listen_mode` 字段（Phase 7 新增）由 `/config` 路由同步给客户端，但第一期服务端永远下发 'server'，该字段值实际不可达

---

## 8. 设计摘要

本设计在已有 RPA 接入（服务端契约 + 客户端视觉定位）之上，补齐平台管理员视角的绑定管理入口，并同步增强租户管理员的禁用/恢复能力。三个关键决策：①不提供删除绑定（保护审计链）；②`agent_base_url` 占位地址用 `https://agent.example.com`（不用 localhost）；③租户后台同步加 disable/enable 按钮（复用共享 composable）。改动集中在平台后台新增 `RpaBindingPanel.vue` + 一个共享 composable，后端零新增端点（全部复用 `wecom_personal_rpa_admin.py` 已有 pause/resume/list_bindings），总预估 4.5 天。
