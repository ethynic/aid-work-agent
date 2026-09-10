# 微信营销自动化 灰度发布与回滚手册（P5）

> 适用范围：`src/weixin_marketing/`（weixin.fixed_content.v1 场景）+ 底座 `src/desktop_automation/` 的发布准备、灰度开启、监控、回滚与故障应急。
> 前置红线：**P0 真机门禁（账号/群身份验证、相同文字新增证据、图片 probe）未通过前，不开启无人值守真实发送**。本手册的全部操作在 `enabled=false` 时零行为变化。
> 权威文档：[微信实施计划 §9/§10](../plans/weixin/plan-weixin-marketing-automation.md) / [阶段宪章 R59](../../.zcode/plans/p1-stage-charters.md)。

---

## 1. 发布前检查（已固化，每次发版重跑）

| 检查 | 命令 | 通过标准 |
|---|---|---|
| 空库全量初始化 | `./scripts/dev_test.sh tests/integration/test_empty_db_bootstrap.py -p no:cacheprovider -q` | 临时空库跑通 `deploy/init-postgres.sql` + 幂等重放零差异；无 CREATEDB 权限环境 skip，用下述手动命令替代 |
| 双模式启动冒烟 | 见 §1.1 | API 导入无异常、路由挂载齐全；background_runner 初始化路径可用、`enabled=false` 下 weixin job 零注册 |
| 受限租户端到端 | `./scripts/dev_test.sh tests/integration/test_weixin_rollout_e2e.py -p no:cacheprovider -q` | allowlist 单租户 publish→tick→假设备→runs 终态；轮换/清理 tick 各一轮；allowlist 外租户零动作 |
| 受信清单三方一致 | `./scripts/dev_test.sh tests/unit/weixin_marketing/test_trusted_manifest_consistency.py -p no:cacheprovider -q` | catalog.py ⊆ Runtime providers.ts ⊆ clients/README.md 安装清单 |
| 回滚实时门控（P5 复审 P1-2 + 增量复核热读） | `./scripts/dev_test.sh tests/unit/weixin_marketing/test_p5_closeout.py -k RollbackRealtimeGate -p no:cacheprovider -q` | 文件级传播：yaml enabled true→false→true 授权结果跟随（免重启）、禁用期许可零新签发、allowlist 外租户拒授权；enabled=false 下迟到 applied 回执仍落账；mtime 缓存与 fresh 解析等价 |
| 载荷复用×清理交错（P5 复审 P1-1） | `./scripts/dev_test.sh tests/unit/weixin_marketing/test_p5_closeout.py -k PayloadReuseCleanupInterleave -p no:cacheprovider -q` | 三种交错下新事件 payload 均可加载非 None（接纳 FOR SHARE × 清理 SKIP LOCKED） |
| 域回归 | `./scripts/dev_test.sh tests/unit/weixin_marketing/ tests/integration/test_weixin_rollout_e2e.py tests/integration/test_empty_db_bootstrap.py -p no:cacheprovider -q` | 全绿 |

手动空库初始化（无 CREATEDB 权限或目标为新环境时，等价 psql 全量执行）：

```bash
docker exec -i aid-postgres psql -U aid_user -d <目标空库> -f deploy/init-postgres.sql
# 幂等验证：再执行一次，SELECT COUNT(*) 抽查关键表行数不变
```

> 服务器 template1 存在 collation version 失配时 `CREATE DATABASE` 会被拒（musl 容器 libc 升级常见）：用 `TEMPLATE template0` 建库，或由 DBA 执行 `ALTER DATABASE template1 REFRESH COLLATION VERSION`。

### 1.1 双模式启动冒烟方法（隔离环境，不触生产调度）

```bash
# API 模式：仅导入（不启动 uvicorn、不触发 lifespan）
python -c "import src.main as m; ps={r.path for r in m.app.routes}; \
  assert any('/weixin-marketing' in p for p in ps) and any('/local-tools' in p for p in ps); print('API OK')"
# background 模式：初始化路径（.env 指向测试库）+ weixin 门控
python -c "from dotenv import load_dotenv; load_dotenv('.env'); \
  import src.background_runner as bg; bg._init_resources(); \
  from src.weixin_marketing.registration import ensure_registered; \
  assert ensure_registered() is False; \
  from src.scheduler.manager import ScheduledTaskManager; \
  from apscheduler.schedulers.background import BackgroundScheduler; \
  m=ScheduledTaskManager(); m._scheduler=BackgroundScheduler(timezone='Asia/Shanghai'); \
  m._register_system_jobs(); \
  assert not [j for j in m._scheduler.get_jobs() if str(j.id).startswith('job_system_weixin_marketing_')]; \
  print('BG OK')"
```

