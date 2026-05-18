# 数字员工实例并发控制完整设计方案

> 版本: v1.0
> 创建日期: 2026-04-29
> 状态: 待评审
> 预计工时: 5人天
> 前置依赖: [订阅与权限表合并改造](subscription_permission_merge_plan.md) 必须先行完成

---

## 一、项目背景

### 1.1 需求来源

SaaS 模式下，按"实例数"收费是比按"token数"更直观、更易被客户理解的计费模式：

- **Token 计费**：用户难以估算每月用量，账单波动大，容易产生争议
- **实例计费**：用户明确知道"我买了2个外贸智能体，可以同时2个人用"，简单透明

**业务场景示例**：
> 某外贸公司5人团队，购买了"外贸智能体"的2个实例配额。
> - 早上9点：A和B正在使用 → 2个实例都被占用
> - C此时发起聊天 → 进入排队，显示"您前面还有1人在等待"
> - A结束对话 → C自动开始对话

### 1.2 当前架构问题

1. **无并发限制**：当前代码一个租户有N个用户，可以同时调用同一个子智能体N次
2. **无排队机制**：用户遇到并发限制时只能收到"系统繁忙"，没有友好的排队体验
3. **多worker安全**：多进程部署时，内存锁无效，需要分布式锁方案

### 1.3 设计目标

| 目标 | 说明 |
|------|------|
| ✅ 按实例数并发控制 | 1个实例 = 1个并发会话槽位 |
| ✅ 公平排队 | FIFO队列，先到先服务 |
| ✅ 多worker安全 | 数据库级分布式锁，支持多进程部署 |
| ✅ 会话超时释放 | 用户长时间无操作自动释放锁 |
| ✅ 实时状态反馈 | 显示排队位置、预计等待时间 |
| ✅ 管理员监控 | 查看当前所有实例使用情况、强制释放 |

---

## 二、整体架构设计

### 2.1 调用链路

```
用户请求 /api/chat/stream
    ↓
[1] TenantContextMiddleware 解析 tenant_id
    ↓
[2] check_agent_access 验证权限 + 获取 instance_quota
    ↓
[3] ConcurrencyControlService.acquire_lock()
    │
    ├─ 有可用槽位 → 获取锁成功 → 执行对话
    │                         ↓
    │                     对话结束 → release_lock()
    │
    └─ 无可用槽位 → 进入排队队列
                      ↓
                 轮询等待唤醒
                      ↓
                 前面用户结束 → 获取锁 → 执行对话
```

### 2.2 核心组件

| 组件 | 职责 | 位置 |
|------|------|------|
| **ConcurrencyControlService** | 核心并发控制服务：加锁、释放、队列管理 | `src/saas/services/concurrency_control.py` |
| **AgentTaskLocks 表** | 分布式锁存储：当前持有的实例锁 | PostgreSQL |
| **AgentTaskQueue 表** | 排队队列存储 | PostgreSQL |
| **SessionLockMiddleware** | 请求级别的锁管理（可选） | `src/saas/middleware/lock_middleware.py` |

---

## 三、数据库设计

### 3.1 实例锁表：agent_task_locks

```sql
-- deploy/init-postgres.sql

CREATE TABLE IF NOT EXISTS agent_task_locks (
    id SERIAL PRIMARY KEY,
    lock_id TEXT UNIQUE NOT NULL,          -- 锁唯一标识：{tenant_id}:{subagent_type}:{slot}
    tenant_id TEXT NOT NULL,               -- 租户ID
    subagent_type TEXT NOT NULL,           -- 子智能体类型
    session_id TEXT NOT NULL,              -- 持有锁的会话ID
    user_id TEXT,                           -- 持有锁的用户ID
    acquired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 获取锁时间
    last_heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 最后心跳时间
    expires_at TIMESTAMP NOT NULL,         -- 锁过期时间（防死锁）
    
    -- 元数据
    request_id TEXT,                        -- 请求ID（用于追踪）
    client_ip TEXT,                         -- 客户端IP
    user_agent TEXT,                        -- 用户代理
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引：按租户+子智能体快速查询
CREATE INDEX IF NOT EXISTS idx_agent_locks_tenant_type 
    ON agent_task_locks(tenant_id, subagent_type);

-- 索引：按会话ID查询（用于释放锁）
CREATE INDEX IF NOT EXISTS idx_agent_locks_session 
    ON agent_task_locks(session_id);

-- 索引：过期清理
CREATE INDEX IF NOT EXISTS idx_agent_locks_expires 
    ON agent_task_locks(expires_at);
```

