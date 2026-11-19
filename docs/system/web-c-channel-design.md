# 官网客服渠道（web_c）设计方案

> 状态：Phase 1+2 已实现（2026-11-16，代码完成待部署真机验收；实现偏离点见下）
> 增补：uid 模式（仅 userid 链接，免签名，2026-11-19 设计定版并实现，见 §3.5）
> 实现偏离：C 端上传落盘名用 `{file_id}{ext}`（设计原稿 upload_{uuid12}.{ext}）——Runner 侧 `resolve_scoped_uploaded_file` 磁盘兜底按 basename==file_id 匹配，Redis TTL 过期后仍可解析；C 端前端以 GET runner 快照轮询驱动流式渲染（SSE 事件流为无载荷变更通知，每帧触发快照刷新，连续失败回退轮询），SSE 透传端点保留。
> 定版命名：channel_type = `web_c`，显示名 = **官网客服**
> 定版命名：channel_type = `web_c`，显示名 = **官网客服**
> 关联：[ChannelRunner 架构](agent-application-architecture-design.md)、[数据库系统表说明](database_system_table.md)、[消息表分离规则](../incidents/context-reconstruction-pitfalls.md)

## 1. 背景与目标

现有渠道中，wecom_kf（微信客服）允许 C 端用户免登录与智能体会话，但要求租户开通企业微信；Web 聊天仅面向 B 端（租户员工）且必须登录。本方案新增 **官网客服（web_c）** 渠道：租户管理员在本租户开通配置后，将「AI 聊天」入口嵌入租户官网（按钮跳转或 iframe），C 端访客免注册/免登录直接对话。

身份三种来源：

| 模式 | 场景 | 身份凭证 | 历史会话可见范围 |
|------|------|---------|----------------|
| 签名链接（ext） | 租户官网自有登录体系 | 官网服务端生成的 HMAC 签名链接（防伪造） | 同一 userid，跨设备可见 |
| 匿名（anon） | 官网无登录体系 | 前端生成随机 uid 存 localStorage + cookie 双写 | 同一浏览器可见；换浏览器/清存储丢失（本质属性） |
| 仅 userid 链接（uid，2026-11-19 增补） | 官网只能改前端、无签名服务 | URL 直接携带官网 userid（不验签，§3.5） | 无（服务端强制关闭，防伪造 userid 读他人历史）；租户管理员在「官网接待客户」页可看全部 |

Phase 1 交付渠道核心（配置、C 端聊天页、执行链路、附件/图片上传、留资接入、防滥用与计费）；Phase 2 仿照「微信接待客户」页交付租户侧「官网接待客户」查看页（含留资线索 Tab）。转人工暂不考虑（裁决 2026-11-16）。

## 2. 总体架构

```
租户官网（按钮/iframe）
  -> C 端聊天页 frontend/web（公开路由 /c/{tenant_id}/{config_id}，免登录）
    -> C 端 API src/saas/api/web_c.py（身份验签 + 限流 + 积分拦截）
      -> ChannelRunnerAgent（source='web_c'，session kind='channel'）
        -> Runner worker（执行、token 计费结算）
      <- Runner events SSE 透传（token 级流式展示）
    <- channel_sessions / channel_messages（会话与消息持久化）
```

关键决策：

1. **web_c 按渠道语义落地**，会话与消息写入 `channel_sessions` / `channel_messages`（不混入 B 端 `chat_messages`，遵守消息表分离规则）；Runner 侧 `session.kind='channel'`，上下文重建读渠道消息表。
2. **复用既有渠道管线**：ChannelFactory 按 `(tenant_id, channel_type, config_id)` 缓存 adapter；session_id 由 `ChannelSessionManager._generate_session_id` 自动生成；防滥用、计费归属沿用渠道口径。**零新增数据表**。
3. **流式直连 Runner events**：web_c 是我方自营页面，不走 ChannelRunnerAgent 的同步轮询回发（那是给微信/钉钉等异步渠道的），改为「提交受理拿 runner_id -> SSE 透传 Runner events」实现 token 级流式；消息落库职责由 C 端 API 层补齐（见 §5.3）。
4. **平台通用代码零租户专名**：web_c 的所有租户差异（密钥、greeting、绑定子智能体）都在 `tenant_channel_configs.config` JSON 内。

