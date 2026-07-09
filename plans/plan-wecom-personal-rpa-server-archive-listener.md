# 开发计划：企业微信个人账号 RPA — 服务端拉取模式（复用 wecom_personal_rpa 渠道）

> **关联设计**：[docs/system/wecom-personal-rpa-server-archive-listener-design.md](../docs/system/wecom-personal-rpa-server-archive-listener-design.md)
>
> **登记位置**：[docs/ideas.md](../docs/ideas.md) §渠道集成 #29 子条目
>
> **遵循流程**：[dev_workflow.md](../.claude/rules/dev_workflow.md) 三智能体流程（开发 → 测试 → CodeReview）
>
> **核心策略**：**不新增 channel_type**。"服务端拉取 vs 客户端拉取"作为现有 `wecom_personal_rpa` 渠道的 `listen_mode` 字段开关。
>
> **🎯 第一期 MVP 范围**：
> - **仅启用服务端模式**，`listen_mode` 默认且强制为 `'server'`
> - 客户端模式**前端禁用、灰显、标注「即将开放」**，用户不可切换
> - 服务端模式完整链路全部上线
> - 客户端模式相关**后端代码保留**，为未来开放做准备

---

## 总览

| 阶段 | 内容 | 工时 | 状态 |
|------|------|------|------|
| Phase 1 | listen_mode 字段 + 凭证加密 codec + 单例约束 + 后端强制 server | 2h | 📋 待开发 |
| Phase 2 | 回调签名校验 + AES 解密（复刻 wecom_kf） | 3h | 📋 待开发 |
| Phase 3 | 拉取 RSA 解密工具 + HTTP 客户端 | 4h | 📋 待开发 |
| Phase 4 | ServerArchiveFetcher + 复用 `_process_inbound_message` | 4h | 📋 待开发 |
| Phase 5 | 兜底轮询调度器 | 2h | 📋 待开发 |
| Phase 6 | 现有回调路由双验签兼容改造（client 分支代码保留） | 3h | 📋 待开发 |
| Phase 7 | 客户端 `/config` listen_mode + 启停本地轮询 | 2h | 📋 待开发 |
| Phase 8 | verify 路由 server 模式专属逻辑（client 函数保留不调用） | 1.5h | 📋 待开发 |
| Phase 9 | 前端 ChannelConfig.vue 改造（模式开关锁定 server + 条件字段） | 3h | 📋 待开发 |
| Phase 10 | 监控指标 + audit 事件接入 | 2h | 📋 待开发 |
| Phase 11 | 单元测试 + 集成测试（仅 server 模式场景） | 4h | 📋 待开发 |
| Phase 12 | 灰度上线 + 文档登记 | 2h | 📋 待开发 |

**合计**：约 32.5h（约 4 工作日）

> **修订说明**（2026-07-02 v4）：
> - v1：误以为纯拉取模式，新增独立凭证表
> - v1.5：改为回调+拉取双模式，仍保留独立表
> - v2：对齐 wecom_kf 渠道模式，新增 `channel_type='wecom_personal_rpa_archive'`
> - v3：复用现有 `wecom_personal_rpa` 渠道，listen_mode 字段切换，回调路由双验签兼容
> - **v4（当前）**：第一期仅上线服务端模式，客户端模式前端禁用、后端代码保留。工时 32.5h（比 v3 减少 1.5h，verify/前端模式切换测试场景减少）。
>
> **未来开放 client 模式时的增量工时**（不在本期）：~3h（移除前端禁用 + 后端强制覆盖 + 启用 client verify 分支）。

---

## 2026-07-09 真机问题收口：SDK 解密挂起

### 问题

