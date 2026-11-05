# Runner Worker 看门狗与队列堆积告警设计（供决策）

> 状态：**待决策**（用户要求先出方案再考虑实现，2026-11-05）
> 背景：2026-11-05 TPG80509 生产事故——excel_process 读取 used range 膨胀的 Excel（声明 ~104.8 万行）在 async handler 里同步逐格遍历，冻结 aid-runner-worker 事件循环 50 分钟，所有智能体无响应，唯一破局是重启容器。
> P0 修复（已实施）：excel handler 全量 to_thread 包裹 + 双 ExcelReader 底层归一到 `src/tools/file/excel_reader.py` + used range 膨胀防御（流式探测真实数据边界）。

## 1. 问题本质：现有防御体系建立在"事件循环不被阻塞"的隐含前提上

worker 现有四层防御（`src/services/agent_runner/worker.py`）：

| 防御 | 机制 | 被事件循环冻结击穿的原因 |
|------|------|------|
| 租约 lease 30s | 心跳续约，过期可被 reap | 心跳协程 `_heartbeat` 与任务同在一个事件循环，循环冻结则心跳停发 |
| 心跳 heartbeat 10s | `heartbeat_seconds×2 ≤ lease_seconds` 校验 | 同上 |
| `reap_expired` | 过期 running → interrupted 可重认领 | 主循环 `run()` 里同步执行，循环冻结则 reap 也不跑 |
| concurrency 4 | 并发上限 | 冻结的任务占用 1 个槽位，其他 3 个任务依赖同一冻结的循环 |

**结论**：一个违反假异步规范的工具调用（`async def` 里直接调重 CPU 同步函数）即可击穿全部防线，租约过期后其他 worker 可以接走任务（多 worker 部署时可缓解），但单 worker 部署下队列整体停摆，进程自身无任何自愈手段。

P0 修复消灭了本次的具体触发点，但**结构性风险仍在**：未来任何新增工具/技能里的假异步调用都会复发。看门狗是最后一道防线——不依赖被监控对象自身的正确性。

## 2. 方案一：事件循环阻塞看门狗（进程内 watchdog 线程）

### 2.1 原理

看门狗是**独立 OS 线程**，不受事件循环阻塞影响。它周期性向事件循环投递一个回调（`loop.call_soon_threadsafe`）并等待执行：

- 正常：回调在毫秒级内执行，`Event.set()` 被触发。
- 循环被同步代码冻结：回调排队但无法执行，等待超时 → 判定阻塞。

这是业界标准做法（等价于 asyncio debug 模式的 `loop.slow_callback_duration` 检测，但 watchdog 线程在生产可用且能触发动作）。

### 2.2 实现草案

```python
# src/services/agent_runner/watchdog.py（草案，未实现）
import os
import threading
import time
from loguru import logger


class LoopWatchdog(threading.Thread):
    def __init__(self, loop, warn_seconds=5.0, critical_seconds=30.0, on_critical=None):
        super().__init__(daemon=True, name="runner-loop-watchdog")
        self.loop = loop
        self.warn_seconds = warn_seconds          # 阻塞预警线（日志 + tlog）
        self.critical_seconds = critical_seconds  # 阻塞处置线（建议 = lease_seconds）
        self.on_critical = on_critical            # 处置回调（见 2.3 决策点）

    def run(self):
        consecutive_critical = 0
        while True:
            fired = threading.Event()
            start = time.monotonic()
            self.loop.call_soon_threadsafe(fired.set)
            completed = fired.wait(timeout=self.critical_seconds)
            elapsed = time.monotonic() - start
            if not completed:
                consecutive_critical += 1
                if consecutive_critical >= 2:
                    self.on_critical(consecutive_critical)  # 栈转储 + 退出/告警
                else:
                    logger.warning(f"事件循环阻塞 {elapsed:.0f}s（第 {consecutive_critical} 次），继续观察")
            else:
                if elapsed > self.warn_seconds:
                    logger.warning(f"事件循环出现 {elapsed:.1f}s 阻塞，疑似假异步调用，已恢复")
                consecutive_critical = 0
            time.sleep(5)
```

接入点：worker 启动时 `LoopWatchdog(asyncio.get_running_loop()).start()`，仅 runner-worker 进程启用（runner-api / 主 API 无任务循环，不需要）。

