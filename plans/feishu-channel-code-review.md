# 飞书渠道接入 - 代码审核报告

> 审核对象：`docs/channel/feishu/integration_guide.md` 及对应实现
> 审核日期：2026-06-19
> 审核范围：`src/channels/feishu/` 全部模块 + `src/saas/api/channel_routes.py` 飞书回调 + `src/saas/api/channel_config.py` 飞书配置项 + `src/saas/services/channel_factory.py`

## 一、严重问题（阻断级 / 必须修复）

### 1.1 飞书 adapter 在每次回调中被重新创建，导致内存级状态全部失效

**位置**：

- `src/saas/api/channel_routes.py:1309`（POST 回调主入口）
- `src/saas/api/channel_routes.py:1127`（后台异步处理）
- `src/saas/services/channel_factory.py:31-53`（`create_adapter` 每次都 `adapter_class(**config)`）

**现象**：每次飞书回调（含 url_verification、消息事件、其他事件）都会通过 `ChannelFactory.create_from_tenant_config` 重新 `FeishuAdapter(...)`。`FeishuAdapter.__init__` 中所有进程内状态都是全新初始化：

| 失效状态 | 期望行为 | 实际行为 |
|---------|---------|---------|
| `self._access_token` / `self._token_expires` | 命中缓存，2 小时内不刷新 token | 每次回调都重新调用 `/auth/v3/tenant_access_token/internal`，等于完全没有缓存 |
| `self._bot_open_id` | 懒加载一次后复用 | 每条群聊消息都触发 `/bot/v3/info` 调用 |
| `self._rate_limiter: Dict[str, deque]` | 滑动窗口累计 | 每次新建空 deque，**速率限制完全失效** |
| `self._http_client: Optional[httpx.AsyncClient]` | 持久连接池复用 | 每次新建 AsyncClient，原实例 **从不调用 `close()`**，造成 socket / 文件描述符泄漏 |

**对文档声明的破坏**：
- 文档 §1 "已实现能力"宣称「`tenant_access_token` 并发刷新锁 ✅」「每 user 滑动窗口速率限制 ✅」「HTTP 连接池 ✅」—— 在多租户 SaaS 模式下**这三项功能实质上都是失效的**。
- 文档 §8 第 4 条「速率限制：每用户每窗口 10 条」—— 实际不会触发。

**修复方向**：

1. 在 `ChannelFactory` 中按 `(tenant_id, channel_type, config_id)` 缓存 adapter 实例，配置变更时主动失效；
2. 或在 `FeishuAdapter` 内部用模块级 `_INSTANCE_LOCK` + 缓存字典；
3. 在 FastAPI 应用 `shutdown` 事件或周期任务中调用所有 adapter 的 `close()`。
4. 速率限制器如需多 worker 共享，应迁移到 Redis（参考 `backend_dev.md` 多 worker 规范）。

### 1.2 飞书 adapter 的 `httpx.AsyncClient` 从不关闭，资源泄漏

**位置**：`src/channels/feishu/adapter.py:130-137`、`139-143`

**现象**：`FeishuAdapter.close()` 已实现，但**整个项目无任何位置调用**（仅在 wecom_kf 路径有 `await adapter.close()`，飞书路径缺失）。叠加 1.1 的「每请求新建 adapter」，会导致 worker 进程的 fd 数量持续增长，长时间运行后触发 `Too many open files`。

**修复方向**：

1. 路由处理完成后立即 `await adapter.close()`（最直接，但失去连接池价值）；
2. 或与 1.1 联动，使用 adapter 缓存 + 应用关闭时统一 close。

### 1.3 文档与代码矛盾：飞书 `encrypt_key` 实际是必填字段

**位置**：

- 文档 §3.1 表格标注 `encrypt_key` 为「可选」
- 文档 §4.1 明确支持非加密模式
- 代码 `src/saas/api/channel_config.py:55-60` 将 `encrypt_key` 列入 `_REQUIRED_FIELDS["feishu"]`

**现象**：`create_channel` 在 line 88 强制校验所有 `_REQUIRED_FIELDS` 字段非空，因此用户在管理后台**无法保存非加密模式的飞书配置**——非加密模式仅在 `configs/config.yaml` 单租户模式下可用。