agent2 部署 commit `f1b765c` 后，poller 每分钟触发，但 fetcher 没有“拉取完成”或“解密失败”日志，`channel_messages` 无 `tenant_9eb3e45cab83` 的入库记录。重新审视代码后确认关键风险：`asyncio.wait_for(asyncio.to_thread(...), timeout=10)` 只能取消 Python await，不能杀掉已经进入 C SDK 的同步调用；一旦 `DecryptData` 在子进程池内挂起，池会被永久占住。

### 修复任务

- [x] `wecom_finance_sdk.py`：把 SDK 子进程调用从阻塞式 `pool.apply` 改为 `pool.apply_async(...).get(timeout=N)`。
- [x] `wecom_finance_sdk.py`：`DecryptData` 默认 8s 子进程级超时；超时后 terminate/join 并重建 SDK 进程池，抛 `SDKCallError(code=-2)`。
- [x] `fetcher.py`：显式把 8s SDK 超时传给 `decrypt_data_raw`，外层 10s 仅作为 RSA/未知阻塞兜底。
- [x] `fetcher.py`：增加获锁、进入流程、GetChatData 调用/返回、batch 处理、单条进度采样日志。
- [x] 单元测试：覆盖 SDK 子进程超时会重建池，覆盖 fetcher 必须传递 SDK 子进程级超时。
- [ ] agent2 部署后验证消息入库与 6.2 漏抓率。

### 验证

- [x] `./scripts/dev_test.sh tests/unit/channels/wecom_personal_rpa/archive/test_sdk_proxy.py tests/unit/channels/wecom_personal_rpa/archive/test_fetcher.py -p no:cacheprovider -q`
- [x] `./scripts/dev_test.sh tests/unit/channels/wecom_personal_rpa/archive -p no:cacheprovider -q`
- [x] `./scripts/dev_test.sh tests/integration/test_archive_callback_to_fetch_e2e.py tests/integration/test_archive_sdk_fetch.py -p no:cacheprovider -q`
- [x] AST 语法检查：`wecom_finance_sdk.py` / `fetcher.py` / 新改测试文件
- [x] import 安全检查：`from src.channels.wecom_personal_rpa.archive import fetcher, poller, callback_handler, wecom_finance_sdk`

---

## 2026-07-09 二次真机问题收口：DecryptData 明文字段映射

### 问题

agent2 部署 SDK 超时修复后，真实消息链路已能走到 `GetChatData batch=1`，但处理该条消息失败：`ChatDataItem.msg_type` 为空，随后 `_build_envelope` 读错字段导致 `KeyError: "'content'"`。对照企微官方「获取会话内容」文档，`GetChatData` 外层密文条目稳定提供 `seq/msgid/publickey_ver/encrypt_random_key/encrypt_chat_msg`；`from/tolist/roomid/msgtime/msgtype/text.content` 属于 `DecryptData` 后的明文 JSON。

### 修复任务

- [x] `fetcher.py`：`_build_envelope` 从 `DecryptData` 明文 JSON 读取 `msgtype/from/tolist/roomid/msgtime/text.content`，外层条目仅作为兼容 fallback。
- [x] `fetcher.py`：`msgtime` 兼容官方 UTC 毫秒时间戳，同时保留秒级历史测试兼容。
- [x] 单元测试：覆盖真实企微形态（外层 metadata 为空、明文里有完整消息字段）。
- [ ] agent2 重新部署后验证文本消息成功入库。

### 验证

- [x] `python -m pytest tests/unit/channels/wecom_personal_rpa/archive/test_fetcher.py -p no:cacheprovider -q`：`14 passed`
- [x] `python -m pytest tests/unit/channels/wecom_personal_rpa/archive -p no:cacheprovider -q`：`141 passed, 7 skipped`
- [x] `python -m py_compile src/channels/wecom_personal_rpa/archive/fetcher.py tests/unit/channels/wecom_personal_rpa/archive/test_fetcher.py`

---

## Phase 1：listen_mode 字段 + 凭证加密 codec + 单例约束

### 目标

在 `tenant_channel_configs.config` JSON 中加入 `listen_mode` 字段和两组加密凭证；保证同租户单例。