**锁ID设计：** `{tenant_id}:{subagent_type}:{slot_number}`

示例：
- `tenant_abc:trade-specialist:0`
- `tenant_abc:trade-specialist:1`

这样设计的好处：
1. 每个槽位对应一行记录，方便统计当前持有数
2. 用 `slot_number` 从 0 到 `instance_quota-1`，清晰知道哪些被占用

### 3.2 排队队列表：agent_task_queue

```sql
CREATE TABLE IF NOT EXISTS agent_task_queue (
    id SERIAL PRIMARY KEY,
    queue_id TEXT UNIQUE NOT NULL,          -- 队列项ID
    tenant_id TEXT NOT NULL,               -- 租户ID
    subagent_type TEXT NOT NULL,           -- 子智能体类型
    session_id TEXT NOT NULL,              -- 会话ID
    user_id TEXT,                           -- 用户ID
    position INTEGER NOT NULL,              -- 队列位置（从0开始）
    status TEXT DEFAULT 'waiting',          -- waiting / processing / expired / cancelled
    
    -- 排队元数据
    enqueued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 入队时间
    estimated_start_at TIMESTAMP,          -- 预计开始时间
    wait_timeout_at TIMESTAMP NOT NULL,    -- 排队超时时间（默认30分钟）
    
    -- 唤醒通知（可选，用于轮询优化）
    wake_up_token TEXT,                     -- 唤醒令牌
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引：按租户+子智能体查询队列
CREATE INDEX IF NOT EXISTS idx_agent_queue_tenant_type 
    ON agent_task_queue(tenant_id, subagent_type, position);

-- 索引：按会话ID查询
CREATE INDEX IF NOT EXISTS idx_agent_queue_session 
    ON agent_task_queue(session_id, status);

-- 索引：超时清理
CREATE INDEX IF NOT EXISTS idx_agent_queue_timeout 
    ON agent_task_queue(wait_timeout_at);
```

---

## 四、核心业务逻辑

### 4.1 获取锁流程

```python
# src/saas/services/concurrency_control.py

class ConcurrencyControlService:
    """数字员工并发控制服务"""
    
    async def acquire_lock(
        self,
        tenant_id: str,
        subagent_type: str,
        session_id: str,
        user_id: Optional[str] = None,
        lock_timeout: int = 3600,  # 锁默认1小时超时
    ) -> AcquireLockResult:
        """
        尝试获取实例锁
        
        Returns:
            AcquireLockResult:
                - success: bool
                - lock_id: 锁ID（成功时）
                - queue_position: 排队位置（失败时）
                - queue_length: 队列总长度
                - estimated_wait_seconds: 预计等待时间
        """
        # Step 1: 获取租户的实例配额
        instance_quota = self._get_instance_quota(tenant_id, subagent_type)
        if instance_quota <= 0:
            return AcquireLockResult(
                success=False,
                error="No instance quota available"
            )
        
        # Step 2: 检查当前持锁数量
        current_locks = self._count_current_locks(tenant_id, subagent_type)
        
        # Step 3: 该会话是否已经持有锁？（重入支持）
        existing_lock = self._get_session_lock(session_id, subagent_type)
        if existing_lock:
            # 更新心跳时间
            self._refresh_lock(existing_lock)
            return AcquireLockResult(
                success=True,
                lock_id=existing_lock,
                is_reentrant=True
            )
        
        # Step 4: 还有可用槽位吗？
        if current_locks < instance_quota:
            # 有可用槽位，尝试获取锁
            lock_id = self._try_acquire_first_available_slot(
                tenant_id, subagent_type, session_id, user_id, lock_timeout
            )
            if lock_id:
                return AcquireLockResult(success=True, lock_id=lock_id)
        
        # Step 5: 没有可用槽位，进入排队
        queue_result = self._enqueue_for_wait(
            tenant_id, subagent_type, session_id, user_id
        )
        return AcquireLockResult(
            success=False,
            is_queued=True,
            queue_position=queue_result.position,
            queue_length=queue_result.total_length,
            estimated_wait_seconds=queue_result.estimated_wait
        )
```