## 3. 身份与安全设计

### 3.1 channel_user_id 构成

| 模式 | channel_user_id 格式 | 说明 |
|------|---------------------|------|
| 签名 | `ext_{官网userid}` | 官网 userid 原样透传，`ext_` 前缀与匿名区分，便于接待页统计 |
| 匿名 | `anon_{uuid4}` | 前端生成，localStorage + cookie 双写 |
| 仅 userid | `uid_{官网userid}` | 不验签只做归属；与 `ext_` 分命名空间，伪造 uid 链接触碰不到签名用户的会话（§3.5） |

session_id 自动生成为 `{tenant_id}_web_c_{channel_user_id}_{subagent_id}`（channel_chat_id 为空不纳入）。

### 3.2 签名链接协议（ext 模式）

- URL 形态：`https://{域名}/c/{tenant_id}/{config_id}?u={userid}&name={urlencode(昵称)}&ts={unix秒}&sig={签名}`
- 签名：`sig = HMAC-SHA256(key=channel_secret, message=f"{u}.{ts}.{name}")` 的 hex 小写；`name` 参与签名但可缺省（缺省时签名串用空串）。
- 校验：`ts` 与服务端时间差 ≤ 24h；重放容忍（同一链接期内复用可接受，泄露影响面等同 wecom_kf 单客户链接泄露）。
- `channel_secret` 在租户开通 web_c 渠道时由服务端自动生成（`secrets.token_hex(32)`），存 `tenant_channel_configs.config` JSON，渠道配置页展示 + 可重置（重置后旧链接全部失效，前端需提示租户更新官网）。
- **租户官网必须在其服务端签名**（官网有登录体系即有服务端），前端 JS 内嵌密钥等于没有签名。配置页给出签名算法说明与伪代码示例（Python/Node/PHP 三选一按需）。
- 伪造防线意义：无签名时任何人改 URL 即可冒充任意官网用户读其历史会话，这是本方案的硬安全边界；租户无法做服务端签名的，引导走匿名模式。

### 3.3 匿名身份持久化（anon 模式）

- 前端生成 `anon_{uuid4}` 后同时写 localStorage（key 含 config_id，多租户多渠道不串）与 cookie（1 年，`SameSite=Lax`，path 域级），读取时 localStorage 优先、cookie 兜底互写。
- 浏览器兼容预期（写入配置页帮助文字）：
  - 按钮新开标签（第一方上下文）：长期保留，无 ITP 限制。
  - iframe 嵌入：Chrome/Firefox 按顶级站点分区，同租户官网内稳定；**Safari ITP 对第三方 iframe 的 localStorage/IndexedDB 有 7 天不活跃清除，第三方 cookie 直接全禁** -> iframe + Safari 下匿名身份约 7 天有效，属浏览器策略非缺陷，文档明示。
  - 用户在意持久性 -> 引导租户走签名链接模式。

### 3.4 C 端 API 鉴权

所有 `/api/c/*` 请求免 Bearer token，鉴权按模式：

| 模式 | 请求携带 | 服务端校验 |
|------|---------|-----------|
| ext | `u` + `ts` + `sig`（query 或 header） | config_id 归属租户且 `channel_type='web_c'` 且 `enabled`；HMAC 验签；ts 时效 |
| anon | `uid`（query 或 header） | config_id 归属租户且 enabled；uid 格式（`anon_` + uuid） |
| uid | `u`（query 或 header） | config_id 归属租户且 enabled 且 `identity_mode='uid'`；userid 格式白名单（同 ext 正则）；无签名校验（§3.5） |

校验失败一律 401/403，不区分失败原因细节（防枚举）。所有会话/消息查询强制叠加 `tenant_id + session_id 前缀` 双重过滤，杜绝跨租户、跨身份读他人会话。

### 3.5 uid 模式（仅 userid 链接，免签名，2026-11-19 增补）