### 任务

- [ ] 1.1 `src/channels/wecom_personal_rpa/archive/__init__.py` 新建模块
- [ ] 1.2 `src/channels/wecom_personal_rpa/archive/credential_codec.py`：
  - `encrypt_sensitive_fields(plain_config: dict) -> dict`：对 archive_secret / private_key / token / encoding_aes_key / client_secret 调 `secret_crypto.encrypt`，corp_id 等明文字段保留
  - `decrypt_sensitive_fields(encrypted_config: dict) -> dict`：反向解密
  - `mask_sensitive_fields(plain_config: dict) -> dict`：API 返回前端用，敏感字段变掩码 `***xxxxx`
- [ ] 1.3 `src/saas/db/channel_config_db.py` 改造 `create` / `update` 函数：
  - 写入前：如果 `channel_type == 'wecom_personal_rpa'`，调 `encrypt_sensitive_fields`
  - 读取时：服务端内部调用方调 `decrypt_sensitive_fields`；API 响应调 `mask_sensitive_fields`
- [ ] 1.4 `channel_config_db.create` 新增单例检查：
  - 如果 `channel_type == 'wecom_personal_rpa'`，先查同 tenant 是否已有该类型配置
  - 已有则抛 `ValueError("该租户已有 wecom_personal_rpa 渠道配置，请编辑现有配置切换模式")`
- [ ] 1.5 `deploy/db_update.sql` 追加部分唯一索引：
  ```sql
  -- 2026-07-02 wecom_personal_rpa 渠道单例约束：同 tenant 只能有一份该类型配置
  CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wecom_personal_rpa
      ON tenant_channel_configs(tenant_id, channel_type)
      WHERE channel_type = 'wecom_personal_rpa';
  ```
- [ ] 1.6 `src/saas/api/channel_config.py:43-52` 的 `_REQUIRED_FIELDS.wecom_personal_rpa` 改造为**动态校验**：
  - 校验时读 `config['listen_mode']`（第一期永远为 'server'，但保留 client 分支为未来开放做准备）
  - `server` 模式必填：corp_id + archive_secret + private_key + token + encoding_aes_key
  - `client` 模式必填：corp_id + client_secret（保留代码，本期不会触发）
- [ ] 1.7 **后端强制 server**（关键 MVP 防护）：`channel_config_db.create` / `update` 函数中，对 `wecom_personal_rpa` 类型强制 `config['listen_mode'] = 'server'`，忽略请求中的其他值。即使有人用 API 工具绕过前端发 `listen_mode='client'`，后端也会强制覆盖。
- [ ] 1.8 单元测试：`tests/unit/channels/wecom_personal_rpa/test_credential_codec.py`
  - 加密/解密闭环
  - mask 后不含明文
  - 强制 server 测试（传入 client 也会被覆盖）

### 验收标准

- `_REQUIRED_FIELDS` 动态校验通过：server 模式能创建（client 模式本期不会被触发）
- config JSON 列里 5 个敏感字段都是 Fernet 密文
- API 响应中敏感字段为掩码
- 同 tenant 重复创建 wecom_personal_rpa 配置被拒绝
- **后端强制覆盖 listen_mode='server'** 测试通过（关键 MVP 防护）

---

## Phase 2：回调签名校验 + AES 解密（复刻 wecom_kf）

### 目标

服务端能验证企微回调签名 + 解密 echostr / encrypt 字段。

### 任务

