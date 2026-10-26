# 双环境 ERROR/WARNING 日志收集与处理清单（2026-10-19 至 10-26）

> 收集时间：2026-10-26。范围：生产（243 `/var/www/agent`）、测试（254 `/var/www/agent2`）。
> 来源：① `log/agent/aid-work-agent_2026101[9-26].log` 按 `\| ERROR/WARNING \|` 全量提取；② 各环境主库 `log_error` 表（`timestamp >= '2026-10-19'`）交叉核对。
> 原始行留存：本地 `/tmp/logreview20261026/{prod,test}.log`（临时，处理后可删）。
> 上期清单：[log-error-review-20261012.md](log-error-review-20261012.md)（本期对上期各项的闭环状态一并核对）。

## 1. 总量概览

### 1.1 文件日志（应用 ERROR/WARNING 行数）

| 环境 | ERROR | WARNING | 爆发日 |
|------|-------|---------|--------|
| 生产 | 51 | 192 | 10-19（41E，ERP 500 + LLM 单点故障）、10-21（52 条，含全栈重启事件） |
| 测试 | 3 | 285 | 10-21（175 条，apscheduler missed + deadlock + 计费未配价同日） |

### 1.2 log_error 表

- 生产 `aid_work_agent.log_error`：50 条（10-19: 41、10-20: 3、10-21: 6），与文件日志趋势一致。
- 测试 `aid_work_agent2.log_error`：3 条（10-21）。
- 上期遗留口径：agent3 已于 10-15 停用，测试库不再混入 agent3 错误（本期已验证无混入）。

## 2. 上期（10-12）清单闭环核对

| 上期编号 | 事项 | 本期状态 |
|---------|------|---------|
| P0-1 | 生产 LLM failover 链（Kimi 401 + 智谱余额/429） | 🔶 **部分恢复**：智谱余额不足与 429 本期未再出现（已处理）；**Kimi key 仍无效**（10-19 仍有 1 条 `Incorrect API key provided`），key 未更换 |
| P0-2 | wecom_kf 45009 限频 | ✅ 本期无复现（已部署修复） |
| P1-1 | 测试 weixin_marketing 凭证解密失败（持续累计 110+） | 🔶 **大幅缓解**：本期仅 10-21 1 条（且来自 session_tasks 受控文本，非 weixin_marketing dispatch）；原 5s 一条的持续报错消失 |
| P1-3 | 两租户 external_push 缺 APP_ID | ❌ **未处理且扩大**：本期 6 个租户（原 2 个），子智能体扩到 pre-sales / auto-form-filler，详见 P1-2 |
| P1-4 | cp_tool 跨租户探测 | ✅ 本期无复现 |
| P2-3 | 计费 model 为空 | ❌ **量增大**：上期 19 条（测试+仿真），本期生产单独 42 条且持续到 10-25，升级为 P1-3 |
| P2 | erp11094 module_data_update 500 KeyError | ❌ 仍在发生（本期为 module_listing_view 接口，见 P1-1），建议同步对接方 |

## 3. 生产环境问题清单

### P1-1 ERP 接口 500 导致 external_push 推送失败（21 ERROR + 11 ERROR，10-19~10-21）

```
src.tools.network.http_api:_parse_response:750
  HTTP API 调用失败: method=POST url=https://erp1109[4|7].aidingyi.cn/api/v1/erp.module/module_listing_view
  status=500 error='Unexpected Error: KeyError'（Flask 堆栈，第三方 ERP 服务端 bug）

src.services.recap.runner:_run_tasks:463
  [recap] task=external_push 失败（不影响对话）: 推送循环未收到完成报告，最后模型输出:
```