### 4.2 关键实现：分布式锁的原子性

**问题**：多worker同时检查并获取锁，可能产生竞态条件。

**解决方案**：使用 PostgreSQL 的 `INSERT ... ON CONFLICT DO NOTHING` + 行锁。

```python
def _try_acquire_first_available_slot(
    self, tenant_id: str, subagent_type: str, 
    session_id: str, user_id: str, timeout: int
) -> Optional[str]:
    """
    尝试获取第一个可用槽位，使用数据库级原子操作
    
    算法：
    1. 生成所有可能的槽位ID: slot_0, slot_1, ..., slot_{quota-1}
    2. 用 LEFT JOIN 找出未被占用的槽位
    3. 用 FOR UPDATE SKIP LOCKED 跳过已被其他事务锁定的行
    4. INSERT ... ON CONFLICT DO NOTHING 原子插入
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 使用 CTE 生成槽位列表并找出空闲槽位
        cursor.execute("""
            WITH slots AS (
                SELECT generate_series(0, %s - 1) AS slot_num
            ),
            occupied_slots AS (
                SELECT split_part(lock_id, ':', 3)::int AS slot_num
                FROM agent_task_locks
                WHERE tenant_id = %s AND subagent_type = %s
                  AND expires_at > CURRENT_TIMESTAMP
            ),
            free_slots AS (
                SELECT slot_num
                FROM slots
                WHERE slot_num NOT IN (SELECT slot_num FROM occupied_slots)
                ORDER BY slot_num
                LIMIT 1
                FOR UPDATE SKIP LOCKED  -- 关键：跳过已锁定的行
            )
            INSERT INTO agent_task_locks (
                lock_id, tenant_id, subagent_type, session_id, user_id, expires_at
            )
            SELECT 
                %s || ':' || %s || ':' || slot_num,
                %s, %s, %s, %s,
                CURRENT_TIMESTAMP + (%s || ' seconds')::interval
            FROM free_slots
            ON CONFLICT (lock_id) DO NOTHING
            RETURNING lock_id;
        """, (
            instance_quota,
            tenant_id, subagent_type,
            tenant_id, subagent_type,
            tenant_id, subagent_type, session_id, user_id,
            timeout
        ))
        
        row = cursor.fetchone()
        conn.commit()
        return row["lock_id"] if row else None
```

### 4.3 释放锁流程

```python
async def release_lock(self, session_id: str, subagent_type: Optional[str] = None) -> bool:
    """
    释放会话持有的锁
    
    释放后自动唤醒队列头部的等待者
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. 查找并删除锁
        if subagent_type:
            cursor.execute("""
                DELETE FROM agent_task_locks
                WHERE session_id = %s AND subagent_type = %s
                RETURNING tenant_id, subagent_type
            """, (session_id, subagent_type))
        else:
            cursor.execute("""
                DELETE FROM agent_task_locks
                WHERE session_id = %s
                RETURNING tenant_id, subagent_type
            """, (session_id,))
        
        rows = cursor.fetchall()
        conn.commit()
        
        # 2. 对每个释放的锁，唤醒队列中的下一个等待者
        for row in rows:
            self._wake_up_next_waiter(row["tenant_id"], row["subagent_type"])
        
        return len(rows) > 0
```

