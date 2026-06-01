# 回复风格管理系统设计

> 版本: 2.0
> 日期: 2026-06-01
> 状态: 已开发
> 前序文档: [回复风格系统设计 v1.0](design-reply-style.md)

## 1. 背景与目标

### 1.1 现状

回复风格系统 v1.0 已完成开发（见 `docs/system/design-reply-style.md`），核心机制：

- 风格定义存储为 `src/prompts/styles/*.md` 磁盘文件
- `StyleManager` 在 Agent 初始化时加载所有 `.md` 文件到内存
- 风格优先级链：用户长期记忆 → 子智能体配置 → 全局默认
- 目前仅有 `human-like.md` 一个风格

### 1.2 问题

1. 风格只能通过修改磁盘文件管理，非技术人员无法操作
2. 没有版本管理，修改后无法回滚
3. 没有前端 UI，无法在 Portal 页面管理风格
4. 风格无法按数字员工实例级别配置

### 1.3 目标

1. 在租户 Portal 页面新增"回复风格"管理菜单
2. 支持新增、编辑、删除回复风格
3. 支持版本管理，编辑自动创建新版本，可回滚到任意历史版本
4. 修改后立即生效，无需重启服务
5. 每个数字员工实例可独立配置回复风格

---

## 2. 方案概要

采用**数据库存储**方案，将风格定义从磁盘文件迁移到数据库：

- 新增 `reply_styles` 表存储风格定义，支持版本管理
- `agent_instances` 表新增 `reply_style_id` 字段，实例级别配置
- 改造 `StyleManager` 从数据库加载风格，支持热更新
- 前端新增管理页面，遵循项目 UI 规范

---

## 3. 数据库设计

### 3.1 新增表：`reply_styles`

```sql
CREATE TABLE IF NOT EXISTS reply_styles (
    id SERIAL PRIMARY KEY,
    style_id TEXT NOT NULL,              -- 风格唯一标识，如 "human-like"
    tenant_id TEXT NOT NULL,             -- 租户ID（"system" 为系统内置）
    name TEXT NOT NULL,                  -- 显示名称，如 "拟人风格"
    description TEXT,                    -- 风格描述
    content TEXT NOT NULL,               -- 风格内容（Markdown）
    version INTEGER NOT NULL DEFAULT 1,  -- 版本号
    is_active BOOLEAN NOT NULL DEFAULT TRUE,  -- 是否为当前激活版本
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(style_id, tenant_id, version) -- 同租户同风格不同版本唯一
);
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `style_id` | TEXT | 风格标识符，租户内唯一。创建后不可修改 |
| `tenant_id` | TEXT | 所属租户。`"system"` 表示系统内置，所有租户可见 |
| `name` | TEXT | 前端显示名称 |
| `description` | TEXT | 风格简短描述 |
| `content` | TEXT | 风格 Markdown 内容，注入系统提示词 |
| `version` | INTEGER | 版本号，自动递增。编辑时创建新版本 |
| `is_active` | BOOLEAN | 同一 `style_id` + `tenant_id` 下仅一条为 `true` |

### 3.2 修改表：`agent_instances` 新增字段

```sql
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS reply_style_id TEXT;
```

### 3.3 系统内置风格种子数据

将现有 `src/prompts/styles/human-like.md` 内容作为系统内置风格插入：

```sql
INSERT INTO reply_styles (style_id, tenant_id, name, description, content, version, is_active)
VALUES ('human-like', 'system', '拟人风格', '像真人同事一样对话，隐藏 AI 工作过程',
        '<human-like.md 内容>', 1, true);
```

系统内置风格（`tenant_id = 'system'`）：
- 所有租户可见、可使用
- 租户不可编辑、不可删除
- 租户可创建自己的风格覆盖同名内置风格

---

## 4. 后端设计

### 4.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/saas/db/reply_style_db.py` | 风格 CRUD 数据库操作 |
| `src/saas/api/reply_styles.py` | 风格管理 API 路由 |