**定位**：匿名（anon）与签名链接（ext）之间的折中。租户官网只改前端（嵌入聊天页链接时带上官网 userid），无需后端签名服务；代价是**无跨访问会话连续性**（设计使然）。

| 维度 | anon | uid | ext |
|------|------|-----|-----|
| 官网改造 | 无 | 仅前端 | 前端 + 后端签名 |
| 身份可信 | 不可信（浏览器随机） | 不可信（URL 可伪造） | 可信（HMAC 验签） |
| 跨设备/跨访问连续性 | 无 | 无 | 有 |
| 历史会话入口 | 开关可选 | 服务端强制关闭 | 无（按 userid 跨设备续聊） |

**安全模型**：userid 不验签 = 任何人可伪造他人 userid，因此 **userid 只做归属（接待页按人统计），绝不作为会话读取凭据**。防线三条：

1. **无确定性默认会话**：uid 模式不调用 `get_or_create_session` 按 userid 派生默认会话（ext/anon 沿用），首条消息一律惰性创建随机后缀会话（`_s{12hex}`，复用 `_create_extra_session_sync`），session_id 48bit 熵不可枚举。
2. **会话 id 由前端 sessionStorage 持有**（key `web_c_sess_{config_id}`，每标签页独立，try/catch 降级内存持有）：真实用户同一标签页刷新/站内跳转保持会话；关标签页再进即新会话（等价「每次进入都开新会话」且刷新不断线）；攻击者伪造 userid 后本地无受害者 session_id，只能开新空会话。sessionStorage 绑定标签页而非页面加载，租户官网整体刷新（iframe 随之重载）不丢会话。
3. **服务端闸门（不依赖前端）**：
   - `/bootstrap`：uid 模式 `sessions` 恒空、`show_history` 恒 false（开关只挡 UI 不挡 API，必须在服务端置空）；
   - `GET /sessions`：uid 模式恒返回空列表；
   - `_assert_session_owned` 在 uid 模式额外要求 `_s{8hex}` 以上随机后缀，防将来任何确定性会话 id 在 uid 模式下可寻址。

**channel_user_id 前缀 `uid_{userid}`**：与 `ext_{userid}` 分命名空间，同一官网用户走签名与走 uid 链接是两个独立访客/会话域，伪造 uid 链接无法触碰签名用户的任何会话。

**前端行为**（`webC.ts` / `WebCChat.vue`）：URL 有 `u` 即透传（签名三元组齐备为 ext，仅 u 为 uid 链接，可用性由后端 `identity_mode` 门控，非 uid 渠道 403）；uid 模式下当前会话读写 sessionStorage、首条消息后落存储、「新会话」按钮清存储进空白聊天、历史会话入口仅 anon 模式渲染。

**残余接受项**：
- display_name 来自 URL `name` 参数（请求自身参数，不读库），伪造者只能污染自己会话的显示名，无法读取他人信息；接待页可能看到伪造的 name/userid（归属污染接受，与 ext 的「官网 userid 唯一」约定同级）。
- sessionStorage 在隐私模式/部分浏览器第三方 iframe 策略下不可用时降级为内存持有 → 刷新即新会话，只损失连续性不损失安全性。
- uid 模式下签名参数（ts/sig）被忽略，不做「验签通过升级 ext」的混合语义，避免同一链接两套会话域。

**UX 口径（对接文档/配置页）**：uid 模式无跨访问连续性，想要连续性升级签名链接。

## 4. Phase 1 详细设计

### 4.1 配置模型（复用 `tenant_channel_configs`，零 DDL）

一条 web_c 渠道 = `tenant_channel_configs` 一行：`channel_type='web_c'`，`config` JSON：

```json
{
  "enabled": true,
  "greeting": "您好，我是XX公司的智能助手...",
  "channel_secret": "<自动生成的32字节hex>",
  "identity_mode": "anon"        // ext / anon / uid（2026-11-19 起下线 both 组合模式，缺省 anon），控制页面是否显示历史入口等
}
```

绑定子智能体沿用渠道级 `subagent_type` 字段（与 wecom_kf 渠道级绑定同口径）。接入改动点：

