# 钉钉接入智能体 - 实施计划

## 开发进度

### AgentRunner 接入（后续独立事项）

> 状态：🔧 进行中（开发与两轮审核修复完成，待部署真机验收）。Agent/AgentRunner 架构重构已完成，本项仅让原钉钉渠道使用独立 Runner 提供对话服务。
>
> 下文为原钉钉渠道建设时的实施基线；其中“需要完全重写”描述当时的旧适配器，不是本次 Runner 接入要求。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 设计核对与计划更新 | ✅ 完成（2026-10-09） | 按接入前仓库核对接入点、公共设施与缺口，设计见下 |
| Phase 2 | Runner 共享缺口最小修复 | ✅ 完成（2026-10-09） | channel-result 放开三源；verbose source 门扩展；finalizer 历史门扩三源（CR 发现双写缺口）；peer sources 配置 |
| Phase 3 | 钉钉对话接入点替换 | ✅ 完成（2026-10-09） | `_process_tenant_dingtalk_background` 换用 `ChannelRunnerAgent`，渠道业务不动 |
| Phase 4 | 独立测试与 CodeReview（首轮） | ✅ 完成（2026-10-09） | 当时证据：bridge 45+4 用例、回调路由 26 回归、CR P0 finalizer 双写已修；后被第一轮审核认定测试不足，由 Phase 4a/4b 扩充（本行保留为历史记录） |
| Phase 4a | 审核修复 R1–R3 + 入口/参数化测试 | ✅ 完成（2026-10-09） | error 显式分流发非空失败提示（群聊保持原 reply_target）；worker 用户重建（nickname 优先 + 渠道字段）；skip_save 防零用量空账单；新增入口 28 用例 + bridge 参数化至 59；独立测试与 CR 通过 |
| Phase 4b | 真实接线与 worker 执行测试固化（R4.2）+ 计划同步（R4.1） | ✅ 完成（2026-10-09） | 每渠道 completed/failed/foreign_actor/history_unavailable 4×2 组合经真实入口+真实薄桥+HTTP/ASGI+真实授权/脱敏（含钉钉单聊/群聊 reply 目标）；真实 `RunnerWorker.execute` 断言 Runtime 用户与 verbose 配置；CR 无 P0/P1；验证记录见下 |
| Phase 5 | 部署与真机验收 | 📋 待开发 | 用户授权部署后单独核对原钉钉行为，不以 Web/KF 验收代替；未验证范围见「验证记录」 |

