# 数字员工实例选择模式设计方案

> 版本: v2.0
> 创建日期: 2026-04-29
> 状态: 待评审
> 预计工时: 4人天（比v1方案减少1天）
> 设计原则: **用户手动选择实例，简化并发控制，强化拟人感**
> 前置依赖: [订阅与权限表合并改造](subscription_permission_merge_plan.md)

---

## 一、设计思路调整

### 1.1 旧方案 vs 新方案

| 维度 | v1 自动分配模式 | **v2 用户选择模式（新）** |
|------|----------------|------------------------|
| **用户体验** | 系统自动分配，用户不知道在跟哪个实例聊 → 神秘感 | 用户选择"外贸智能体-小明"、"外贸智能体-小红" → 拟人感 |
| **代码复杂度** | 复杂：FIFO队列、唤醒、竞态处理 | 简单：每个实例有独立状态，直接选中锁定 |
| **业务匹配度** | 匹配"云服务"概念 | 匹配"数字员工"概念，更直观 |
| **计费理解** | 2个实例 = 2个并发槽位 | 2个实例 = 我的两个员工，每个随时待命 | ✅
| **历史上下文** | 会话级记忆，换实例上下文丢失 | 每个实例有**自己的聊天历史**，延续性强 | ✅✅

### 1.2 用户视角体验

```
┌─────────────────────────────────────────────────────────────┐
│                     数字员工工作台                             │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ 外贸小明     │  │ 外贸小红     │  │ + 新增实例   │      │
│  │ 🟢 空闲      │  │ 🟡 忙碌中    │  │              │      │
│  │              │  │ 正在跟张三聊 │  │ 可购买更多   │      │
│  │ [开始对话]   │  │ [排队等待]   │  │ 实例配额     │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                               │
│  您购买了 2 个外贸智能体实例，还可购买 3 个（上限5个）        │
└─────────────────────────────────────────────────────────────┘
```

**用户故事：**
- 早上9点，A选择"外贸小明"开始对话
- B也想聊天，看到"外贸小明"忙碌、"外贸小红"空闲
- B选择"外贸小红"开始对话
- C来的时候两个都在忙 → 选任意一个点击"排队等待"
- A结束对话 → C收到通知，可以开始使用了

---

## 二、整体架构

### 2.1 核心概念

| 概念 | 说明 |
|------|------|
| **实例类型 (Subagent Type)** | 如"外贸智能体"、"HR智能体"，对应 subagent_type |
| **实例 (Instance)** | 租户购买的具体数字员工，如"外贸智能体-小明"、"外贸智能体-小红"。有自己的名字、头像、描述 |
| **实例配额 (Instance Quota)** | 租户最多可以创建多少个实例 |
| **实例状态** | 空闲 / 忙碌 / 离线 |

### 2.2 调用链路

```
用户进入租户前台
    ↓
加载该租户可用的数字员工实例列表（含状态）
    ↓
用户点击某个实例 → [开始对话]
    ↓
[1] 检查实例状态：
    ├─ 空闲 → 锁定实例 → 创建会话 → 开始聊天
    └─ 忙碌 → 进入该实例的等待队列
                    ↓
                排队中... 可看到前面等待人数
                    ↓
                前面用户结束 → 收到通知 → 锁定实例 → 开始聊天
```

---

## 三、数据库设计

### 3.1 agent_instances 表增强（核心）

```sql
-- deploy/init-postgres.sql

-- 已有字段保留，新增以下字段：
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS instance_name TEXT;           -- 实例名称："外贸小明"
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS avatar TEXT DEFAULT '🤖';     -- 头像 emoji 或 URL
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS description TEXT;              -- 实例描述
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS personality_traits TEXT;       -- 性格特征（JSON数组）
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'idle';    -- idle / busy / offline
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS current_session_id TEXT;       -- 当前活跃会话ID
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS current_user_id TEXT;          -- 当前使用者
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP;           -- 锁定开始时间
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS lock_expires_at TIMESTAMP;     -- 锁过期时间
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS total_chats INTEGER DEFAULT 0; -- 累计对话次数
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS total_messages INTEGER DEFAULT 0; -- 累计消息数

-- 索引
CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant_type 
    ON agent_instances(tenant_id, subagent_type, status);
```