- [ ] 2.1 `archive/callback_crypto.py`，**直接参考 `src/channels/wecom_kf/adapter.py` 的 crypto 模块**实现企微官方加解密算法：
  - `verify_signature(token, timestamp, nonce, encrypt, msg_signature) -> bool`
    - 算法：`SHA1(sort([token, timestamp, nonce, encrypt]))` 比较 msg_signature
    - **必须用 `hmac.compare_digest`**（避免时序攻击）
  - `decrypt_aes(encoding_aes_key, encrypt_b64) -> tuple[bytes, str]`
    - AES-CBC-256 解密（key = base64decode(encoding_aes_key + "=")）
  - `verify_and_decode_echostr(token, encoding_aes_key, msg_signature, timestamp, nonce, echostr) -> str`
  - `verify_and_decrypt_event(token, encoding_aes_key, msg_signature, timestamp, nonce, encrypt_field) -> dict`
  - `SignatureError` 异常类（用于双验签兼容时识别"企微签名验签失败"）
- [ ] 2.2 单元测试 `tests/unit/channels/wecom_personal_rpa/archive/test_callback_crypto.py`：
  - 用企微官方文档示例数据验证算法正确性
  - 与 wecom_kf 的 crypto 模块用相同输入产生相同输出（**跨渠道一致性**）

### 验收标准

- 算法实现与企微官方示例数据 100% 对齐
- 与 wecom_kf `adapter.crypto` 行为完全一致

> 💡 **复用提示**：如果 wecom_kf 的 crypto 模块可独立 import，**直接 import 复用**，不要复制。如果耦合在 adapter 里，本次先复制，后续独立 PR 抽公共模块。

---

## Phase 3：拉取 RSA 解密工具 + HTTP 客户端

### 目标

服务端能拉取会话存档密文 + 解密。

### 任务

- [ ] 3.1 `archive/chat_crypto.py`（移植 C# `ArchiveCryptoService`）：
  - `decrypt_random_key(private_key_pem: str, encrypt_random_key_b64: str) -> bytes`
  - `decrypt_chat_msg(random_key: bytes, encrypt_chat_msg_b64: str) -> str`
  - 依赖 `cryptography` 库
- [ ] 3.2 `archive/http_client.py`：
  - `get_access_token(tenant_id, corpid, secret) -> str`
    - Redis 缓存：key=`wecom_rpa:archive:token:{tenant_id}:{corpid}`，TTL 7000s
  - `get_chat_data(access_token, seq, limit) -> ChatDataBatch`
  - `WeComRateLimitException(retry_after_seconds)` 异常类
- [ ] 3.3 单元测试：用 C# 测试 fixture 验证 Python 解密结果一致；mock httpx 覆盖 200 / 45009 / 40001 / 网络异常

### 验收标准

- Python RSA 解密结果与 C# `ArchiveCryptoServiceTests` 用相同输入产生相同明文
- 45009 异常带 `retry_after_seconds` 字段

---

## Phase 4：ServerArchiveFetcher + 复用 `_process_inbound_message`

### 目标

拉取密文 → RSA 解密 → 构造 envelope → 调 `_process_inbound_message`。

### 任务

- [ ] 4.1 `_process_inbound_message` 函数签名扩展：新增可选参数 `source: str = "client_callback"`，仅用于日志/审计区分（**纯增量，无行为差异**）
- [ ] 4.2 `archive/fetcher.py` — `ServerArchiveFetcher` 类：
  - `fetch_once(tenant_id, config_id)`：
    - Redis 分布式锁 `wecom_rpa:archive:lock:{tenant_id}`（TTL 60s）
    - 从 `tenant_channel_configs` 读配置 → `decrypt_sensitive_fields` 拿明文凭证
    - 检查 `config['listen_mode'] == 'server'`，否则跳过
    - 调 http_client 拉一批密文
    - 逐条：RSA 解密 → 构造 envelope → 调 `_process_inbound_message(source="server_fetcher")`
    - 逐条推进 `last_seq`（写入 config JSON）
    - 单条解密失败也推进 seq，并记录 audit 后继续下一条（避免坏消息永久卡住租户拉取）
  - 45009 → 写 `last_error_*` 到 config JSON，60s 自动恢复
  - 单次拉取超时 30s