**修复方向**：

- 若坚持生产环境必须加密：更新文档 §3.1 标记 `encrypt_key` 为必填，并删除 §4.1 非加密模式章节；
- 若保留非加密模式：从 `_REQUIRED_FIELDS["feishu"]` 中移除 `encrypt_key`，由前端按需提示。

## 二、高危问题（安全 / 一致性）

### 2.1 签名校验使用 `==` 而非 `hmac.compare_digest`，存在时序攻击风险

**位置**：`src/channels/feishu/crypto.py:207`

```python
return calculated == signature
```

**修复**：

```python
import hmac
return hmac.compare_digest(calculated, signature)
```

### 2.2 签名校验未做时间戳偏移检查，存在重放窗口

**位置**：`src/channels/feishu/crypto.py:187-207`、`adapter.py:628-653`

**现象**：文档 §7.2 提到「飞书允许 ±1 小时」时间戳偏差，但代码未做任何时间戳校验。攻击者截获一次合法请求后，可在 1 小时内无限重放（仅靠 `event_id` 去重兜底，TTL 仅 5 分钟，超出后即可重放）。

**修复**：在 `verify_signature` 入口加 `abs(time.time() - int(timestamp)) > 3600` 校验。

### 2.3 GET 回调端点完全跳过验证，可被任意触发

**位置**：`src/saas/api/channel_routes.py:1276-1288`

```python
@router.get("/t/{tenant_id}/feishu/callback")
async def tenant_feishu_callback_get(tenant_id: str, challenge: str = Query(None)):
    if challenge:
        return {"challenge": challenge}
    return {"status": "ok"}
```

**现象**：任何外部访问者传入 `?challenge=任意字符串` 都能拿到 `{"challenge": "..."}`。飞书 url_verification 是 POST 流程，此 GET 兜底无任何鉴权或 token 校验。虽然目前飞书实际不会走 GET，但代码暴露了一个"无验证回声"端点，违反文档 §8「签名校验必须启用」原则。

**修复**：

- 若飞书历史版本确有 GET 验证：复用 POST 流程的 token 校验逻辑；
- 若纯兼容兜底：删除该路由，或限制为只返回 `{"status": "ok"}` 不回显 challenge。

### 2.4 `MessageDeduplicator.is_duplicate` 吞掉 DB 异常并按"重复"处理

**位置**：`src/channels/idempotency.py:76-80`

```python
except Exception as e:
    conn.rollback()
    logger.error(f"去重检查异常: {e}")
    return True  # 视为重复
```

**现象**：数据库连接抖动 / 死锁 / 临时不可达时，**所有飞书事件被当作重复消息丢弃**，用户消息静默丢失，仅留下 error 日志。文档 §5.4 宣称去重生效，但未说明此降级语义。

**修复**：

- DB 异常时不应静默丢消息，应让请求失败（返回 500）让飞书重试；
- 或返回 `False` 放行（依赖下游业务幂等）；
- 至少在日志中区分"DB 异常降级"和"正常重复命中"，并加监控告警。

## 三、中危问题（功能 / 性能）

### 3.1 `asyncio.create_task` 未保留引用，任务可被 GC 回收

**位置**：`src/saas/api/channel_routes.py:1369-1371`

```python
asyncio.create_task(
    _process_tenant_feishu_background(tenant_id, data, subagent_type=subagent_type)
)
```

**现象**：Python 官方文档明确警告——未保留引用的 task 可能被垃圾回收器在任意时刻回收，导致后台处理"无声消失"。在高并发回调场景下尤其危险。

**修复**：维护一个 `set[asyncio.Task]`，任务完成后通过 `done_callback` 移除：

```python
_task_set: set = set()
task = asyncio.create_task(...)
_task_set.add(task)
task.add_done_callback(_task_set.discard)
```

### 3.2 速率限制器在多 worker 下完全失效

**位置**：`src/channels/feishu/adapter.py:121, 145-163`