- 时间强相关：erp 500 与 recap 失败成对出现（08:17:58→08:18:04、23:29:17/26→23:29:25/32）。
- 涉及租户：`tenant_29304e2196e8`（erp11097）、`tenant_c148f4efb4dc`（erp11094），均为 sales-assistant。推送循环内调 ERP 列表视图 500 后推送放弃。
- **根因已定位（2026-10-26，254 ERP 端 error/access 日志实证）**：`order` 参数元素格式错误 → 服务端裸取 `v['type']` 抛 KeyError。ERP 接口文档只写「order: 排序规则」未给元素结构，sales-assistant 的 LLM 猜成 `{"attr":"id","value":"desc"}` / `{"attr":"id","sort":"desc"}`（均无 `type` 键）；`core/application/module.py:446` `if v['type'] == 'asc'` 直接 KeyError，全局异常处理器只回类名，故我方仅见「Unexpected Error: KeyError」。不带 order 的同款请求全部 200。
- **修复（qbweb3.1 本地已改，待部署 254 c_qba）**：module.py 两处（446 列表 / 874 导出）order 解析容错（attr 兼容 field/column、方向兼容 value/sort、缺省 desc）；app.py 全局 500 报错带异常详情与业务帧位置（`Unexpected Error: KeyError: 'type' @module.py:446`）；接口文档补 order 元素结构表。事故入参隔离验证通过。
- **处理**：
  - [x] 同步修复代码到 254 `/var/www/qbapi` 并重启 c_qba 容器（2026-10-26 用户已同步，代码在位：module.py:448/879、app.py:287）
  - [x] 在 254 c_qba 容器内跑 `test_key_apis` 回归（2026-10-26：9 passed / 2 skipped 数据条件跳过；apifox_smoke 11 passed）
  - [x] 事故入参回归（2026-10-26，c_qba 容器内实测）：`{"attr":"id","value":"desc"}`、`{"attr":"id","sort":"desc"}` 两种原 500 入参均 200；缺省方向/大写 DESC/field 别名容错均 PASS；desc/asc 方向实测生效（造数验证后已清理）
  - [ ] 修复部署后观察 production 日志确认 module_listing_view 500 不再复现、推送恢复（下轮巡检核对）

### P1-2 tenant_api_doc 租户环境变量 APP_ID 未配置（57 WARNING，10-19~10-23）

```
[tenant_<id>_doc] 租户环境变量未配置，占位符保留原样 vars=['APP_ID']
[external_systems] 接口文档占位符未解析（环境变量未配置），入口按未配置处理
```

| 租户 | sales-assistant | pre-sales | auto-form-filler |
|------|-----------------|-----------|------------------|
| tenant_ea24cd1a1097 | 12+10 | 12 | 12 |
| tenant_d9527ad8bc7e | 15 | 6 | 6 |
| tenant_af2eefa4594d | — | 4 | 4 |
| tenant_29304e2196e8 | — | 2 | 2 |
| tenant_48434f6195ed | 2 | 2 | 2 |
| tenant_c148f4efb4dc | — | 2 | 2 |

（计数含 render_doc_placeholders + load_sso_config 两条日志）

- 上期 P1-3 的延续且租户范围扩大（2→6）。涉及 SSO 入口与 external_push，未配置时入口按未配置处理、推送放弃。
- **逐租户核对结论（2026-10-26，生产库 subagent_env_vars / tenant_agent_instances / channel_sessions / chat_sessions + 日志时间线实证）**：

| 租户 | APP_ID 配置 | 实际在用 | 结论 |
|------|------------|---------|------|
| tenant_29304e2196e8 | ✅ 10-21 已配（sales-assistant） | sales-assistant 在用（渠道 79 会话，最后 10-25） | 已闭环：10-22 起告警消失 |
| tenant_c148f4efb4dc | ✅ 10-21 已配（sales-assistant） | sales-assistant 在用（渠道 109 + web 34 会话，最后 10-26） | 已闭环：10-22 起告警消失 |
| tenant_48434f6195ed | ❌ 未配 | sales-assistant 在用（渠道 3 会话，最后 10-26，当天持续告警） | **在用，需前台补配 APP_ID** |
| tenant_d9527ad8bc7e | ❌ 未配 | sales-assistant 低频（渠道 9 会话，最后 10-23） | 待确认是否继续使用：在用则补配，弃用则关 external_push |
| tenant_af2eefa4594d | ❌ 未配（仅 AGENT_TOKEN，10-10 配） | sales-assistant 用过（渠道 22 会话，最后 10-21 后无活动） | 待确认是否停用：停用则关 external_push |
| tenant_ea24cd1a1097 | ❌ 无相关配置 | 仅 travel-consultant（渠道 28 + web 32 会话），不用 sales-assistant/pre-sales/auto-form-filler | 无需配置，告警为枚举噪音（见下） |