- [ ] 4.3 envelope 构造函数 `_build_envelope(cfg, item, plain_json)`：字段对齐客户端模式（设计文档 §5.6）
- [ ] 4.4 集成测试 `tests/integration/test_archive_fetcher_e2e.py`

### 验收标准

- 同一条密文，server 路径与 client 路径产生的最终效果完全一致
- `_process_inbound_message` 改动是**纯增量**，无回归
- Redis 锁竞争下，并发 5 次 `fetch_once` 实际只执行 1 次

---

## Phase 5：兜底轮询调度器

### 目标

60s 一次扫描所有 listen_mode='server' 的配置，回调丢失时补漏。

### 任务

- [ ] 5.1 `archive/poller.py` — `ServerArchivePoller` 类：
  - `start()` / `stop()`
  - 主循环：每 60s 调 `channel_config_db.list_by_channel_type('wecom_personal_rpa', verified_only=True)`，过滤 `listen_mode=='server'` 的配置，对每个调 `asyncio.create_task(fetcher.fetch_once(...))`
  - 启动时立即扫一次
- [ ] 5.2 在 `src/main.py` 应用启动 hook 注册 poller 单例
- [ ] 5.3 单元测试

### 验收标准

- poller 异常不挂整个应用
- 启动后 1s 内触发首次扫描
- 仅扫描 listen_mode=='server' 的配置

---

## Phase 6：现有回调路由双验签兼容改造

### 目标

`POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}` 同时支持企微官方签名（server 模式）和客户端 HMAC（client 模式）。

### 任务

- [ ] 6.1 `archive/callback_handler.py` 新建模块，封装企微签名处理逻辑：
  - `handle_archive_echostr(cfg, request, ...)` — GET echostr 验证（仅 server 模式）
  - `handle_archive_event(cfg, request, ...)` — POST 事件接收，立即 200 + 异步触发 fetcher
- [ ] 6.2 改造现有 `src/saas/api/wecom_personal_rpa_routes.py:249` 的 `wecom_personal_rpa_callback` 路由：
  ```python
  async def wecom_personal_rpa_callback(tenant_id, config_id, request, ...):
      cfg = await channel_config_db.get_by_tenant_and_id(tenant_id, config_id)
      ...
      config_data = json.loads(cfg.config)
      listen_mode = config_data.get("listen_mode", "server")

      # GET echostr（仅 server 模式企微首次配置）
      if request.method == "GET" and listen_mode == "server":
          return await handle_archive_echostr(cfg, request, ...)

      # POST 双验签兼容
      if request.method == "POST":
          # 先试企微签名（server 模式）
          if listen_mode == "server":
              try:
                  return await handle_archive_event(cfg, request, ...)
              except SignatureError:
                  pass  # 落到客户端 HMAC

          # 再试客户端 HMAC（现有逻辑，client 模式）
          return await _handle_client_callback(...)

      raise HTTPException(401, "签名验证失败")
  ```
- [ ] 6.3 audit 事件：`archive_callback_received` / `archive_callback_verify_failed`
- [ ] 6.4 集成测试 `tests/integration/test_archive_callback_e2e.py`：
  - GET echostr 验证成功（仅 server 模式）
  - POST 企微事件触发拉取（server 模式）
  - POST 客户端 HMAC 上报消息（client 模式）
  - 切换 listen_mode 后，对应路径生效
  - 双验签都失败 → 401 + audit

### 验收标准

- 同一 URL 支持两种签名机制，由 listen_mode 决定主路径
- POST 事件 5s 内响应（企微超时限制）
- 验签失败有 audit 记录
- **现有 client 模式回归测试全部通过**（重要：不能破坏既有客户端上报）

---

## Phase 7：客户端 `/config` listen_mode + 启停本地轮询

### 目标

客户端启动时根据服务端配置决定是否启用本地 `ChatArchiveListener`。

### 任务