### 4.4 排队与唤醒机制

```python
def _enqueue_for_wait(
    self, tenant_id: str, subagent_type: str, session_id: str, user_id: str
) -> QueueResult:
    """进入排队队列"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. 计算新位置 = 当前队列长度
        cursor.execute("""
            SELECT COALESCE(MAX(position), -1) + 1 AS next_pos
            FROM agent_task_queue
            WHERE tenant_id = %s AND subagent_type = %s AND status = 'waiting'
        """, (tenant_id, subagent_type))
        position = cursor.fetchone()["next_pos"]
        
        # 2. 插入队列
        queue_id = f"queue_{uuid.uuid4().hex[:12]}"
        cursor.execute("""
            INSERT INTO agent_task_queue (
                queue_id, tenant_id, subagent_type, session_id, user_id,
                position, wait_timeout_at, status
            ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP + '30 minutes', 'waiting')
        """, (queue_id, tenant_id, subagent_type, session_id, user_id, position))
        
        conn.commit()
        
        # 3. 估算等待时间
        estimated_wait = self._estimate_wait_time(tenant_id, subagent_type, position)
        
        # 4. 获取当前队列长度
        cursor.execute("""
            SELECT COUNT(*) AS total FROM agent_task_queue
            WHERE tenant_id = %s AND subagent_type = %s AND status = 'waiting'
        """, (tenant_id, subagent_type))
        total_length = cursor.fetchone()["total"]
        
        return QueueResult(
            queue_id=queue_id,
            position=position,
            total_length=total_length,
            estimated_wait=estimated_wait
        )

def _wake_up_next_waiter(self, tenant_id: str, subagent_type: str) -> Optional[str]:
    """
    唤醒队列头部的等待者
    
    注意：这里只是"标记"为可唤醒，并不真正推送通知。
    前端通过轮询 /api/chat/queue-status 检查是否到号了。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 找到队列头部的第一个等待者
        cursor.execute("""
            SELECT queue_id, session_id FROM agent_task_queue
            WHERE tenant_id = %s AND subagent_type = %s AND status = 'waiting'
            ORDER BY position
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (tenant_id, subagent_type))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        # 标记为 processing（表示可以开始了）
        cursor.execute("""
            UPDATE agent_task_queue
            SET status = 'processing', updated_at = CURRENT_TIMESTAMP
            WHERE queue_id = %s
        """, (row["queue_id"],))
        
        conn.commit()
        
        # 记录日志（可用于后续推送通知）
        logger.info(f"Woke up session {row['session_id']} from queue")
        return row["session_id"]
```

---

## 五、API 设计

### 5.1 聊天接口改造（现有接口）

**POST /api/chat/stream**

**新增响应头：**

```
X-Queue-Position: 3          # 当前排队位置（未排队时不返回）
X-Queue-Length: 5            # 队列总长度
X-Estimated-Wait: 120        # 预计等待秒数
X-Lock-Id: lock_abc123       # 持锁成功时返回锁ID
```

**排队时的响应体（HTTP 429）：**
```json
{
    "success": false,
    "error": "All instances are busy, please wait in queue",
    "queue_info": {
        "position": 3,
        "total_length": 5,
        "estimated_wait_seconds": 120,
        "check_interval_seconds": 5
    }
}
```

### 5.2 队列状态查询接口（新增）

**GET /api/chat/queue-status?session_id=xxx**

```json
{
    "success": true,
    "data": {
        "status": "waiting",      // waiting / ready / expired / not_in_queue
        "position": 2,
        "total_length": 4,
        "estimated_wait_seconds": 90,
        "subagent_type": "trade-specialist"
    }
}
```

