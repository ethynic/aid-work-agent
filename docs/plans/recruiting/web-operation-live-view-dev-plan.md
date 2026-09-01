# 网页操作实时视图开发计划（Boss CDP 首期）

> 版本：v2.0（2026-09-01）
>
> 状态：📋 待开发
>
> 设计基线：[网页操作实时视图设计 v3](../../design/recruiting/login-assist-page-stream-design.md)
>
> 实验依据：[CDP 页面实时帧实验纪要](../../research/cdp-page-stream-experiment-notes.md)

本计划是交给 GLM5.3 的施工单。技术选型以设计 v3 为唯一依据；任务中的文件为必须修改/新增落点。发现现有符号名有小差异时可按同目录惯例调整，禁止自行改变状态机、API、DDL、WSS 认证或部署拓扑。

## 1. 完成定义与执行规则

全部完成才可把 `docs/ideas.md` 条目移入 `docs/ideas_finished.md`：

- 未 consent：无 stream/session command、无截图、无 producer 连接。
- consent 后 P95 首帧 ≤2 秒；用户停止或无 viewer 15 秒后 producer 停止。
- 普通 Boss 操作、`boss_login_qr` 真机、Web 恢复、跨租户安全、100 stream 压测全部通过。
- API 多 worker 与 Gateway 单 worker 部署可运行；帧未进入任何持久设施。
- Runtime/Boss/frontend build 与既有回归全绿。

每个 Phase 按项目 `dev-workflow` 串行走开发→独立测试→独立 CodeReview；完成后勾选本文件。开始开发时将 ideas 状态改为 🔧 部分完成。不得自动 commit/push。

## 2. 实施顺序与依赖

```text
P0 协议/DDL冻结
 ├─> P1 控制面与授权 ─> P2 Gateway ─> P4 Web UI
 └─> P3 Runtime/Boss producer ──────────┘
                         └─> P5 登录辅助 ─> P6 集成/安全/灰度
```

P1 完成后 P2/P3 可并行；P4 等 P1/P2；P5 等 P1/P2/P3。真实 Playwright 不在首发范围，只做 contract fake。

## 3. Phase 0：协议、配置与数据库骨架（1 天）

状态：⬜ 未开始

### 修改文件

- 新增 `src/page_stream/models.py`：所有 enum、dataclass/Pydantic DTO、CAS 迁移表。
- 新增 `src/page_stream/protocol.py`：协议常量、limits、metadata 校验；不得包含业务 DB 访问。
- 新增 `contracts/page-stream/v1/*.schema.json`：`hello`、`frame`、`heartbeat`、`gateway_error`、`stream_command`。
- 新增 `contracts/page-stream/v1/fixtures/*.json`：每个 schema 的 valid/invalid golden fixture。
- 修改 `src/config/settings.py`、`configs/config.yaml`、`.env.example`：读取下列配置并做启动校验。
- 修改 `deploy/init-postgres.sql`、`deploy/db_update.sql`：设计 v3 的 5 张表、CHECK、索引、外键/tenant 索引。

### 固定配置

```text
PAGE_STREAM_ENABLED=false
PAGE_STREAM_TENANT_ALLOWLIST=<逗号分隔tenant id，默认空即无人可用>
PAGE_STREAM_USER_ALLOWLIST=<逗号分隔user id，默认空表示允许已入围tenant的全部用户>
PAGE_STREAM_BOSS_NORMAL_ENABLED=false
PAGE_STREAM_BOSS_LOGIN_ENABLED=false
PAGE_STREAM_GATEWAY_PUBLIC_URL=wss://<当前域名>/api/page-stream/v1
PAGE_STREAM_GATEWAY_INTERNAL_URL=http://aid-page-stream-gateway:8010
PAGE_STREAM_CONTROL_INTERNAL_URL=http://aid-agent-api:8000
PAGE_STREAM_INTERNAL_TOKEN=<至少32字节随机值，必填>
PAGE_STREAM_TICKET_SIGNING_KEY=<至少32字节且与internal token不同，必填>
PAGE_STREAM_PORT=8010
PAGE_STREAM_MAX_STREAMS=100
PAGE_STREAM_MAX_VIEWERS=100
PAGE_STREAM_MAX_FRAME_BYTES=524288
PAGE_STREAM_MAX_EDGE=1920
PAGE_STREAM_MAX_FPS=2
PAGE_STREAM_QUALITY=70
PAGE_STREAM_OFFER_TTL_SECONDS=120
PAGE_STREAM_TICKET_TTL_SECONDS=60
PAGE_STREAM_SESSION_TTL_SECONDS=600
PAGE_STREAM_VIEWER_GRACE_SECONDS=15
```