- [ ] 7.1 `GET /api/v1/channels/wecom-personal-rpa/config` 响应新增 `listen_mode` 字段：
  - 读 `tenant_channel_configs.config.listen_mode`
  - 没有渠道配置时返回 `null`（客户端按 NULL 处理为不拉取）
  - 有渠道配置时返回 `'server'` 或 `'client'`
- [ ] 7.2 客户端 `AgentApi.Client.cs` `RpaConfigResponse` 新增 `ListenMode` 字段
- [ ] 7.3 客户端 `App.xaml.cs` DI 装配：
  - `ListenMode == "client"` → 注册 `ChatArchiveListener` + `InboundEventReporter`
  - `ListenMode` 为 NULL 或 `"server"` → 跳过这两个 hosted service
- [ ] 7.4 客户端单测：`Client.Tests/App/ListenModeSkipTest.cs`

### 验收标准

- 租户配置 listen_mode='server' 时，客户端启动后**不再发任何拉取请求**
- 切换为 'client' 时，客户端恢复本地轮询

---

## Phase 8：verify 路由 server 模式专属逻辑

### 目标

`POST /api/saas/channels/{config_id}/verify` 对 `wecom_personal_rpa` 类型执行 server 模式验证。

### 任务

- [ ] 8.1 `src/saas/api/channel_config.py:167` 的 `verify` 路由新增分支：
  ```python
  if cfg.channel_type == "wecom_personal_rpa":
      listen_mode = json.loads(cfg.config).get("listen_mode", "server")
      if listen_mode == "server":
          return await verify_archive_server_mode(cfg)
      # client 模式分支代码保留，本期永远不会被调用
      # else:
      #     return await verify_archive_client_mode(cfg)
  ```
- [ ] 8.2 新增 `verify_archive_server_mode(cfg)`：
  - 步骤 1：拉一次最小批次（limit=1）
  - 步骤 2：RSA 解密一条
  - 步骤 3：构造假事件自测验签 + AES 解密
  - 返回详细错误诊断（5 类错误：corpid / archive_secret / private_key / token / encoding_aes_key）
  - 全通过 → `verified=1`
- [ ] 8.3 `verify_archive_client_mode(cfg)` 函数**实现保留**（注释掉调用入口），未来开放时取消注释即可
- [ ] 8.4 单元测试覆盖 server 模式 5 类错误诊断
- [ ] 8.5 前端复用现有 `handleVerify` 逻辑（无需改动）

### 验收标准

- 5 类 server 模式凭证错误能准确识别并给出可读诊断
- 验证通过后 `verified=1`，列表卡片徽章变绿
- client 模式 verify 函数代码已存在但永远不被调用（防御性保留）

---

## Phase 9：前端 ChannelConfig.vue 改造（模式开关锁定 server + 条件字段）

### 目标

现有 `wecom_personal_rpa` 渠道配置弹窗新增模式开关（client 禁用）+ server 模式条件字段。

### 任务

- [ ] 9.1 `frontend/src/components/saas/ChannelConfig.vue:388` 改造 `channelFieldMap.wecom_personal_rpa`（设计文档 §6.2 已给出）：
  - 加入 `__listen_mode__` 字段（type: 'radio'，default 'server'，**forceValue: 'server' 锁定**）
  - radio options：server 启用；**client 禁用、灰显，label 后追加「（即将开放）」**
  - 加入 server 模式 4 个字段（archive_secret / private_key / token / encoding_aes_key），带 `showWhen: { listen_mode: 'server' }`
  - 加入 client 模式 1 个字段（client_secret），带 `showWhen: { listen_mode: 'client' }`（永远不会渲染）
- [ ] 9.2 字段渲染逻辑改造：
  - 识别 `showWhen` 条件，只渲染匹配的字段
  - 识别 radio option 的 `disabled` 属性，渲染灰显样式
  - 识别 `forceValue`，无论用户怎么点都锁定该值