**status 说明：**
- `waiting`：仍在排队中
- `ready`：已到号，可以开始聊天
- `expired`：排队超时已失效
- `not_in_queue`：不在队列中

### 5.3 主动取消排队（新增）

**DELETE /api/chat/queue?session_id=xxx**

```json
{
    "success": true,
    "cancelled": true
}
```

### 5.4 管理员接口（新增）

**GET /api/saas/concurrency/status**

```json
{
    "success": true,
    "data": {
        "total_active_locks": 156,
        "total_waiting_in_queue": 42,
        "tenants_with_queue": 12,
        "busiest_subagent": "trade-specialist",
        "average_wait_seconds": 68
    }
}
```

**GET /api/saas/concurrency/tenant/{tenant_id}**

```json
{
    "success": true,
    "data": {
        "tenant_id": "tenant_abc",
        "subagents": [
            {
                "type": "trade-specialist",
                "instance_quota": 2,
                "active_locks": 2,
                "queue_length": 3,
                "locks": [
                    {
                        "lock_id": "tenant_abc:trade-specialist:0",
                        "session_id": "sess_xxx",
                        "user_id": "user_yyy",
                        "acquired_at": "2026-04-29T09:15:00",
                        "held_seconds": 320
                    }
                ],
                "queue": [
                    {"position": 0, "session_id": "...", "wait_seconds": 180}
                ]
            }
        ]
    }
}
```

**POST /api/saas/concurrency/force-release/{lock_id}**

```json
{
    "success": true,
    "released": true
}
```

---

## 六、前端集成方案

### 6.1 排队 UI 组件

```vue
<!-- frontend/src/components/ChatQueueStatus.vue -->
<template>
  <div class="queue-status" v-if="isQueued">
    <div class="queue-card">
      <div class="queue-icon">
        <ClockIcon />
      </div>
      <div class="queue-info">
        <h3>您正在排队中</h3>
        <p class="position">第 {{ position }} 位 / 共 {{ totalLength }} 人等待</p>
        <p class="estimate">预计等待约 {{ formatTime(estimatedWait) }}</p>
        <div class="progress-bar">
          <div class="progress" :style="{ width: progressPercent + '%' }"></div>
        </div>
        <button @click="cancelQueue" class="cancel-btn">
          取消排队
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const isQueued = ref(false)
const position = ref(0)
const totalLength = ref(0)
const estimatedWait = ref(0)

let pollTimer: number | null = null

async function checkQueueStatus() {
  const res = await fetch(`/api/chat/queue-status?session_id=${sessionId}`)
  const data = await res.json()
  
  if (data.data.status === 'ready') {
    // 到号了！开始聊天
    isQueued.value = false
    startChat()
  } else if (data.data.status === 'expired') {
    // 排队超时
    isQueued.value = false
    showToast('排队已超时，请重新发起')
  } else if (data.data.status === 'waiting') {
    // 更新状态
    position.value = data.data.position
    totalLength.value = data.data.total_length
    estimatedWait.value = data.data.estimated_wait_seconds
  }
}

onMounted(() => {
  // 每5秒轮询一次
  pollTimer = window.setInterval(checkQueueStatus, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
```

### 6.2 聊天流程

```
用户输入消息 → 点击发送
    ↓
调用 /api/chat/stream
    ↓
┌─ 响应 200？ → 正常开始 SSE 流式输出
│
└─ 响应 429？ → 显示排队UI
         ↓
    轮询 /api/chat/queue-status
         ↓
    ┌─ status=ready？ → 重新调用 /api/chat/stream
    └─ status=waiting？ → 继续轮询
```

---

## 七、后台定时任务

### 7.1 锁超时清理

