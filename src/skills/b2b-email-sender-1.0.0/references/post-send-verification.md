# 发送后逐封送达核验（必须做）

> **优先级最高**：铁律禁止的是**发送前**探测发件邮箱是否可用
> （查密码环境变量、SMTP 登录测试、自动 DNS 体检），**不是**发送后核验结果。
> 下面每一项在发送后都照做，一条都不能省。

## 1. 核验顺序

```bash
SK=<b2b-email-sender 技能目录>
# ① 打开数据回拉（追踪像素 24/7 累积，幂等可天天跑；写回 emails.open_count）
python "$SK/scripts/fetch_opens.py" --from-email <from_email> --campaign-id <N>

# ② 战绩聚合（按 emails.hook_id 输出 JSON）
python "$SK/scripts/lead_db.py" hook-stats --campaign-id <N>

# ③ 回写钩子库战绩
python "$SK/../b2b-lead-research-1.0.0/scripts/hook_library.py" record \
  --business <business_key> --market <国家码> --stats-file <hook-stats输出.json>
```

回复数据由定时任务 `reply_monitor.py`（每小时）持续拉回 `bs_ai_lead_gen_replies` 表；
即时核验时也可手动跑一轮再执行 ②③。

## 2. 逐封状态判定

读 `send_emails.py` 的发送日志（`campaign_dir/logs/` 下 send_log）的 `status`
与 `error`（SMTP 回执原文），状态同时回写 `bs_ai_lead_gen_emails.status`：

| `status` | 判定 | 动作 |
|---|---|---|
| `sent` | 已投递 | 记 `sent`，等打开/回复数据 |
| `throttled`（`554 MI:STC` / `5.5.0` / rate limit 等） | 发件方限流 | **整批停发**，降速续跑；**绝不加入退订名单** |
| `bounced` 且命中硬退信码（`550 5.1.1` / user unknown 等） | 地址无效 | **立即加入退订名单** `unsubscribed.csv`（地址级，租户级共享） |
| `bounced` 且命中黑名单码（`550 5.7.1` / blacklisted / reputation） | 发件端信誉或策略问题 | **整批停发**，排查后按 SKILL.md 附录独立方法体检 DNS（仅人工指令） |
| `bounced` 软退信（`450` / `451` / try again 等） | 临时故障 | 退避重试，不加入退订名单 |
| `failed`（会话污染：`503 nested MAIL` / `please run connect() first` 等） | 连接层失败 | 丢弃旧会话重连续跑，不当成地址无效 |

## 3. 必须区分「收件人退信」与「发件端限流」

若 `bounced=0` 但 `failed`/`throttled` 集中出现以下形态：

- `554 MI:STC`（典型样本：网易企业邮 `554 b'5.5.0 MI:STC ...'`）
- `503 nested MAIL` / `please run connect() first`
- 长连接会话污染导致的连续失败

这是**发件方反垃圾拦截 + SMTP 会话污染**，**不是地址无效**。处理：

1. 整批停发；
2. 这些地址标 `throttled`，**绝不加入退订名单**；
3. 不要在原 SMTP 会话里立即重试（`send_emails.py` 已内置会话复检与回收）；
4. 降速续跑（调大 `min_interval_seconds`、`--max-minutes` 切段）。

把限流误判成坏地址、写进退订名单，是不可逆的数据损失。

## 4. 数据落点（我方 DB 管线口径）

- 逐封状态：`bs_ai_lead_gen_emails.status`（sent / failed / bounced / throttled / draft）
- 打开数：`bs_ai_lead_gen_emails.open_count`（`fetch_opens.py` 累加）
- 回复：`bs_ai_lead_gen_replies`（`reply_monitor.py` 意图判定后落表）
- 退订名单：`storage/tenants/{tid}/lead-gen/unsubscribed.csv`（email,date,reason，
  租户级共享，跨 campaign 生效）
- 战绩聚合：`lead_db.py hook-stats` 按 `emails.hook_id` 输出 JSON

## 5. 汇总必须回答的四个问题

1. 成功 / 失败 / 硬退信 / 软退信 / 限流各多少？
2. 哪些地址进了退订名单，理由分别是什么？
3. 有没有「零退信但大量 throttled/failed」的限流特征？
4. 打开率与回复率按 `市场|客群|钩子` 怎么排，下一轮集中到哪个钩子？

第 4 问由 `hook-stats` + `hook_library.py record` 回写后，
`hook_library.py recommend --business <key> --market <码>` 直接给出排名。

## 6. 打开率的已知损耗（别当绝对值看）

纯文本客户端不触发像素、企业网关剥离图片、Gmail 图片缓存、Apple Mail 预取，
都会让打开率失真。**组间比较有效**（哪个钩子更好），绝对值不要当真。
追踪像素会轻微降低送达率；投诉率 > 0.1% 立刻停发。

## 7. 不属于核验的动作（禁止混入）

- ❌ 发送前检查环境变量里有没有 SMTP 密码值
- ❌ 发送前 `smtplib` 登录连通性验证 / 自测发信
- ❌ 自动跑发件域名 SPF/DKIM/DMARC 体检并据此阻塞
- ❌ 把「凭据没提供 / 未体检 / 未登录成功」写成阻断项或回头追问用户
- ❌ 因为「要不要先跑自测」「是否批准发送」停等用户答复

DNS 体检只在用户明确下指令（「体检一下发件域名」「查一下 SPF/DKIM」）时，
按 `SKILL.md` 文末附录的独立方法执行（`domain_check.py`），不进入本核验流程。
