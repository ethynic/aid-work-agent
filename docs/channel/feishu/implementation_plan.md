# 飞书渠道对接方案

## 开发进度

### AgentRunner 接入（后续独立事项）

> 状态：🔧 进行中（开发与两轮审核修复完成，待部署真机验收）。Agent/AgentRunner 架构重构已完成，本项仅让原飞书渠道使用独立 Runner 提供对话服务。
>
> 下文为原飞书渠道建设时的方案与问题基线；接入 Runner 不重新执行其中的 adapter 改造或重写步骤。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 设计核对与计划更新 | ✅ 完成（2026-10-09） | 按接入前仓库核对接入点、公共设施与缺口，设计见下 |
| Phase 2 | Runner 共享缺口最小修复 | ✅ 完成（2026-10-09） | channel-result 放开三源；verbose source 门扩展；finalizer 历史门扩三源（CR 发现双写缺口）；peer sources 配置 |
| Phase 3 | 飞书对话接入点替换 | ✅ 完成（2026-10-09） | `_process_tenant_feishu_background` 换用 `ChannelRunnerAgent`，渠道业务不动 |
| Phase 4 | 独立测试与 CodeReview（首轮） | ✅ 完成（2026-10-09） | 当时证据：bridge 45+4 用例、回调路由 26 回归、CR P0 finalizer 双写已修；后被第一轮审核认定测试不足，由 Phase 4a/4b 扩充（本行保留为历史记录） |
| Phase 4a | 审核修复 R1–R3 + 入口/参数化测试 | ✅ 完成（2026-10-09） | error 显式分流发非空失败提示；worker 用户重建（nickname 优先 + 渠道字段）；skip_save 防零用量空账单；新增入口 28 用例 + bridge 参数化至 59；独立测试与 CR 通过 |
| Phase 4b | 真实接线与 worker 执行测试固化（R4.2）+ 计划同步（R4.1） | ✅ 完成（2026-10-09） | 每渠道 completed/failed/foreign_actor/history_unavailable 4×2 组合经真实入口+真实薄桥+HTTP/ASGI+真实授权/脱敏；真实 `RunnerWorker.execute` 断言 Runtime 用户与 verbose 配置；CR 无 P0/P1；验证记录见下 |
| Phase 5 | 部署与真机验收 | 📋 待开发 | 用户授权部署后单独核对原飞书行为，不以 Web/KF 验收代替；未验证范围见「验证记录」 |

