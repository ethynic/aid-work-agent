# 三环境 ERROR/WARNING 日志收集与处理清单（2026-10-01 至 10-12）

> 收集时间：2026-10-12 13:30 前后。范围：生产（243 `/var/www/agent`）、仿真（243 `agent1`）、测试（254 `agent2`）。
> 来源：① `log/agent/aid-work-agent_202610*.log` 按 `\| ERROR/WARNING \|` 全量提取；② 各环境主库 `log_error` 表（`timestamp >= '2026-10-01'`）。
> 原始行留存：本地 `/tmp/logreview/{prod,sim,test}.log`（临时，处理后可删）。

## 1. 总量概览

### 1.1 文件日志（应用 ERROR/WARNING 行数）

| 环境 | ERROR | WARNING | 爆发日 |
|------|-------|---------|--------|
| 生产 | 666 | 228 | 10-11（536 ERROR）、10-09（48E/161W）、10-10（37E/37W） |
| 仿真 | 8 | 62 | 10-09（8E/55W） |
| 测试 | 95 | 56 | 10-10（75E）、10-12（19E，防滥用扫描） |

### 1.2 log_error 表

- 生产 `aid_work_agent.log_error`：661 条（10-04~10-11），与文件日志一致。
- 仿真 `aid_work_agent1.log_error`：87 条，**为 sim.sh 同步的生产数据**（10-04~10-09 分布与生产重合），不代表仿真自身错误。
- 测试 `aid_work_agent2.log_error`：168 条，**混入 agent3（在线开发，共用库）**的启动错误，见 §4.3。

## 2. 生产环境问题清单

### P0-1 LLM failover 链整体故障（10-11 爆发 500+ 条）

| 签名 | 次数 | 日期分布 |
|------|------|---------|
| zhipu 429 Too Many Requests（`src.llm.providers.zhipu:chat:137`） | 124 | 10-10: 12，10-11: 112 |
| 智谱「余额不足或无可用资源包」（`gateway chat/_call_with_pool/chat_direct`） | ~254 | 10-10: 30，10-11: 224 |
| Moonshot（Kimi）401 Incorrect API key（`src.llm.providers.qwen:chat:168`） | 180 | 10-06: 12，10-11: 168 |

**分析**：10-11 一天内 zhipu 同时出现限频与余额不足、Kimi key 无效，failover 链（a/b/c）末端全部不可用，直接影响对话可用性。简历 VL 评估失败（`proxy_tool` 10-10，6 份简历）与 resume_vl_service 重试警告同根因（上游智谱余额不足）。

**处理**：
- [ ] 检查并更换 Moonshot API key（qwen provider failover 配置中的 key，10-06 起已失效）
- [ ] 智谱账户充值/购买资源包（余额不足 10-10 起持续）
- [ ] 核查 zhipu provider 对 429 是否有指数退避重试；没有则补（参考 wecom_kf 45009 修复模式）
- [ ] 评估 failover 链告警：链上多家同时不可用时应有显著告警而非仅落日志

### P0-2 wecom_kf sync_msg 45009 限频（10-11，30 次）

`_process_tenant_wecom_kf_messages:1762`，10-11 10:01 集中爆发。**已修复**（commit ad71afb9：指数退避重试 + 拉取任务去重合并），待部署验证。

- [ ] 部署后观察 agent.aidingyi.cn 日志确认不再复现

### P1-3 external_push / api-meta 占位符缺配置（10-08~10-10，249 条 WARNING）

```
[tenant_api_doc] 租户环境变量未配置，占位符保留原样 tenant=tenant_af2eefa…d, subagent=sales-assistant, vars=['APP_ID']
[external_push] 租户文档 api-meta 解析失败，放弃推送 tenant=…
```

两个租户（tenant_af2eefa…、tenant_d9527ad8bc7e）的 sales-assistant 每轮推送全部放弃（56+56、27+27 次）。

**处理**：
- [ ] 确认两租户 sales-assistant 的 api 文档与推送是否在用；在用则补配租户环境变量 `APP_ID`，不在用则关闭该子智能体的 external_push recap 任务

### P1-4 cp_tool 跨租户探测 / 路径不存在（10-09，42 ERROR + 15 WARNING）

LLM 反复探测 `kb_68d5ec23082f.pdf`（跨租户路径、`/app/knowledge/` 平铺路径、`/app/content.docx` 等编造路径），安全防护全部正确拒绝。关联：cp 平铺路径兜底修复（commit 6c5bdc2d）待 agent1 验证原 trace 场景。

**处理**：
- [ ] 在 agent1 验证 6c5bdc2d 平铺路径兜底后，观察此类「源文件不存在」是否消退
- [ ] 跨租户拒绝属防护正常工作，不修；如频发可考虑在知识库检索空结果时给 LLM 更明确的路径指引

### P2-5 其他（低频）

| 签名 | 次数 | 说明 |
|------|------|------|
| zhipu 400 Bad Request | 8（10-06 20:43） | HTTPStatusError 无 body 详情，未复现；再出现需抓完整请求参数排查 |
| skill_execute 路径拼接错误 | 1（10-10） | spec-to-quotation-list：`/app/src/skills/spec-to-quotation-list-1.0.0//app/src/skills/…/analyze_pdf.py` 绝对路径被二次拼接，SKILL.md 脚本调用方式需修正 |
| skill_execute exit 1（pre-sales-api） | 1（10-09） | stdout 仅 `---`，偶发；伴随同会话 cp_tool 探测，属同一线索 |
| http_api → erp11094 module_data_update 500 KeyError | 2（10-11 22:47） | 第三方 ERP 服务端 bug，建议同步给对接方 |
| wechat_mp 清单接口错误 ret=110 | 1（10-08） | 微信公众平台偶发，已有重试驱动，观察 |
| 工具幻觉 WARNING（read / excel_process / knowledge_file_search 不存在） | ~6 | LLM 调用未装配的工具；executor 已有提示纠偏，观察 |
| 生产 db_update 水位停在 2026-09-21 | — | 非错误；下次部署自动应用 18 个积压块（含浏览器租约迁移），部署时留意启动日志 |