### 实现要求

- DDL 与设计字段一一对应；`db_update.sql` 全部 `IF NOT EXISTS` 或等价幂等迁移；Gateway event 用 `event_id` 表持久去重。
- 外键/复合 UNIQUE 按设计 §4.5 原样实现且 `ON DELETE RESTRICT`；历史审计不能级联丢失。
- Python enum value 必须与 DB CHECK 和 TypeScript union 完全相同。
- 增加 schema fixture 校验测试，拒绝未知 message type、未知字段和大于协议上限的元数据。

### 测试/验收

```bash
./scripts/dev_test.sh tests/unit/page_stream/test_protocol.py tests/unit/page_stream/test_migrations.py -q
```

- 全新库执行 init 后表/索引完整；已有库连续执行 `db_update.sql` 两次无错误。
- 环境变量缺少 internal token 且 feature flag 开启时启动失败；关闭 flag 不影响现有服务。

## 4. Phase 1：控制面、事务门禁与 Agent 事件（3 天）

状态：⬜ 未开始

### 修改文件

- 新增 `src/page_stream/repository.py`：offer/session/command CRUD、CAS、claim、reaper 查询。
- 新增 `src/page_stream/service.py`：业务策略、consent/stop/close_scope、ticket 签发协调。
- 新增 `src/page_stream/tickets.py`：标准库 HMAC-SHA256 签发/校验、固定 claims 和 ticket error。
- 新增 `src/api/page_streams.py`：设计 §7.1 与 internal callback API。
- 修改 `src/main.py`：include router；SSE 透传结构化事件；Agent execution finally 关闭 scope。
- 修改 `src/local_tools/repository.py`：`create_invocation_with_page_offer`、登录 claim 门禁。
- 修改 `src/local_tools/proxy_tool.py`：识别受信 surface/scope，创建/复用 offer，push 结构化事件。
- 修改 `src/local_tools/api.py`：stream command claim/ack/state API。
- 修改 `src/core/agent.py`：`ExecutionContextFactory.for_agent_call` 显式传 `channel=self._detect_source_type()`；`_run_local_required_tool` 从 context 注入 session/channel/execution scope；progress queue 支持结构化 passthrough。
- 新增 `src/page_stream/reaper.py` 并修改 `src/background_runner.py`：每 5 秒处理过期 offer/session/command、设备离线、过期 claim；claim/consent API 同时做 opportunistic cleanup。

### 必须按顺序实现

1. repository 先写 5 表事务方法和 CAS；单元测试覆盖唯一索引竞态与 callback event_id 去重。
2. 把原 `create_invocation` SQL 抽为接收已有 connection 的私有 helper；原公开方法行为不变。新方法在同一事务创建 invocation+offer+link，任一步失败整笔回滚。
3. `claim_next` 用 `NOT EXISTS` 关联门禁阻止未 consent 的登录 invocation；reaper 到期直接终结，不放行。
4. consent 严格实现设计 §5.2 Tx1→幂等 Gateway PUT→Tx2 saga。重试 requested 必须重跑 register+Tx2；stale pending 15 秒由 reaper Gateway DELETE+failed；终态 client_request_id 返回原 session，新点击必须新 UUID。
5. stop/close_scope 在同一事务把 session 置 stopping 并 upsert 唯一 stop command；终态重复 stop 返回成功。
6. start claim 原子生成 command token hash+session producer lease hash并 `requested→starting`；stop claim 仅 command token。实现固定 ack endpoint：start ack success 仅 command ack，first_frame 才 live；stop ack success 才 ended。producer-ticket/state 仅 producer lease。
7. ticket claims 固定为设计 §8；Gateway 单进程维护已消费 jti 到 exp。测试后端不得打印 token/header。
8. turn 入口生成/复用 `_turn_execution_id=self.execution_id or 'ae_'+uuid4.hex` 并写 ToolExecutionContext；`LocalToolProxyTool` 读取 `session_id/channel/tool_call_id/agent_execution_id`，缺字段时不建 offer；仅 channel=`chat` 建 offer，其余调用原 `create_invocation`。Desktop 共用 chat SSE，不开放 `desktop_remote_gateway`。