```python
# src/saas/tasks/concurrency_cleanup.py

async def cleanup_expired_locks():
    """清理过期的锁，每5分钟执行一次"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 查找过期锁
        cursor.execute("""
            DELETE FROM agent_task_locks
            WHERE expires_at < CURRENT_TIMESTAMP
            RETURNING tenant_id, subagent_type, lock_id
        """)
        
        expired = cursor.fetchall()
        conn.commit()
        
        # 唤醒每个被释放锁的下一位等待者
        for row in expired:
            _wake_up_next_waiter(row["tenant_id"], row["subagent_type"])
        
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired locks")

async def cleanup_expired_queue_items():
    """清理超时的排队项，每10分钟执行一次"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM agent_task_queue
            WHERE wait_timeout_at < CURRENT_TIMESTAMP
              AND status = 'waiting'
        """)
        count = cursor.rowcount
        conn.commit()
        
        if count > 0:
            logger.info(f"Cleaned up {count} expired queue items")
```

### 7.2 心跳刷新机制

**方案：** 在 SSE 流式输出期间，每次发送一个消息块就刷新一次锁的 heartbeat。

```python
# 在 /api/chat/stream 的 SSE generator 中
async def event_generator():
    try:
        for chunk in agent_response:
            yield f"data: {json.dumps(chunk)}\n\n"
            
            # 每发送 N 个 chunk 刷新一次锁（或者每30秒刷新一次）
            if should_refresh_heartbeat():
                ConcurrencyControlService.refresh_heartbeat(lock_id)
    finally:
        # 对话结束，释放锁
        await ConcurrencyControlService.release_lock(session_id, subagent_type)
```

---

## 八、实施计划（5天）

### Day 1：数据库 + 基础服务

| 任务 | 说明 |
|------|------|
| ✅ 创建 agent_task_locks 表 | 锁存储表 |
| ✅ 创建 agent_task_queue 表 | 队列存储表 |
| ✅ 编写 ConcurrencyControlService 骨架 | 基础 CRUD 方法 |
| ✅ 实现 acquire_lock 核心逻辑 | 数据库级原子锁获取 |

### Day 2：排队机制

| 任务 | 说明 |
|------|------|
| ✅ 实现 _enqueue_for_wait 方法 | 入队逻辑 |
| ✅ 实现 _wake_up_next_waiter 方法 | 唤醒逻辑 |
| ✅ 实现 release_lock 方法 | 释放锁 + 自动唤醒 |
| ✅ 实现队列状态查询 | 查询排队位置 |

### Day 3：API 集成

| 任务 | 说明 |
|------|------|
| ✅ 改造 /api/chat/stream 接口 | 集成并发检查 |
| ✅ 新增队列状态查询 API | GET /api/chat/queue-status |
| ✅ 新增取消排队 API | DELETE /api/chat/queue |
| ✅ 新增管理员监控 API | 并发状态查询、强制释放 |

### Day 4：前端 + 定时任务

| 任务 | 说明 |
|------|------|
| ✅ 前端排队 UI 组件 | ChatQueueStatus.vue |
| ✅ 前端聊天流程改造 | 排队轮询逻辑 |
| ✅ 后台定时清理任务 | 过期锁、超时队列清理 |
| ✅ 心跳刷新机制 | 长对话期间保持锁有效 |

### Day 5：测试 + 优化

| 任务 | 说明 |
|------|------|
| ✅ 单元测试 | 各种边界场景 |
| ✅ 并发压测 | 多线程同时抢锁 |
| ✅ 集成测试 | 完整聊天排队流程 |
| ✅ 性能优化 | 慢查询优化、索引优化 |

---

## 九、测试计划

### 9.1 单元测试

| 测试用例 | 说明 |
|----------|------|
| test_acquire_lock_success | 有可用槽位时成功获锁 |
| test_acquire_lock_quota_exceeded | 配额用完时进入排队 |
| test_acquire_lock_reentrant | 同会话再次请求自动重入 |
| test_release_lock | 释放锁后槽位可用 |
| test_queue_fifo_order | 队列严格FIFO顺序 |
| test_wake_up_next | 释放锁后唤醒下一位 |
| test_lock_expiry_cleanup | 过期锁自动清理 |