- **补充发现**：
  - 告警量与在用无关：`load_sso_configs` 经 `list_sso_candidate_subagent_names` 枚举**全部**内置/自定义子智能体名（`src/services/tenant_api_doc.py:239`），凡技能白名单命中 pre-sales-api / form-filling-api 模板者都会渲染 `${APP_ID}` 并在未配置时告警，即使租户从未使用该智能体。ea24cd1a1097 的告警全部属此类。
  - `${APP_ID}` 引用点仅两个兜底模板：`configs/api_doc_templates/pre-sales-api.md`（sales-assistant 技能白名单含 pre-sales-api，故其同样告警）与 `configs/api_doc_templates/form-filling-api.md`（auto-form-filler）；6 租户均无租户上传文档（`storage/tenants/{tid}/templates/` 不存在）。
  - sales-assistant 基础定义 external_push=every_round enabled，未配置租户每轮 recap 都告警并放弃推送。
- **处理**：
  - [x] 逐租户确认是否在用（2026-10-26 核对完成，见上表；48434f6195ed 待补配，d9527ad8bc7e / af2eefa4594d 待业务确认后补配或关任务）
  - [ ] 评估告警收敛：同一（租户, 子智能体）组合的未配置告警去重/降频，避免刷屏；可考虑候选枚举先按租户实际启用的智能体过滤（消除 ea24cd1a1097 类噪音）

### P1-3 计费 model 为空（42 WARNING，10-19~10-25 持续）

```
src.services.billing:calculate_llm_credit_cost_with_breakdown:337 / :382
  计费：model 为空，credit_cost=0
```

- 上期 P2（19 条）升级：本期生产 42 条、credit_cost 恒为 0，说明有调用点不回传 model 字段导致漏计费，且持续发生（最近 10-25 17:22）。
- **排查结论（2026-10-26，生产库 chat_records + 生产日志实证）**：**误报，无实际漏计费，无需回填**。
  - 生产库 10-19 起 model 为空共 52 条：`wecom_kf` 46 条 + `hongtao_shop_embedding` 5 条 + `wechat_mp_embedding` 1 条。
  - wecom_kf 46 条全部为**语音消息 ASR 计费记录**（0 LLM token、usage_breakdown 仅 asr、credit_cost=1.00 纯 ASR）：`channel_routes.py` wecom_kf 路径 `skip_save = not asr_success`，语音识别成功时保存只含 ASR 用量的 record，该 record 从未 `set_model`（LLM 费用由 Runner 结算，落同会话另一条 `model=deepseek-flash` 记录，抽样核对计费完整）。告警链路：`save()` -> `calculate_llm_credit_cost_with_breakdown`（生产日志当前全部 `billing.py:382`，文档头部 :337 为上期旧版本行号）。
  - 根因是 `billing.py` 中 `calculate_llm_credit_cost_with_breakdown` **先判 model 再判 usage_calls**，无 LLM 用量的纯 ASR 记录也触发告警；告警频率 = 微信客服语音消息量，故持续出现。embedding 6 条为离线向量化记录（`knowledge/service.py` 链路），纯 embedding 无 chat model 属设计使然，且该路径不触发 382 告警。
  - 排除「测试代码漏传 model」假设：全部记录来自生产渠道语音/离线向量化，非测试路径。
- **处理**：
  - [x] 定位未回传 model 的调用点（2026-10-26：非调用点缺 model，是纯 ASR/embedding 记录的误报，见上）
  - [x] 修复告警误报（2026-10-26：`billing.py` `calculate_llm_credit_cost_with_breakdown` 将 `if not usage_calls` 空用量静默返回提前到 model 检查之前；真实 LLM 用量缺 model 仍告警）——待部署
  - [x] 回填核对（2026-10-26：无需回填，wecom_kf 的 LLM 费用在同会话其他记录正常落账、ASR 1 积分正常；embedding 积分正常）

### P2-4 10-21 全栈重启事件（一次性，已恢复）

20:54~20:57（CST）redis → api → postgres 全栈容器重启（`docker inspect` StartedAt 确认），引发瞬时错误：