只做必要的调用、身份和结果适配；不新增渠道专用锁闸门、inbox/投递管线、消费者或容器。详细边界见[AgentRunner 架构 §8.1](../../system/agent-application-architecture-design.md#81-后续接入的固定边界)，KF 参考实现见[微信客服恢复计划](../../plans/plan-wecom-kf-channel-restore.md)，两轮审核记录见[审核报告](../../reviews/feishu-dingtalk-agentrunner-review-2026-10-09.md)。

#### 接入前基线核对（2026-10-09，描述接入前代码状态，非现状）

- 接入点唯一：`_process_tenant_dingtalk_background`（`src/saas/api/channel_routes.py`）内原为 `agent_router.get_agent(subagent_type, session_id)` 构造进程内 Agent，经 `channel_session_manager.process_and_persist → session_queue.enqueue_and_process → agent.process_message_sync` 执行；主 API 进程内运行模型与引擎。**接入后**该处为 `ChannelRunnerAgent(source='dingtalk', ...)`。
- 会话：`get_or_create_session(channel_type='dingtalk', channel_user_id=message.user_id, subagent_id=subagent_type or '')`；`channel_chat_id` 未落库（恒空），接入前后一致。群聊（conversation_type="2"）仅影响发送方向 reply_target（openConversationId），会话身份仍按发送用户。
- 用户上下文：`ensure_user_registered` 建号，`build_agent_user_for_channel` 补全并写回 `users` 表 phone/nickname 后构造 User 传给 agent（name 优先级 nickname > username）。**接入后**该 User 不再随请求传递，由 worker `_agent_user` 重建（见设计表）。
- 历史：引擎 `tool_messages` 事件经 progress_callback 汇入 `process_and_persist` 批量事务，与 user/assistant 一同写 `channel_messages`；接入后改由 channel-result 脱敏取回，落库路径不变。
- 费用：接入前渠道 `SessionRecordManager` 经 `set_model/set_provider` 记录进程内 llm token 用量；接入后见设计表「费用责任」。
- verbose：`resolve_verbose_feedback_config` + `make_send_verbose` 已接线（conversation_type/reply_target 与 final 同规则），事件由进程内 Agent 经 feedback_state 产出；接入后配置随请求传递给 Runner。

#### 接入设计（含两轮审核修订）

| 项 | 决定 |
|----|------|
| 替换边界 | 仅把 `agent_router.get_agent(...)` 换成 `ChannelRunnerAgent(source='dingtalk', session_id=..., channel_user_id=message.user_id, channel_chat_id=None, profile_id=subagent_type or 'main', config_id=config_id)`；回调验签/去重、隐藏命令、自动注册、send_response/send_verbose（含群聊 reply_target 规则）、`process_and_persist`、session_queue 合并/取消全部保留 |
| 参数映射 | `channel_chat_id=None` 与 `channel_sessions` 行一致（授权校验 `(channel_chat_id or None) == None`）；`profile_id` 与会话行 `subagent_id` 对齐（Runner `CHANNEL_PROFILE_MISMATCH` 校验）；`config_id` 随 `request_data.channel_config_id` 持久化，仅供追踪 |
| 失败交付（R1） | `process_and_persist` 显式分流 `status="error"`：`mark_error("对话处理失败")`、经 send_response 发非空提示「抱歉，处理您的消息时遇到了问题，请稍后重试。」——回复目标由渠道闭包决定，群聊仍发 openConversationId、单聊发 userId；不写空 assistant、不发空正文、不触发成功 recap；merged follower 提前 return 不受影响；取消仍走空 success 语义；不重交任务、不重放工具副作用 |
| 历史责任 | `channel_messages` 仍由原 `process_and_persist` 事务写入；完成时经 `GET /v1/runners/{id}/channel-result` 取脱敏工具消息汇入；读取失败按失败交付处理（发提示、不发正文） |
| 费用责任（R3） | 模型/工具费用由 Runner receipts/finalizer 结算；渠道入口 `start_record` 后 `skip_save=True`（KF 同款归属）——无渠道侧独立用量时不写零用量 chat_record，避免对话数/耗时统计重复；`end_record` 收尾保留。无证据表明模型费用双扣，本项仅统计口径修复 |
| 用户上下文（R2） | 不随请求传 `channel_user_info`（KF 专属），不以自由请求字段建立身份；worker `_agent_user(source, channel_user_id)` 从已授权 Runner 行与 `users` 表重建原渠道语义：channel 来源 name 优先级 nickname > username > phone（修复技术账号名进入「当前用户」提示词），并恢复 `channel_type`/`channel_user_id` 字段；Web 构造不变 |
| 不新增 | 渠道专属 runtime scope/工具上下文、channel_chat_id 落库、群聊 per-conversation 会话拆分、附件/媒体新能力、专用消费者或容器 |

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

- 接线测试真/假边界：真实后台入口函数体、ChannelRunnerAgent、RunnerServiceClient（httpx→ASGI）、API 全路由、RunnerManager/RunnerAuthorizer、channel-result 脱敏、`process_and_persist` error 分流、`RunnerWorker.execute`/`_agent_user`/`channel_verbose_config`/`iter_with_verbose_feedback` 均为真实代码；桩仅落在平台 adapter、内存 DB 行、session_queue 契约镜像（不覆盖 merge/pending 路径）、环境配置，以及 worker 执行侧的 MemoryRunners/MemoryExecutions（受理即终态 + 最小方法集）、记录型 Runtime/factory、finalizer 与 trace IO（`_persist_trace`）假件——与两个测试文件内的边界注释一致。钉钉群聊（conversation_type="2"）reply 到 openConversationId 且 content 带 conversation_type 已在接线用例断言。
- 预存失败 2 个（未处理，与本任务无关）：`test_artifact_refs` symlink（Windows WinError 1314）、`test_wecom_kf_servicer_visibility` 下载链接绝对 URL（并行任务 settings 改动）。
- **未验证范围**：真实钉钉平台投递、Runner 进程 lifespan、真实 PostgreSQL 结算（agent_runner_usage_receipts）、跨进程 Redis 合并/取消竞争、`tests/integration/agent_runner_service/` 隔离库套件（本机无 aid_test 库）；真机验收待用户授权部署后单独执行（来源授权、单聊/群聊对话结果、追问、新消息取代旧任务、历史含工具消息、失败提示、费用不双记）。

## 原渠道建设方案（历史基线）

本文档定义钉钉渠道对接的具体实施步骤、任务分解和验收标准。

## 实施背景

### 现状

- 企业微信渠道：已上线，运行稳定
- 飞书渠道：已上线，运行稳定
- 钉钉渠道：**现有代码为企微风格的错误实现（XML/SHA1），需要完全重写**

### 关键差异

| 项目 | 企微 | 飞书 | 钉钉 |
|------|------|------|------|
| 消息格式 | XML | JSON | JSON |
| 签名算法 | SHA1 | SHA256 | HmacSHA256 |
| 消息加密 | AES-256-CBC | AES-256-CBC | 无（HTTPS 传输） |
| 发送接口 | 单接口 | 单接口 | 单聊/群聊分接口 |
| access_token | 需主动获取 | 需主动获取 | 需主动获取 |

### 核心原则

1. **复用飞书的架构模式**：crypto.py / media.py / message_builder.py / adapter.py 四模块分离
2. **适配钉钉的 API 差异**：签名算法、发送接口、消息格式
3. **保持多租户兼容性**：使用相同的 ChannelConfig 表和路由模式
4. **保持向后兼容**：不破坏已上线的企微/飞书渠道

---

## 任务分解

### 阶段一：核心模块开发（3-5 天）

#### 任务 1.1：创建 crypto.py（签名验证）

**目标**：实现钉钉 HmacSHA256 签名验证

**文件**：`src/channels/dingtalk/crypto.py`

**关键实现**：

```python
class DingTalkCrypto:
    def __init__(self, app_secret: str):
        self.app_secret = app_secret
    
    def verify_signature(self, timestamp: str, sign: str) -> bool:
        """验证请求签名"""
        # HmacSHA256(timestamp + "\n" + appSecret, appSecret)
        ...
    
    def check_timestamp(self, timestamp: str, max_diff: int = 3600) -> bool:
        """检查时间戳是否在合理范围内（±1 小时）"""
        ...
```

**验收标准**：

- [ ] 单元测试覆盖签名验证成功/失败场景
- [ ] 单元测试覆盖时间戳过期场景
- [ ] 与钉钉官方签名算法一致（参考 integration_guide.md §4.1）

---

#### 任务 1.2：创建 message_builder.py（消息构建）

**目标**：实现钉钉消息构建器，支持文本/Markdown/图片/文件/卡片

**文件**：`src/channels/dingtalk/message_builder.py`

**关键实现**：

```python
class DingTalkMessageBuilder:
    @staticmethod
    def build_text(text: str) -> Dict[str, Any]:
        """构建文本消息，返回 {"msgKey": "sampleText", "msgParam": "..."}"""
        ...
    
    @staticmethod
    def build_markdown(title: str, text: str) -> Dict[str, Any]:
        """构建 Markdown 消息，返回 {"msgKey": "sampleMarkdown", "msgParam": "..."}"""
        ...
    
    @staticmethod
    def build_image(image_url: str) -> Dict[str, Any]:
        """构建图片消息"""
        ...
    
    @staticmethod
    def build_file(media_id: str, file_name: str) -> Dict[str, Any]:
        """构建文件消息"""
        ...
    
    @staticmethod
    def split_long_message(text: str, max_bytes: int = 4000) -> List[str]:
        """长消息三级拆分（复用飞书的实现逻辑）"""
        ...
```

**验收标准**：

- [ ] 单元测试覆盖所有 build_* 方法
- [ ] 单元测试覆盖长消息拆分场景
- [ ] msgKey 和 msgParam 格式符合钉钉官方规范

---

#### 任务 1.3：创建 media.py（媒体文件处理）

**目标**：实现钉钉图片/文件的下载和上传

**文件**：`src/channels/dingtalk/media.py`

**关键实现**：

```python
class DingTalkMedia:
    def __init__(self, access_token_getter: Callable[[], Awaitable[str]]):
        self._get_access_token = access_token_getter
    
    async def download_image(self, download_code: str) -> Optional[Tuple[str, bytes]]:
        """通过 downloadCode 下载图片"""
        ...
    
    async def download_file(self, download_code: str, file_name: str) -> Optional[Tuple[str, bytes]]:
        """通过 downloadCode 下载文件"""
        ...
    
    async def upload_media(self, file_path: str, media_type: str = "file") -> Optional[str]:
        """上传媒体文件，返回 mediaId"""
        ...
```

**验收标准**：

- [ ] 单元测试覆盖下载/上传成功/失败场景
- [ ] 使用 httpx.AsyncClient 连接池
- [ ] 错误处理完善（超时、HTTP 错误、网络异常）

---

#### 任务 1.4：重写 adapter.py（渠道适配器）

**目标**：完全重写钉钉适配器，替换错误的企微风格实现

**文件**：`src/channels/dingtalk/adapter.py`

**关键实现**：

```python
class DingTalkAdapter(ChannelAdapter):
    def __init__(self, tenant_id: str, app_key: str, app_secret: str, ...):
        self.crypto = DingTalkCrypto(app_secret)
        self.media = DingTalkMedia(self._get_access_token)
        self.message_builder = DingTalkMessageBuilder()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._token_lock = asyncio.Lock()
        self._rate_limiter = RateLimiter(window=60, max_requests=10)
    
    @property
    def channel_type(self) -> str:
        return "dingtalk"
    
    async def parse_message(self, raw_message: Dict) -> Optional[ParsedMessage]:
        """解析钉钉消息（JSON 格式）"""
        body = json.loads(raw_message.get("body", "{}"))
        msg_type = body.get("msgtype")
        conversation_type = body.get("conversationType")
        # 提取 senderId, text, downloadCode 等
        ...
    
    async def send_text(self, user_id: str, text: str, conversation_type: str = "1"):
        """发送文本消息（单聊 conversationType="1"，群聊 conversationType="2"）"""
        if conversation_type == "1":
            await self._send_single_chat(user_id, text)
        else:
            await self._send_group_chat(user_id, text)
    
    async def send_long_message(self, user_id: str, text: str, conversation_type: str = "1"):
        """发送长消息（自动拆分）"""
        parts = self.message_builder.split_long_message(text)
        for part in parts:
            await self.send_text(user_id, part, conversation_type)
            await asyncio.sleep(0.5)  # 避免触发速率限制
    
    async def _send_single_chat(self, user_id: str, text: str):
        """发送单聊消息：POST /v1.0/robot/oToMessages/batchSend"""
        ...
    
    async def _send_group_chat(self, conversation_id: str, text: str):
        """发送群聊消息：POST /v1.0/robot/groupMessages/send"""
        ...
    
    async def _refresh_access_token(self) -> str:
        """刷新 access_token（带并发锁）"""
        async with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at - 300:
                return self._access_token
            # POST /v1.0/oauth2/accessToken
            ...
    
    async def _get_access_token(self) -> str:
        """获取 access_token（自动刷新）"""
        if not self._access_token or time.time() >= self._token_expires_at - 300:
            await self._refresh_access_token()
        return self._access_token
    
    def _check_rate_limit(self, user_id: str) -> bool:
        """检查用户速率限制"""
        return self._rate_limiter.check(user_id)
```

**验收标准**：

- [ ] 单元测试覆盖 parse_message（文本/图片/文件/群聊/单聊）
- [ ] 单元测试覆盖 send_text（单聊/群聊）
- [ ] 单元测试覆盖 access_token 刷新（并发场景）
- [ ] 单元测试覆盖速率限制
- [ ] 集成测试覆盖完整消息处理流程

---

### 阶段二：路由与集成（2-3 天）

#### 任务 2.1：重写 channel_routes.py 中的钉钉路由

**目标**：替换错误的企微风格路由，实现钉钉签名验证和消息处理

**文件**：`src/saas/api/channel_routes.py`

**关键修改**：

```python
# 删除旧的企微风格路由（lines 840-863）

@router.post("/t/{tenant_id}/dingtalk/callback/{config_id}")
async def tenant_dingtalk_callback_post(
    tenant_id: str,
    config_id: str,
    request: Request,
):
    """钉钉回调入口"""
    # 1. 提取签名头
    timestamp = request.headers.get("timestamp")
    sign = request.headers.get("sign")
    
    if not timestamp or not sign:
        raise HTTPException(status_code=400, detail="missing signature headers")
    
    # 2. 获取租户配置
    config = await _get_channel_config(tenant_id, "dingtalk")
    if not config:
        raise HTTPException(status_code=404, detail="tenant not configured")
    
    # 3. 验证签名
    crypto = DingTalkCrypto(config["app_secret"])
    if not crypto.verify_signature(timestamp, sign):
        logger.warning(f"[Tenant DingTalk] 签名验证失败: tenant={tenant_id}")
        raise HTTPException(status_code=403, detail="invalid signature")
    
    # 4. 读取消息体
    body = await request.body()
    raw_body_str = body.decode("utf-8")
    body_json = json.loads(raw_body_str)
    msg_id = body_json.get("msgId")
    
    # 5. 事件去重
    dedup_key = f"dingtalk:{tenant_id}:{msg_id}"
    if await _get_dingtalk_event_dedup().exists(dedup_key):
        logger.info(f"[Tenant DingTalk] 重复事件: tenant={tenant_id}, msgId={msg_id}")
        return {"success": True}
    
    # 6. 标记已处理
    await _get_dingtalk_event_dedup().mark_seen(dedup_key, ttl=300)
    
    # 7. 异步处理
    asyncio.create_task(
        _process_tenant_channel_message(tenant_id, "dingtalk", body_json, raw_body_str)
    )
    
    # 8. 立即返回 200
    return {"success": True}
```

**验收标准**：

- [ ] 路由能正确接收钉钉回调
- [ ] 签名验证失败时返回 403
- [ ] 重复事件被正确过滤
- [ ] 异步处理不阻塞路由响应
- [ ] 集成测试覆盖完整回调流程

---

#### 任务 2.2：添加钉钉事件去重工具

**目标**：在 channel_routes.py 中添加钉钉专用的事件去重缓存

**文件**：`src/saas/api/channel_routes.py`

**关键实现**：

```python
_dingtalk_event_dedup: Optional[EventDedup] = None

def _get_dingtalk_event_dedup() -> EventDedup:
    """获取钉钉事件去重器（单例）"""
    global _dingtalk_event_dedup
    if _dingtalk_event_dedup is None:
        _dingtalk_event_dedup = EventDedup(prefix="dingtalk_event")
    return _dingtalk_event_dedup
```

**验收标准**：

- [ ] 单例模式，避免重复创建
- [ ] 与飞书/企微的事件去重器独立
- [ ] TTL 设置为 5 分钟（300 秒）

---

#### 任务 2.3：注册钉钉适配器到 ChannelManager

**目标**：在系统启动时注册钉钉适配器

**文件**：`src/main.py` 或 `src/channels/__init__.py`

**关键修改**：

```python
from src.channels.dingtalk.adapter import DingTalkAdapter

async def _register_channel_adapters():
    # ... 现有企微/飞书注册逻辑
    
    # 注册钉钉
    for config in await _get_dingtalk_configs():
        adapter = DingTalkAdapter(
            tenant_id=config["tenant_id"],
            app_key=config["app_key"],
            app_secret=config["app_secret"],
            welcome_message=config.get("welcome_message"),
        )
        channel_manager.register(adapter)
```

**验收标准**：

- [ ] 系统启动时自动加载所有已配置的钉钉租户
- [ ] 适配器正确注册到 ChannelManager
- [ ] 日志显示已注册的钉钉租户列表

---

### 阶段三：测试与联调（2-3 天）

#### 任务 3.1：编写单元测试

**目标**：为所有核心模块编写单元测试

**文件**：

- `tests/unit/channels/test_dingtalk_crypto.py`
- `tests/unit/channels/test_dingtalk_message_builder.py`
- `tests/unit/channels/test_dingtalk_media.py`
- `tests/unit/channels/test_dingtalk_adapter.py`

**覆盖率要求**：

- crypto.py: 100%（签名验证、时间戳检查）
- message_builder.py: 90%+（所有 build_* 方法、长消息拆分）
- media.py: 80%+（下载/上传成功/失败场景）
- adapter.py: 90%+（parse_message、send_text、token 刷新、速率限制）

---

#### 任务 3.2：编写集成测试

**目标**：测试完整消息处理流程

**文件**：`tests/integration/test_dingtalk_routes.py`

**测试场景**：

1. 正常消息接收 → 签名验证 → 去重 → 异步处理
2. 签名验证失败 → 返回 403
3. 重复事件 → 返回 200 但不处理
4. 单聊消息 → 调用 send_single_chat
5. 群聊消息 → 调用 send_group_chat
6. 长消息 → 自动拆分发送

---

#### 任务 3.3：联调测试

**目标**：在真实钉钉环境中测试

**步骤**：

1. 创建钉钉测试应用
2. 配置回调 URL（使用 ngrok 或类似工具暴露本地服务）
3. 用钉钉客户端发送测试消息
4. 检查日志和数据库记录
5. 验证机器人回复

**验收标准**：

- [ ] 单聊消息能正常接收和回复
- [ ] 群聊 @机器人消息能正常接收和回复
- [ ] 图片/文件消息能正常下载
- [ ] 长消息能正确拆分发送
- [ ] 事件去重生效（重试不重复处理）
- [ ] 速率限制生效（超限用户被拒绝）

---

### 阶段四：文档与部署（1-2 天）

#### 任务 4.1：更新前端管理页面

**目标**：在管理后台「渠道配置」页面添加钉钉选项

**文件**：`frontend/src/components/ChannelConfig.vue`

**关键修改**：

```vue
<BaseSelect v-model="form.channel_type">
  <option value="wecom">企业微信</option>
  <option value="feishu">飞书</option>
  <option value="dingtalk">钉钉</option>
</BaseSelect>

<!-- 钉钉配置表单 -->
<template v-if="form.channel_type === 'dingtalk'">
  <BaseInput v-model="form.app_key" label="AppKey" required />
  <BaseInput v-model="form.app_secret" label="AppSecret" type="password" required />
  <BaseInput v-model="form.welcome_message" label="欢迎消息" />
</template>
```

**验收标准**：

- [ ] 能成功添加钉钉渠道配置
- [ ] 配置保存到数据库
- [ ] 前端显示钉钉渠道状态

---

#### 任务 4.2：更新文档

**目标**：完善钉钉接入文档

**文件**：

- `docs/channel/dingtalk/integration_guide.md`（已完成）
- `docs/channel/dingtalk/implementation_plan.md`（本文档）
- `docs/ideas.md`（登记钉钉接入进度）

---

#### 任务 4.3：部署上线

**目标**：将钉钉渠道部署到生产环境

**步骤**：

1. 合并代码到主分支
2. 部署到测试环境，运行完整测试
3. 部署到生产环境
4. 配置第一个生产租户的钉钉渠道
5. 监控日志和错误率
6. 用户验收测试

---

## 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 钉钉 API 变更 | 高 | 参考官方文档，预留调整空间 |
| 签名算法实现错误 | 高 | 单元测试覆盖，与官方 SDK 对比 |
| access_token 并发刷新 | 中 | 使用 asyncio.Lock 保证线程安全 |
| 速率限制过严 | 中 | 提供配置项，支持调整 |
| 群聊消息识别错误 | 中 | 明确 conversationType 判断逻辑 |

---

## 时间估算

| 阶段 | 任务 | 预估时间 |
|------|------|----------|
| 阶段一 | 核心模块开发 | 3-5 天 |
| 阶段二 | 路由与集成 | 2-3 天 |
| 阶段三 | 测试与联调 | 2-3 天 |
| 阶段四 | 文档与部署 | 1-2 天 |
| **总计** | | **8-13 天** |

---

## 依赖关系

```
阶段一（核心模块）
  ├─ 任务 1.1 crypto.py
  ├─ 任务 1.2 message_builder.py
  ├─ 任务 1.3 media.py
  └─ 任务 1.4 adapter.py（依赖 1.1-1.3）
      │
      ▼
阶段二（路由与集成）
  ├─ 任务 2.1 重写路由（依赖 1.4）
  ├─ 任务 2.2 事件去重（依赖 2.1）
  └─ 任务 2.3 注册适配器（依赖 1.4）
      │
      ▼
阶段三（测试与联调）
  ├─ 任务 3.1 单元测试（依赖阶段一）
  ├─ 任务 3.2 集成测试（依赖阶段二）
  └─ 任务 3.3 联调测试（依赖阶段二）
      │
      ▼
阶段四（文档与部署）
  ├─ 任务 4.1 前端管理页面（可并行）
  ├─ 任务 4.2 更新文档（可并行）
  └─ 任务 4.3 部署上线（依赖阶段三）
```

---

## 验收标准（总体）

- [ ] 钉钉渠道能正常接收和回复消息（单聊 + 群聊）
- [ ] 签名验证机制正确实现
- [ ] 事件去重机制正确实现
- [ ] access_token 并发刷新安全
- [ ] 速率限制机制正确实现
- [ ] 媒体文件（图片/文件）能正确下载和上传
- [ ] 长消息能正确拆分发送
- [ ] 单元测试覆盖率达标（90%+）
- [ ] 集成测试覆盖主要场景
- [ ] 联调测试通过（真实钉钉环境）
- [ ] 前端管理页面支持钉钉配置
- [ ] 文档完整（integration_guide.md + implementation_plan.md）
- [ ] 生产环境部署成功
