# 对话上下文重建避坑速查

> 本文件是 wecom_kf 上下文丢失两次复盘(详见 [wecom-kf-context-loss-research.md](../incidents/wecom-kf-context-loss-research.md) §9~§10)提炼的**高频陷阱**。修改「消息历史加载 / 上下文重建 / 窗口裁剪」相关代码前先看这里。

## 坑 1：「最近 N 条」窗口写反了(最常见)

```sql
-- ❌ 错：取的是最老的 N 条
SELECT * FROM messages WHERE session_id=%s ORDER BY id ASC LIMIT N

-- ✅ 对：子查询取最近 N 条，再正序
SELECT * FROM (
    SELECT * FROM messages WHERE session_id=%s ORDER BY id DESC LIMIT N
) t ORDER BY id ASC
```

**症状**：长会话(消息数 > N)一上线就「遗忘最近一轮、重复提问」；短会话从不触发，所以能潜伏很久。
**涉及**：`MessageDB.list_by_session`(web)、`ChannelSessionManager.get_messages`(渠道)。

## 坑 2：读取侧靠 fallback 分发，被陈旧数据劫持

```python
# ❌ 错：表A 有数据就用A，否则用B
if db_messages_from_A: use(A)
else: use(B)
```

数据迁移期同一主键可能跨表都有数据(旧表残留 + 新表实时)，fallback 会让陈旧旧表数据屏蔽新表真实对话。
**正解**：按**会话来源**显式分流——`ChannelSessionManager.is_channel_session(session_id)`(查 `channel_sessions` 登记表)判定，渠道读 `channel_messages`、web 读 `chat_messages`，**严格分离，不靠 fallback**。

## 坑 3：裁剪后窗口开头不是 user → LLM API 400

截断边界可能落在 assistant 甚至 tool 结果上。DeepSeek/OpenAI 兼容 API 要求序列首条(system 之后)必须是 user，否则报 400。
**正解**：`Agent._reorder_messages_for_llm` 末尾已做「丢弃开头非 user 直到第一条 user」。改裁剪逻辑时别破坏这个对齐。

## 坑 4：web 与 channel 是两个地方，读写都得分开

| 来源 | 写入表 | 读取表 |
|------|--------|--------|
| web 端 | `chat_messages` | `chat_messages` |
| 渠道(wecom_kf/wecom/dingtalk/feishu) | `channel_messages` | `channel_messages` |

**渠道消息绝不能写/读 `chat_messages`，反之亦然。** session_id 格式也不同(web=`session_/web_/cli_`，渠道=`tenant_{tid}_{channel}_{user}_{subagent}`)。

## 坑 5：默认值多处漂移

同一阈值在 `configs/config.yaml` + `src/config/settings.py` + 类默认值三处都有，**改一处漏其他 → 行为不一致**(本次踩过)。改配置类阈值时三处一起改。

---

**一句话**：上下文重建 = ① 按来源读对表(坑2/4) ② 取最近N条(坑1) ③ 开头对齐user(坑3) ④ 默认值同步(坑5)。少一个都会「长会话/迁移后」才暴露。