| 位置 | 改动 |
|------|------|
| `src/saas/api/channel_config.py:280` | 渠道类型白名单加 `web_c`；`_REQUIRED_FIELDS`（:58）web_c 为空（无第三方凭证），创建时自动生成 `channel_secret` |
| `src/saas/services/channel_factory.py:116-158` | `_ADAPTER_CLASSES` 加 `web_c -> WebCAdapter` 映射 |
| `src/models/message.py:14-19` | `ChannelType` 加 `WEB_C = 'web_c'` |
| `src/saas/models/enums.py:421-447` | `ChatRecordSourceType` 加 `web_c` |
| `configs/config.yaml:531` | `agent_runner.peers` 的 web peer `sources` 加 `"web_c"`（否则渠道进程调 Runner 403，见 `src/services/agent_runner/authorization.py:30`） |

web_c 的 adapter 是薄壳：无第三方回调、无 crypto、`parse_message` 仅做 `UnifiedMessage` 组装、`send_message` 为 no-op（回复经 SSE 与落库投递，不经 adapter 推送）。`verify_signature` 用默认放行。

### 4.2 C 端 HTTP API（新模块 `src/saas/api/web_c.py`）

挂载 prefix `/api/c/web_c/{tenant_id}/{config_id}`，全部走 §3.4 鉴权 + 限流：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/bootstrap` | GET | 校验渠道可用性；返回 greeting、绑定子智能体信息、身份回显（ext 模式回显 name）、最近会话摘要 |
| `/chat` | POST | 提交消息：`{text, client_request_id}` -> `{runner_id, session_id}` |
| `/runners/{runner_id}/events` | GET (SSE) | 透传 Runner events（`text/event-stream`，支持 `Last-Event-ID` 续传）；终态时负责落库（§5.3） |
| `/sessions` | GET | 当前身份的历史会话列表（`channel_sessions`，按 updated_at DESC） |
| `/sessions/{session_id}/messages` | GET | 指定会话历史消息（新会话从旧会话切回时渲染） |
| `/runners/{runner_id}/cancel` | POST | 用户主动停止当前执行（透传 Runner cancel） |
| `/upload` | POST (multipart) | C 端附件/图片上传（§4.6），返回 `{file_id, name, type, mime_type, size}` ref |
| `/files/{file_id}` | GET | C 端自有附件代理下载（§4.6，免登录身份校验 + 归属校验） |

安全要点：`runner_id`/`session_id` 必须校验归属于当前验签身份（防遍历 runner_id 窃听他人事件流）；SSE 代理层在 Runner 侧事件与本地 session 绑定做一致性检查。

### 4.3 执行链路与流式

提交路径复用 `ChannelRunnerAgent`（`src/channels/runner_agent.py`），**新增一个拆分方法**：把 `process_message_sync` 中「活跃 runner 处理 + 追问 reply/controls + 提交受理」段（:115-188）抽出为 `submit(user_input, attachments) -> runner_row`（保留取消旧 runner、追问经 reply control 接车的语义不变），既有 `process_message_sync` 改为调用 `submit()` 后沿用原轮询段，行为不回归。

C 端流程：

1. `POST /chat`：验签 -> 限流 -> 积分拦截 -> `get_or_create_session` -> 用户消息 `add_message`（`channel_messages`，role=user）-> `ChannelRunnerAgent.submit()` -> 返回 `runner_id`。
2. `GET /runners/{runner_id}/events`：SSE 透传 Runner events，前端 token 级渲染。
3. **终态落库（代理层职责）**：事件流出现 `completed` 时，读 Runner `/v1/runners/{runner_id}/channel-result` 的 output 与 public snapshot 的 `downloadableFiles`/`images` 投影（口径同 `runner_agent.py:222-232`），assistant 消息落 `channel_messages`。终态回复按会话与 `runner_id` 去重。用户消息与直接回复按会话、角色和 `client_request_id` 去重，同一次请求可保留两条消息。唯一索引仅匹配官网客服会话前缀，其他渠道继续使用各自的落库规则。SSE 断开后，下次拉取 `/sessions/{sid}/messages` 前触发补写检查。
4. 提问等待（clarification）：以渠道文本化口径渲染（沿用 `ask_render` 的 single/segments 结构，web_c 归入 single 渠道组），用户以普通文本回复 -> 下一轮 `/chat` 走既有 reply control 路径（`submit()` 已含）。结构化表单交互列 Phase 2+ 升级项。
   - **反问落库（2026-11-19 补）**：`waiting` 非终态，SSE 不关闭、终态落库覆盖不到中途 ask_user；代理层在 `/chat` 返回 waiting、`GET /runners/{runner_id}` 轮询命中 waiting、`/sessions/{sid}/messages` 拉取前补写检查三处，把渠道文本化反问按 `kind=clarification` 落 `channel_messages`（幂等键 `clarify_{wait_id}`，复用 `uq_web_c_messages_client_request` 唯一部分索引），保证接待页与历史可见（含访客反问后关页场景）。
   - **C 端停车交互（2026-11-19 补）**：前端 `WebCChat.vue` 快照出现 `waiting_text` 时主动结束本轮观察（abort SSE）、反问作为 assistant 消息入列并恢复输入框——不主动断开会因流不关闭导致输入框永久禁用；用户回复走新一轮 `/chat` 自动应答等待中的澄清。
5. 同会话并发提交：沿用 `submit()` 的既有语义（取消未完成 runner 后受理新消息）；前端按钮单飞防抖。

多 worker 兼容：落库走 DB、限流走 Redis、runner 状态读 Runner 服务，进程内零跨请求状态（符合 Gunicorn 多 worker 规范）。

### 4.4 用户档案与防滥用

- 首次出现身份时创建 `users` 记录：`source='web_c'`，name = 签名 name 或 `官网访客{uid后4位}`（构建方式参考 `agent_user_builder.py:44`，但 web_c 无第三方用户信息可拉，直接本地构建）。
- 防滥用三层绑定自动生效：`anti_abuse/service.py` 的 `resolve_binding`（:41-71）按 `users.source` 渠道类型降级匹配 channel 绑定，租户可在防滥用中心对 web_c 渠道单独配置策略；`rules.py` 的 `SESSION_SOURCE_TYPES` 白名单加 `web_c`。
- 同步拦截（匿名入口是滥用高发区，观察扫描不够）：
  - **积分余额硬阻断**：`POST /chat` 前查 `tenants.credit_balance`，不足即拒（复用 `_check_tenant_credit_blocked` 逻辑抽公共函数，`src/main.py:582` 现版仅 B 端路径用）。
  - **限流**：Redis 计数，`uid + IP` 双维（匿名 uid 可无限重生成，纯 uid 维度无效），默认每身份每分钟 5 条、每 IP 每小时 60 条，超限 429 + 明确文案；阈值入渠道 config JSON 可调。
- 计费归属：`start_record(tenant_id, source_type='web_c')`；token 结算由 Runner 侧完成（渠道 record 仅侧账，口径同 wecom_kf，`channel_routes.py:3052`）。C 端用量计到租户积分，开通页明示。

### 4.5 C 端聊天页（前端）

- 路由：`/c/:tenant_id/:config_id`（公开路由，不套 PortalLayout/AppHeader 登录体系）；`?embed=1` 为 iframe 紧凑模式（隐藏页头，深浅色跟随宿主参数可选）。SPA fallback 已由 nginx `try_files` 覆盖，无需新增下载限速。
- 建议形态：**按钮新开标签为推荐集成形态**（匿名身份持久性最好），iframe 同 URL + `?embed=1` 支持；配置页给出两种集成代码片段。
- UI：单会话对话视图为主（消息流 + 输入框 + 停止按钮 + 流式光标），greeting 开场卡；历史会话入口按 `identity_mode` 决定是否展示（2026-11-19 起仅签名链接模式开放，anon/uid 在 bootstrap 强制 False，渠道配置弹框同步禁用置灰）；附件双向支持：用户上传（图片粘贴/选择 + 文件选择，走 §4.6）与 AI 产出可下载文件（复用 `ImageRef`/downloadableFiles 渲染与 §4.6 代理下载）。
- 组件独立于 B 端聊天组件（消息结构不同：channel_messages + 渠道文本化提问），避免耦合 `useAgent` 的 B 端 SSE 契约；复用 `marked` 渲染、`ImageGallery`、`DownloadFileCard` 等原子组件。

### 4.6 附件与图片上传（Phase 1 纳入，裁决 2026-11-16）

**上传端点**：`POST /upload`（multipart），鉴权同 §3.4，独立于 chat 限流（每身份每分钟 10 个文件，Redis 计数）：

- 限制：单文件 ≤ 20MB；MIME 白名单（jpg/png/gif/webp、pdf、docx/doc/xlsx/xls/pptx/ppt、txt/md/cvs）；文件名清洗（去路径分隔符与控制字符），落盘名用 `upload_{uuid12}.{ext}`。
- 存储：`get_tenant_storage_path(tenant_id, scene='conversation', filename=...)`（租户附件存储规范）。
- 注册：复用 `uploaded_file:{file_id}` Redis 命名空间（`/api/files/{file_id}/download` 同命名空间可直接服务）；`file_assets` 已 DB 持久化（防丢名），落库口径与既有注册 helper 一致（参考 `channel_routes.py:3369` 出站注册 helper，抽取公共函数复用）。图片类同时可走 `ImageRegistry.register(source='user_upload', usage='attachment')`（同一命名空间协议）。

**入 Runner**：`/chat` 提交时把 ref 数组按 `runner_agent.py:118-121` 现行口径传 Runner（`type` 归一为 image/file、有 file_id 去 content）。

**下载回显**：C 端无登录态，`/api/files/{file_id}/download` 的票据机制绑定 tenant/user/role 不适用 -> 新增 C 端代理下载端点 `GET /files/{file_id}`：校验 file_id 元数据归属当前租户 **且** 该 file_id 出现在当前身份的会话消息中（防遍历窃取他人文件），`StreamingResponse` + ASCII filename fallback（遵循下载接口规范）。

**滥用与计费**：上传不计费；子智能体用 image_read/VL 等工具读图产生的调用按既有计费路径结算。上传大文件占带宽 -> 上传接口与代理下载端点纳入 nginx 下载限速正则（三份 conf 同步加路由，遵循后端下载接口规范第 1 节）。

### 4.7 留资接入（Phase 1 纳入，裁决 2026-11-16）

复用 pre-sales 留资链路，web_c 无专用改动：

- 写入路径不变：绑定子智能体在 web_c 对话中按技能提示词触发 `record_lead_capture` 工具（`src/tools/lead_capture/record_lead_capture.py`）-> `LeadCaptureDB.create` 落 `bs_lead_capture_leads`；身份从 `ToolExecutionContext` 取（tenant/session/user），工具无需感知渠道。
- web_c 口径：`channel_chat_id=None`（该列是 wecom_kf open_kfid 语义）、`kf_account_name='官网客服'`（名称快照）、`session_id=当前 web_c session_id`（表已支持，web_c 线索靠 session_id 关联渠道会话识别，零 DDL）。
- C 端聊天页无专门留资 UI，联系方式收集走子智能体对话流程（含文本化提问）；AI 产出可下载文件（如报价单）经 §4.6 代理下载交付。
- Phase 2 接待页留资 Tab 见 §5.2。

### 4.8 租户管理端配置页（ChannelConfig.vue 扩展）

新「官网客服」渠道页签：

- 启用开关、greeting 编辑、绑定子智能体（渠道级下拉，同现有 `available-subagents` 接口）。
- `channel_secret` 展示（掩码 + 复制）与重置（确认框明示旧链接失效）。
- 集成代码片段生成：按钮链接 / iframe 代码，ext 模式附签名伪代码说明。
- 帮助文字按 [frontend_dev.md 帮助文字同步规范](../../.claude/rules/frontend_dev.md) 撰写（身份模式差异、Safari iframe 7 天预期、计费归属）。

## 5. Phase 2 详细设计：官网接待客户页

仿 `ExternalCustomerService.vue`（微信接待客户）做 **纯只读查看页**，无 kf 账号/引流员工/留资体系，大幅简化。

### 5.1 后端（新模块 `src/saas/api/web_c_customers.py`，prefix `/api/saas/web-c-customers`）

| 端点 | 数据来源 |
|------|---------|
| GET `/users` | `UserDB.list_external_users`（`src/db/models.py:480`，增加 `channel_type='web_c'` 参数化）+ `channel_sessions` 聚合（first/last_session_at）；搜索 = 昵称/uid 前缀；日期段过滤 |
| GET `/users/{user_id}/sessions` | `channel_session_manager.list_sessions`（channel_type='web_c'，租户校验） |
| GET `/sessions/{session_id}/messages` | `get_messages_paginated`（复用 external_customers.py:405 同款；`metadata.kind='transfer_to_human_marker'` 等渠道特例 web_c 无，直接透传原始消息） |
| GET `/stats` | 日期段内：会话数、消息数、活跃客户数，按绑定子智能体分组 |
| GET `/lead-stats` / GET `/leads` / PATCH `/leads/{lead_id}` | 复用 `bs_lead_capture_leads`（`src/saas/db/lead_capture_db.py`），web_c 过滤 = join `channel_sessions`（session_id 归属 + `channel_type='web_c'`），零 DDL；联系方式解密与阶段编辑同 wecom_kf 口径 |
| GET `/attachments/download` | 复用 external-customers 同款（`storage/tenants/{tid}/conversation/` + session 归属校验），或直接共用现端点 |

权限：`require_admin`（接待查看属管理功能，无 wecom_kf 引流员工式数据裁剪）。

### 5.2 前端（`WebCService.vue`）

- 布局：顶部 Tab 切换（对齐 `ExternalCustomerService.vue` 结构）：`对话记录` / `统计` / `留资线索`；`AppHeader` 标题「官网接待客户」。
- Tab1 对话记录：左右两栏（客户列表 w-96 + 会话查看）；客户列表行 = **用户 × 会话**（每条渠道会话一行，2026-11-19 裁决：uid 模式每次进入即新会话，按用户折叠会让历史问答看似消失）= 头像（匿名用默认图）+ 名称（签名 name / `官网访客xxxx`）+ `uid: ext_xxx/anon_xxx` 徽标 + 该会话起止时间（精确到分钟）；行带 `session_id`，点击直接展示该会话，右侧会话下拉仍可切该用户其他会话（`list_external_users` `group_by_session=True`）；筛选 = 搜索框 + 渠道下拉；分页（遵循 [list-page-convention](../../.claude/rules/list-page-convention.md)）。消息区只读消息流（用户/AI 消息、用户上传与 AI 产出附件下载卡、图片画廊）；无转人工标记、撤回徽章、servicer 消息等 wecom_kf 特例。
- Tab2 统计：日期段预设 + 统计卡片（会话数、消息数、活跃客户数）+ 按绑定子智能体分组表。
- Tab3 留资线索：复用 wecom_kf 留资口径——统计卡片（总留资数 + 按联系方式类型）+ 线索列表（序号/客户姓名/联系方式/联系方式类型/意向度/阶段/留资时间/操作）+ 线索详情与阶段编辑弹框（简化版，无客服账号/归属员工/servicer 维度）；web_c 线索过滤 = 后端 join `channel_sessions`（`session_id` + `channel_type='web_c'`），复用 `/lead-stats`、`/leads`、PATCH `/leads/{lead_id}` 端点加渠道过滤参数，零 DDL。
- 路由 `/t/:tenant_id/web-c-customers`（`agentRoutes.ts` 注册，模式同 `tenant-web-sessions`）；菜单入口 `MenuSidebar.vue`「官网接待客户」，管理员可见（web-sessions 模式 :708-723）。

### 5.3 渠道归属展示/筛选与消息倒序（2026-11-18 微调）

同租户可能有 2 个及以上 web_c 渠道配置，接待页需按渠道区分：

- **归属记录**：C 端创建会话（默认 + 「新会话」）写入 `channel_sessions.metadata.channel_config_id`；存量无归属会话由本渠道访客下次到达时幂等补记（`web_c.py` `_backfill_session_channel_config`，jsonb 合并）。匿名 uid 按渠道配置隔离（`web_c_anon_{config_id}`），补记安全；ext userid 跨官网复用时理论上可能误归（按官网 userid 唯一约定，风险可忽略）。
- **展示/筛选**：接待页客户行显示渠道名；渠道数 >1 时搜索区出现渠道下拉（`GET /channels` 新端点）。筛选口径：用户在指定渠道有归属会话，或（存量无归属 + 兜底渠道 = 租户最早创建的 web_c 配置）。`list_external_users` 增加 `channel_config_id` / `channel_config_fallback_id` 参数；`GET /users/{user_id}/sessions` 同口径过滤。
- **消息倒序**：接待页消息按时间倒序展示（最新在顶部，分页第 1 页 = 最新 50 条）；官网客户聊天页（`WebCChat.vue`）保持时间升序，两处口径不同。

## 6. 开放问题裁决记录（2026-11-16）

| # | 问题 | 裁决 |
|---|------|------|
| 1 | C 端附件/图片上传 | **纳入 Phase 1**（设计见 §4.6） |
| 2 | 转人工 | **暂不考虑**（web_c 无企微坐席体系；将来若需，先定义「人工」承接方） |
| 3 | 渠道级单子智能体绑定 | **足够**，不做访客侧多子智能体切换 |
| 4 | 匿名身份与签名身份合并 | **不做**，两套 session 独立 |
| 5 | 留资线索 | **纳入**：Phase 1 复用留资链路落库（§4.7），Phase 2 接待页含留资线索 Tab（§5.2） |
| 6 | 签名时效 | **24h 可以**，后续按需配置化 |
| 7 | 仅 userid 链接折中方案（免签名、免后端改造） | **纳入**（2026-11-19）：`identity_mode` 增 `uid`；无确定性默认会话 + sessionStorage 持有会话 + bootstrap/sessions 服务端强制置空，见 §3.5 |

## 7. 验证计划

- 单测：HMAC 验签（有效/过期/篡改/name 缺省）、session_id 生成、`ext_/anon_/uid_` 身份构建、uid 模式免签名解析与非 uid 渠道 403、uid 模式会话 id 随机后缀闸门、额外会话熵 ≥12 hex、限流计数、积分拦截、终态落库幂等、C 端 API 的 config_id 归属与 enabled 校验、runner_id/session_id 归属校验、上传 MIME 白名单/大小限制/文件名清洗、代理下载归属校验（跨身份 file_id 拒绝）、留资 web_c 口径字段（channel_chat_id=None/kf_account_name 快照）。
- 集成：TestClient 提交 -> mock Runner client；SSE 透传事件序列；`submit()` 拆分后既有渠道回归（wecom_kf/dingtalk/feishu 渠道管线单测全绿）。
- 前端：`npm run build` + 聊天页/接待页组件测试。
- 真机（agent2）：签名链接与匿名两条身份路径、iframe（含 Safari 预期登记）、附件/图片上传与代理下载（含大文件限速）、留资全链路（对话触发落表 + 接待页线索 Tab）、防滥用限流触发、计费落 `chat_records`（source_type=web_c）核对（按 [billing_audit.md](../../.claude/rules/billing_audit.md) 自检）。

## 8. 部署事项

- `configs/config.yaml` 改动（peers sources）随代码发布生效；无新表、无 `db_update.yaml` 变更。
- nginx：`/c/` 路径与 `/api/c/` 走既有 SPA/API 转发即可；C 端上传与代理下载端点需加入三份 conf 的下载限速正则（§4.6）；C 端无登录态，`TenantContextMiddleware` 对 `/api/c/*` 的租户解析改为从 URL path 参数取（不走 X-Tenant-Id/token）。
- 渠道启停 = 租户级配置行 `enabled`，无平台级 yaml 开关（如需平台总开关，仿 `weixin_marketing.enabled` 门控模式）。