**完整表结构：**

| 字段 | 说明 | 示例 |
|------|------|------|
| instance_id | 实例唯一ID | inst_abc123 |
| tenant_id | 租户ID | tenant_xyz |
| subagent_type | 子智能体类型 | trade-specialist |
| **instance_name** | 实例名称 | 外贸小明 |
| **avatar** | 头像 | 🤵‍ |
| **description** | 描述 | 擅长外贸邮件和客户开发 |
| **personality_traits** | 性格 | ["专业", "严谨", "高效"] |
| **status** | 状态 | idle / busy / offline |
| **current_session_id** | 当前会话 | sess_xxx |
| **current_user_id** | 当前用户 | user_yyy |
| **locked_at** | 锁定时间 | 2026-04-29 09:15:00 |
| **lock_expires_at** | 锁过期 | 2026-04-29 10:15:00 |
| display_name | 显示名称 | 外贸智能体 |
| subscription_id | 关联订阅 | sub_zzz |
| config | 配置JSON | {...} |
| allowed_skills | 技能列表 | ["email", "customer"] |
| total_chats | 累计对话数 | 156 |
| total_messages | 累计消息数 | 2340 |

### 3.2 实例等待队列表：agent_instance_queue（简化版）

```sql
CREATE TABLE IF NOT EXISTS agent_instance_queue (
    id SERIAL PRIMARY KEY,
    queue_id TEXT UNIQUE NOT NULL,
    instance_id TEXT NOT NULL,           -- 等待的具体实例ID
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,             -- 等待者的会话ID
    user_id TEXT NOT NULL,                -- 等待的用户ID
    position INTEGER NOT NULL,            -- 队列位置
    status TEXT DEFAULT 'waiting',        -- waiting / ready / expired / cancelled
    
    enqueued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    wait_timeout_at TIMESTAMP NOT NULL,   -- 排队超时时间
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_instance_queue_instance 
    ON agent_instance_queue(instance_id, position);
CREATE INDEX IF NOT EXISTS idx_instance_queue_session 
    ON agent_instance_queue(session_id);
CREATE INDEX IF NOT EXISTS idx_instance_queue_timeout 
    ON agent_instance_queue(wait_timeout_at);
```

### 3.3 chat_sessions 表关联调整

```sql
-- 会话与实例绑定
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS instance_id TEXT;

-- 索引：按实例查询历史会话
CREATE INDEX IF NOT EXISTS idx_chat_sessions_instance 
    ON chat_sessions(instance_id, created_at DESC);
```

---

## 四、核心业务逻辑

### 4.1 实例服务：InstanceService