**现象**：`self._rate_limiter: Dict[str, deque]` 是进程内字典，Gunicorn 多 worker 模式下各 worker 独立计数，用户实际可发送 `worker数 × 10` 条/分钟。叠加 1.1 的"每请求新建 adapter"，单 worker 内也失效。

**违反规范**：`backend_dev.md` 明确禁止用内存变量存储"跨请求共享"状态。

**修复**：迁移到 Redis 滑动窗口（`ZADD` + `ZREMRANGEBYSCORE` + `ZCARD`）。

### 3.3 飞书媒体文件存储路径违反租户隔离规范

**位置**：`src/channels/feishu/adapter.py:64, 103-106`、`src/channels/feishu/media.py:57`

```python
media_upload_dir: str = "./storage/uploads/feishu"
```

**现象**：违反 `CLAUDE.md` / `backend_dev.md` 「租户附件存储规范」——所有租户的飞书媒体文件都堆在 `storage/uploads/feishu/` 下，无 `tenant_id` 维度隔离。

**修复**：

1. `FeishuAdapter.__init__` 接受 `tenant_id` 参数；
2. `ChannelFactory.create_adapter` 从 `cfg` 中透传 `tenant_id`；
3. 媒体存储改为 `storage/tenants/{tenant_id}/conversation/{filename}`，使用 `src/core/storage.py:get_tenant_storage_path`。

### 3.4 `_send_with_retry` 在 token 过期时不重置重试计数

**位置**：`src/channels/feishu/adapter.py:521-577`

**现象**：当收到 `99991663`（token 过期）时调用 `_invalidate_token()` 后 `continue`，**消耗一次 retry 配额**。若用户连续收到该错误码（极少见但可能），3 次重试可能全部耗在 token 刷新上，最终返回 False。应单独用 `refreshed` 标志位，token 刷新后重置 retry 计数。

### 3.5 `_init_bot_open_id` 失败导致群聊消息全部静默丢弃

**位置**：`src/channels/feishu/adapter.py:234-265, 355-380`

**现象**：若 `/bot/v3/info` 调用失败（网络抖动 / 权限缺失），`_bot_open_id` 永远为 None，之后所有群聊消息都被 `_is_bot_mentioned` 判定为"未 @机器人"丢弃，仅留 warning 日志。文档 §7.3 提及"无日志 = 群聊未 @机器人"，但实际可能是 bot 信息获取失败的连锁反应。

**修复**：

- `_init_bot_open_id` 失败时缓存失败时间戳，避免每次请求都重试打接口；
- 或在解析 mentions 时直接信任飞书的 `mention.id.open_id`，不需要本地缓存 bot open_id（飞书 mentions 数组里已经标明哪个 key 是机器人）。

### 3.6 后台处理失败时错误提示发送路径与原会话不一致

**位置**：`src/saas/api/channel_routes.py:1259-1271`

**现象**：异常分支用 `event_data.get("event", {}).get("sender", {}).get("sender_id", {}).get("open_id", "")` 作为兜底 `user_id`。若 `event_data` 结构变化（如飞书调整字段），发送失败但用户无任何反馈。叠加 1.1 的新建 adapter，第一次错误提示也要重新刷 token。

### 3.7 `download_file` 的 `Content-Disposition` 解析过于简陋

**位置**：`src/channels/feishu/media.py:160-166`

```python
if "filename=" in disposition:
    final_name = disposition.split("filename=")[-1].strip('"')
```

**问题**：

- 不支持 RFC 5987 `filename*=UTF-8''xxx` 编码（飞书中文文件名常用此格式）；
- 不处理转义字符 / 多个 `filename=` 字段；
- 拼接 `os.path.join(save_dir, final_name)` 时若 `final_name` 含 `../` 存在路径穿越风险。

**修复**：用 `email.message.Message` 或正则严格解析，并对 `final_name` 做白名单清洗（仅保留字母数字汉字 `._-`）。

### 3.8 媒体下载未复用持久化 HTTP 连接池

**位置**：`src/channels/feishu/media.py:83, 141, 208, 266`

