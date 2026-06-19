# 钉钉接入智能体 - 实施计划

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

@router.post("/t/{tenant_id}/dingtalk/callback")
async def tenant_dingtalk_callback_post(
    tenant_id: str,
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