### 9.2 并发压测

```python
# tests/load/test_concurrency_stress.py

async def test_100_users_compete_for_2_slots():
    """
    100个用户同时抢2个槽位
    预期：2人立刻获得，98人排队
    验证：没有超发（>2人同时获得锁）
    """
    pass
```

### 9.3 集成测试

| 场景 | 步骤 |
|------|------|
| 完整排队体验 | A获锁 → B排队 → A释放 → B获锁 |
| 取消排队 | 用户排队中取消，队列位置自动前移 |
| 异常场景 | 用户直接关闭页面 → 锁超时自动释放 |
| 多worker竞态 | 2个进程同时抢锁 → 只有1个成功 |

---

## 十、边界场景与处理

| 场景 | 问题 | 解决方案 |
|------|------|---------|
| **用户直接关闭页面** | 锁永远不释放 | 锁超时机制（默认1小时）+ 心跳刷新（活跃对话自动续期） |
| **排队太久用户走了** | 队列积累过期项 | 排队超时（默认30分钟）+ 定时清理 |
| **应用重启** | 内存中的锁状态丢失 | 全部锁存在数据库，重启不影响 |
| **数据库主从延迟** | 刚释放锁就查询可能还显示存在 | 锁查询只查主库，不走从库 |
| **同一个用户多标签页** | 同一人占用多个槽位 | 可选：按 user_id 限制（需求确认） |
| **子智能体类型变更** | 排队中租户配额变了 | 配额增加时自动唤醒更多人；配额减少时不强制踢出已持锁用户 |

---

## 十一、监控与告警

### 关键指标

| 指标 | 告警阈值 | 说明 |
|------|---------|------|
| 平均排队时间 > 5分钟 | WARN | 用户体验下降 |
| 平均排队时间 > 10分钟 | ERROR | 严重拥堵 |
| 锁超时释放率 > 10% | WARN | 可能有异常断开问题 |
| 队列深度 > 20 | WARN | 某个子智能体特别繁忙 |

### 日志规范

```python
# 持锁
logger.info(f"[Concurrency] Lock acquired: {lock_id}, session: {session_id}")

# 释放
logger.info(f"[Concurrency] Lock released: {lock_id}, held: {held_seconds}s")

# 入队
logger.info(f"[Concurrency] Enqueued: {session_id}, pos: {position}")

# 唤醒
logger.info(f"[Concurrency] Woke up: {session_id}")
```

---

## 十二、后续优化方向

| 优化 | 说明 | 优先级 |
|------|------|--------|
| WebSocket 主动推送 | 替代轮询，实时唤醒 | P2 |
| 优先级排队 | VIP用户优先 | P3 |
| 预估等待时间AI预测 | 基于历史数据预测而非简单平均 | P3 |
| 按 user_id 限制 | 同一用户最多占N个槽位 | P2 |
| Redis 锁替代 | 性能更高（但引入Redis依赖） | P3 |

---

## 附录：涉及文件完整清单

### 新增文件

| 文件路径 | 说明 |
|----------|------|
| `src/saas/services/concurrency_control.py` | 核心并发控制服务 |
| `src/saas/api/concurrency.py` | 并发管理 API |
| `src/saas/tasks/concurrency_cleanup.py` | 定时清理任务 |
| `frontend/src/components/ChatQueueStatus.vue` | 排队UI组件 |
| `frontend/src/api/concurrency.ts` | 前端API封装 |

### 修改文件

| 文件路径 | 改动 |
|----------|------|
| `deploy/init-postgres.sql` | 新增2张表 + 索引 |
| `deploy/db_update.sql` | 数据库变更脚本 |
| `src/main.py` | 集成并发检查到 chat 接口 |
| `src/saas/permissions/checker.py` | 增加 get_instance_quota 方法 |
| `frontend/src/views/Chat.vue` | 集成排队逻辑 |

---

**方案完稿，等待评审后即可开始实施！**
