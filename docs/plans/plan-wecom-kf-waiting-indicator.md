# 微信客服（wecom_kf）处理超时等待提示功能计划

> 状态：🔧 部分完成（2026-08-20 开发+单测完成，待部署真机验证）
> 登记：[docs/ideas.md 渠道集成 #58](../ideas.md)

## Context

企业微信客服渠道（wecom_kf）在智能体处理微信侧用户消息时，如果 LLM 调用了多轮工具、耗时较长（>15s），用户会长时间收不到任何回复，不知道智能体是否在工作。

本改动：当处理单条微信用户消息超过 N 秒（默认 15s）仍未回复时，先给用户发一条提示语（默认"我正在处理您的问题，可能需要几分钟，请稍等下。"）。N 秒和提示语在租户渠道配置页（/t/:tenant_id/channels）的 wecom_kf 编辑弹窗中可配置。**已与用户确认：配置放渠道级**（`tenant_channel_configs.config` 顶层新增 `waiting_indicator` 字段，整条渠道所有客服账号统一生效）。

**关键约束**（探索确认）：`process_and_persist` 内部 `session_queue.enqueue_and_process` 在处理期间持有 Redis 锁（watchdog 续期），**禁止从外部用 `asyncio.wait_for` 直接取消**，否则 CancelledError 穿透导致锁/cancel 标志泄漏。必须用**非取消式 watchdog**（`create_task` + `asyncio.shield` + `wait_for`，超时只发提示、不取消任务）。

**现状**：`WeComKfAdapter.send_waiting_indicator()`（adapter.py:557）和各渠道 adapter 均定义了该方法但生产代码零调用点；`settings.py` 有 `WeComWaitingIndicatorConfig` 但未接线。本改动恰好补上 wecom_kf 这条通路。

## 改动文件

### 1. `src/channels/wecom_kf/prompts.py`（后端，常量）

在 `MSG_EXPIRED`/`MSG_CREDIT_EXHAUSTED` 之后追加：

```python
# ============ 处理超时等待提示 ============
DEFAULT_WAITING_INDICATOR_MESSAGE = "我正在处理您的问题，可能需要几分钟，请稍等下。"
DEFAULT_WAITING_INDICATOR_DELAY_SECONDS = 15.0
```

### 2. `src/channels/wecom_kf/adapter.py`（后端，接收配置）

`__init__` 中 `self._render_enabled = kwargs.get("render_tables", True)` 之后新增：

```python
# 渠道级等待提示配置（config 顶层 waiting_indicator dict，经 ChannelFactory **config 注入，缺省空 dict）
self.waiting_indicator: dict = kwargs.get("waiting_indicator") or {}
```

无需改 `send_waiting_indicator`（已存在，内部用 `self.current_open_kfid` 调 `send_text`）。

配置生效链路：前端 PUT 渠道配置 → `channel_config.py:172/204` 调 `ChannelFactory.invalidate_adapter` → adapter 缓存重建 → 新配置经 `adapter_class(**config)` 注入。无缓存延迟。

### 3. `src/saas/api/channel_routes.py`（后端，核心 watchdog）

模块级新增两个 helper（放在 `_process_tenant_wecom_kf_messages` 上方）：

```python
def _get_waiting_indicator_cfg(adapter) -> dict:
    """读取渠道级 waiting_indicator 配置，返回 {delay_seconds, message}；未启用返回 {}"""
    wi = getattr(adapter, "waiting_indicator", None) or {}
    if not wi.get("enabled", True):      # 未配置默认启用（开箱即用）
        return {}
    try:
        delay = float(wi.get("delay_seconds", DEFAULT_WAITING_INDICATOR_DELAY_SECONDS))
    except (TypeError, ValueError):
        delay = DEFAULT_WAITING_INDICATOR_DELAY_SECONDS
    if delay <= 0:
        return {}
    message = str(wi.get("message") or "").strip() or DEFAULT_WAITING_INDICATOR_MESSAGE
    return {"delay_seconds": delay, "message": message}


async def _process_with_waiting_indicator(adapter, ext_userid, cfg, coro):
    """非取消式超时 watchdog：超时先发提示语，任务继续跑，最后返回其结果。
    绝不能取消 coro —— session_queue 处理器持有 Redis 锁，取消会泄漏锁/cancel 标志。"""
    task = asyncio.create_task(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=cfg["delay_seconds"])
    except asyncio.TimeoutError:
        try:
            await adapter.send_waiting_indicator(ext_userid, cfg["message"])
            _kf_tlog("等待提示已发送: user={user}", user=ext_userid)
        except Exception as e:
            logger.warning(f"[wecom_kf] 等待提示发送失败: {e}")
        return await task   # 不取消 task；等其自然完成返回真实结果
```

调用处改动（`process_and_persist` 的 try 内；此时 `adapter` 已是 auto_fill 分支重建后的最终实例，`adapter.current_open_kfid` 已设置）：

```python
try:
    # 处理超时等待提示：仅对真正走智能体的消息启用（merged 场景 process 秒回不触发）
    waiting_cfg = _get_waiting_indicator_cfg(adapter)
    call = channel_session_manager.process_and_persist(...)   # 原参数不变
    if waiting_cfg:
        result = await _process_with_waiting_indicator(
            adapter, unified_msg.user_id, waiting_cfg, call
        )
    else:
        result = await call
    ...
```

发送目标用 `unified_msg.user_id`（企微 external_userid，与 `send_response` 闭包一致）。

### 4. `frontend/web/components/saas/ChannelConfig.vue`（前端，配置表单）

**模板**：wecom_kf 客服账号管理区块之后、回调地址提示之前，新增"等待提示"区块（复用 `bg-canvas rounded-lg p-3` 卡片样式 + `BaseInput` + 原生 checkbox）：

```html
<div v-if="form.channel_type === 'wecom_kf'" class="mt-3 pt-3 border-t border-default">
  <div class="bg-canvas rounded-lg p-3 space-y-3">
    <label class="flex items-center gap-2 text-sm text-default cursor-pointer">
      <input type="checkbox" v-model="wi.enabled" class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
      <span class="font-medium">处理超时等待提示</span>
      <span class="text-xs text-muted">智能体处理超过 N 秒未回复时，先发送提示语</span>
    </label>
    <div v-if="wi.enabled" class="grid grid-cols-1 md:grid-cols-2 gap-3 pl-6">
      <div>
        <label class="text-sm text-muted mb-1 block">超时秒数</label>
        <BaseInput v-model="wi.delay_seconds" type="number" min="1" placeholder="15" />
      </div>
      <div>
        <label class="text-sm text-muted mb-1 block">提示语</label>
        <BaseInput v-model="wi.message" placeholder="我正在处理您的问题，可能需要几分钟，请稍等下。" />
      </div>
    </div>
  </div>
</div>
```

**script**：
- 新增 `wi = reactive({ enabled: true, delay_seconds: '15', message: '' })`（delay_seconds 用 string 存储，BaseInput modelValue 为 string，提交时转 number）
- `editChannel` 初始化：浅拷贝 `ch.config?.waiting_indicator`，`delay_seconds` 转 number，message 空回退默认文案
- `openAddChannel` 重置 `wi` 为默认
- `handleSubmit` wecom_kf 时写回 `payload.config.waiting_indicator`：
  - 启用：`{ enabled: true, delay_seconds: wi.delay_seconds, message: wi.message }`
  - 关闭：`{ enabled: false }`

### 5. 测试 `tests/unit/channels/test_wecom_kf_waiting_indicator.py`

- `WeComKfAdapter(**{..., "waiting_indicator": {...}})` 后 `self.waiting_indicator` 正确赋值；不传时默认 `{}`
- `_get_waiting_indicator_cfg`：空 `{}`/`enabled=false`/`delay_seconds<=0` → 返回 `{}`；字段缺省回退常量；`delay_seconds="abc"` 容错回退默认
- `_process_with_waiting_indicator`（mock `send_waiting_indicator`）：①coro 快速完成 → 不发提示、返回原结果；②coro 挂起超时 → 提示恰好发 1 次、coro 未被取消且最终返回结果；③提示语发送抛异常 → 不向上抛、主结果仍返回

### 6. `docs/ideas.md`（文档登记）

渠道接入分区新增 #58 条目，状态 `🔧 部分完成`，说明：渠道级 `config.waiting_indicator`（enabled/delay_seconds/message）、默认 15s + 固定话术、非取消式 watchdog、前端 /t/:tenant_id/channels 可配置、merged 场景不触发。

## 验证

1. 后端测试：`./scripts/dev_test.sh tests/unit/channels/test_wecom_kf_waiting_indicator.py -p no:cacheprovider -q` 全绿（容器环境 `docker ps | grep aid-agent-api` 探测）
2. 语法/import 检查：`docker exec aid-agent-api python -c "from src.saas.api.channel_routes import _get_waiting_indicator_cfg"`（宿主机环境去前缀）
3. 前端：`cd frontend && npm run build` 0 错误
4. 配置读写链路核对：前端保存 → `config.waiting_indicator` 落 `tenant_channel_configs.config` → `invalidate_adapter` → 新 adapter 经 `**config` 注入 `self.waiting_indicator`

## 备注

- 未配置时默认启用（`enabled` 缺省 True）：存量 wecom_kf 渠道开箱即得提示，属 UX 改进，可接受
- 微信客服单次咨询 5 条回复限制：提示语占用 1 条回复配额，属需求明确要求的 trade-off（长图化已承接大部分配额风险）
- 超时后 fire-and-forget 任务协程会继续占用到 agent 跑完，不阻塞其他消息，可接受