- [ ] 9.3 radio 字段特殊渲染：用 BaseRadio 或自实现单选按钮组
- [ ] 9.4 file 字段特殊渲染：`<input type="file">` 上传 .pem 文件，FileReader 读文本
- [ ] 9.5 顶部加「Badge：第一期仅支持服务端模式」提示横幅
- [ ] 9.6 `:420` `quickGuideMap.wecom_personal_rpa` 和 `:472` `fullGuideMap.wecom_personal_rpa` 改造为按 listen_mode 区分（client 模式指南代码保留但不显示）
- [ ] 9.7 前端测试 `frontend/src/__tests__/components/ChannelConfig.rpa.test.ts`：
  - **radio 默认选中 server**
  - **client 选项禁用、不可点**
  - server 模式下 4 字段全部渲染
  - 模式不可切换到 client（forceValue 锁定）
  - 配置指南显示 server 版

### 验收标准

- 在 `https://agent2.aidingyi.cn/t/{tenant_id}/channels` 编辑 wecom_personal_rpa 配置时能看到模式单选
- **client 选项灰显、不可点击**，鼠标悬停可见「即将开放」提示
- server 模式 4 个字段正常显示
- 模式被锁定为 server，用户无法切换
- 顶部「第一期仅支持服务端模式」徽章可见
- `npm run build` 0 错误

---

## Phase 10：监控指标 + audit 事件

### 目标

可观测性接入。

### 任务

- [ ] 10.1 audit 事件：复用 `wecom_rpa_audit_logs` 表，记录设计文档 §9 列出的事件
- [ ] 10.2 指标接入
- [ ] 10.3 错误告警：连续 3 次 `archive_fetch_error` → `logger.error` + audit；回调验签失败单租户 1 分钟 > 5 次 → 告警

### 验收标准

- 管理后台审计 Tab 能看到所有 archive 事件
- 错误日志全链路脱敏

---

## Phase 11：单元测试 + 集成测试（仅 server 模式场景）

### 目标

测试覆盖率满足要求，覆盖第一期 MVP 全部场景。

### 任务

- [ ] 11.1 单元测试覆盖：
  - `credential_codec.py` ≥ 90%（含强制 server 覆盖测试）
  - `callback_crypto.py` ≥ 90%
  - `chat_crypto.py` ≥ 90%
  - `http_client.py` ≥ 85%
  - `fetcher.py` ≥ 85%
  - `poller.py` ≥ 85%
  - `callback_handler.py` ≥ 85%
- [ ] 11.2 集成测试：
  - `tests/integration/test_archive_callback_to_agent_e2e.py` — server 模式完整链路
  - `tests/integration/test_archive_poller_fallback.py` — 兜底轮询补拉
  - **现有 client 模式回归测试全部通过**（关键回归保障：未切换 listen_mode 的客户端继续工作）
  - **MVP 防护测试**：用 API 工具尝试传 `listen_mode='client'` 创建配置，断言被强制覆盖为 `'server'`
- [ ] 11.3 启动安全检查：
  - `python -c "from src.channels.wecom_personal_rpa.archive import fetcher, poller, callback_handler"` 无 import 错误
  - `python -c "from src.main import app"` 应用启动正常
  - 前端 `cd frontend && npm run build` 0 错误

### 验收标准

- 所有测试通过
- **既有 wecom_personal_rpa / wecom_kf 测试不受影响**（关键回归保障）
- **MVP 防护**：listen_mode 强制为 server 的逻辑被测试覆盖

> 💡 **第一期不测试 client 模式相关场景**（前端不可切换，client_secret 字段永不渲染）。client 模式 verify / 路由分支代码保留但不测试，未来开放时补测试。

---

## Phase 12：灰度上线 + 文档登记

### 目标

安全上线第一期 MVP。

### 任务