### 核心测试文件

- `tests/unit/page_stream/test_repository.py`
- `tests/unit/page_stream/test_service.py`
- `tests/unit/page_stream/test_tickets.py`
- `tests/unit/page_stream/test_api.py`
- `tests/unit/local_tools/test_page_stream_offer.py`
- `tests/unit/local_tools/test_stream_control_api.py`
- 扩展现有 Agent/SSE 测试，证明 structured event 不变形且不进入模型消息。

### 门禁命令与验收

```bash
./scripts/dev_test.sh tests/unit/page_stream tests/unit/local_tools -q
```

- 并发 20 次相同 consent 只生成一个 session/start command；模拟 register 后崩溃可由重试/reaper 收敛，无永久 requested/Gateway orphan。
- 未 consent 的登录 invocation 连续 claim 均取不到；普通 invocation 不受影响。
- offer 与 invocation 任意插入点注入异常后均无半条数据。
- 跨 tenant/user/device 全部返回 404；ticket 重放第二次失败。
- 同 execution 的 3 次 Boss invocation 只有一个普通 offer 和三条 link。
- start/stop ack 状态矩阵、command token 与 producer lease 互换攻击、lease 终态失效全部拒绝。
- active-offer DTO 含/不含 active_stream、完整 status DTO、401/404/409/410/429/503 错误体逐项断言。

## 5. Phase 2：单进程 PageStreamGateway（2 天）

状态：⬜ 未开始

### 修改文件

- 新增 `src/page_stream/gateway_app.py`：独立 FastAPI app、两个 WebSocket endpoint、`/healthz` 和仅内网 internal register/delete。
- 新增 `src/page_stream/gateway_hub.py`：stream record、latest frame、viewer maxsize=1 queue、grace timer。
- 新增 `src/page_stream/gateway_auth.py`：subprotocol 解析、HMAC ticket 校验、内存 jti 原子消费、active session registry。
- 新增 `src/page_stream/gateway_callback.py`：读取 `PAGE_STREAM_CONTROL_INTERNAL_URL`，callback 3 秒超时、1/2/4 秒退避，event_id 重试不变。
- 新增 `tests/unit/page_stream/test_gateway_*.py`。
- 新增 `scripts/load_test_page_stream.py`：只生成合成 JPEG，不读取真实页面。

### 实现要求

- producer/viewer URL 的 `{stream_id}` 只能是 UUID path 参数；ticket 只能来自 `Sec-WebSocket-Protocol`，query 不接受 ticket。
- Gateway handshake 回显 `page-stream-v1`，绝不回显 `ticket.*`。
- producer 必须 hello 后才能发 frame；metadata/binary 必须严格成对，使用单 producer 锁。
- 验证 JPEG magic、byte_length、512KiB、1920px、2fps；错配分别用 1008/1009 关闭。
- 每 viewer queue=1，替换旧帧；hub 不保留历史。最后 viewer 离开 15 秒回调 `viewer_gone`，由 API 按 purpose 决定 stop/cancel。
- producer 5 秒 hello、10 秒 heartbeat、25 秒 idle；metadata 后 2 秒 binary timeout；viewer 发送任何消息均关闭。每 session 一个 viewer，新 viewer 关闭旧 viewer。SIGTERM 停止接入、逐 stream 回调 `gateway_shutdown`、关闭 sockets、清内存。
- 日志字段 allowlist：stream_id、role、event、duration_ms、bytes、count、error_code；禁止 headers/query/binary。

### 测试/验收

```bash
./scripts/dev_test.sh tests/unit/page_stream/test_gateway_auth.py tests/unit/page_stream/test_gateway_hub.py tests/unit/page_stream/test_gateway_protocol.py -q
python scripts/load_test_page_stream.py --streams 100 --fps 2 --seconds 600
```