| 签名 | 次数 |
|------|------|
| PostgreSQL 连接失效重新获取（server closed the connection unexpectedly） | 14 |
| Redis 连接失败降级内存（aid-redis name resolution 失败） | 2 + SSE listener 1 |
| browser resume worker 异常（RuntimeError/OperationalError） | 6 |
| notification dispatcher failure | 1 |
| 设备领取 invocation 失败（连续 2 次无效连接） | 1 |

当前全部容器 healthy，无需处理；留档供排查时对照「10-21 20:54 全栈重启」。

### P2-5 LLM 单点故障残留（10-19，少量）

| 签名 | 次数 | 说明 |
|------|------|------|
| Moonshot（Kimi）401 Incorrect API key | 1 | 上期 P0-1 遗留，key 一直未换；failover 链末端仍有一个坏点 |
| 智谱 ReadTimeout / 500 网络错误 / 等待 Key 超时 | 5 | 偶发，failover 正常兜住 |

- **处理**：
  - [ ] 更换 Moonshot API key（长期遗留，随手处理）

### P2-6 BOSS 工具不在设备 Provider 批准清单（3 WARNING，10-20 09:43）

`boss_list_jobs` / `boss_filter_options` / `boss_filter` 三个 invocation 被置为失败。桌面端（BOSS 0.3.23 验收版）Provider 批准清单与技能调用的工具集不匹配。

- **处理**：
  - [ ] 核对桌面端批准清单配置与 boss 技能的工具名单，补齐或在技能侧收敛调用面

### P3-7 观察项（低频/正常）

| 签名 | 次数 | 说明 |
|------|------|------|
| chunker 强制分割（文本超过 6000 字符） | 21（10-20 单日） | 大文档导入，正常分块行为 |
| agent 返回空响应 wecom_kf | 5 | 4 次同一 session（tenant_ea24cd1a1097 travel-consultant，10-19~10-22 反复）+ sales-assistant 1 次；上期同类伴随 failover 故障，本期 failover 正常仍出现，建议看一次该 session 的 trace |
| Embedding InternalError.Algo.Embedding_pipeline_Error | 1（10-20） | 阿里云侧偶发，文档上传失败可重试 |
| wecom_kf 未知事件类型（FromUserName=XuPengCheng） | 1（10-23） | 非标准回调事件，忽略 |
| hongtao_shop 停机收尾失败（stale 回收兜底） | 1（10-23） | 已有兜底，观察 |
| 密码登录失败 | 2 | 用户行为，非系统问题 |

## 4. 测试环境问题清单（agent2）

### P1-1 apscheduler 一次性提醒任务 missed（~122 WARNING，10-20~10-24）

```
Run time of job "陈宇电话沟通提醒" (trigger: date[2026-10-20 14:40:00 CST]) was missed by 3 days, 18:42:18
Run time of job "王晓产品演示提醒" (trigger: date[2026-10-20 14:30:00 CST]) was missed by ...
```

- 3 个 date 触发器（10-20 14:30 / 10-20 14:40 / 10-21 10:00）错过执行时间后**反复重注册反复 missed**（10-20 到 10-24 多个时间点成批出现），说明提醒从未送达，且任务一直挂在调度器里每次启动都刷 missed。
- 关联：10-20 同日有「一次性任务缺少执行时间 task_id=...」WARNING，疑似同一链路（session_tasks 一次性提醒 → scheduler 注册）的字段缺失或时间换算问题。
- **排查结论（2026-10-26，代码定位，已修复待部署）**：与 session_tasks 无关，本链路是 scheduled_tasks 通用定时任务（create_scheduled_task 工具创建）。三个缺陷叠加：
  1. 创建端不校验：create_scheduled_task 对 once 类型不校验 run_at 存在性/格式/是否未来时间--LLM 未解析出 run_at 落库 cron_expression=NULL（即「一次性任务缺少执行时间」WARNING），解析出过去时间也照常入库，任务创建即死，提醒从未送达
  2. 过期任务永不作废：once 任务 status 恒为 active，每次进程重启 list_active() 重注册过期 DateTrigger 刷 missed；且启动注册未填 _reconcile_seen、首轮 reconcile（30s）会再重注册一次--每次重启每个死任务刷 2 条 missed，3 个死任务 × 约 20 次重启 ≈ 122 条
  3. 执行成功不收尾：executor 对 once 任务成功执行后不改状态，同样被重启重注册
