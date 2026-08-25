# 招聘面试邀约企微通知设计（两点式：邀约前知会 + 邀约后通报）

> 状态：🔧 Phase 1 开发完成（2026-08-19）
> 背景：邀约面试是需要相关负责人的关键动作——发起前让群里知晓并有机会拦，
> 完成后通报邀了谁、时间安排；筛选/打招呼/发消息属日常操作，通知=骚扰，不发。
> 2026-08-19 开发期设计调整：原「事后通报由 interview_demo 工具层全自动内嵌触发」
> 不可行——boss_interview_demo 入参只有 remark（无候选人名/日期，日期固定明天只填
> 不发送），候选人名只存在于 agent 对话上下文。因此事前知会与事后通报统一为
> `boss_interview_notify` 一个工具的两种模式 kind=pre|done，均由 SUBAGENT 链路规定
> 调用（见 §2 调整说明）。

## 0. 决策记录（为什么只做面试两点式）

- 打招呼/发消息高频且低风险，逐条通知或汇总推送都只会让人关掉群通知——用户明确「其他都没必要发」。
- 面试邀约低频、高价值、占用面试官资源——事前知会（有异议线下拦）+ 事后通报（面试官知道自己什么时候有面）是真实管理动作。
- 事前通知是**知会式非阻塞**（企微群机器人是单向通道，无法在企微里回复确认；「是否邀约」的确认仍在 agent 对话里由操作人完成，企微只是同步给相关负责人）。

## 1. 两条消息（全部企微群机器人，markdown）

**① 邀约前知会**（`boss_interview_notify` 工具发出，SUBAGENT 在用户同意邀面后、执行邀约前调用）：

```markdown
【面试邀约知会】PHP开发工程师
招聘智能体拟邀约以下候选人面试：
> 陈远健 · 90分 · 5年电商后端 Yii2/ThinkPHP/MySQL/Redis
> 常晓飞 · 88分 · 7年 PHP 开发经验
拟安排时间：8月20日（周三）15:00 / 8月21日（周四）10:00
操作人：{user}。如对邀约有异议请尽快联系；无异议将按计划发出邀约。
```

**② 邀约后通报**（`boss_interview_notify(kind=done)`，SUBAGENT 在 interview_demo 逐人完成后调用）：

```markdown
【面试邀约已完成】PHP开发工程师
已向以下候选人发出面试邀约：
> 陈远健 · 8月20日（周三）15:00
> 常晓飞 · 8月21日（周四）10:00
请面试官及相关同事留意日程与后续沟通。
```

@提醒：企微 markdown 不支持 @，at_mobiles 非空时补发一条 text 消息携带 @（仅事后通报需要；事前知会默认不 @，避免打扰）。

## 2. 触发实现（一个工具两种模式，均由 SUBAGENT 链路规定调用）

| 消息 | 触发方式 | 可靠性设计 |
|---|---|---|
| ① 事前知会 | 云端工具 `boss_interview_notify(kind=pre)`（纯云端执行，同 boss_jobs_list 混合模式，不碰设备）：入参 kind/job_name/candidates[{name,score,highlight,time}]（1-10 人）/note? | SUBAGENT 链路硬性规定「用户同意邀面 → 先 notify(pre) → 再 interview_demo」；notify **失败不阻塞邀约**（工具仍返回 success，message 说明原因，agent 转告用户后继续）——知会是流程增强，不是闸门 |
| ② 事后通报 | `boss_interview_notify(kind=done)`（同一工具的第二种模式），SUBAGENT 在 interview_demo 逐人完成后调用，传实际名单+面试时间。**调整说明（2026-08-19）**：原设计为 interview_demo 云端 execute 成功前 fire-and-forget 全自动内嵌——但 boss_interview_demo 入参只有 remark（无候选人名/日期，日期固定明天只填不发送），候选人名只存在于 agent 对话上下文，工具层拿不到名单，全自动不可行，改为链路规定调用 | 失败同样不阻塞（留痕 failed 可手动补推）；推送故障绝不影响邀约工具返回 |

## 3. 租户配置（照 work_report_preferences 范式，最小集）

新表 `bs_recruiting_notify_settings`（tenant_id 唯一）：

| 字段 | 说明 |
|---|---|
| enabled | 总开关，默认 false（未配置不发，避免空跑） |
| webhook_url | 企微机器人 webhook（**Fernet 加密存储，API 掩码返回**，沿 channel_config 范式） |
| at_mobiles | 事后通报 @人 手机号 JSON 数组，默认 [] |
| pre_notify_enabled | 事前知会开关（默认 true；有人嫌吵可只留事后） |

## 4. 发送与留痕

- 新建 `src/services/wecom_bot.py`：`send_markdown(url, content)` / `send_text(url, content, at_mobiles)`——POST `qyapi.weixin.qq.com/cgi-bin/webhook/send`；markdown >2048 字节截断续发；errcode 校验；失败重试 1 次。
- 通知留痕表 `bs_recruiting_notify_logs`（tenant_id/kind=pre|done/candidates JSONB/content/push 状态与错误/created_at）——可查可补推（补推走手动 API）。
- SUBAGENT.md 面试章节补两步流程；`boss_interview_notify` 进受信清单与 tools.allowed。

## 5. Phase 划分

| Phase | 内容 | 验收 |
|---|---|---|
| 1 核心闭环 | wecom_bot 发送；settings 表 + notify_logs 表；`boss_interview_notify` 工具 pre/done 两模式（受信清单/SUBAGENT 规则）；手动补推 API | 真机：对话走「同意邀面 → 群里收到事前知会 → 邀约完成 → 群里收到事后通报（名单+时间）」两条消息；关闭 enabled 后全静默；webhook 故障不影响邀约执行 |
| 2 配置界面 | 招聘操作页「通知设置」卡（webhook/开关/@人，掩码显示）+ 通知记录查看 | typecheck + 手动验收 |
| 3 可选增强 | 钉钉/飞书机器人同模板复用；邀约改期/取消的第三类通知 | 按需 |

## 6. 明确不做

- **不做任何筛选/打招呼/发消息的企微推送**（用户定调：发了就是骚扰）
- 不做定时日报/周报/成果汇总推送（前两版方案废弃，决策记录见 §0）
- 不做企微侧回复确认/审批流（群机器人单向；确认在对话里完成）
- 不依赖 LLM 自由发挥通知内容（pre/done 均为链路规定调用 + 固定模板，LLM 只负责从对话上下文带出名单与时间）
