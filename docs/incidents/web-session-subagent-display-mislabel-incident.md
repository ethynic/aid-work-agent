# Web 会话 subagent_id 为空显示「主智能体」复盘

> **发现时间**：2026-10-16（用户报告：2026-09-07 20:33 会话显示「主智能体」，但租户 tenant_e32fb6c2e00a 仅授权「在线电商客服」）
> **影响**：仅网页端会话列表/筛选的智能体**显示错标**；实际执行智能体、计费归属、租户隔离均正确，无越权执行。

---

## 1. 问题现象

网页端会话页面（`/t/{tenant_id}/web-sessions`）中，2026-08-27 ~ 09-07 期间的部分会话智能体标签显示为「主智能体」，而该租户只应出现「在线电商客服」（after-sales）。

## 2. 排查结论：显示错标，执行层无问题

以 09-07 会话 `session_f736a2874cea` 为例（生产 243 库 + 旧机 254 归档日志 `aid-work-agent_20260907.log`）：

```
20:29:06 Chat session created: ... tenant: tenant_e32fb6c2e00a, subagent: None
20:29:06 [AgentRouter] Created standalone agent: session_f736a2874cea:after-sales
20:29:06 [AGENT] LLM call starting ..., is_master=False
```

- `chat_sessions.subagent_id` 落库为空 → 前端按空值显示「主智能体」
- 执行层实际路由到 **after-sales 独立 Agent**（`_resolve_default_subagent` 自动路由，src/main.py:48），`is_master=False`
- `chat_records` 计费正确挂在 `tenant_e32fb6c2e00a`

## 3. 根因

会话由**首条消息隐式创建**：`chat_stream` 内 `SessionDB.create(...)`（src/main.py:1297）**不传 `subagent_id`**；而前端「新会话」按钮走 `POST /api/chat/sessions`（src/api/session.py:117）会带 `subagent_id`。08-27 ~ 09-07 窗口期前端某入口直接发消息未先建会话，落入隐式路径。同一用户 08-25 前与 09-15 后的会话均有正确 `after-sales`，09-15 生产迁移新机后窗口自然关闭。

## 4. 复核（2026-10-16）：问题已不存在

| 环境 | 范围 | 结果 |
|------|------|------|
| 生产 243 | 最近 1 天 web 会话 | 0 条 subagent_id 为空 |
| 测试 254 | 最近 7 天 web 会话 | 21 条为空：19 条单元测试残留（tenant_id 为 NULL 的 fixture，可忽略）；2 条是 platform_admin 在多智能体租户 tenant_1dc997a1806b（6+ 个智能体）主动用主智能体聊天，属合法行为（`default_single` 路由仅对「租户仅 1 个可用智能体」生效） |

规律：普通租户用户会话从不出现空值；历史上出现空值的均为 platform_admin 代租户聊天场景。

## 5. 残余风险（新增聊天入口必读）

隐式建会话路径（src/main.py:1297）**至今仍不写 `subagent_id`**，现行由两处兜住：

1. 前端 `ChatContainer.handleSend`（ChatContainer.vue:566~579）发送前保证会话已创建且带 `subagent_id`
2. 执行侧自动路由兜底：
   - 旧循环 `_resolve_default_subagent`（src/main.py:48）
   - Runner `default_single` 路由（src/services/agent_runner/profiles.py:14 `default_profile`，租户仅 1 个可用智能体时自动路由）

**因此该缺陷只表现为显示错标，不影响执行与计费。** 但若未来新增绕过「先建会话」的聊天入口（新前端页面、桌面端、嵌入式对话等），错标会复现。新增聊天入口时的检查要点：

- [ ] 发送首条消息前是否先经 `POST /api/chat/sessions` 建会话并带 `subagent_id`？
- [ ] 若走隐式路径，是否在解析出实际执行的 subagent 后回写 `chat_sessions.subagent_id`？