- **修复**：创建端校验 run_at（必填 + 未来时间 + 格式归一化，兼容缺秒/T 分隔/斜杠日期等 LLM 变体）；manager._register_job 对缺失/不可解析/超宽限（300s，与 misfire_grace_time 一致）过期的 once 任务显式置 expired 不注册；executor 对成功执行的 once 任务置 completed；启动注册后即填 _reconcile_seen 消除首轮重复注册。存量 3 条脏数据部署后首次注册自愈为 expired
- **追加：渠道场景尽力投递（2026-10-26，已修复待部署）**：wecom_kf 等渠道创建的提醒即使调度修复也无法保证送达（平台 48h 窗口/配额/接待状态限制）。三层投递：创建时如实告知（渠道创建返回附提示文案）；执行成功后经 ChannelFactory 直投一次，失败登记 scheduled_task_deliveries（pending，新表，db_update.yaml 2026-10-26 15:00:00）；客户下次来消息时入站钩子合并补投（挂在 AI 可接待分支、转人工/拦截判定之后）。每日 04:45 清理：pending 超 3 天置 expired，终态保留 7 天。代码：src/scheduler/delivery.py + executor/delivery 钩子 + channel_routes 补投挂点 + ScheduledTaskDeliveryDB
- **独立测试与 CodeReview 结论（2026-10-26）**：178 单测全绿（tests/unit/scheduler/）+ wecom_kf adapter 回归 85 全绿；无 P0。已按审查意见修复：① 失败的 once 任务在 5 分钟宽限内被 reconcile 反复重注册补跑（重复计费）-- _register_job 对 last_run_at 非空的过期 once 任务直接作废；② 直投写共享 adapter 的 current_open_kfid 有并发交错窗口 -- send_text 增加显式 open_kfid 参数，投递链路不碰共享状态；③ 双空租户 fail-closed 拒绝；④ 补投 attempts 达 5 次自动 expired；⑤ 标记逐条容错、补投钩子日志对齐 opt(exception=True)、任务名截 50 字、纯日期串 run_at 拒绝、前端状态映射补 expired。已知限制（登记不修）：多客服账号租户直投可能取到非会话账号的 adapter（open_kfid 与凭证不匹配时企微报错降级 pending，不会错发，补投兜底）；补投先于 Agent 回复发送（临近窗口耗尽时可能占用配额）；跨事件循环复用缓存 adapter 的 httpx 连接池（5s 窗口内并发触发可能报错降级 pending）。真机端到端（真实企微 API 直投/48h 窗口/补投）待部署验收

### P2-2 本地工具 invocation 租约过期 / 许可清扫（46 WARNING，10-20~10-22）

- invocation 租约过期置 unknown：33 条；写动作许可过期清扫：13 条。设备（桌面端）不在线或未领取导致租约到期。
- **处理**：观察；若对应设备确应在线，核查桌面端当日连接状态。

### P2-3 session_tasks deadlock（5 WARNING，10-21 08:01~10:37）

完成评估/决策 tick `deadlock detected` ×4 + 决策处理异常 ×1。并发竞态，单日集中，之后未复现。

- **处理**：观察；再集中出现时抓 PG 死锁日志定位两条冲突 SQL。

### P2-4 计费模型未配置单价（32 WARNING，10-21 单日）

`test-model` ×17、`review-original-model` ×15 按兜底 deepseek-flash 价格计费。测试流量，不影响生产；如需核对测试积分可在价目表补行。上期已知。

### P2-5 单次偶发（观察即可）

| 签名 | 次数 | 说明 |
|------|------|------|
| recap 触发入口异常 PROFILE_NOT_FOUND | 2（10-21） | 同日集中，观察 |
| 受控文本解密失败（st_test_7d6d459fc679） | 1（10-21） | 上期 weixin_marketing 持续解密失败本期已基本消失，此处为另一单点，观察 |
| wechat_mp 领取撞 running 唯一约束（让位） | 1 | 竞态兜底正常 |
| 一次性任务缺少执行时间 | 1（10-20） | 并入 P1-1 排查 |
| 密码登录失败 | 1 | 用户行为 |