```python
# src/saas/services/instance_service.py

class InstanceService:
    """数字员工实例管理服务"""
    
    def list_tenant_instances(self, tenant_id: str, subagent_type: Optional[str] = None) -> List[dict]:
        """
        获取租户的数字员工实例列表（含实时状态）
        
        返回：
        [
            {
                "instance_id": "inst_xxx",
                "instance_name": "外贸小明",
                "avatar": "🤵‍",
                "description": "擅长外贸邮件...",
                "status": "idle",  # idle / busy / offline
                "current_user_name": None,  # 空闲时None
                "queue_length": 0,           # 当前等待人数
                "total_chats": 156,
                "created_at": "..."
            },
            ...
        ]
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # 清理过期锁（每次查询时顺便清理，懒加载）
            self._cleanup_expired_locks(conn)
            
            where_clause = "WHERE tenant_id = %s"
            params = [tenant_id]
            
            if subagent_type:
                where_clause += " AND subagent_type = %s"
                params.append(subagent_type)
            
            cursor.execute(f"""
                SELECT 
                    ai.*,
                    COALESCE(q.queue_length, 0) as queue_length
                FROM agent_instances ai
                LEFT JOIN (
                    SELECT instance_id, COUNT(*) as queue_length
                    FROM agent_instance_queue
                    WHERE status = 'waiting'
                    GROUP BY instance_id
                ) q ON ai.instance_id = q.instance_id
                {where_clause}
                ORDER BY ai.created_at ASC
            """, params)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def try_lock_instance(
        self,
        instance_id: str,
        session_id: str,
        user_id: str,
        lock_timeout: int = 3600  # 默认锁1小时
    ) -> LockResult:
        """
        尝试锁定实例（原子操作）
        
        返回：
            success: 是否成功
            was_idle: 锁定前是否空闲
            queue_position: 如果忙碌，排队位置（从0开始）
            queue_length: 队列总长度
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # 1. 先清理该实例的过期锁
            self._cleanup_instance_expired_locks(conn, instance_id)
            
            # 2. 检查实例当前状态
            cursor.execute("""
                SELECT status, current_session_id FROM agent_instances
                WHERE instance_id = %s
                FOR UPDATE  -- 行锁，防止并发
            """, (instance_id,))
            row = cursor.fetchone()
            
            if not row:
                return LockResult(success=False, error="Instance not found")
            
            current_status = row["status"]
            
            # 3. 空闲 → 直接锁定
            if current_status == "idle":
                cursor.execute("""
                    UPDATE agent_instances
                    SET 
                        status = 'busy',
                        current_session_id = %s,
                        current_user_id = %s,
                        locked_at = CURRENT_TIMESTAMP,
                        lock_expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE instance_id = %s AND status = 'idle'
                    RETURNING instance_id
                """, (session_id, user_id, lock_timeout, instance_id))
                
                locked = cursor.fetchone()
                conn.commit()
                
                if locked:
                    logger.info(f"Instance {instance_id} locked by {session_id}")
                    return LockResult(success=True, was_idle=True)
                
                # 竞态：有人抢先锁定了，进入排队
                current_status = "busy"
            
            # 4. 忙碌 → 进入排队
            if current_status == "busy":
                # 计算排队位置
                cursor.execute("""
                    SELECT COALESCE(MAX(position), -1) + 1 as next_pos
                    FROM agent_instance_queue
                    WHERE instance_id = %s AND status = 'waiting'
                """, (instance_id,))
                position = cursor.fetchone()["next_pos"]
                
                # 插入队列
                queue_id = f"q_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO agent_instance_queue (
                        queue_id, instance_id, tenant_id, session_id, user_id,
                        position, wait_timeout_at
                    )
                    SELECT 
                        %s, %s, ai.tenant_id, %s, %s, %s,
                        CURRENT_TIMESTAMP + '30 minutes'
                    FROM agent_instances ai
                    WHERE ai.instance_id = %s
                    RETURNING (
                        SELECT COUNT(*) FROM agent_instance_queue 
                        WHERE instance_id = %s AND status = 'waiting'
                    ) as total
                """, (queue_id, instance_id, session_id, user_id, position, instance_id, instance_id))
                
                total = cursor.fetchone()["total"]
                conn.commit()
                
                logger.info(f"Session {session_id} queued for instance {instance_id}, pos {position}")
                
                return LockResult(
                    success=False,
                    was_idle=False,
                    is_queued=True,
                    queue_position=position,
                    queue_length=total
                )
            
            # offline 状态
            return LockResult(success=False, error="Instance is offline")
    
    def release_instance(self, instance_id: str, session_id: str) -> bool:
        """
        释放实例，并唤醒队列头部的等待者
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # 1. 释放锁（只有持有锁的会话才能释放）
            cursor.execute("""
                UPDATE agent_instances
                SET 
                    status = 'idle',
                    current_session_id = NULL,
                    current_user_id = NULL,
                    locked_at = NULL,
                    lock_expires_at = NULL,
                    total_chats = total_chats + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE instance_id = %s AND current_session_id = %s
                RETURNING instance_id
            """, (instance_id, session_id))
            
            released = cursor.fetchone()
            conn.commit()
            
            if not released:
                return False
            
            logger.info(f"Instance {instance_id} released by {session_id}")
            
            # 2. 唤醒队列头部的等待者
            self._wake_up_next_waiter(conn, instance_id)
            conn.commit()
            
            return True
    
    def _wake_up_next_waiter(self, conn, instance_id: str) -> Optional[str]:
        """唤醒队列头部的用户，标记为可以开始"""
        cursor = conn.cursor()
        
        # 找到队列头部第一个
        cursor.execute("""
            SELECT queue_id, session_id FROM agent_instance_queue
            WHERE instance_id = %s AND status = 'waiting'
            ORDER BY position
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (instance_id,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        # 标记为 ready 状态
        cursor.execute("""
            UPDATE agent_instance_queue
            SET status = 'ready', updated_at = CURRENT_TIMESTAMP
            WHERE queue_id = %s
        """, (row["queue_id"],))
        
        logger.info(f"Woke up session {row['session_id']} for instance {instance_id}")
        return row["session_id"]
    
    def _cleanup_expired_locks(self, conn):
        """清理所有过期的实例锁"""
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE agent_instances
            SET 
                status = 'idle',
                current_session_id = NULL,
                current_user_id = NULL,
                locked_at = NULL,
                lock_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'busy' AND lock_expires_at < CURRENT_TIMESTAMP
            RETURNING instance_id
        """)
        
        released = cursor.fetchall()
        for row in released:
            self._wake_up_next_waiter(conn, row["instance_id"])
            logger.info(f"Auto released expired lock: {row['instance_id']}")
```