只做必要的调用、身份和结果适配；不新增渠道专用锁闸门、inbox/投递管线、消费者或容器。详细边界见[AgentRunner 架构 §8.1](../../system/agent-application-architecture-design.md#81-后续接入的固定边界)，KF 参考实现见[微信客服恢复计划](../../plans/plan-wecom-kf-channel-restore.md)，两轮审核记录见[审核报告](../../reviews/feishu-dingtalk-agentrunner-review-2026-10-09.md)。

#### 接入前基线核对（2026-10-09，描述接入前代码状态，非现状）

- 接入点唯一：`_process_tenant_feishu_background`（`src/saas/api/channel_routes.py`）内原为 `agent_router.get_agent(subagent_type, session_id)` 构造进程内 Agent，经 `channel_session_manager.process_and_persist → session_queue.enqueue_and_process → agent.process_message_sync` 执行；主 API 进程内运行模型与引擎。**接入后**该处为 `ChannelRunnerAgent(source='feishu', ...)`。
- 会话：`get_or_create_session(channel_type='feishu', channel_user_id=message.user_id, subagent_id=subagent_type or '')`；`channel_chat_id` 未落库（恒空），接入前后一致。群聊场景 parse_message 已在入口过滤未 @ 机器人事件。
- 用户上下文：`ensure_user_registered` 建号，`build_agent_user_for_channel` 补全并写回 `users` 表 phone/nickname 后构造 User 传给 agent（name 优先级 nickname > username）。**接入后**该 User 不再随请求传递，由 worker `_agent_user` 重建（见设计表）。
- 历史：引擎 `tool_messages` 事件经 progress_callback 汇入 `process_and_persist` 批量事务，与 user/assistant 一同写 `channel_messages`；接入后改由 channel-result 脱敏取回，落库路径不变。
- 费用：接入前渠道 `SessionRecordManager` 经 `set_model/set_provider` 记录进程内 llm token 用量；接入后见设计表「费用责任」。
- verbose：`resolve_verbose_feedback_config` + `make_send_verbose` 已接线，事件由进程内 Agent 经 feedback_state 产出；接入后配置随请求传递给 Runner。

#### 接入设计（含两轮审核修订）

| 项 | 决定 |
|----|------|
| 替换边界 | 仅把 `agent_router.get_agent(...)` 换成 `ChannelRunnerAgent(source='feishu', session_id=..., channel_user_id=message.user_id, channel_chat_id=None, profile_id=subagent_type or 'main', config_id=config_id)`；回调验签/去重、隐藏命令、自动注册、send_response/send_verbose、`process_and_persist`、session_queue 合并/取消全部保留 |
| 参数映射 | `channel_chat_id=None` 与 `channel_sessions` 行一致（授权校验 `(channel_chat_id or None) == None`）；`profile_id` 与会话行 `subagent_id` 对齐（Runner `CHANNEL_PROFILE_MISMATCH` 校验）；`config_id` 随 `request_data.channel_config_id` 持久化，仅供追踪 |
| 失败交付（R1） | `process_and_persist` 显式分流 `status="error"`：`mark_error("对话处理失败")`、经 send_response 发非空提示「抱歉，处理您的消息时遇到了问题，请稍后重试。」；不写空 assistant、不发空正文、不触发成功 recap；merged follower 提前 return 不受影响；取消仍走空 success 语义；不重交任务、不重放工具副作用 |
| 历史责任 | `channel_messages` 仍由原 `process_and_persist` 事务写入；完成时经 `GET /v1/runners/{id}/channel-result` 取脱敏工具消息汇入；读取失败按失败交付处理（发提示、不发正文） |
| 费用责任（R3） | 模型/工具费用由 Runner receipts/finalizer 结算；渠道入口 `start_record` 后 `skip_save=True`（KF 同款归属）——无渠道侧独立用量时不写零用量 chat_record，避免对话数/耗时统计重复；`end_record` 收尾保留。无证据表明模型费用双扣，本项仅统计口径修复 |
| 用户上下文（R2） | 不随请求传 `channel_user_info`（KF 专属），不以自由请求字段建立身份；worker `_agent_user(source, channel_user_id)` 从已授权 Runner 行与 `users` 表重建原渠道语义：channel 来源 name 优先级 nickname > username > phone（修复技术账号名进入「当前用户」提示词），并恢复 `channel_type`/`channel_user_id` 字段；Web 构造不变 |
| 不新增 | 渠道专属 runtime scope/工具上下文、channel_chat_id 落库、附件/媒体新能力、专用消费者或容器 |

#### Runner 共享缺口最小修复（已随本项实施，含审核补充）

1. `manager.channel_result` 的 `source != 'wecom_kf'` 403 限制放开到 feishu/dingtalk（脱敏逻辑不变）——否则工具消息历史回退。
2. `worker.py` verbose 配置门提炼为 `channel_verbose_config(source, request_data, base)` 并扩为三源（`channel_user_info` 仍仅 KF）——否则 verbose 中间反馈静默失效。
3. `finalizer.py` 的 `write_history` 门由 `!= 'wecom_kf'` 扩为三源渠道均不写——否则 Runner 与渠道 `process_and_persist` 对同一轮 `channel_messages` 双写（首轮 CR 发现，计划初稿漏列）。
4. `configs/config.yaml` `peers.web.sources` 增加 `feishu`、`dingtalk`（共用 web peer，不新增令牌）；部署环境配置同步。
5. `src/channels/session.py` `process_and_persist` 显式 error 分流（R1，共享层既有缺口，全渠道受益）。
6. `worker.py` `_agent_user` 渠道用户重建（R2）。
7. 两渠道入口 `skip_save=True`（R3）。

#### 验证记录（2026-10-09，本机 Windows/Git Bash，venv Python 3.12.6；`scripts/dev_test.sh` 宿主机降级模式——容器 aid-agent-api 未运行）

标准命令与实际结果（最终代码状态）：

```bash
bash ./scripts/dev_test.sh tests/unit/channels/test_channel_agent_runner_wiring.py \
  tests/unit/services/agent_runner/test_worker_execution.py \
  tests/unit/channels/test_channel_runner_service_independent.py \
  tests/integration/test_channel_agent_runner_entries.py \
  tests/integration/test_feishu_routes.py tests/integration/test_dingtalk_routes.py \
  tests/integration/test_wecom_kf_reply_delivery.py -p no:cacheprovider -q
# → 138 passed（接线 10 + worker 执行 2 + bridge 59 + 入口 28 + 回调 26 + KF 交付 13）
bash ./scripts/dev_test.sh tests/unit/channels tests/unit/services/agent_runner -q
# → 1130 passed, 7 skipped, 2 failed（均为预存环境问题，与本任务无关）
```

- 接线测试真/假边界：真实后台入口函数体、ChannelRunnerAgent、RunnerServiceClient（httpx→ASGI）、API 全路由、RunnerManager/RunnerAuthorizer、channel-result 脱敏、`process_and_persist` error 分流、`RunnerWorker.execute`/`_agent_user`/`channel_verbose_config`/`iter_with_verbose_feedback` 均为真实代码；桩仅落在平台 adapter、内存 DB 行、session_queue 契约镜像（不覆盖 merge/pending 路径）、环境配置，以及 worker 执行侧的 MemoryRunners/MemoryExecutions（受理即终态 + 最小方法集）、记录型 Runtime/factory、finalizer 与 trace IO（`_persist_trace`）假件——与两个测试文件内的边界注释一致。
- 预存失败 2 个（未处理，与本任务无关）：`test_artifact_refs` symlink（Windows WinError 1314）、`test_wecom_kf_servicer_visibility` 下载链接绝对 URL（并行任务 settings 改动）。
- **未验证范围**：真实飞书平台投递、Runner 进程 lifespan、真实 PostgreSQL 结算（agent_runner_usage_receipts）、跨进程 Redis 合并/取消竞争、`tests/integration/agent_runner_service/` 隔离库套件（本机无 aid_test 库）；真机验收待用户授权部署后单独执行（来源授权、对话结果、追问、新消息取代旧任务、历史含工具消息、失败提示、费用不双记）。

## 原渠道建设方案（历史基线）

> 基于企业微信渠道已上线的成熟架构，飞书渠道对接重点在于：修复现有 `FeishuAdapter` 中的错误实现，补齐缺失的媒体处理、长消息拆分、消息构建器、连接池复用等能力，并调整路由层以适配飞书的 challenge-response 验证机制。

## 1. 现状评估

### 1.1 已就绪的基础设施

| 组件 | 状态 | 说明 |
|------|------|------|
| `ChannelFactory._ADAPTER_CLASSES["feishu"]` | ✅ 已注册 | 工厂可按类型动态加载 `FeishuAdapter` |
| `tenant_channel_configs` 表 + 租户配置 API | ✅ 已就绪 | 支持 feishu 类型的配置 CRUD |
| `_REQUIRED_FIELDS["feishu"]` | ✅ 已定义 | `app_id, app_secret, verification_token, encrypt_key` |
| 路由 `GET/POST /t/{tenant_id}/feishu/callback/{config_id}` | ⚠️ 存根 | GET 仅回 challenge，POST 已接入 `_process_tenant_channel_message` |
| `src/channels/feishu/adapter.py` | ⚠️ 半成品 | 基本骨架在，但存在多处错误实现（见下节） |

### 1.2 现有 `FeishuAdapter` 的问题（已核对官方文档）

| # | 问题 | 影响 | 官方正确做法 |
|---|------|------|------------|
| 1 | AES 密钥错误：直接用 `encrypt_key.encode()[:32].ljust(32, b'\0')` | 解密失败 | AES key = `SHA256(encrypt_key)`（32 字节） |
| 2 | 解密后数据结构错误：代码直接 `json.loads(decrypted)` | JSON 解析失败 | 解密后 = `[16字节随机串] + [4字节大端序消息长度] + [JSON内容] + [app_id]`，必须按偏移读取 |
| 3 | 签名验证方式错误：用 HMAC-SHA256 自算签名 | 签名校验失败 | v2.0 事件用 `X-Lark-Signature` 请求头，算法 `SHA256(timestamp + nonce + encrypt_key + body)`；url_verification 用 `token` 字段比对 |
| 4 | URL 验证流程错误：代码只处理 GET challenge query | URL 验证失败 | 飞书发 POST JSON `{type:"url_verification", token:"xxx", challenge:"xxx"}`，需校验 token 后返回 `{challenge:"xxx"}` |
| 5 | `get_access_token` 无 `asyncio.Lock` | token 抖动 | 必须加锁 + 双重检查 |
| 6 | 每次请求 `async with httpx.AsyncClient()` | 性能差 | 单例 `httpx.AsyncClient` 连接池 |
| 7 | `send_message` 仅支持纯文本 | 能力缺失 | 支持 text / post / interactive / image / file，且 `content` 字段必须是 JSON **字符串**（不是对象） |
| 8 | 无长消息拆分 | 超长消息失败 | 企微已实现三级策略，需复用 |
| 9 | 无欢迎消息支持 | 新会话无引导 | 参考企微 |
| 10 | 无速率限制 | 触发飞书限流 | 每 user 滑动窗口 |
| 11 | 无媒体上传/下载 | 文件图片无法处理 | 需对接 `/im/v1/images` 和 `/im/v1/files` |
| 12 | 群聊 @机器人处理缺失 | 群聊场景不可用 | 需清理 `content` 中的 `@_user_X` 占位符，结合 `mentions` 数组还原 |
| 13 | 未区分单聊/群聊 | 群聊中机器人响应所有消息 | 需检查 `chat_type`：`p2p` 直接处理，`group` 必须确认 @ 了机器人 |
| 14 | 未获取机器人自身 `open_id` | 无法判断群聊 @ 目标 | 需调用 `GET /open-apis/bot/v3/info` 获取并缓存 |
| 15 | 未实现 `format_response` 方法 | 抽象方法未完整 | 必须实现 |

### 1.3 飞书与企业微信的关键差异

| 维度 | 企业微信 | 飞书 |
|------|---------|------|
| URL 验证 | GET 回调带 `msg_signature/timestamp/nonce/echostr`，需 AES 解密 echostr 返回明文 | POST JSON `{type:"url_verification", token:"xxx", challenge:"xxx"}`，校验 token 后返回 `{challenge:"xxx"}`；配置加密模式时整个 body 可能在 `encrypt` 字段中 |
| 消息合法性校验 | 必选 AES 加解密 + SHA1 签名 | **三选一**：① url_verification 用 `token` 字段比对 verification_token；② v2.0 事件校验 `X-Lark-Signature` 请求头，算法 `SHA256(timestamp + nonce + encrypt_key + body)`；③ 加密模式 AES 解密成功即合法 |
| AES 密钥 | `Base64Decode(encoding_aes_key)` 直接作为 key，IV = key[:16] | AES key = `SHA256(encrypt_key)`（32 字节），IV = Base64 解码密文后的前 16 字节（IV 在密文前缀，不在 key 中） |
| 密文结构 | AES 密文直接拼接（无 IV 前缀） | Base64 解码后 = IV(16字节) + AES密文；解密后 = 随机串(16字节) + 消息长度(4字节大端序) + JSON + app_id |
| access_token | `corpid + secret` → `https://qyapi.weixin.qq.com/cgi-bin/gettoken` | `app_id + app_secret` → `tenant_access_token/internal` |
| 消息格式 | XML（加密后） | JSON（可选加密） |
| 接收消息 API | 推送 XML 到回调 URL | 事件订阅 `im.message.receive_v1`，JSON |
| 发送消息 | `https://qyapi.weixin.qq.com/cgi-bin/message/send` | `https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id` |
| 用户 ID | `UserId`（企业内部唯一） | `open_id`（应用维度）/ `union_id`（同开发者下）/ `user_id`（租户维度） |
| 富文本消息 | markdown / textcard / news | interactive（卡片 JSON）/ post（富文本）/ image / file |
| 消息去重 | 通过 `MsgId` 去重 | 通过 `message_id` 去重 |

---

## 2. 待办任务清单

### 2.1 新增 `src/channels/feishu/crypto.py` — 加解密与校验

参考 `wecom/crypto.py` 的结构实现 `FeishuCrypto` 类。**严格按飞书官方 SDK `lark-oapi` 的解密逻辑**：

```python
class FeishuCrypto:
    def __init__(self, verification_token: str, encrypt_key: str):
        self.verification_token = verification_token
        # 飞书官方：AES key = SHA256(encrypt_key)，固定 32 字节
        self.aes_key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        # 注意：IV 不在 key 中取，而是从 Base64 解码密文后的前 16 字节取

    def decrypt(self, encrypt_data: str) -> dict:
        """
        解密 encrypt 字段。严格按以下顺序：
        1. Base64 解码 → enc_bytes
        2. IV = enc_bytes[:16]，ciphertext = enc_bytes[16:]
        3. AES-256-CBC 解密 → 去 PKCS7 填充 → content_bytes
        4. content_bytes 结构：[16字节随机串] + [4字节大端序消息长度] + [JSON] + [app_id]
           - 跳过前 16 字节随机串
           - 读取 4 字节大端序得到 msg_len
           - 截取 msg_len 字节得到 JSON 字符串
        5. JSON 解析返回 dict
        """

    def verify_url_verification(self, body: dict) -> Optional[str]:
        """
        处理 url_verification 请求：
        1. 若 body 含 encrypt 字段，先 decrypt 得到明文
        2. 校验 body["token"] == self.verification_token
        3. 返回 body["challenge"]
        校验失败返回 None
        """

    def verify_signature(self, timestamp: str, nonce: str, body: str, signature: str) -> bool:
        """
        校验 v2.0 事件的 X-Lark-Signature 请求头：
        content = timestamp + nonce + encrypt_key + body
        计算 SHA256(content) 与 signature 比对
        注意：content 里拼接的是 encrypt_key 原文，不是 SHA256 后的 key
        """
```

**关键点（与企微差异）**：
- AES key 用 `SHA256(encrypt_key)`（不是直接取字节）
- IV 从密文前 16 字节取（不是从 key 取）
- 解密后要先剥 16 字节随机串 + 4 字节长度前缀才能拿到 JSON（**不能直接 json.loads**）
- PKCS7 填充块大小 = 32 字节（与企微相同）
- v2.0 签名校验用 `encrypt_key` 原文参与 SHA256，不是 aes_key

### 2.2 新增 `src/channels/feishu/message_builder.py` — 消息构建

参考 `wecom/message_builder.py` 实现 `FeishuMessageBuilder`。**关键：返回的 `content` 字段已经是 JSON 字符串**（与 5.2 P1 避坑点对应），调用方可以直接放入请求体，不需要再 `json.dumps`。

```python
class FeishuMessageBuilder:
    @staticmethod
    def build_text(text: str) -> dict:
        """
        返回：{"msg_type": "text", "content": json.dumps({"text": text})}
        注意 content 已经是 JSON 字符串，不是对象
        """

    @staticmethod
    def build_post(title: str, paragraphs: list) -> dict:
        """富文本消息（post），content 为 JSON 字符串。支持加粗/链接/图片"""

    @staticmethod
    def build_interactive(elements: list, header: dict = None) -> dict:
        """卡片消息（interactive），content 为 JSON 字符串。用于复杂排版"""

    @staticmethod
    def build_image(image_key: str) -> dict:
        """图片消息，content = json.dumps({"image_key": image_key})"""

    @staticmethod
    def build_file(file_key: str, filename: str) -> dict:
        """文件消息，content = json.dumps({"file_key": ..., "file_name": ...})"""

    @staticmethod
    def detect_message_type(text: str, downloadable_files: list) -> str:
        """根据内容智能选择消息类型（text / post / interactive）"""
```

**关键点**：
- 飞书卡片消息用 `interactive` 类型，JSON 结构包含 `header` + `elements`
- Markdown 在飞书中用 `post`（富文本）或卡片中的 `markdown` 元素
- 飞书单条消息有长度限制（文本约 4000 字符），超长仍需拆分
- **所有 build_*() 方法的 `content` 返回值都是 JSON 字符串**（避免调用方踩 P1 坑）

### 2.3 新增 `src/channels/feishu/media.py` — 媒体处理

参考 `wecom/media.py` 实现 `FeishuMedia`：

```python
class FeishuMedia:
    def __init__(self, get_access_token: Callable): ...

    async def download_image(self, image_key: str, save_dir: str) -> str:
        """GET /open-apis/im/v1/images/{image_key}"""

    async def download_file(self, file_key: str, save_dir: str) -> str:
        """GET /open-apis/im/v1/files/{file_key}"""

    async def upload_image(self, image_path: str, image_type: str = "message") -> str:
        """POST /open-apis/im/v1/images，返回 image_key"""

    async def upload_file(self, file_path: str, file_type: str = "file") -> str:
        """POST /open-apis/im/v1/files，返回 file_key"""
```

**关键点**：
- 飞书的 image_key / file_key 是一次性使用的下载凭证
- 上传时 image 和 file 走不同 API 路径
- 下载时需在 URL 中指定 `image_type` 或 `file_type`

### 2.4 重构 `src/channels/feishu/adapter.py`

按以下顺序修复/增强：

#### 2.4.1 修复初始化与连接池

```python
def __init__(self, app_id, app_secret, verification_token="", encrypt_key="",
             welcome_message="", max_bytes=4000, split_on_paragraph=True,
             rate_limit_window=60, rate_limit_max=10, media_upload_dir="/tmp"):
    ...
    self.crypto = FeishuCrypto(verification_token, encrypt_key) if encrypt_key else None
    self.message_builder = FeishuMessageBuilder()
    self.media = FeishuMedia(self.get_access_token)
    self._http = httpx.AsyncClient(
        timeout=30, connect=10, limits=httpx.Limits(max_connections=100)
    )
    self._token_lock = asyncio.Lock()
    # 速率限制（每 user 滑动窗口）
    self._rate_limit_window = rate_limit_window
    self._rate_limit_max = rate_limit_max
    self._user_message_times: dict = defaultdict(list)
```

#### 2.4.2 修复 `get_access_token` 并发安全

```python
async def get_access_token(self) -> str:
    if self._access_token and time.time() < self._token_expires:
        return self._access_token
    async with self._token_lock:
        # 双重检查
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        # 调用飞书 tenant_access_token/internal API，带指数退避重试
        ...
```

#### 2.4.3 修复 `parse_message`

v2.0 事件结构（严格按飞书官方 `im.message.receive_v1` schema）：

```json
{
  "schema": "2.0",
  "header": {
    "event_id": "f7984f25108f8137722bb63cee927e66",
    "event_type": "im.message.receive_v1",
    "create_time": "1630397830000",
    "token": "xxx",  // 明文模式用此字段比对 verification_token
    "app_id": "cli_xxx",
    "tenant_key": "xxx"
  },
  "event": {
    "sender": {
      "sender_id": {"open_id": "ou_xxx", "user_id": "xxx", "union_id": "on_xxx"},
      "sender_type": "user"
    },
    "message": {
      "message_id": "om_xxx",
      "chat_id": "oc_xxx",
      "chat_type": "p2p",  // 或 "group"
      "message_type": "text",
      "content": "{\"text\":\"@_user_1 帮我查天气\"}",  // JSON 字符串
      "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot_xxx"}, "name": "机器人"}]
    }
  }
}
```

**处理要点**：
- `header.event_type` 判断事件类型（路由层已做，adapter 里不必重复判断）
- `event.message.chat_type` 区分单聊/群聊：
  - `p2p`：直接处理
  - `group`：必须检查 `mentions` 数组，确认有 @ 机器人，然后清理 `@_user_X` 占位符
- `event.message.content` 是 **JSON 字符串**（不是对象），需要 `json.loads`
- `event.message.mentions` 中每个元素的 `key` 对应 content 中的占位符，`id.open_id` 是被 @ 者的 open_id
- 判断 @ 机器人：用 `GET /open-apis/bot/v3/info` 获取机器人自身 `open_id`，与 mention 的 `id.open_id` 比对，结果缓存在 `self._bot_open_id`

#### 2.4.4 增强 `send_message`

- 长消息拆分：复用企微的三级策略（段落 → 行 → 字节）
- 根据 `UnifiedResponse` 的内容类型智能选择：
  - 纯文本 → `message_builder.build_text()`，返回的 `content` 已是 JSON 字符串
  - 带 markdown → `message_builder.build_interactive()`（卡片）或 `build_post()`（富文本）
  - 有 `downloadable_files` → 先发文本，再逐个上传并发送文件/图片
- **关键避坑（P1）**：`message_builder.build_*()` 的设计保证 `content` 字段是 JSON 字符串（不是对象），调用方直接放入请求体即可，**禁止**再做 `json.dumps`
- 调用 `message_builder` 构建消息体后直接发请求：

```python
msg_body = message_builder.build_text(message.text)  # content 已是 JSON 字符串
body = {
    "receive_id": message.reply_to,
    "msg_type": msg_body["msg_type"],
    "content": msg_body["content"],  # 已是字符串，直接用
}
```

- 使用 `uuid` 字段做幂等性去重（飞书支持），避免重复发送

#### 2.4.5 新增速率限制

参考企微实现，在 `send_message` 入口做滑动窗口检查：
- 每 user 默认 60s 内最多 10 条
- 绕过：`send_waiting_indicator` 发出的等待提示不计入

#### 2.4.6 新增欢迎消息

参考企微的 `welcome_message` 配置，在 `_process_tenant_channel_message` 中首次会话时发送。

#### 2.4.7 启动时获取机器人自身 `open_id`（供群聊 @判断使用）

```python
async def _init_bot_open_id(self) -> None:
    """
    调用 GET /open-apis/bot/v3/info 获取机器人自身 open_id，缓存到 self._bot_open_id。
    在 adapter 首次使用时懒加载，或在 __init__ 后由工厂方法主动 await。
    群聊 parse_message 中需要据此判断 mention 是否 @ 了本机器人。
    """
    if self._bot_open_id:
        return
    async with self._http_session():
        resp = await self._http.get(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {await self.get_access_token()}"},
        )
        resp.raise_for_status()
        self._bot_open_id = resp.json().get("bot", {}).get("open_id")
```

**使用点**：`parse_message` 处理 `chat_type == "group"` 时，遍历 `event.message.mentions`，只有 `mention.id.open_id == self._bot_open_id` 才认为是 @ 了本机器人；否则忽略该消息（避免群内所有对话都触发机器人响应）。

### 2.5 完善路由层 `src/saas/api/channel_routes.py`

当前 GET 路由过于简单，且 POST 路由没有处理 `url_verification` 和加密事件，必须重写。

**合并 GET 和 POST 为统一的入口**（飞书 URL 验证实际走 POST）：

```python
@router.post("/t/{tenant_id}/feishu/callback/{config_id}")
async def tenant_feishu_callback_post(
    tenant_id: str, config_id: str, request: Request
):
    """飞书事件统一入口（url_verification 和事件回调都走这里）"""
    body = await request.body()
    body_str = body.decode()

    adapter, _, config = ChannelFactory.create_from_tenant_config(tenant_id, "feishu")
    if not adapter:
        return JSONResponse({"code": 404, "msg": "config not found"}, status_code=404)

    data = json.loads(body_str)

    # 1. 加密事件先解密（url_verification 也可能被加密）
    if "encrypt" in data and adapter.crypto:
        try:
            data = adapter.crypto.decrypt(data["encrypt"])
        except Exception as e:
            logger.error(f"[Feishu] 解密失败: {e}")
            return JSONResponse({"code": 400, "msg": "decrypt failed"}, status_code=400)

    # 2. v2.0 事件签名校验（X-Lark-Signature 请求头）
    if adapter.crypto:
        signature = request.headers.get("X-Lark-Signature")
        timestamp = request.headers.get("X-Lark-Request-Timestamp")
        nonce = request.headers.get("X-Lark-Request-Nonce")
        if signature and not adapter.crypto.verify_signature(timestamp, nonce, body_str, signature):
            logger.warning(f"[Feishu] 签名验证失败: tenant={tenant_id}")
            return JSONResponse({"code": 403, "msg": "invalid signature"}, status_code=403)

    # 3. url_verification 挑战：校验 token，返回 challenge
    if data.get("type") == "url_verification":
        if adapter.crypto and data.get("token") != adapter.crypto.verification_token:
            return JSONResponse({"code": 403, "msg": "token mismatch"}, status_code=403)
        return {"challenge": data["challenge"]}

    # 4. 事件去重（飞书会对无 2xx 响应的请求重试）
    event_id = data.get("header", {}).get("event_id")
    if event_id and await _is_duplicate_event(event_id):
        return JSONResponse({"code": 0, "msg": "duplicate"})

    # 5. 只处理 im.message.receive_v1 消息事件
    event_type = data.get("header", {}).get("event_type")
    if event_type != "im.message.receive_v1":
        # 其他事件（im.message.recalled_v1 等）暂不处理，返回 200 避免重试
        return JSONResponse({"code": 0, "msg": "event ignored"})

    # 6. 立即返回 200，避免飞书重试
    # 7. 异步处理消息（复用 _process_tenant_channel_message）
    asyncio.create_task(_process_tenant_channel_message(tenant_id, "feishu", body, body_str))
    return JSONResponse({"code": 0, "msg": "ok"})


@router.get("/t/{tenant_id}/feishu/callback/{config_id}")
async def tenant_feishu_callback_get(tenant_id: str, config_id: str, challenge: str = Query(None)):
    """兼容旧版 GET challenge（实际飞书用 POST，保留做兜底）"""
    if challenge:
        return {"challenge": challenge}
    return {"status": "ok"}
```

**关键点**：
- 飞书 URL 验证实际是 POST，不是 GET，必须合并处理
- 加密模式下 `url_verification` 的 body 也可能在 `encrypt` 字段中，需先解密
- v2.0 签名校验在原始 body 上计算（解密前），不能先解密再校验
- 事件去重用 Redis 缓存 `event_id`，TTL 5 分钟
- 必须立即返回 2xx，否则飞书会重试导致重复处理
- `im.message.receive_v1` 的事件结构是 `{header: {event_type, event_id, ...}, event: {sender, message}}`，解析时注意层级

### 2.6 测试方案

在 `tests/unit/channels/` 新增 `test_feishu_crypto.py` 和 `test_feishu_adapter.py`。**测试用例必须覆盖下文第 5 节所有勘误过的行为**，防止回归。

#### 2.6.1 `test_feishu_crypto.py`（加解密核心）

| 用例 | 验证点 |
|------|--------|
| `test_aes_key_is_sha256_of_encrypt_key` | `crypto.aes_key == hashlib.sha256(encrypt_key.encode()).digest()`，长度 32 |
| `test_iv_comes_from_ciphertext_prefix_not_key` | Base64 解码后 `IV = enc_bytes[:16]`，**不是** `aes_key[:16]`。构造已知明文+已知 IV 的密文验证 |
| `test_decrypt_strips_random_prefix_and_length_header` | 解密后数据 = `[16字节随机] + [4字节大端序 msg_len] + [JSON] + [app_id]`，必须按偏移读取 JSON，不能直接 `json.loads` 整段 |
| `test_decrypt_pkcs7_padding_block_size_32` | 填充块大小 = 32（与企微相同） |
| `test_verify_signature_uses_encrypt_key_not_aes_key` | v2.0 签名：`SHA256(timestamp + nonce + encrypt_key原文 + body)`，拼接的是 encrypt_key 原文而不是 aes_key |
| `test_verify_url_verification_post_body` | POST JSON `{type:"url_verification", token, challenge}`，校验 token 后返回 `{challenge}`，**不是 GET query 参数** |
| `test_verify_url_verification_with_encrypt_field` | 加密模式下 url_verification body 的 `encrypt` 字段需先解密再校验 token |

#### 2.6.2 `test_feishu_adapter.py`（业务逻辑）

| 用例 | 验证点 |
|------|--------|
| `test_parse_text_message_v2_event_structure` | 解析 `im.message.receive_v1` 事件，从 `event.message.content`（JSON 字符串）中提取 text |
| `test_parse_group_message_filters_mentions` | 群聊消息清理 `@_user_X` 占位符，还原成真实文字 |
| `test_parse_group_message_ignores_if_not_mentioned` | 群聊中没 @ 机器人的消息直接忽略（`chat_type == "group"` 且 mention 中没有 bot_open_id） |
| `test_parse_image_message_extracts_image_key` | 解析图片事件 `message_type == "image"`，提取 `image_key` |
| `test_send_message_content_is_json_string` | **关键避坑**：发送请求体 `content` 字段是 JSON 字符串（`json.dumps({"text": ...})`），不是对象，否则报 `content is invalid` |
| `test_send_long_message_three_tier_split` | 复用企微三级策略（段落 → 行 → 字节） |
| `test_access_token_refresh_lock` | 并发 100 次 `get_access_token` 只触发 1 次 HTTP 请求 |
| `test_rate_limit_per_user_sliding_window` | 60s 内超出 10 条后触发限流，不同 user 互不影响 |
| `test_bot_open_id_lazy_loaded_and_cached` | `_init_bot_open_id` 只调用一次 `/open-apis/bot/v3/info`，结果缓存 |
| `test_format_response_implemented` | 抽象方法已实现，不抛 `NotImplementedError` |

#### 2.6.3 `test_feishu_routes.py`（路由层，放在 `tests/integration/`）

| 用例 | 验证点 |
|------|--------|
| `test_post_url_verification_returns_challenge` | POST `{type:"url_verification", token, challenge}` → 返回 `{challenge}` |
| `test_post_url_verification_rejects_wrong_token` | token 不匹配返回 403 |
| `test_v2_event_signature_verified` | 带 `X-Lark-Signature` 头，签名正确放行；签名错误返回 403 |
| `test_event_deduplication_by_event_id` | 5 分钟内重复 `event_id` 直接返回 200 不处理 |
| `test_non_message_event_returns_200` | `im.message.recalled_v1` 等非消息事件返回 200 避免飞书重试 |
| `test_route_responds_200_immediately` | 消息处理走 `asyncio.create_task` 异步，路由立即返回 2xx |

---

## 3. 配置项说明

### 3.1 租户配置字段

租户在管理后台「渠道配置」页面新增飞书渠道时需填写的字段（`_REQUIRED_FIELDS["feishu"]` 已定义）：

| 字段 | 来源 | 说明 |
|------|------|------|
| `app_id` | 飞书开放平台 > 应用 > 凭证与基础信息 | 应用唯一标识，`cli_` 开头 |
| `app_secret` | 同上 | 应用密钥 |
| `verification_token` | 事件与回调 > 事件配置 | 用于明文模式校验事件来源 |
| `encrypt_key` | 事件与回调 > 事件配置 | 用于加密模式解密事件，32 字符 |

### 3.2 飞书应用权限

需要在飞书开放平台申请以下权限：

| 权限名称 | 权限标识 | 用途 |
|---------|---------|------|
| 获取与发送单聊、群组消息 | `im:message` | 发送消息 |
| 读取用户发给机器人的单聊消息 | `im:message.receive_v1:readonly` | 接收消息事件 |
| 获取用户基本信息 | `contact:user.base:readonly` | 获取用户姓名、头像 |
| 获取与上传聊天中的图片和文件 | `im:resource` | 处理图片/文件消息 |

### 3.3 事件订阅

在飞书开放平台「事件与回调」中添加：

| 事件名称 | 事件标识 | 用途 |
|---------|---------|------|
| 接收消息 | `im.message.receive_v1` | 接收用户发给机器人的消息 |

回调 URL 格式：`https://your-domain.com/t/{tenant_id}/feishu/callback/{config_id}`

---

## 4. 实施顺序

```
Phase 1: crypto.py（加解密核心）
  ↓
Phase 2: message_builder.py（消息构建）
  ↓
Phase 3: media.py（媒体处理）
  ↓
Phase 4: 重构 adapter.py（连接池 + 并发锁 + 长消息拆分 + 速率限制 + 欢迎消息）
  ↓
Phase 5: 完善路由层（challenge 处理 + 事件解密 + 事件去重）
  ↓
Phase 6: 单元测试 + 集成测试
  ↓
Phase 7: 租户后台管理页面（前端 ChannelConfig 页增加飞书表单）
```

**Phase 7 前端部分**：租户后台「渠道配置」页面已有 wecom 的表单，需参考增加 feishu 表单（app_id / app_secret / verification_token / encrypt_key 四个字段）。

---

## 5. 风险与注意事项

### 5.1 通用风险

| 风险 | 缓解措施 |
|------|---------|
| 飞书加密算法细节与文档不完全一致 | 在 crypto.py 中添加飞书官方 SDK 测试用例作为单元测试（见 2.6.1） |
| `im.message.receive_v1` 事件会重试 | 路由层用 `event_id` 做去重（Redis 缓存 5 分钟） |
| 飞书卡片消息 JSON 结构较复杂 | 先只支持 text + post，interactive 卡片作为后续增强 |
| access_token 2 小时过期，并发场景下可能短暂失效 | token 缓存提前 300s 刷新；失败时 invalidate 并重试一次 |

### 5.2 已识别的关键避坑点（来自官方文档核对）

> 这些坑在企微对接时曾引发 bug，本次飞书对接**必须在编码阶段通过单元测试主动覆盖**。

| # | 坑点 | 错误做法 | 正确做法 | 单元测试 |
|---|------|---------|---------|---------|
| P1 | 发送消息 `content` 字段类型 | 直接传 JSON 对象 | **必须是 JSON 字符串**：`"content": json.dumps({"text": ...})`，否则报 `content is invalid` | `test_send_message_content_is_json_string` |
| P2 | 群聊消息 `@_user_X` 占位符 | 原样转发给 Agent | 必须结合 `event.message.mentions` 数组，把 `@_user_1` 还原成 `@机器人名`（或清洗掉） | `test_parse_group_message_filters_mentions` |
| P3 | AES IV 来源 | `IV = aes_key[:16]`（企微做法） | **`IV = Base64解码密文后的前 16 字节`**（飞书做法，IV 在密文前缀，不在 key 中） | `test_iv_comes_from_ciphertext_prefix_not_key` |
| P4 | 解密后数据结构 | 直接 `json.loads(decrypted)` | 必须先剥 `[16字节随机] + [4字节大端序长度]`，再 `json.loads` 中间 JSON | `test_decrypt_strips_random_prefix_and_length_header` |
| P5 | URL 验证方法 | GET query 参数 challenge | **POST JSON body** `{type:"url_verification", token, challenge}` | `test_post_url_verification_returns_challenge` |
| P6 | v2.0 签名拼接内容 | 用 `aes_key`（SHA256 后的）参与签名 | 用 **`encrypt_key` 原文**参与 SHA256：`SHA256(timestamp + nonce + encrypt_key + body)` | `test_verify_signature_uses_encrypt_key_not_aes_key` |
| P7 | 群聊是否 @ 了本机器人 | 看到 mention 就处理 | 必须调用 `/open-apis/bot/v3/info` 拿到 bot_open_id，比对 `mention.id.open_id`，否则群内所有 @ 都触发 | `test_parse_group_message_ignores_if_not_mentioned` |

---

## 6. 文件清单

最终需要新增/修改的文件：

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/channels/feishu/__init__.py` | 修改 | 导出 FeishuAdapter / FeishuCrypto / FeishuMessageBuilder / FeishuMedia |
| `src/channels/feishu/adapter.py` | 重写 | 修复现有错误，增强功能，新增 `_init_bot_open_id` |
| `src/channels/feishu/crypto.py` | 新增 | 加解密 + token 校验 + v2.0 签名校验 |
| `src/channels/feishu/message_builder.py` | 新增 | 消息构建器（text / post / interactive / image / file） |
| `src/channels/feishu/media.py` | 新增 | 媒体上传/下载 |
| `src/saas/api/channel_routes.py` | 修改 | 完善飞书路由（POST url_verification + 事件解密 + v2.0 签名校验 + 事件去重） |
| `tests/unit/channels/test_feishu_crypto.py` | 新增 | 加解密测试（覆盖 7 个避坑点 P3/P4/P6 等） |
| `tests/unit/channels/test_feishu_adapter.py` | 新增 | 业务逻辑测试（覆盖 P1/P2/P7 等） |
| `tests/integration/test_feishu_routes.py` | 新增 | 路由层集成测试（url_verification POST、v2.0 签名、事件去重） |
| `frontend/src/components/saas/ChannelConfig.vue` | 修改 | 租户后台增加飞书配置表单（Phase 7） |
