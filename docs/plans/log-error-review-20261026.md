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
  - [ ] 同步修复代码到 254 `/var/www/qbapi` 并重启 c_qba 容器（重启即生效，代码目录挂载）
  - [ ] 在 254 c_qba 容器内跑 `test_key_apis` 回归（需容器内可连被测服务）
  - [ ] 修复部署后观察 production 日志确认 module_listing_view 500 不再复现、推送恢复

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
- **处理**：
  - [ ] 逐租户确认是否在用：在用则在前台补配租户环境变量 `APP_ID`；不在用则关闭对应子智能体的 external_push recap 任务
  - [ ] 评估告警收敛：同一（租户, 子智能体）组合的未配置告警去重/降频，避免刷屏

### P1-3 计费 model 为空（42 WARNING，10-19~10-25 持续）

```
src.services.billing:calculate_llm_credit_cost_with_breakdown:337 / :382
  计费：model 为空，credit_cost=0
```

- 上期 P2（19 条）升级：本期生产 42 条、credit_cost 恒为 0，说明有调用点不回传 model 字段导致漏计费，且持续发生（最近 10-25 17:22）。
- **处理**：
  - [ ] 定位未回传 model 的调用点（337 与 382 两个函数分别对应哪类计费路径），按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 checklist 排查
  - [ ] 修复后回填漏计的 chat_records（如可定位）

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
- **处理**：
  - [ ] 排查 session_tasks 一次性提醒的注册链路：为何 date trigger 在创建后 5 分钟即错过（missed by 0:05:13 首次即 miss）、为何错过后被反复重注册
  - [ ] 核对 misfire_grace_time 与过期任务的清理策略；过期提醒应显式作废而不是永远挂着

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

同生产 P1-2，租户为测试租户（pre-sales / auto-form-filler / aidefine-sales-assistant / auto-form-filler-v2）。随生产侧一并处理口径。

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