- role/stream 错配、票据重放、未知 subprotocol、双 producer、超帧、过速、乱序都被拒绝。
- 慢 viewer 不增加队列长度；100 stream 10 分钟后内存回到基线 ±10%。
- 扫描测试日志无 `ticket.`、JPEG base64 或完整 URL。

## 6. Phase 3：Runtime 控制循环与 Boss CDP producer（3 天）

状态：⬜ 未开始

### Runtime 文件

- 修改 `clients/agent-tool-runtime/package.json`：显式添加 `ws` 与 `@types/ws`，不能依赖 bundled Boss 包的传递依赖。
- 新增 `clients/agent-tool-runtime/src/stream/protocol.ts`、`frameSource.ts`、`gatewayClient.ts`、`streamControlLoop.ts`、`bossCdpFrameSource.ts`。
- 修改 `clients/agent-tool-runtime/src/cli.ts` 与 `pollLoop.ts`：在同一 runtime 生命周期并行 heartbeat、invocation claim、`StreamControlLoop`；shutdown 顺序为停 control claim→abort producer Map→最多 5 秒 state 回报→provider shutdown。
- 修改 `src/apiClient.ts`：claim/ack/state typed client。
- 修改 `src/manifestVerifier.ts` 和 trusted manifest/capabilities：声明 `page-stream/1.0`、`boss-cdp` source key。
- 修改 `src/cli.ts`/doctor 对应实现：增加只 probe、不截图的 stream source 检查。

### Boss 包文件

- 新增 `clients/boss-resume-assistant/src/main/chrome/BossPageTargetResolver.ts` 和 `src/pageStream.ts`；`package.json.exports['./page-stream']='./dist/src/pageStream.js'`，Runtime 只允许 `import ... from 'boss-resume-assistant/page-stream'`，禁止 deep-import dist。
- 修改 `clients/boss-resume-assistant/src/main/cdp/CdpGateway.ts` 的 `attachToRecommendPage()`、`src/main/operations/bossContext.ts` 的 `getUrl()` 及 operations 入口，统一调用 resolver/attached target id。
- 修改 `CdpGateway.ts` 的 screenshot wrapper：支持 `quality/fromSurface/clip`；`src/main/cdp/methodPolicy.ts` 保持截图白名单且继续禁止 `Runtime.*`。
- `source_ref` 由云端在 offer 事务写为 `invocation:<first_invocation_id>`，start command 原样下发；target id 永不进入 invocation result、DB、LLM 或 UI。

### 实现要求

- `StreamControlLoop` 独立 20 秒 long-poll，不经过 `ProviderManager` 的串行 invocation queue。
- 对一个 stream 维护 AbortController。start 用 command 的 `source_adapter_key+source_ref` probe，连 producer WSS 后用 command token ack；stop abort 后用 stop command token ack。日常状态/取新票只用 producer lease。
- WSS 断开不自动无限重连：1/2/4 秒共 3 次，仍失败上报 `STREAM_GATEWAY_UNAVAILABLE` 并 stop source。
- CDP capture 固定 q70/1fps，策略只可下调或升到 2fps；SHA-256 去重。每帧前验证 origin。
- resolver 0/多 target fail-loud；严禁沿用“第一个 zhipin.com tab”。stop 只 detach。

### 测试与命令

在各 package 现有测试 runner 下新增 resolver、frame source、control loop、WSS client、manifest 测试：

```bash
npm --prefix clients/boss-resume-assistant run typecheck
npm --prefix clients/boss-resume-assistant test
npm --prefix clients/agent-tool-runtime run typecheck
npm --prefix clients/agent-tool-runtime test
```

- Runtime 正执行一个 60 秒 invocation 时仍可在 1 秒内领取 start/stop command；stream producer 在后台 Map 运行，不阻塞下一条 stop claim。
- 0/1/2 个 Boss tab 分别返回 not-found/成功/ambiguous；所有原 Boss operation 回归通过。
- 静止 20 秒只出 1 帧；origin 跳出 allowlist、Chrome 关闭、Gateway 断线均 ≤5 秒停止。

## 7. Phase 4：Web Agent 事件与只读播放器（2 天）

状态：⬜ 未开始

### 修改文件

