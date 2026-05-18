## Context

当前系统使用 Loguru 将错误日志输出到文件 `log/agent/error.log`（仅 ERROR 级别），平台管理员需要登录服务器查看，效率低且无法追踪处理状态。

系统已有成熟的管理后台（PortalLayout + `/portal` 路由），平台管理员通过左侧菜单导航访问各管理功能。现有菜单项：仪表盘、租户管理、数字员工管理、平台Token消耗。

## Goals / Non-Goals

**Goals:**
- 新增 `log_error` 数据库表，持久化 Error 级别日志
- 在管理后台新增"错误日志"菜单项和页面
- 管理员可按时间倒序查看错误列表、复制完整错误信息
- 管理员可修改错误记录处理状态：未处理 → 已处理/忽略
- 管理员可一键清理 30 天前的旧日志（数据库记录 + 文件日志）
- 原有文件日志不受影响

**Non-Goals:**
- 不记录 Warning、Info 级别日志到数据库
- 不做实时推送/告警通知
- 不做错误日志的自动分析和聚合
- 不修改现有 Loguru 日志配置

## Decisions

### 1. 错误拦截点：自定义 Loguru sink（patched handler）

**方案**：通过 Loguru 的 `add()` 方法添加自定义 sink，在 sink 中判断 `level` 是否为 ERROR，若是则写入数据库。

**理由**：不侵入现有代码，所有通过 `logger.error()` 输出的错误自动捕获。相比在各处 try-catch 中添加数据库写入代码，此方案统一、无遗漏。

**替代方案**：
- FastAPI middleware 统一捕获异常 → 只能捕获 HTTP 请求中的异常，无法捕获后台任务、调度任务的错误
- 修改所有 `logger.error()` 调用点 → 改动量大、容易遗漏

### 2. 数据库表设计

```sql
CREATE TABLE IF NOT EXISTS log_error (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    module TEXT,           -- 错误来源模块（如 agent.py:process_message:1234）
    error_type TEXT,       -- 异常类型（如 ValueError、HTTPException）
    message TEXT NOT NULL, -- 错误消息
    traceback TEXT,        -- 完整堆栈
    status TEXT DEFAULT 'unprocessed',  -- unprocessed / processed / ignored
    processed_by TEXT,     -- 处理人
    processed_at TIMESTAMP -- 处理时间
);

CREATE INDEX IF NOT EXISTS idx_log_error_timestamp ON log_error(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_log_error_status ON log_error(status);
```

**设计要点**：
- 不设 `tenant_id`，属于平台级系统表（与 `scheduled_task_logs` 同级）
- `status` 使用 TEXT 字段，值为 `unprocessed`、`processed`、`ignored`
- 时间戳加降序索引，优化默认排序查询
- `traceback` 存储完整堆栈，支持复制

### 3. 前端路由和菜单

在 `PortalLayout.vue` 的 `portalMenuItems` 中添加：
```js
{ path: '/portal/error-logs', label: '错误日志', icon: '📋' }
```

在 `main.ts` 的 `/portal` children 中添加路由。

### 4. API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/error-logs` | 查询错误日志列表，支持分页和状态筛选 |
| PUT | `/api/admin/error-logs/{log_id}/status` | 更新处理状态 |
| DELETE | `/api/admin/error-logs/cleanup` | 清理 30 天前的数据库错误记录和文件日志 |

复用 `src/api/admin_reports.py` 的权限校验模式（`is_platform_admin`）。

### 5. 前端页面设计

遵循项目标准布局（PortalLayout 已提供左侧菜单和顶部区域），页面组件只需关注内容：

```
┌─────────────────────────────────────────────┐
│  AppHeader: 错误日志    [状态筛选] [清理30天]│
├─────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────┐│
│  │ 时间 ↓   │ 模块    │ 错误类型 │ 消息摘要 │ 状态   │ 操作 │
│  ├─────────────────────────────────────────┤│
│  │ 05-18... │ agent.. │ ValueEr │ 连接超.. │ 未处理 │[详情][处理]│
│  │ 05-18... │ gate..  │ HTTPErr │ API 调.. │ 已处理 │[详情]│
│  └─────────────────────────────────────────┘│
│  [分页器]                                   │
└─────────────────────────────────────────────┘
```

点击"详情"弹出模态框，展示完整错误信息（时间、模块、类型、消息、堆栈），支持一键复制。

点击"清理旧日志（30天）"按钮，弹出确认对话框，确认后调用 `DELETE /api/admin/error-logs/cleanup`。

### 6. 清理机制设计

**数据库清理**：执行 `DELETE FROM log_error WHERE timestamp < NOW() - INTERVAL '30 days'`，返回删除条数。

**文件日志清理**：遍历 `log/agent/` 目录下的日志文件（`error.log.*`、`aid-work-agent.log.*`），通过文件修改时间判断，删除超过 30 天的文件。

**清理入口**：统一由 `DELETE /api/admin/error-logs/cleanup` 接口触发，同时清理数据库和文件，返回各自的清理数量。仅平台管理员可调用。

**安全措施**：
- 清理前弹出确认对话框，显示将要删除的数据量
- 文件路径限制在 `log/agent/` 目录内，防止路径穿越
- 清理操作记录日志

## Risks / Trade-offs

- **数据库写入性能**：高频错误可能造成数据库压力。→ 使用异步写入（`asyncio.to_thread` 或 `loguru` 的 `enqueue` 机制），不阻塞主流程；数据库写入失败只记录 warning，不影响文件日志
- **表膨胀**：长期运行后表数据量增大。→ 查询加分页（每页 20 条）；后续可按时间归档清理
- **堆栈信息可能含敏感数据**：→ API 响应时通过 `sanitize_error_info()` 过滤敏感信息（复用现有逻辑）