### 4.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/prompts/style_manager.py` | 改为从数据库加载，支持租户隔离和热更新 |
| `src/core/agent.py` | `_resolve_reply_style()` 新增实例级别优先级 |
| `src/saas/db/agent_instance_db.py` | `update()` 白名单新增 `reply_style_id` |
| `src/saas/api/agent_instances.py` | `InstanceUpdateRequest` 新增 `reply_style_id` |
| `src/main.py` | 注册 `/api/saas/reply-styles` 路由 |
| `deploy/init-postgres.sql` | 新增 `reply_styles` 表 |
| `deploy/db_update.sql` | 增量变更记录 |

### 4.3 API 设计

**路由前缀**: `/api/saas/reply-styles`，所有接口需要租户管理员权限。

#### 列出风格

```
GET /api/saas/reply-styles
```

返回当前租户的风格 + 系统内置风格（去重：租户自定义优先）。

**Response**:
```json
{
  "styles": [
    {
      "style_id": "human-like",
      "name": "拟人风格",
      "description": "像真人同事一样对话",
      "version": 1,
      "is_system": true,
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

#### 新增风格

```
POST /api/saas/reply-styles
```

**Request**:
```json
{
  "style_id": "professional",
  "name": "专业助手",
  "description": "信息密度高，展示推理过程",
  "content": "## 回复风格指南\n\n..."
}
```

创建 version=1，`is_active=true`。

#### 获取风格详情

```
GET /api/saas/reply-styles/{style_id}
```

返回当前激活版本的内容。

#### 更新风格（创建新版本）

```
PUT /api/saas/reply-styles/{style_id}
```

**Request**:
```json
{
  "name": "专业助手（优化版）",
  "description": "更新后的描述",
  "content": "## 更新后的风格内容\n\n..."
}
```

逻辑：
1. 查询当前最大 version
2. 创建新记录 version=max+1，`is_active=true`
3. 将旧版本的 `is_active` 设为 `false`
4. 触发 `StyleManager.reload()`

#### 删除风格

```
DELETE /api/saas/reply-styles/{style_id}
```

硬删除该 `style_id` + `tenant_id` 的所有版本。系统内置风格不可删除。

#### 列出版本历史

```
GET /api/saas/reply-styles/{style_id}/versions
```

**Response**:
```json
{
  "versions": [
    {
      "version": 2,
      "is_active": true,
      "content": "...",
      "created_at": "..."
    },
    {
      "version": 1,
      "is_active": false,
      "content": "...",
      "created_at": "..."
    }
  ]
}
```

#### 激活指定版本（回滚）

```
POST /api/saas/reply-styles/{style_id}/versions/{version}/activate
```

逻辑：
1. 将当前激活版本 `is_active` 设为 `false`
2. 将目标版本 `is_active` 设为 `true`
3. 触发 `StyleManager.reload()`

#### 热更新触发

```
POST /api/saas/reply-styles/reload
```

手动触发 `StyleManager.reload()`，通常不需要手动调用（编辑/激活操作自动触发）。

### 4.4 StyleManager 改造

```python
class StyleManager:
    def __init__(self):
        self._styles: dict = {}           # (tenant_id, style_id) -> content
        self._system_styles: dict = {}    # style_id -> content（系统内置）
        self._load_disk_files()           # 磁盘文件作为 fallback
        self._load_from_db()              # 数据库加载覆盖

    def _load_disk_files(self):
        """加载磁盘风格文件（fallback）"""
        # 保留原逻辑，用于数据库不可用时降级

    def _load_from_db(self):
        """从数据库加载所有激活的风格"""
        # SELECT * FROM reply_styles WHERE is_active = true
        # 按 tenant_id 分组存储

    def get_style(self, style_id: str, tenant_id: str = None) -> str | None:
        """获取风格内容，支持租户隔离"""
        # 1. 查租户自定义风格 (tenant_id, style_id)
        # 2. 查系统内置风格 (system, style_id)
        # 3. 查磁盘 fallback

    def list_styles(self, tenant_id: str = None) -> list[dict]:
        """列出可用风格（含元信息）"""
        # 合并租户自定义 + 系统内置

    def reload(self):
        """热更新：重新从数据库加载"""
        self._styles.clear()
        self._system_styles.clear()
        self._load_disk_files()
        self._load_from_db()
