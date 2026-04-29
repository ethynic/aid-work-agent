# 并发控制边缘场景解决方案

> 版本: v1.0
> 创建日期: 2026-04-29
> 状态: 待评审

---

## 问题一：会话何时结束？槽位如何释放？

### 1.1 问题描述

```
时间线：
10:00  用户A提问 → 智能体思考
10:01  智能体回答完毕 ✓
   ↓
(用户A去喝咖啡，中间29分钟没有操作)
   ↓
10:30  用户A回来继续追问
```

**问题：10:01 - 10:30 这29分钟，实例应该继续被用户A占用吗？**

| 方案 | 优点 | 缺点 |
|------|------|------|
| ❌ 一直占用到用户手动退出 | 不会打断对话流程 | 严重浪费资源，一个用户聊5分钟占一下午 |
| ❌ 回答完立即释放 | 资源利用率最高 | 体验糟糕：用户刚想追问，发现需要重新排队 |
| ⭕ **"活跃对话窗口"方案** | 平衡体验和资源 | 需要精心设计超时时间 |

---

### 1.2 推荐方案：活跃对话窗口

#### 核心思想

> **智能体回答完毕后，不立即释放锁，保留一个"思考时间窗口"。**
> 窗口内用户继续说话 → 刷新窗口；窗口内无操作 → 自动释放锁。

```
用户提问
    ↓
🔒 锁定开始
    ↓
智能体回答完毕
    ↓
⏱️ 启动【3分钟思考计时器】
    ├─ 2分钟时用户追问 → ✅ 刷新计时器，重新计时3分钟
    └─ 3分钟到了无操作 → 🔓 自动释放锁
```

#### 状态机设计

```
          用户提问
             │
             ▼
     ┌───────────────┐
     │   锁定中       │
     │  (思考中)      │
     └───────┬───────┘
             │
      智能体回答完毕
             │
             ▼
     ┌───────────────┐
     │  思考窗口中    │ ◄────────┐
     │  (3分钟倒计时) │          │
     └───────┬───────┘          │
             │                  │
             ├─ 用户追问 → 刷新计时 ┘
             │
         3分钟超时
             │
             ▼
     ┌───────────────┐
     │   释放锁       │
     │   (空闲)       │
     └───────────────┘
```

#### 具体实现

```python
# src/saas/services/instance_service.py

def refresh_lock(self, instance_id: str, session_id: str, window_minutes: int = 3) -> bool:
    """
    刷新思考窗口（每次智能体回答后调用）
    
    将锁过期时间重置为 "当前时间 + window_minutes"
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE agent_instances
            SET lock_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval,
                updated_at = CURRENT_TIMESTAMP
            WHERE instance_id = %s AND current_session_id = %s
            RETURNING instance_id
        """, (window_minutes, instance_id, session_id))
        
        success = cursor.fetchone() is not None
        conn.commit()
        
        if success:
            logger.info(f"Lock refreshed: {instance_id}, window = {window_minutes}min")
        return success
```

**调用时机：**

```python
# 在 /api/chat/stream 的 SSE 输出中
async def event_generator():
    full_response = ""
    async for chunk in agent_response:
        yield chunk
        full_response += chunk
    
    # ✅ 智能体回答完毕，刷新思考窗口
    InstanceService.refresh_lock(instance_id, session_id, 3)
```

#### 时间参数选择

| 窗口时长 | 体验 | 资源利用率 |
|---------|------|-----------|
| 1分钟 | ⭐⭐⭐⭐⭐ 高 | ⭐⭐⭐⭐⭐ 很高 |
| **3分钟** | ⭐⭐⭐⭐ 好 | ⭐⭐⭐⭐ 好 | ✅ 推荐
| 5分钟 | ⭐⭐⭐ 较好 | ⭐⭐⭐ 一般 |
| 10分钟 | ⭐⭐ 宽松 | ⭐⭐ 低 |

**3分钟是黄金平衡点**：既给了用户充分的思考时间，又不会浪费太久。

#### 前端提示优化

当锁即将过期时（剩余<30秒），前端显示温柔提示：

```
💡 温馨提示：您已经3分钟没有说话了，如果您想继续对话请尽快回复，
否则实例将在 30秒 后自动释放给其他人使用。
```

如果用户在倒计时中输入了文字，自动续期。

---

### 1.3 用户回来后的处理（锁已被释放）

用户10:30回来时发现锁被释放了，三种处理方式：

| 方案 | 体验 | 说明 |
|------|------|------|
| **A. 静默重新获取锁** | ⭐⭐⭐⭐⭐ | 用户发送消息时自动尝试重新锁定。如果空闲则直接获得，如果被占用则进入排队。用户无感知。 |
| **B. 提示用户重新确认** | ⭐⭐⭐ | 发送按钮变成"重新获取实例"，用户点击后锁定。有点打断但透明。 |
| **C. 会话迁移** | ⭐⭐⭐⭐ | 如果原实例被B占用，让用户选择"等B用完"或"切换到其他空闲实例"。复杂但灵活。 |

**推荐方案 A（静默重锁）：**

```javascript
// 前端发送消息时的伪代码
async function sendMessage() {
    try {
        await callChatAPI(message)
    } catch (error) {
        if (error.code === 'LOCK_EXPIRED') {
            // 🔄 锁过期了，静默尝试重新锁定
            const lockResult = await tryLockInstance(instanceId, sessionId)
            if (lockResult.success) {
                // ✅ 重新获得锁了，自动重发
                return await callChatAPI(message)
            } else if (lockResult.isQueued) {
                // 😅 实例被别人占了，进入排队
                showQueueModal(lockResult)
            }
        }
    }
}
```