- 新增 `frontend/web/api/pageStreams.ts`：active offers、consent、ticket、status、stop。
- 修改 `frontend/web/api/agent.ts`：`page_view_available/page_stream_state_changed` union。
- 修改 `frontend/web/types/index.ts`：DTO/state/error 类型。
- 修改 `frontend/web/composables/useAgent.ts`：会话级 `activePageViewOffers`；SSE upsert；loadSession 后主动查询。
- 新增 `frontend/web/components/page-stream/LivePageView.vue`。
- 新增 `PageViewConsentCard.vue`、`PageViewTray.vue`。
- 修改 `frontend/web/components/ChatContainer.vue`，把 tray 放在 `MessageList.vue` 底部；不修改 `MessageItem.vue` 去伪造历史消息。
- 小范围重构 `frontend/web/components/browser/BrowserView.vue`：抽共享 decoder/composable，保持原人工接管 API/UX 不变。

### 交互实现

1. SSE/active query 只显示 offer；不 consent。
2. 用户点击生成 UUID `client_request_id`，按钮 pending 时防重复；网络重试复用 UUID。
3. consent 成功 → viewer-ticket（API 先重新 register Gateway）→ `new WebSocket(url,['page-stream-v1','ticket.'+ticket])`。
4. metadata 到达后只接受下一条 binary；seq 非递增或 length 不符丢弃并显示协议错误。
5. 每次替换前 revoke 前一个 object URL；unmount 全部 revoke/close。
6. `visibilitychange` hidden 30 秒后 DELETE；15 秒内刷新重连只获取新 viewer ticket，不再次 consent。
7. viewer 不发送任何消息；不绑定 canvas/input/clipboard；卡片明确“只读、画面不保存”，无链接复制/下载。
8. 最终 assistant metadata 写入/恢复 `pageViewOffers`，同时 DB active-offer query 兜底未完成 SSE；按 offer_id 去重。

### 测试与命令