---

## 五、API 设计

### 5.1 获取租户实例列表（核心入口）

**GET /api/chat/instances?subagent_type=trade-specialist**

```json
{
    "success": true,
    "data": {
        "instances": [
            {
                "instance_id": "inst_abc123",
                "instance_name": "外贸小明",
                "avatar": "🤵‍",
                "description": "擅长外贸邮件撰写、客户开发、供应商谈判",
                "personality_traits": ["专业", "严谨", "高效"],
                "status": "idle",
                "current_user": null,
                "queue_length": 0,
                "total_chats": 156,
                "created_at": "2026-04-01T00:00:00"
            },
            {
                "instance_id": "inst_def456",
                "instance_name": "外贸小红",
                "avatar": "👩‍💼",
                "description": "擅长市场分析、竞品调研、展会筹备",
                "personality_traits": ["热情", "细致", "数据驱动"],
                "status": "busy",
                "current_user": {
                    "user_id": "user_xxx",
                    "name": "张三",
                    "held_minutes": 15
                },
                "queue_length": 2,
                "total_chats": 89,
                "created_at": "2026-04-01T00:00:00"
            }
        ],
        "quota": {
            "total": 2,          // 租户购买的实例配额
            "used": 2,           // 已创建的实例数
            "max_limit": 5       // 系统上限
        }
    }
}
```

### 5.2 锁定实例（开始对话前调用）

**POST /api/chat/instances/{instance_id}/lock**

Request:
```json
{
    "session_id": "sess_xxx"
}
```

Response（锁定成功 - 200）:
```json
{
    "success": true,
    "data": {
        "locked": true,
        "was_idle": true,
        "instance": {
            "instance_id": "inst_abc123",
            "instance_name": "外贸小明",
            "status": "busy"
        }
    }
}
```

Response（进入排队 - 429）:
```json
{
    "success": false,
    "error": "Instance is busy",
    "queue_info": {
        "position": 1,
        "queue_length": 3,
        "check_interval_seconds": 5
    }
}
```

### 5.3 检查排队状态

**GET /api/chat/instances/{instance_id}/queue-status?session_id=xxx**

```json
{
    "success": true,
    "data": {
        "status": "waiting",      // waiting / ready / expired / not_in_queue
        "position": 1,
        "queue_length": 3,
        "estimated_wait_seconds": 180
    }
}
```

### 5.4 释放实例（对话结束调用）

**POST /api/chat/instances/{instance_id}/release**

```json
{
    "session_id": "sess_xxx"
}
```

### 5.5 取消排队

**DELETE /api/chat/instances/{instance_id}/queue?session_id=xxx**

---

## 六、前端设计

### 6.1 数字员工选择页