`enabled=true` 下的注册断言由 `tests/unit/weixin_marketing/test_dispatch.py::TestGating::test_scheduler_registration_gated_by_enabled` 固化（8 个 job 全集）。

---

## 2. 开启阶梯（灰度顺序，不跳步）

每一步观察至少一个完整调度周期（发布→触发→执行→终态，含 sweep/reclaim tick 各至少一轮）后再进入下一步。

| 阶梯 | 动作 | 观察项 |
|---|---|---|
| 0 就绪 | `weixin_marketing.enabled: false`（默认）。完成 §1 全部检查；Runtime 升级到多 Provider 版本并重新配对 | 新 Runtime 上报 capabilities 含 `providers`/`provider_manifests`；旧 Runtime 行为不变 |
| 1 空跑 | `enabled: true` + `tenant_allowlist: ["tenant_灰度"]`（**单租户**）+ `time_triggers_enabled: true` + `event_triggers_enabled: false` | 各 tick 日志轮转正常；`waiting_device`/`due_to_start` 指标采集；无真实发送（无设备执行 v2） |
| 2 单设备单群 | 灰度租户内绑定**一个**设备 + **一个**群（group_bindings complete），发布一条 once 任务（近未来） | 真机首验：目标群身份核验通过、发送一条、runs 终态 succeeded、账本逐条与 unknown 语义正确 |
| 3 小放量 | 同租户增加任务数（仍单设备），观察一个配额窗口 | 配额预留/落账正确（quota_buckets）；无错群；无重复发送 |
| 4 多租户 | `tenant_allowlist` 逐个追加小租户 | 每租户重复阶段 2 的观察项 |
| 5 开事件/图片 | 按需开 `event_triggers_enabled`（webhook 源接入+密钥轮换演练）/`images_enabled`（素材上传链路） | 事件重复/乱序/重放拦截；素材引用保护与清理 |

放量原则：先单设备/单群，再小租户；**发现错群或自动重复立即停用**（见 §5）。

---

## 3. 监控指标清单（§9 口径）

无独立指标面板时以日志关键词 + SQL 抽查承载（标注 SQL 的项）；接入 obs 系统后按名称对齐。

| 指标 | 来源 | 告警/关注阈值 |
|---|---|---|
| due_to_start 延迟 | `desktop_automation_runs.due_at` vs `started_at`（SQL：pending 且 `due_at < NOW()-5min`） | 持续增长=扫描/派发停滞 |
| waiting_device 积压 | `desktop_automation_deliveries.state='dispatched'` 且 invocation queued（SQL） | 增长=设备离线/不领单 |
| unknown 条目数 | `desktop_automation_deliveries.state='unknown'`（SQL） | 任何增长需人工逐条核对（§5.3） |
| 目标身份阻断 | 审计/日志：`event_match_skipped`、绑定核验 rejected（候选不唯一） | 出现即核对绑定 |
| 图像验证失败 | 日志：素材 hash 不符 / `ASSET_FILE_MISSING`（500） | 出现即停该任务图片块 |
| journal/outbox 积压 | 日志：`outbox 重投`、`毒丸条目收敛`；SQL：`desktop_automation_outbox` 非 done 计数 | attempts 逼近 10=毒丸 |
| 重复事件/触发拦截 | `weixin_marketing_webhook_nonces` 拒绝（403 nonce_replayed）、occurrences UNIQUE 冲突复用 | 频繁重放=对端异常 |
| 各设备单条耗时与视觉费用 | Runtime 侧日志 + `chat_records`（视觉网关计费） | 按设备串行实测口径，不承诺并发数字 |
| 许可过期清扫 | 日志：`许可清扫 expired=N`（R22 预留保留） | 增长=设备执行超预算 |
| 租约回收 | 日志：`租约回收 reclaimed=N` | 增长=worker 崩溃/失联 |
| webhook 密钥轮换收敛 | `weixin_marketing_event_source_keys` status=retiring 长期滞留（应过窗即 retired） | 滞留=event_match tick 停 |
| 磁盘孤儿素材 | 日志：`检出磁盘孤儿素材 N 个`（开关开启时） | 出现即人工核对（扫描不动删） |

容量口径：单微信桌面吞吐按设备串行实测耗时计算；云端多 worker 不增加单设备吞吐。未压测的并发数字一律不对外承诺。

---

## 4. 回滚 checklist（按序执行）

回滚原则：**先关新授权（实时生效）→ 暂停任务 → 取消在途未开始项 → 收在途回执；unknown 保留，不清队列后重发**。不删业务表/审计/journal，不将新任务降级交给旧 prompt scheduler（微信自动化与旧调度链路架构隔离，R47）。