## 3. 仿真环境（agent1）

| 签名 | 次数 | 状态 |
|------|------|------|
| db_update 失败：BROWSER_SCHEMA_COLUMN_INCOMPATIBLE（lease_until，10-02 块） | 6（10-09 13:45） | ✅ 已自愈——当前水位 2026-10-08 15:21:10 已越过该块（10-09 浏览器租约迁移修复生效） |
| cp_tool 跨租户拒绝 | 2 | 同生产 P1-4（复现生产数据所致，防护正常） |
| 计费：model 为空 credit_cost=0 | 4 | 同测试 §4-4，低优 |

## 4. 测试环境（agent2，含 agent3 混入）

### P1-1 secret_crypto 解密失败（10-10 起，持续累计 110+ 条，仍在发生）

```
decrypt_secret 解密失败: 密文无效或主密钥不匹配
```

- 时间线：10-10 15:45 起密集出现（每 5s 一条），10-12 12 点后仍有 32 条，**当前唯一持续增长的问题**。
- 上下文：与 `weixin_marketing.dispatch:run_dispatch_tick`（5s 一次）交错出现，判定为 weixin_marketing 某账号的加密凭证（密文用旧主密钥加密或密文损坏）在每次 tick 被反复解密失败。
- 影响：该账号相关功能不可用；测试环境主密钥疑与密文加密时不一致（参考 APP_SECRET_KEY 优先级高于 RPA_SECRET_KEY 的语义）。

**处理**：
- [ ] 定位具体账号：排查 weixin_marketing 相关表中解密失败的密文行（10-10 15:45 前后新增/变更的配置）
- [ ] 重新配置该账号凭证或用正确主密钥重加密

### P1-2 防滥用扫描 abuse_policies 不存在（10-12 10:45~12:15，19 条）

每 5 分钟一次 `防滥用扫描租户 tenant_9eb3e45cab83 评估失败`，根因 `psycopg2.errors.UndefinedTable: relation "abuse_policies" does not exist`（共库防滥用表未建事故，agent3 依赖 agent2 建表）。

- 现状：agent2 水位已推进至 2026-10-12 11:30:00（file_assets 块），abuse_policies / abuse_events 表已确认存在（applied_at 11:33）；**12:20 后无新错误**，判定已恢复。

- [ ] 观察一轮（至 10-13）确认不再复现即可关闭

### P2-3 计费单价未配置 WARNING

- `模型 test-model 未配置单价，按兜底模型 deepseek-flash 价格计费` ×12、`review-original-model` ×2：测试流量使用未登记价目模型，不影响生产；如测试也要核对积分，可在价目表补行。
- `计费：model 为空，credit_cost=0` ×19（测试+仿真）：建议排查哪些调用未回传 model 字段，低优。

### P2-4 agent3 混入错误（共用 aid_work_agent2.log_error，属在线开发环境）

| 签名 | 次数 | 状态 |
|------|------|------|
| db_update.yaml Permission denied 拒绝启动 | 12（10-01） | agent3 属主事故遗留，属主已统一整改，水位已恢复推进 |
| SUBAGENT.md Permission denied（recruiting-operator / building-supply-chain） | 48（10-01~10-08） | 同上，10-08 后无新错误 |
| db_update 10-03 块执行失败（DO $$ browser schema） | 12（10-05） | agent3 已加 DB_UPDATE_SKIP=1（dev.yml），不再建表，依赖 agent2 部署；已建表，关闭 |
| 视频生成轮询 lock timeout | 1（10-03） | 偶发数据库锁超时，观察 |
| reply_style duplicate key | 1（10-08） | 并发创建竞态，单次，观察 |

### P2-5 渠道偶发 WARNING（观察即可）

- `agent 返回空响应 session=…wecom_kf…travel-consultant` ×2（10-09）：伴随 failover 链故障期，疑似 LLM 全链失败所致，随 P0-1 修复观察。
- `[SessionQueue] 合并窗口期内检测到取消（异常）` ×1、本地工具 invocation 租约过期 ×1：单次，观察。

## 5. 处理顺序建议

1. **P0-1**（生产 failover 链：Kimi key + 智谱余额 + 429 退避）——直接影响对话可用性
2. **P0-2** 部署验证 45009 修复（随下次生产发布）
3. **P1-1**（测试 weixin_marketing 凭证解密失败）——持续增长中
4. **P1-3**（两租户 external_push 缺 APP_ID 配置）
5. **P1-4 / P2 项**按序观察或低优修复

## 6. 数据口径备注

- 文件日志统计口径：`grep -E '\| (ERROR|WARNING) +\|'`，按日期轮转文件 20261001~20261012。
- 生产/测试的 `log_error` 表含 traceback 完整堆栈（`WRITE_LOG_ERROR=true` 时写入），本次以文件日志为主、DB 表做交叉核对。
- 仿真库 log_error 为同步生产数据，评估仿真自身问题以 `/var/www/agent1/log/` 文件日志为准。