```vue
<!-- frontend/src/views/InstanceLobby.vue -->
<template>
  <div class="instance-lobby">
    <h1>数字员工工作台</h1>
    
    <div class="instance-grid">
      <!-- 每个实例卡片 -->
      <div 
        v-for="inst in instances" 
        :key="inst.instance_id"
        class="instance-card"
        :class="inst.status"
      >
        <div class="card-header">
          <span class="avatar">{{ inst.avatar }}</span>
          <span class="status-dot" :class="inst.status"></span>
        </div>
        
        <h3 class="name">{{ inst.instance_name }}</h3>
        <p class="description">{{ inst.description }}</p>
        
        <!-- 状态标签 -->
        <div class="status-info">
          <span v-if="inst.status === 'idle'" class="status-idle">
            🟢 空闲可用
          </span>
          <span v-else-if="inst.status === 'busy'" class="status-busy">
            🟡 忙碌中
            <span v-if="inst.current_user">
              ({{ inst.current_user.name }} 正在使用)
            </span>
          </span>
          <span v-else class="status-offline">
            🔴 离线
          </span>
        </div>
        
        <!-- 排队人数 -->
        <div v-if="inst.queue_length > 0" class="queue-info">
          {{ inst.queue_length }} 人在等待
        </div>
        
        <!-- 操作按钮 -->
        <button 
          @click="startChat(inst)"
          class="start-btn"
          :disabled="inst.status === 'offline'"
        >
          {{ inst.status === 'idle' ? '开始对话' : '排队等待' }}
        </button>
      </div>
      
      <!-- 新增实例卡片（配额未满时） -->
      <div 
        v-if="quota.used < quota.total"
        class="instance-card add-new"
        @click="showCreateModal"
      >
        <div class="plus-icon">+</div>
        <p>创建新实例</p>
        <small>还可创建 {{ quota.total - quota.used }} 个</small>
      </div>
    </div>
    
    <!-- 配额提示 -->
    <div class="quota-hint">
      您购买了 {{ quota.total }} 个 {{ subagentType }} 实例配额，
      已创建 {{ quota.used }} 个。
      <a v-if="quota.used < 5" href="#upgrade">升级配额 →</a>
    </div>
  </div>
</template>
```

### 6.2 排队等待弹窗

```vue
<!-- frontend/src/components/InstanceQueueModal.vue -->
<template>
  <div class="queue-modal" v-if="visible">
    <div class="modal-content">
      <div class="queue-header">
        <span class="instance-avatar">{{ instance.avatar }}</span>
        <h3>正在等待 {{ instance.instance_name }}</h3>
      </div>
      
      <div class="queue-stats">
        <div class="stat">
          <span class="number">{{ position + 1 }}</span>
          <span class="label">您的位置</span>
        </div>
        <div class="stat">
          <span class="number">{{ queueLength }}</span>
          <span class="label">总等待人数</span>
        </div>
        <div class="stat">
          <span class="number">{{ formatTime(estimatedWait) }}</span>
          <span class="label">预计等待</span>
        </div>
      </div>
      
      <div class="progress-bar">
        <div 
          class="progress" 
          :style="{ width: ((queueLength - position) / queueLength * 100) + '%' }"
        ></div>
      </div>
      
      <div class="actions">
        <button @click="cancelQueue" class="cancel-btn">
          取消排队
        </button>
      </div>
      
      <div class="hint">
        排到您时会自动开始对话，请保持页面开启
      </div>
    </div>
  </div>
</template>
```

---

## 七、实例创建与管理

### 7.1 创建实例

**POST /api/saas/instances**

```json
{
    "subagent_type": "trade-specialist",
    "instance_name": "外贸小李",
    "avatar": "👨‍💼",
    "description": "专注于东南亚市场开发",
    "personality_traits": ["外向", "主动", "精通小语种"],
    "config": {
        "system_prompt_tweaks": "..."
    }
}
```

### 7.2 实例配额校验