用户体验：**完全无感知**，就好像实例一直是他的一样。只有当实例真被别人占用了才需要排队。

---

## 问题二：同一用户多设备登录时的自占用

### 2.1 问题描述

```
用户A在PC端登录 → 开始使用"外贸小明"
  ↓
(智能体还在思考问题中)
  ↓
用户A在手机端登录 → 查看实例列表
  ↓
看到"外贸小明"显示：🟡 忙碌中，被【用户A】占用 😅
```

**问题：用户看到自己占着自己，感觉有点奇怪。**

---

### 2.2 推荐方案：同用户会话迁移 + 状态显示优化

#### 三层优化

##### 第一层：状态显示优化

```diff
- 🟡 忙碌中，被【用户A】占用
+ 🟡 忙碌中，正在您的另一台设备上对话
+ [ 点击这里 → 切换到本设备继续对话 ]
```

**逻辑：** 当 `current_user_id == 当前登录用户ID` 时，显示不同文案。

```python
# 构造实例列表响应时
for instance in instances:
    if instance["current_user_id"] == current_user_id:
        instance["status_text"] = "忙碌中，正在您的另一台设备上对话"
        instance["can_take_over"] = True  # 显示"接管"按钮
    else:
        instance["status_text"] = f"忙碌中，{current_user_name} 正在使用"
        instance["can_take_over"] = False
```

##### 第二层：一键接管功能

用户在手机端点击"接管到本设备"：

```
PC端：外贸小明正在回答
  ↓
手机端点击【接管到本设备】
  ↓
1. PC端的锁被释放
2. 手机端获得锁
3. PC端显示："此对话已在另一台设备上接管"
4. 手机端加载历史消息，可以继续聊天
```

**实现代码：**

```python
def take_over_instance(self, instance_id: str, new_session_id: str, user_id: str) -> bool:
    """
    同一用户将实例从一台设备接管到另一台设备
    
    注意：只有实例的当前持有者才能执行此操作
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. 验证确实是同一用户
        cursor.execute("""
            SELECT current_session_id FROM agent_instances
            WHERE instance_id = %s AND current_user_id = %s
        """, (instance_id, user_id))
        
        row = cursor.fetchone()
        if not row:
            return False
        
        old_session_id = row["current_session_id"]
        
        # 2. 更新会话绑定（原子操作）
        cursor.execute("""
            UPDATE agent_instances
            SET 
                current_session_id = %s,
                lock_expires_at = CURRENT_TIMESTAMP + '3 minutes',
                updated_at = CURRENT_TIMESTAMP
            WHERE instance_id = %s AND current_user_id = %s
            RETURNING instance_id
        """, (new_session_id, instance_id, user_id))
        
        success = cursor.fetchone() is not None
        conn.commit()
        
        if success:
            logger.info(f"Instance {instance_id} taken over: {old_session_id} → {new_session_id}")
            
            # 3. 通知旧会话（PC端）：已被接管
            # 通过 SSE 发送特殊事件
            sse_manager.send_to_session(old_session_id, {
                "type": "session_taken_over",
                "message": "此对话已在另一台设备上接管"
            })
        
        return success
```

##### 第三层：同用户的会话合并

接管时，把旧 session 的聊天历史"迁移"到新 session：

```python
def merge_session_history(old_session_id: str, new_session_id: str) -> bool:
    """
    合并两个会话的历史消息，将旧会话的消息归属到新会话
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 将旧消息的 session_id 更新为新会话
        cursor.execute("""
            UPDATE chat_messages
            SET session_id = %s, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
        """, (new_session_id, old_session_id))
        
        conn.commit()
        
        # 旧会话标记为已合并（可选）
        cursor.execute("""
            UPDATE chat_sessions
            SET merged_to = %s, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
        """, (new_session_id, old_session_id))
        conn.commit()
        
        return True
```

这样用户在手机端可以看到完整的聊天历史，无缝衔接。

---

### 2.3 接管流程时序图

```
PC端                             手机端                       数据库
 │                                │                              │
 │──发消息──────────────────────>│                              │
 │<──流式输出────────────────────│                              │
 │                                │──打开实例列表────────────> │
 │                                │<──返回列表──────────────────│
 │                                │  (显示"可接管")              │
 │                                │                              │
 │                                │──点击"接管"──────────────> │
 │                                │                              │──更新session_id
 │                                │<──返回成功──────────────────│
 │                                │                              │
 │<──发送"已接管事件"─────────────│                              │
 │  (提示用户切换)                │                              │
 │                                │──加载历史────────────────> │
 │                                │<──返回历史──────────────────│
 │                                │                              │
 │                                │ ✅ 无缝继续聊天              │
```

---

## 总结

| 问题 | 解决方案 | 体验评分 |
|------|---------|---------|
| **槽位何时释放** | 3分钟思考窗口 + 过期静默重锁 | ⭐⭐⭐⭐⭐ 几乎无感知 |
| **多设备自占用** | 状态文案优化 + 一键接管 + 会话合并 | ⭐⭐⭐⭐⭐ 无缝跨设备 |

两个方案都是**体验升级**而非降级，虽然增加了一些代码逻辑，但带来的用户体验提升是非常值得的！