1. **关新授权（真热读，无需重启）**：`weixin_marketing.enabled: false` ——授权门控（enabled/tenant_allowlist）为**每调用热读**：`get_hot_gate_config()` 每次授权检查 yaml 文件 mtime（文件变化即重解析），yaml 保存后 API 进程内**下一次授权调用**即被拒（403 ADAPTER_DENIED `weixin_marketing_disabled`，许可零签发）——不依赖进程重启，也不同于 tick 节奏等其余配置的 import 时快照。文件不可达/损坏时 fail-closed（拒新授权）。随后仍建议**重启 background_runner** 停掉全部调度 tick（tick 读进程内配置快照且 APScheduler job 注册仅启动时清理）。
   - 更细粒度：仅停时间触发 `time_triggers_enabled: false`；仅停事件 `event_triggers_enabled: false`（同样建议重启生效）。
2. **暂停全部任务**：优先经 API 逐个 pause（`POST /api/weixin-marketing/automations/{id}/pause`，version CAS）——触发既有「撤销未开始工作」语义；量大时可批量 SQL：
   ```sql
   UPDATE bs_weixin_marketing_automations SET status='paused'
   WHERE tenant_id='<tid>' AND status='active';
   ```
   ⚠️ SQL 批量**不触发**未开始工作撤销、不推进 epoch——必须配合第 3 步取消在途项；pause 后授权链亦因 `automation_status:paused` 拒绝。
3. **取消未开始 occurrence/run 的在途 invocation**：优先 API 逐个取消（`POST /api/weixin-marketing/runs/{id}/cancel`：queued invocation→cancelled 终态、claimed/running→cancel_requested，设备侧停止协议完整）。定位待取消项：
   ```sql
   SELECT r.id AS run_id, r.state, i.id AS invocation_id, i.state AS invocation_state
   FROM desktop_automation_runs r
   LEFT JOIN local_tool_invocations i
     ON i.tenant_id = r.tenant_id AND i.business_ref->>'run_id' = r.id::text
   WHERE r.tenant_id='<tid>' AND r.scenario_key='weixin.fixed_content.v1'
     AND r.state NOT IN ('succeeded','failed','cancelled','partial','unknown','expired');
   ```
   批量取消仅限 **queued**（从未被设备领取）且**限微信场景**的 invocation——`business_kind='desktop_automation'` 为底座共享类型（BOSS 等未来场景同用），必须叠加 `business_ref->>'scenario_key'` 过滤，否则会误取消同租户其他场景的排队任务：
   ```sql
   UPDATE local_tool_invocations SET state='cancelled', effect='none'
   WHERE tenant_id='<tid>' AND business_kind='desktop_automation' AND state='queued'
     AND business_ref->>'scenario_key' = 'weixin.fixed_content.v1';
   ```
   ⚠️ claimed/running 一律走 API cancel（cancel_requested 语义），禁止 SQL 直改（破坏设备侧停止协议）。
   已签发未消费 permit 无需手动处理：deadline 在每次使用时 SQL 侧判定，过期即失效（迟到回执按 expired 态接纳结算，R21/R22）。如需状态行收敛，**同样必须限定租户+微信场景**（permits 表为底座共享，缺过滤会波及他租户/他场景）：
   ```sql
   UPDATE local_tool_operation_permits p SET state='expired'
   FROM local_tool_invocations i
   WHERE p.invocation_id = i.id
     AND p.tenant_id = '<tid>'
     AND i.business_ref->>'scenario_key' = 'weixin.fixed_content.v1'
     AND p.state='issued' AND p.deadline < NOW();
   ```
   （R22：过期不释放额度预留，防重复占用。）
4. **在途回执照常接纳（照实）**：operation-result 接纳**不受** enabled 门控（API 路径，既有行为）——设备已开始动作的迟到回执（含 applied/unknown）照常落账与结算（R21/R28；固化断言：`tests/unit/weixin_marketing/test_p5_closeout.py::TestRollbackRealtimeGate::test_late_applied_receipt_lands_while_disabled`——enabled=false 下迟到 applied+verified 仍落账 succeeded）；收集 `desktop_automation_outbox`/runs 的 `run_finished` 台账核对。
5. **unknown 保留不清队列**：`desktop_automation_deliveries` unknown 条目**不得**自动重发（§5.4 不重派红线）；人工经 deliveries resolve（confirmed_not_sent + 受信停止确认）后才可 retry。
6. **数据零删除**：业务表/审计/journal/outbox 全保留；数据库变更只做兼容增量、后清理。
7. **Runtime 侧**：旧 Runtime 继续原 BOSS 链路不受影响；新协议任务只派给支持版本（PROTOCOL_NOT_SUPPORTED 拒绝不降级）。如需完全回退 Runtime：`npm install -g <旧版 tgz>` 后重启后台任务（见 clients/README §七）。
8. **验证回滚生效**：§1.1 冒烟确认 weixin job 零注册；对任一待授权 invocation 重放 write-authorize 应被拒（`weixin_marketing_disabled`，见 §1 检查表新增行）；SQL 确认无新 occurrence 产生。