### 2.3 关键决策点：检测到 critical 阻塞后的动作（需用户裁决）

| 选项 | 行为 | 优点 | 缺点 |
|------|------|------|------|
| a) 仅告警 | 飞书通知 + 日志，人工重启 | 最安全，零误伤 | 夜间/无人时段任务持续冻结，回到本次事故的恢复速度 |
| b) 告警 + 自愈退出 | 连续 2 次超限 → `os._exit(1)`，容器自动重启拉起 | 唯一能自动破局的手段；冻结期间任务本就无进度可保，`reap_expired` 会让过期任务转 interrupted 重新入队 | 依赖容器 restart policy（需确认生产 `restart: unless-stopped`）；系统级异常（CPU 饥饿、宿主机卡顿）可能误判 → 需连续 2 次确认 + 重启频率熔断 |
| c) b + 栈转储 | 退出前 `faulthandler.dump_traceback()` 落 `log/temp/` | 事后可直接定位是哪个函数阻塞 | 多几行代码，无额外风险 |

**推荐 c（b+c 组合）**：critical 判定条件用「连续 2 次 × critical_seconds」避免单次抖动误杀；退出前把全线程栈写入 `log/temp/事件循环阻塞.log`，重启后飞书通知附栈文件路径。

### 2.4 补充：faulthandler 任务级 wall-clock 兜底（可选，与看门狗互补）

看门狗只检测"事件循环被阻塞"；若某任务是合法的纯 await 长任务（如超长 LLM 流式），看门狗不会触发——这是正确行为。但若要给**单任务**加 wall-clock 兜底，可用标准库：

```python
import faulthandler
# execute() 入口：
faulthandler.dump_traceback_later(limit_seconds, exit=True)
# execute() finally：
faulthandler.cancel_dump_traceback_later()
```

超过时限自动 dump 全线程栈并强制退出。**注意**：它不区分"阻塞循环"与"合法长任务"，时限需放宽（如 30 分钟），且 `exit=True` 同样依赖容器重启拉起。是否需要取决于对"单任务最长合法时长"的判断，可后置。

## 3. 方案二：队列堆积监控 + 告警

### 3.1 指标

从 `agent_runners` 表（任务队列）取：

- `status='queued'` 数量；
- 最旧 queued 任务的排队时长（`created_at` 距今）；
- `status='running'` 数量；
- running 任务的 `lease_expires_at` 是否已过期（= 心跳停滞 = 极可能循环冻结）。

### 3.2 实现位置三选一（需用户裁决）

| 选项 | 机制 | 优点 | 缺点 |
|------|------|------|------|
| a) worker 主循环内 | `run()` 每轮已跑 `reap_expired`/`settle_ready`，顺带统计水位并告警 | 零新增组件 | **worker 冻结时告警源也冻结**——不能依赖它报告自己的死亡，只能报"堆积"，报不了"冻结" |
| b) 独立巡检脚本（server-patrol / cron） | 每分钟查 DB，最旧 queued > 阈值 → 飞书告警 | 与 worker 生命周期完全解耦，冻结时也能发现；server-patrol 已覆盖 6 台服务器，复用巡检通道 | 跨进程组件，需维护 |
| c) 主 API 进程内 APScheduler | 与 tmp 清理任务同模式（应用内定时器） | 无跨进程部署成本 | 主 API 与 worker 同机部署时共享宿主机故障面；但循环是独立的，worker 冻结不影响它运行 |

**推荐 c 为主、b 为辅**（若 server-patrol 巡检计划内已有 DB 巡检项，加一条 SQL 即可，成本极低）。关键点：**冻结检测不能依赖 worker 自身**，所以 a 不能单独作为方案。

### 3.3 阈值建议

- 最旧 queued 排队 > 5 分钟 → warning（正常并发下队列秒级消化）
- 最旧 queued 排队 > 15 分钟 → critical（基本可断定 worker 停摆）
- running 中存在 `lease_expires_at` 已过期超过 1 分钟且未被 reaper 处理 → critical（reaper 失效，即主循环冻结的直接证据）

## 4. 方案三：飞书机器人通知通道