创建实例前检查：
```python
def check_create_permission(tenant_id: str, subagent_type: str) -> Tuple[bool, str]:
    """检查是否可以创建新实例"""
    
    # 1. 检查订阅配额
    subscription = get_active_subscription(tenant_id, subagent_type)
    if not subscription:
        return False, "No active subscription"
    
    instance_quota = subscription["instance_quota"]
    
    # 2. 检查已创建实例数
    current_count = count_tenant_instances(tenant_id, subagent_type)
    
    if current_count >= instance_quota:
        return False, f"Instance quota exceeded: {current_count}/{instance_quota}"
    
    return True, "OK"
```

---

## 八、实施计划（4天）

### Day 1：数据库 + 实例管理服务

| 任务 | 说明 |
|------|------|
| ✅ agent_instances 表增强 | 新增 name、avatar、status、锁字段 |
| ✅ 创建 agent_instance_queue 表 | 等待队列表 |
| ✅ chat_sessions 加 instance_id | 会话与实例绑定 |
| ✅ InstanceService 核心实现 | list / lock / release / queue |

### Day 2：API 层

| 任务 | 说明 |
|------|------|
| ✅ GET /api/chat/instances | 获取实例列表（核心入口） |
| ✅ POST /api/chat/instances/{id}/lock | 锁定实例 |
| ✅ GET /api/chat/instances/{id}/queue-status | 排队状态查询 |
| ✅ POST /api/chat/instances/{id}/release | 释放实例 |
| ✅ DELETE /api/chat/instances/{id}/queue | 取消排队 |

### Day 3：前端

| 任务 | 说明 |
|------|------|
| ✅ 数字员工选择大厅页面 | 实例卡片网格展示 |
| ✅ 排队等待弹窗组件 | 显示排队位置、预计时间 |
| ✅ 聊天页面改造 | 显示当前使用的实例头像名称 |
| ✅ 实例创建弹窗 | 自定义数字员工名称、头像、描述 |

### Day 4：集成 + 测试

| 任务 | 说明 |
|------|------|
| ✅ /api/chat/stream 集成实例锁 | 聊天前验证锁持有状态 |
| ✅ SSE 结束自动释放锁 | 对话结束自动释放 |
| ✅ 定时清理任务 | 过期锁、超时队列清理 |
| ✅ 单元测试 + 集成测试 | 各种边界场景 |

---

## 九、关键优势对比

### 相比 v1 自动分配模式

| 维度 | v1 自动分配 | **v2 用户选择模式** |
|------|-----------|-------------------|
| **代码复杂度** | 高（FIFO队列、唤醒、竞态） | 中（每个实例独立状态） |
| **产品体验** | 无感但抽象 | 有拟人感，符合"数字员工"定位 | ✅✅✅ |
| **历史上下文** | 会话级，换实例丢失 | **实例级，永远延续** | ✅✅✅ |
| **用户控制感** | 低，系统自动分配 | 高，用户自主选择 | ✅ |
| **个性化** | 所有实例一致 | 每个实例可以有不同性格、技能 | ✅✅ |
| **排队体验** | 不知道在等谁 | 明确知道在等"小明"还是"小红" | ✅ |
| **总工时** | 5天 | **4天** | ✅ |

---

## 十、后续产品迭代方向

| 功能 | 说明 | 优先级 |
|------|------|--------|
| **实例聊天历史** | 查看"外贸小明"的所有历史对话 | P1 |
| **实例技能定制** | 给"外贸小明"专门开启某些技能 | P1 |
| **实例性格调教** | 用户可以调教自己数字员工的说话风格 | P2 |
| **实例绩效统计** | 每个实例的对话数、消息数、满意度评分 | P2 |
| **实例共享/转移** | 把我的"外贸小明"转移给同事 | P3 |

---

## 总结

**用户选择模式**是更符合"数字员工"产品形态的设计：

1. ✅ **产品体验更好**：每个实例有名字、头像、性格，就是团队的一份子
2. ✅ **代码更简单**：每个实例独立状态，不需要复杂的全局分配
3. ✅ **上下文延续性强**："外贸小明"的聊天历史永远属于"外贸小明"
4. ✅ **可扩展性强**：未来每个实例可以有自己的技能、性格、绩效

这是一个**产品体验提升**同时**代码复杂度降低**的双赢设计调整！