```

### 4.5 风格解析优先级（更新后）

```
0（最高）: 用户长期记忆中的 reply_style
1（新增）: agent_instances.reply_style_id（数字员工实例级别）
2: subagent_config.reply_style（子智能体配置）
3: config.yaml 全局默认
```

`_resolve_reply_style()` 新增实例级别：

```python
def _resolve_reply_style(self, user=None, instance_id=None):
    # 优先级 0: 用户长期记忆
    # ...

    # 优先级 1（新增）: 实例级别
    if instance_id:
        from src.saas.db.agent_instance_db import AgentInstanceDB
        inst = AgentInstanceDB.get_by_id(instance_id)
        if inst and inst.get('reply_style_id'):
            return inst['reply_style_id']

    # 优先级 2: 子智能体配置
    # ...

    # 优先级 3: 全局默认
    # ...
```

**注意**：`_resolve_reply_style()` 需要额外传入 `instance_id` 参数。调用方 `_build_base_system_prompt()` 需要传递当前会话关联的实例 ID。

---

## 5. 前端设计

### 5.1 新增文件

| 文件 | 说明 |
|------|------|
| `frontend/src/api/replyStyle.ts` | 风格 API 调用 |
| `frontend/src/components/saas/ReplyStyleManager.vue` | 风格管理页面 |

### 5.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| `frontend/src/components/MenuSidebar.vue` | `adminSubMenuItems` 新增"回复风格"菜单 |
| `frontend/src/main.ts` | 新增 `reply-styles` 路由 |
| `frontend/src/components/saas/InstanceManager.vue` | 实例编辑中新增风格选择 |

### 5.3 风格管理页面

遵循 `page_patterns.md` 规范，采用标准列表页 + 模态编辑模式。

#### 页面布局

```
┌─────────────────────────────────────────────┐
│ AppHeader（标题：回复风格，汉堡按钮）          │
├─────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────┐ │
│ │ 新增风格按钮                   筛选/搜索  │ │
│ ├─────────────────────────────────────────┤ │
│ │ 序号 │ 名称 │ 描述 │ 版本 │ 操作        │ │
│ │  1   │ 拟人 │ ... │ v1   │ 编辑 版本 删 │ │
│ │  2   │ 专业 │ ... │ v2   │ 编辑 版本 删 │ │
│ ├─────────────────────────────────────────┤ │
│ │           暂无数据                       │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

系统内置风格行：不显示删除按钮，编辑按钮改为"查看"。

#### 编辑弹窗

```
┌─ 新增/编辑回复风格 ─────────────────────────┐
│                                             │
│ 风格标识 *  [professional        ]          │
│ （新增时可编辑，编辑时只读）                   │
│                                             │
│ 风格名称 *  [专业助手              ]          │
│                                             │
│ 风格描述    [信息密度高，展示推理过程]          │
│                                             │
│ 风格内容 *                                  │
│ ┌─────────────────────────────────────────┐ │
│ │ ## 回复风格指南                           │ │
│ │                                         │ │
│ │ 你是一个专业的AI助手...                   │ │
│ │                                         │ │
│ └─────────────────────────────────────────┘ │
│                                             │
│              [取消]  [保存]                   │
└─────────────────────────────────────────────┘
```

#### 版本历史弹窗