### 4.1 复用现成能力评估

`src/notifications/im_webhook.py` 已有 `FeishuBotAdapter`，但与租户通知框架耦合：`render()` 依赖租户事件渲染（`events.render_message_text`）、目标校验走 `web_push` 的 pinned HTTPS 域名白名单（`provider_hosts`）、SendResult 规范化含重试语义。运维告警是**系统级、无租户归属、低频**的通道，硬套租户通知框架会把事件契约、白名单维护一起牵进来。

### 4.2 推荐方案：独立轻量模块

```python
# src/core/ops_alert.py（草案，未实现，约 40 行）
import httpx
from loguru import logger
from src.config.settings import settings


def send_ops_alert(title: str, detail: str = "") -> bool:
    """运维告警 → 飞书自定义机器人。告警通道自身绝不抛错影响主流程。"""
    url = settings.ops.feishu_webhook_url
    if not url:
        return False
    text = f"【运维告警】{title}"
    if detail:
        text += f"\n{detail}"
    try:
        resp = httpx.post(url, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"运维告警发送失败（不影响主流程）: {e}")
        return False
```

- 配置：`OPS_FEISHU_WEBHOOK_URL` 环境变量（.env，**不进 config.yaml 明文提交**），未配置时静默关闭（本地开发零打扰）。
- 飞书侧安全设置：使用「自定义关键词」`运维告警`，不启用签名校验（内网运维通道，低风险）。
- 出站评估：webhook URL 来自运维 .env 而非租户输入，目标固定 open.feishu.cn，无 SSRF 面；不引入租户通知的 pinned HTTPS 白名单机制（那是为防租户注入外发目标设计的，此处威胁模型不适用）。
- 消费方：看门狗 critical 告警、队列堆积告警；后续 log_error 巡检、外部推送失败等运维告警也可收敛到此通道（另行评估，不在本期）。

### 4.3 备选：复用 ImWebhookAdapterBase

仅当希望告警与租户通知走同一发送链路（统一限流/审计）时选它；需给 `provider_hosts` 加 `open.feishu.cn` 并伪造一个无租户事件渲染。改造点反而更多，不推荐首期采用。

## 5. 落地组合建议（待裁决）

| 项 | 建议 | 优先级 |
|----|------|--------|
| 看门狗线程 | warn 5s 记日志 / 连续 2×30s → 栈转储 + `os._exit(1)`（选项 c） | P1，防复发的核心 |
| 队列水位巡检 | 主 API APScheduler 每分钟查水位（选项 c），阈值见 §3.3 | P1，与看门狗互补（覆盖"worker 整体死亡"之外的场景，如 runner-api 不可达） |
| 飞书通知 | 独立 `src/core/ops_alert.py` + `OPS_FEISHU_WEBHOOK_URL` | P1，前两项的输出通道 |
| faulthandler 任务级兜底 | 后置，等出现"合法长任务超时"的实际案例再定 | P2 |
| 重启频率熔断 | watchdog 自愈连续触发（如 1 小时内 ≥3 次）→ 只告警不再自愈，防重启风暴 | P1，随看门狗一起做 |
| 多 worker 部署评估 | 生产目前单 worker（aid-runner-worker）；多 worker 可让冻结期间队列被其他 worker 消化，是部署层面的缓解 | P2，运维决策 |

## 6. 验证策略（实现后）

1. 单测：watchdog 用假 loop（受控 Event）模拟阻塞/恢复，断言 warn/critical 分支与熔断计数。
2. 单测：水位巡检用隔离 DB（`--isolated-db`）构造 queued/过期 running 行，断言告警触发与静默区间。
3. 仿真演练（agent1）：注入一个 `time.sleep(60)` 的假异步测试任务，验证看门狗 30s 告警、自愈重启、reap 后任务重新入队、飞书消息到达。
4. 飞书通道：仿真环境配置真实 webhook，验证关键词命中与超时降级。

## 关联

- 事故与 P0 修复：docs/incidents/ 与本次 excel 工具修复登记（docs/ideas.md）
- worker 心跳/租约现状：`src/services/agent_runner/worker.py`（`_heartbeat`、`run()` 主循环）
- 飞书机器人适配现状：`src/notifications/im_webhook.py`