### P2-6 tenant_api_doc APP_ID 未配置（25 WARNING，10-20~10-21）

同生产 P1-2。25 条告警实际全部来自单一测试租户 `tenant_e9b2fab93a2f`（原登记的 pre-sales / auto-form-filler / aidefine-sales-assistant / auto-form-filler-v2 是子智能体名，非租户）。

- **逐租户核对结论（2026-10-26，测试库 subagent_env_vars / channel_sessions / chat_sessions + 测试日志实证）**：

| 租户 | APP_ID 配置 | 实际在用 | 结论 |
|------|------------|---------|------|
| tenant_e9b2fab93a2f | ✅ 10-21 16:50（sales-assistant） | sales-assistant（渠道 8 + web 33 会话，止 10-23） | 已闭环：10-22 起告警消失；10-20/21 对其他 3 个子智能体名的告警均为枚举噪音（该租户从未使用） |
| tenant_9b9fb62aff5e | ✅ pre-sales 10-19（租户级兜底覆盖全租户） | pre-sales（止 10-19）、aidefine-sales-assistant（10-22 仍活动，external_push 启用 + pre-sales-api 技能）、auto-form-filler（止 10-20） | 功能已覆盖；aidefine-sales-assistant 名下无精确配置，建议补配使语义清晰 |
| tenant_1dc997a1806b | ✅ auto-form-filler 10-19（兜底覆盖） | auto-form-filler（web 6 会话，止 10-20）、pre-sales（止 08-25） | 功能已覆盖；pre-sales 精确项未配但基本停用 |
| tenant_811ba3ad3ac8 | ✅ auto-form-filler / auto-form-filler-v2 10-20 | auto-form-filler（止 10-20） | 已闭环（auto-form-filler-v2 定义无 external_push，配置属预留） |
| tenant_53cfbdef870c | ❌ 无任何配置 | sales-assistant（web 2 会话，止 10-21） | 潜在缺口：日志窗口内未触发 recap/文档加载暂无告警；若启用渠道推送将告警并放弃推送 |
| tenant_d18c257ff434 | ❌ 无任何配置 | sales-assistant（web 4 会话，止 10-21） | 同上 |
| tenant_49a0f3d1706a / tenant_b6459319f621 | 仅 AGENT_TOKEN（7~9 月旧配置） | 无近期活动 | 历史遗留，无影响 |

- **机制说明**：`load_tenant_env_vars` / `load_tenant_env_vars_map` 的租户级兜底——租户内任一智能体配置的变量对全部智能体生效（精确项优先），故上述「兜底覆盖」租户功能上不受影响。
- 测试环境 10-22 起零告警，无生产 P1-2 那类持续噪音；遗留动作仅 53cfbdef870c / d18c257ff434 两个租户待业务确认后再补配。

## 5. 处理顺序建议

1. **P1-1 生产 ERP 500 → external_push 失败**——直接影响客户每轮推送，且需同步对接方，周期最长，先启动
2. **P1-2 APP_ID 六租户配置**——纯配置操作，逐租户确认在用即补
3. **P1-3 计费 model 为空**——涉及收入口径，按 billing_audit 规范排查
4. **测试 P1-1 提醒 missed**——测试环境提醒功能实际不可用，定位注册链路
5. **P2-5 更换 Kimi key**——长期遗留，随手处理
6. **P2-6 BOSS 批准清单**——随 BOSS 桌面端验收节奏处理
7. 其余 P2/P3 观察项按触发条件跟进

## 6. 数据口径备注

- 文件日志统计口径：`grep -E '\| (ERROR|WARNING) +\|'`，按日期轮转文件 20261019~20261026。
- 测试环境 `aid-work-agent.log`（无日期后缀的当前文件）是 2026-06 的陈旧残留（轮转机制上线前的旧文件，37k 行），**不代表近期活动**，已从统计排除；建议择机清理该文件避免下次误采。
- 生产 `log_error` 表仅 50 条远小于文件日志 243 条，文件日志为主、DB 表做交叉核对（同上期口径）。
- agent3 自 10-15 停用后无新日志，本期测试环境数据纯净（无 agent3 混入）。