**现象**：每次 `download_image` / `download_file` / `upload_image` / `upload_file` 都 `async with httpx.AsyncClient()` 新建 client，与 adapter 的 `_get_client()` 持久化策略脱节。在多附件消息场景下连接开销翻倍。

**修复**：`FeishuMedia` 接受 `client_getter: Callable[[], Awaitable[httpx.AsyncClient]]`，复用 adapter 的连接池。

## 四、低危问题（健壮性 / 代码质量）

### 4.1 `decrypt` 中 JSON 解析无独立异常处理

**位置**：`src/channels/feishu/crypto.py:111`

`json.loads(json_bytes.decode("utf-8"))` 失败时抛 `JSONDecodeError`，外层 `parse_message` 捕获后返回 None，但日志只显示"飞书消息解密失败: ..."，未区分"密文损坏"和"内部 JSON 格式异常"，排障困难。

### 4.2 `parse_message` 对 `create_time` 强制 int 转换，非数字会抛异常

**位置**：`src/channels/feishu/adapter.py:325, 351`

```python
create_time_str = message.get("create_time", str(int(time.time() * 1000)))
timestamp=datetime.fromtimestamp(int(create_time_str) / 1000),
```

飞书 `create_time` 是字符串型毫秒时间戳。若飞书未来返回空字符串或非数字，`int()` 抛 `ValueError` 导致整条消息处理失败。应包 try/except 兜底用当前时间。

### 4.3 `parse_message` 中冗余的解密分支

**位置**：`src/channels/feishu/adapter.py:278-284`

路由层 `tenant_feishu_callback_post` 已在 line 1334 完成解密，传入 `_process_tenant_feishu_background` 的 `event_data` 已无 `encrypt` 字段。`parse_message` 第 279 行的 `if self.crypto and raw_message.get("encrypt")` 在多租户路径下永远是死代码。建议明确：路由层负责解密 + 验签，`parse_message` 只处理明文 dict。

### 4.4 `build_post("", paragraphs)` 空标题

**位置**：`src/channels/feishu/adapter.py:470`

飞书 post 消息的 `title` 为空字符串时，部分客户端会渲染空标题行。建议 `title` 为空时不写入 `title` 字段，或直接用 text 类型兜底。

### 4.5 `_clean_mentions` 在 mention 无 name 时直接删除占位符

**位置**：`src/channels/feishu/adapter.py:393-400`

```python
result = result.replace(key, f"@{name}" if name else "")
```

当用户名获取失败时，`@_user_1 你好` 变成 ` 你好`（前导空格），影响智能体理解。建议 fallback 用 `@用户` 而非空字符串。

### 4.6 日志前缀不统一

`adapter.py` 中混用 `[Feishu]`（如 line 174、553）和无前缀（line 160、549）。channel_routes.py 用 `[Tenant Feishu]`。建议统一为 `[Feishu]`，便于日志检索。

### 4.7 `crypto.encrypt` 用 `os.urandom(16)` 作为 IV，但解密路径假设 IV 来自密文前缀

**位置**：`src/channels/feishu/crypto.py:145, 152-153`

加密时把 IV 拼在密文前再 Base64，解密时取前 16 字节作 IV——逻辑自洽，但 `encrypt` 方法主要供测试用，目前无生产调用方。建议在文档/方法注释中明确「此方法仅用于测试，生产链路只走解密」。

### 4.8 `MessageDeduplicator` 表名是 WeCom 时代命名，注释也写 "WeCom"

**位置**：`src/channels/idempotency.py:5, 21-23`

模块文档和类 docstring 都标注为 "WeCom 回调重试"，实际已被 wecom / wecom_kf / dingtalk / feishu 共用。建议改为通用描述。

### 4.9 文档 §6 处理架构图与代码顺序不完全一致

文档第 [4] 步「event_id 去重」发生在 [3] 「url_verification 快速返回」之后；代码中 url_verification 在 line 1340 提前返回，event_id 去重在 line 1354 才执行——顺序一致，但文档 [5]「仅处理 im.message.receive_v1」实际位于代码 line 1363，在去重之后。建议在文档中补充说明「先去重再过滤事件类型」的设计原因（避免 url_verification 之外的无关事件也写去重表）。