```
┌─ 版本历史：专业助手 ─────────────────────────┐
│                                             │
│ v2（当前）  2026-06-01 10:30  [查看]        │
│ v1          2026-05-28 14:20  [查看] [激活]  │
│                                             │
│                           [关闭]             │
└─────────────────────────────────────────────┘
```

点击"查看"显示该版本的完整内容（只读弹窗）。

### 5.4 路由与菜单

**路由**（`main.ts`）：
```js
{
  path: 'reply-styles',
  name: 'tenant-reply-styles',
  component: () => import('./components/saas/ReplyStyleManager.vue')
}
```

**菜单项**（`MenuSidebar.vue` adminSubMenuItems）：
```js
{ path: `${base}/reply-styles`, label: '回复风格', icon: '💬' }
```

### 5.5 实例管理中的风格选择

在 `InstanceManager.vue` 的实例编辑弹窗中新增"回复风格"下拉选择（BaseSelect），选项从风格列表 API 获取。

---

## 6. 实施步骤

### Step 1: 数据库变更

修改文件：
- `deploy/db_update.sql` — 添加 reply_styles 表和 agent_instances 字段
- `deploy/init-postgres.sql` — 同步全量建表语句

### Step 2: 后端 DB 层

新增文件：
- `src/saas/db/reply_style_db.py` — 风格 CRUD

修改文件：
- `src/saas/db/agent_instance_db.py` — update 白名单新增 reply_style_id

### Step 3: 后端 API 层

新增文件：
- `src/saas/api/reply_styles.py` — 风格管理路由

修改文件：
- `src/saas/api/agent_instances.py` — 请求模型新增 reply_style_id
- `src/main.py` — 注册路由

### Step 4: StyleManager 改造

修改文件：
- `src/prompts/style_manager.py` — 数据库加载 + 租户隔离
- `src/core/agent.py` — 优先级链新增实例级别

### Step 5: 前端实现

新增文件：
- `frontend/src/api/replyStyle.ts` — API 调用
- `frontend/src/components/saas/ReplyStyleManager.vue` — 管理页面

修改文件：
- `frontend/src/components/MenuSidebar.vue` — 新增菜单
- `frontend/src/main.ts` — 新增路由
- `frontend/src/components/saas/InstanceManager.vue` — 风格选择

### Step 6: 种子数据迁移

- 将 human-like.md 内容插入 reply_styles 表
- 确保系统启动时自动执行种子数据

---

## 7. 风险与注意事项

1. **数据库连接依赖**：StyleManager 原来只依赖磁盘文件，改造后依赖数据库。需保留磁盘文件作为 fallback，数据库不可用时降级
2. **热更新延迟**：多 worker 进程下，一个 worker 触发 reload，其他 worker 需要在下次请求时感知变化。方案：StyleManager.get_style() 每次调用时检查缓存 TTL（如 30 秒），过期则重新从数据库加载
3. **系统内置风格保护**：系统内置风格（tenant_id='system'）不允许租户编辑和删除，API 层需校验
4. **style_id 冲突**：租户创建的 style_id 可能与未来新增的系统内置风格冲突。策略：租户自定义优先，系统内置作为 fallback
5. **agent_instances.reply_style_id 外键**：不使用数据库外键约束（遵循项目规范），在应用层校验 style_id 是否存在

---

## 8. 与现有系统的关系

### 8.1 与回复风格系统 v1.0 的关系

v2.0 完全兼容 v1.0：
- 磁盘文件风格保留为 fallback
- 优先级链向下兼容（新增实例级别在中间位置）
- 用户记忆中的风格偏好不受影响
- SUBAGENT.md 中的 reply_style 配置不受影响
- config.yaml 全局默认不受影响

### 8.2 与 personality_traits 的关系

`agent_instances.personality_traits` 存储性格标签（JSON 数组），`reply_style_id` 存储回复风格 ID。两者互补：personality_traits 影响人设，reply_style 影响输出格式。