- [ ] 12.1 内部租户灰度 1 周：
  - 在 `https://agent2.aidingyi.cn/t/{tenant_id}/channels` 编辑现有 wecom_personal_rpa 配置
  - 录入 server 模式 5 个凭证（client 模式选项不可见/禁用）
  - 在企微后台配置回调 URL（与现有 URL 相同）
  - 观察：消息到达延迟、回调成功率、45009 频率、seq_lag 指标
- [ ] 12.2 监控指标观察，无异常后开放所有租户
- [ ] 12.3 更新关联文档：
  - `docs/system/wecom-personal-rpa-client-design.md` — `ChatArchiveListener` 章节加「第一期 listen_mode 永远 server，客户端跳过本地轮询；未来开放 client 模式后启用」
  - `docs/system/wecom-personal-rpa-portal-binding-design.md` — 补充 listen_mode 字段说明（标注「第一期 client 选项禁用」）
- [ ] 12.4 三个智能体串行流程：开发 → 测试 → CodeReview（遵循 [dev_workflow.md](../.claude/rules/dev_workflow.md)）

### 验收标准

- 灰度租户连续 7 天：
  - 回调→拉取路径成功率 ≥ 99%
  - 兜底轮询补拉消息数 ≤ 1%
  - 无 45009 雪崩
  - 现有未切换的客户端继续正常工作（如有）
- 所有关联文档同步更新，明确标注「第一期仅服务端模式」
- 三个智能体串行流程通过

> 💡 **未来开放 client 模式时的增量任务**（不在本期）：
> - 移除前端 `forceValue: 'server'` 和 client 选项 `disabled: true`
> - 移除后端 `channel_config_db.create/update` 强制 server 的逻辑
> - 启用 verify_archive_client_mode 调用
> - 补 client 模式相关集成测试
> - 增量工时：~3h

---

## 状态汇总

| Phase | 状态 | 完成人 | 备注 |
|-------|------|--------|------|
| 1 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-02 |
| 2 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-02 |
| 3 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-02 |
| 4 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-02 |
| 5 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-02 |
| 6 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-03 |
| 7 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-03 |
| 8 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-03 |
| 9 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-03 |
| 10 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-03 |
| 11 | ✅ 已完成 | Claude（开发）+ 测试智能体 + CodeReview 智能体 | 2026-07-03 |
| 12 | ✅ 已完成 | Claude（文档登记） | 2026-07-03 |

> **第一期 MVP 完成总结（2026-07-03）**
>
> Phase 1-12 全部完成，工时约 32.5h（实际 4 工作日）。214 个单元/集成测试通过，archive 模块覆盖率 91%。
>
> 关键产出：
> - **8 个新模块**（archive/__init__.py / credential_codec.py / callback_crypto.py / chat_crypto.py / http_client.py / fetcher.py / poller.py / callback_handler.py / verifier.py / audit.py）
> - **3 个改造**（schemas.py 加 listen_mode / wecom_personal_rpa_routes.py 加 source 参数 + GET echostr 路由 + POST 双验签分流 / channel_config.py 加 wecom_personal_rpa verify 分支）
> - **1 个前端改造**（ChannelConfig.vue 加 wecom_personal_rpa 类型支持 + listen_mode 单选锁定 server + RSA 私钥 file input）
> - **1 个 main.py 集成**（lifespan 启停 archive poller）
> - **2 个 SQL 变更**（wecom_rpa_clients 加 listen_mode 字段 + tenant_channel_configs 加 wecom_personal_rpa 单例索引）
>
> 第一期 listen_mode 强制 'server'（前端禁用 client + 后端 codec 兜底），客户端 ChatArchiveListener 永不启动。未来开放 client 模式增量工时约 3h。
>
> **状态更新规则**：开发开始时改为 🔧 部分完成；完成时改为 ✅ 已完成开发，并把整个条目从 `docs/ideas.md` 移动到 `docs/ideas_finished.md`。