### 4.10 `_feishu_event_dedup: dict[str, MessageDeduplicator]` 全局字典无清理

**位置**：`src/saas/api/channel_routes.py:1098-1105`

每来一个新 tenant_id 就创建一个 `MessageDeduplicator` 实例（每个实例都会 `_ensure_table`，幂等但仍有 DB 开销），字典永远不清理。租户数多时会持续增长。建议改为模块级单例 + 在 `is_duplicate` 时把 tenant_id 拼到 message_id 前缀中。

## 五、文档与代码不一致汇总

| 文档位置 | 文档描述 | 代码实际 | 处理建议 |
|---------|---------|---------|---------|
| §1 已实现能力 | "tenant_access_token 并发刷新锁 ✅" | SaaS 模式下每请求新建 adapter，锁失去意义 | 修复 1.1 后保留表述 |
| §1 已实现能力 | "每 user 滑动窗口速率限制 ✅" | 多 worker + 每请求新建 adapter，实质失效 | 修复 1.1 + 3.2 后保留表述 |
| §1 已实现能力 | "HTTP 连接池 ✅" | 连接池创建后立即随 adapter 丢弃 | 修复 1.1 + 1.2 |
| §3.1 配置表 | `encrypt_key` 标"可选" | `_REQUIRED_FIELDS` 强制必填 | 修文档或改代码 |
| §4.1 非加密模式 | "仅校验 verification_token" | SaaS 模式下根本无法保存无 encrypt_key 的配置 | 修代码 1.3 |
| §6 处理架构 | 流程顺序清晰 | 实际 url_verification 早于 event_id 去重 | 文档补充说明 |
| §8 第 4 条 | "速率限制：每用户每窗口 10 条" | 实质失效 | 修复 3.2 |
| §8 第 5 条 | "基于 PostgreSQL 的分布式去重，多 worker 部署安全" | 去重本身正确，但 `is_duplicate` 异常降级语义未文档化 | 修复 2.4 + 补文档 |

## 六、修复优先级建议

| 优先级 | 问题编号 | 说明 |
|--------|---------|------|
| P0 | 1.1, 1.2 | adapter 复用 + httpx client 关闭，否则长跑必崩 |
| P0 | 1.3 | 文档与代码二选一对齐，否则用户配置无法保存 |
| P1 | 2.1, 2.2, 2.3 | 签名安全 + GET 端点鉴权 |
| P1 | 2.4 | DB 异常降级语义，影响消息可靠性 |
| P1 | 3.1 | asyncio task 引用持有 |
| P2 | 3.2, 3.3 | 速率限制迁移 Redis + 媒体存储租户隔离 |
| P2 | 3.4, 3.5, 3.6 | 重试逻辑 + bot open_id 容错 + 错误兜底 |
| P2 | 3.7, 3.8 | 文件名解析 + 连接池复用 |
| P3 | 4.x | 代码质量与文档微调 |

## 七、建议的后续动作

1. **立即修复 P0 问题**：adapter 缓存 + close 调用 + encrypt_key 字段一致性。这三项决定了飞书渠道在生产环境是否可上线。
2. **P1 安全加固**：统一替换为 `hmac.compare_digest`，加时间戳校验，GET 路由加鉴权或删除，DB 异常路径改为返回 500 触发飞书重试。
3. **P2 性能与隔离**：速率限制迁移 Redis，媒体存储按 `storage/tenants/{tenant_id}/conversation/` 规范化。
4. **补充测试**：

   - `tests/unit/tools/test_feishu_crypto.py`：加时序攻击场景、时间戳偏移场景、PKCS7 边界 case；
   - `tests/integration/test_feishu_callback.py`：模拟 url_verification 加密/非加密两条路径、重复 event_id 路径、签名失败路径、DB 异常路径；
   - `tests/integration/test_feishu_adapter_lifecycle.py`：验证 adapter 缓存命中与 close 行为。
5. **文档同步**：修复完成后更新 `integration_guide.md` 中"已实现能力"表，对每一项能力标注实际生效路径（单租户 / 多租户 / 多 worker）。