新增 `frontend/web/__tests__/components/page-stream/` 和 `frontend/web/__tests__/composables/useAgent.pageStream.test.ts`：

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run typecheck:desktop
npm --prefix frontend test -- --run web/__tests__/components/page-stream web/__tests__/composables/useAgent.pageStream.test.ts
npm --prefix frontend run build
npm --prefix frontend run build:desktop
```

- mounted、历史恢复、SSE 重连断言 consent API 调用数为 0。
- 刷新查到 active_stream 时只调用 viewer-ticket，不调用 consent；无 active_stream 才显示按钮。
- 双击/请求重试只创建一个 stream；停止、hidden、过期、掉线路径均有确定 UI。
- 原 `HumanAssistanceCard/BrowserView` 回归，证明控制模式未受影响。

## 8. Phase 5：Boss 登录辅助（2 天）

状态：⬜ 未开始

### 修改范围

- 在 Boss operations 目录新增 `loginQr.ts`、`loginStatus.ts` 及其单元/真机测试。
- 同步 Boss `toolDefs`、manifest 和 CLI/MCP handler。
- 同步 Runtime trusted manifest/manifest digest。
- 同步云端 local tool catalog/proxy policy：`boss_login_qr.page_view={purpose:login_qr,start_policy:wait_for_consent}`；status 不开启 page view。
- 同步 recruiting-operator `tools.allowed` 与授权说明；更新相关设计/工具清单数量，禁止留下 “18 tools” 等过时断言。

### 固定算法

按设计 §10 实现 `BossLoginRegionResolver`：DOMSnapshot attributes class token、parentIndex 祖先、160–260px 方形 IMG 唯一规则、CDP pressed/released center click。offer 120 秒、operation/session 10 分钟、1 秒轮询、5 秒 progress、clip+16px、精确文本刷新、URL 成功判断、本地确定性 overlay heal。严禁 CSS Runtime、固定坐标、Win32 click、整页 QR 退化或云端 LLM overlay。

登录 viewer 连续消失 30 秒时，service 同时请求取消关联登录 invocation；普通 observe stream stop 不取消业务 invocation。

### 测试/验收

```bash
npm --prefix clients/boss-resume-assistant test
npm --prefix clients/agent-tool-runtime test
./scripts/dev_test.sh tests/unit/local_tools tests/unit/page_stream -q
```

真机清单：未登录→Web 点击→2 秒内 QR→手工触发/等待一次过期刷新→手机扫码→overlay 关闭→Agent 报成功→stream 终止。另测不点击 120 秒、用户中止、Chrome 关闭、selector 失效；均不越权继续。

## 9. Phase 6：部署、端到端、安全和灰度（2 天）

状态：⬜ 未开始

### 部署文件

- 修改 `docker-compose.prod.yml`、`docker-compose.prod-eas.yml`、`docker-compose.test.yml`、`docker-compose.dev.yml`：新增 service `aid-page-stream-gateway`，容器名分别按设计固定，命令/端口/healthcheck/日志限制一致，宿主机仅 127.0.0.1 绑定。
- 修改 `deploy/agent.aidingyi.cn.conf`、`agent2.aidingyi.cn.conf`、`agent3.aidingyi.cn.conf`、`192.168.200.72.conf`：独立 WSS location、端口 8010/8011/8014。
- 修改 `deploy/agent_update.sh`、`agent2_update.sh`、`agent3_update.sh`、`check_deployment.sh`：compose 启动、`docker update` 资源限制、健康检查、日志和回滚同时覆盖对应 gateway 容器。
- 修改 `src/config/settings.py`、`configs/config.yaml`、`.env.example`、部署文档/隐私说明：列出配置、feature flags、单实例限制、帧不保存和告警。

### Nginx 必需配置语义

`location /api/page-stream/v1/` 必须先于通用 `/api/`；`proxy_http_version 1.1`、Upgrade/Connection、`proxy_buffering off`、`proxy_request_buffering off`、`proxy_read_timeout 75s`，并使用环境对应的 gateway 端口。access log 不记录 query；协议本身也不传 ticket query。

### E2E/安全矩阵

- `tests/integration/page_stream/`：真实 Postgres+Redis、API 4 workers、Gateway 1 worker、fake Runtime/WebSocket。
- `tests/e2e/page_stream/`：Web 登录态、SSE offer、consent、首帧、刷新、stop、scope finish。
- 跨 tenant/user/device、ticket replay、role swap、stream swap、伪 internal token、非法 origin、双 producer、连接耗尽、超帧、超速、日志扫描。
- 100 stream 变化 JPEG 压测记录 CPU、RSS、入/出带宽、P95 首帧、drop、清理时间；报告写入 `docs/research/` 并登记 `docs/ideas.md`。

### 灰度与回滚

flag 默认 false，求值顺序固定为 global AND tenant AND user AND 场景（Boss-normal/Boss-login）。灰度顺序：全局关闭 → 内部 tenant 普通只读 → 内部 tenant login → 小批 tenant。Gateway 故障不阻断普通 Boss operation；登录 fail-closed。关闭全局 flag 后拒绝新 consent，reaper 生成 stop，表保留审计。

## 10. 文件级交付检查表

- [ ] 协议 schema/fixtures 与 Python/TS 类型一致
- [ ] 5 表 DDL、repository、CAS、reaper、Gateway event 幂等
- [ ] 原子 invocation+offer 与 wait claim 门禁
- [ ] consent/ticket/status/stop/internal event API
- [ ] Runtime command loop 与 producer WSS
- [ ] Gateway single-worker latest-frame hub
- [ ] 共享 Boss target resolver 与 CDP frame source
- [ ] Web tray/card/player/恢复逻辑
- [ ] `boss_login_qr/status` 全清单同步
- [ ] compose/Nginx/update/check/隐私文档
- [ ] unit/integration/e2e/load/security 证据
- [ ] 设计、计划、`docs/ideas.md` 状态同步

## 11. 禁止开发者自行决定的事项

- 不得把 ticket 放 query、把 JPEG 放 Redis/DB、复用 `BrowserViewHub`、让 LLM 调用 start stream。
- 不得把普通流绑定单 invocation 后要求用户反复同意；以 execution scope 聚合。
- 不得把 consent 设为 offer 终态；允许有效期内重新观看，每次新 session。
- 不得让登录 invocation 在 consent/offer 超时后继续 claim。
- 不得选择第一个 BOSS tab、使用固定坐标、启用 CDP `Runtime.*` 或 QR 找不到时退化整页。
- 不得在本期迁移真实 Playwright；只交付统一 contract 与 fake adapter。