---

## 5. 故障应急

### 5.1 发错群 / 目标身份错误（最高优先级）
1. 立即 `time_triggers_enabled: false` + `event_triggers_enabled: false`（如允许直接 `enabled: false`）。
2. pause 相关 automation（工作台或 API；pause 撤销未开始工作）。
3. **禁用**涉事 group_binding（state=disabled），重新核验群身份后再启用。
4. 保留 runs/deliveries/审计行供追责；不得删除数据。

### 5.2 自动重复发送
1. 立即 `enabled: false` 停全部触发。
2. 排查方向：occurrences UNIQUE 复用是否失效（同 trigger_key 应只一条）、设备侧重试链、租约回收误重派（回收不重派 may_have_started 条目——如出现即为缺陷，上报）。
3. unknown 条目**不得**批量重发（§4 第 4 条）。

### 5.3 unknown 条目处置
- 逐条人工核对群内实际发送情况 → deliveries resolve（verdict + decision=confirmed_not_sent 需说明）→ 满足双证据（含设备通道停止确认）后按需 retry。
- 无受信停止确认渠道时 fail-closed：不重试，等待 P0 真机能力补齐。

### 5.4 设备失联 / 长期 waiting_device
- invocation 超截止自动终态；run 租约到期由 reclaim 收敛（不重派已提交条目）。
- 设备恢复后新任务正常派发；历史 unknown 按 §5.3。

### 5.5 webhook 洪峰 / 异常源
- 限流 429 是预期行为（未接纳明确拒绝）；必要时停 `event_triggers_enabled` 或禁用单个 event source（status 置非 active）。
- 密钥泄露：rotate-key（旧 key 进 900s retiring 并行窗后自动 retired；收敛由 event_match tick 执行）。

---

## 6. 配置键总表（configs/config.yaml `weixin_marketing` 节）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总门控：false=零注册零执行零 DB 动作 |
| `time_triggers_enabled` | `true` | 时间触发细分开关（enabled 内） |
| `event_triggers_enabled` | `false` | 事件触发订阅与匹配 worker |
| `images_enabled` | `false` | 图片内容块/素材上传链路 |
| `evidence_real_mode` | `false` | true=真实证据校验器缺失即 fail-closed（R43） |
| `max_blocks` | `20` | 每 revision 内容块上限 |
| `max_interval_frequency` | `300` | interval 触发最小间隔秒数 |
| `dispatch_batch_size` | `20` | 执行驱动 tick 单批 run 数 |
| `retention_days` | `90` | 素材 retention_until 依据（上传时冻结） |
| `tenant_allowlist` | `[]` | 空=不限租户；非空=命中才放行（时间扫描 SQL 下推） |
| `quotas.window_seconds` / `tenant_limit` / `task_limit` / `target_limit` / `account_limit` | `3600`/`100`/`30`/`10`/`60` | 发送配额（quota scopes，R9/R19：used+reserved 判定） |
| `time_scan_interval_seconds` | `5` | 时间扫描 tick 间隔 |
| `dispatch_interval_seconds` | `5` | 执行驱动 tick 间隔 |
| `permits_sweep_interval_seconds` | `30` | 许可过期清扫 tick 间隔 |
| `runs_reclaim_interval_seconds` | `60` | run 租约回收 tick 间隔 |
| `asset_max_bytes` / `asset_max_pixels` | `10485760`/`25000000` | 素材上传上限 |
| `assets_cleanup_interval_seconds` | `3600` | 过期素材清理 tick 间隔 |
| `data_retention_days` | `30` | P5：payloads/occurrences 保留期（天） |
| `retention_cleanup_interval_seconds` | `86400` | P5：保留期清理 tick 间隔 |
| `assets_orphan_scan_enabled` | `false` | P5：磁盘孤儿素材扫描（只读告警，默认关） |
| `assets_orphan_scan_interval_seconds` | `86400` | P5：孤儿扫描 tick 间隔 |
| `event_match_interval_seconds` / `event_match_batch_size` | `5`/`100` | 事件匹配 worker 节奏 |
| `webhook_rate_limit_per_minute` | `120` | webhook 按源限流 |
| `webhook_max_body_bytes` | `262144` | webhook 请求体上限（413） |
| `webhook_key_rotate_window_seconds` | `900` | 密钥轮换旧新并行窗 |

> 改 yaml 后：授权门控（enabled/tenant_allowlist）经 `get_hot_gate_config()` 每调用热读（mtime 缓存，免重启即生效）；其余配置消费点（tick 节奏/配额/素材上限等）读进程内 settings 快照，**需重启 background_runner 生效**（APScheduler job 注册仅启动时执行一次）。
