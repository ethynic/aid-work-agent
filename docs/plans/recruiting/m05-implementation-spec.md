# M0.5 实施规格：Agent 工具执行位置与招聘子智能体

> 关联：[plan-recruiting-cli-agent-integration.md](plan-recruiting-cli-agent-integration.md) M0.5；上位设计：[recruiting-cli-agent-integration-design.md](../../design/recruiting/recruiting-cli-agent-integration-design.md) §4、§11、§14。
>
> 本文件是 M0.5 开发的具体实施决策。

## 1. 已勘察的现有机制（可直接采信）

- `BaseTool`（src/tools/base.py）：类属性 + Pydantic InputModel → 自动 schema。
- 工具过滤：`_register_builtin_tools()` 注册全部工具，子智能体由 `_filter_tools_by_config()`（agent.py:525）按 `subagent_config.get_allowed_tools()` 过滤（inherit=false + allowed 列表）。**主智能体不过滤，看到全部注册工具**。
- 受信上下文注入：agent.py:2874 附近已有 `execution_args["_trusted_tenant_id"/"_trusted_user_id"]` 模式（目前仅 browser_automation 用）。
- SSE：agent 主循环是 generator，`yield make_event("tool_start"/"progress"/"tool_result")`；工具执行是单个 `await self.tool_executor.execute(...)`，**无 mid-tool 进度机制**（无事件队列）。
- 取消：`cancel_check` 闭包在每个工具执行前检查（agent.py:2567），工具执行中不检查。
- `src/local_tools/`（M0.3）：repository.create_invocation / get_invocation / list_events(since_seq) / request_cancel / claim 链路全部就绪。**CR 遗留：create_invocation 不校验 device 归属，本阶段 proxy 调用前必须自行校验**。
- catalog.py：TRUSTED_PROVIDERS 静态注册表。

## 2. ExecutionTarget 元数据

`src/tools/base.py` 增加：

```python
class ExecutionTarget(str, Enum):
    SERVER = "server"
    LOCAL_REQUIRED = "local_required"
    EITHER = "either"

class BaseTool(ABC):
    execution_target: ExecutionTarget = ExecutionTarget.SERVER   # 现有工具默认 SERVER，零改动
```

（Enum 定义放 base.py 顶部，不新建文件。）

## 3. LocalToolProxy（src/local_tools/proxy_tool.py）

一个 `LocalToolProxyTool(BaseTool)` 基类 + 7 个子类（每个一个文件不必要，全部放 proxy_tool.py）：

| 工具 | InputModel 关键字段 | 授权上限（设计 §14，硬校验） |
|---|---|---|
| `boss_filter` | experience?, educations?, salary?（至少一个） | — |
| `boss_clear_filter` | 无 | — |
| `boss_goto` | target: Literal["recommend","chat"] | — |
| `boss_greet` | limit: int = 1 | **le=3** |
| `boss_accept_resume` | limit: int = 1, preview: bool = True | **le=1** |
| `boss_reject_current` | 无 | 固定 1 |
| `boss_interview_demo` | remark?: str（max 140） | 非写动作 |

共同类属性：`execution_target = ExecutionTarget.LOCAL_REQUIRED`、`category = "local_boss"`、中文 display_name、中文 description（含副作用说明）。

### execute() 流程（async）

1. 从 kwargs 取 `_trusted_tenant_id` / `_trusted_user_id`（agent 注入，见 §5）；缺失 → success=false「无法确定用户身份」。
2. **设备闸门**：查 selected + status=active + `last_seen_at` 距今 ≤30s 的设备；capabilities_json 含 boss-recruiting provider。任一不满足 → success=false + `code="DEVICE_UNAVAILABLE"` + 中文引导文案（「请先在『本地工具』页面配对并启动本机 Runtime」），**不创建 invocation**。
3. `repository.create_invocation(...)` → 内部启动轮询循环（`asyncio.to_thread` 包同步 DB）：
   - 每 0.5s 读新 events（seq 游标）→ 推入 `_progress_queue`（agent 注入的 asyncio.Queue，见 §5）；
   - 每 0.5s 查 invocation state 到终态；
   - 超时（greet/accept 10 分钟，其余 3 分钟）→ request_cancel + 返回 success=false code=TIMEOUT（注：TIMEOUT 是 proxy 本地码，云端 invocation 状态机不受影响）；
4. 终态映射为工具结果：`{success, code, message, effect, data, invocation_id}`；**effect=unknown 时 message 明确附加「实际效果未知，禁止重试，请提示用户人工检查」**。
5. DESKTOP_NOT_INTERACTIVE / CHROME_UNAVAILABLE / NOT_LOGGED_IN / WRONG_PAGE / PAYWALL / UI_CHANGED / BUSY → message 直传 Runtime/CLI 的中文文案（已是用户可读），LLM 据此停止并向用户说明。

## 4. 注册与可见性

- `_register_builtin_tools()` **不注册** boss 工具。
- 新增 `_register_local_proxy_tools()`：仅当 `self.mode != MASTER` 且 `subagent_config.get_allowed_tools()` 与 7 个 boss 工具名有交集时注册（注册后由既有 `_filter_tools_by_config()` 按 allowed 过滤）。效果：主智能体和其他子智能体（inherit=true 或 allowed 无交集）永远看不到 boss 工具——满足设计 §11「主 Agent 和其他子智能体不获得 BOSS 工具」。
- 「设备可用才标记 available」（设计 §11）MVP 近似：**工具静态可见，execute() 内设备闸门返回明确引导**（满足 M0.5 测试「Runtime 离线时给出连接本机的明确引导」）。子智能体 tools.inherit=false 无 browser tool，绕过不可能。此近似记录于此，MVP 后评估动态过滤。

## 5. agent.py 集成（外科手术式）

工具执行段（agent.py:2870 附近）对 LOCAL_REQUIRED 工具走特殊分支（仿 browser_automation 特殊 args 模式）：

```python
tool = self.tool_registry.get_tool(tool_name)
if tool is not None and getattr(tool, "execution_target", None) == ExecutionTarget.LOCAL_REQUIRED:
    execution_args = dict(tool_args)
    execution_args["_trusted_tenant_id"] = _resolve_tenant_id
    execution_args["_trusted_user_id"] = user.user_id if user else None
    progress_queue: asyncio.Queue = asyncio.Queue()
    execution_args["_progress_queue"] = progress_queue
    task = asyncio.create_task(self.tool_executor.execute(tool_name, execution_args))
    while not task.done():
        try:
            evt = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
            yield make_event("progress", data=evt["text"])     # ⏳ 招呼进度 2/3 ...
        except asyncio.TimeoutError:
            pass
        if cancel_check and cancel_check():
            await asyncio.to_thread(repository.request_cancel, proxy_current_invocation_id, _resolve_tenant_id)
    result = task.result()
```

- `proxy_current_invocation_id`：proxy execute() 创建 invocation 后立即放入 queue 首条消息（`{"type":"started","invocation_id":...}`），agent 记录之；cancel_check 命中时 request_cancel。**不在循环里 break**——等 proxy 自身到终态（Runtime 协作式取消后写 cancelled/failed），保证 tool_call 有配对结果。
- 队列耗尽后 task 完成 → 走既有 tool_result 分支。
- `_filter` 等无关路径不动；SERVER 工具路径零变化。

## 6. 招聘子智能体

`subagents/recruiting-operator/SUBAGENT.md`（严格遵守 architecture.md 的格式规范：system_prompt 在 body，frontmatter 无 system_prompt，闭合 `---` 顶格）：

```yaml
---
name: 招聘操作智能体
description: 在用户本机已登录 BOSS 直聘的 Chrome 上执行招聘操作（筛选、打招呼、接收简历、标记不合适、约面试演示）
version: 1.0.0
author: system
capabilities: [boss_filter, boss_greet, boss_accept_resume, boss_reject, boss_interview]
triggers:
  keywords: [BOSS, boss, 直聘, 打招呼, 牛人, 招聘, 候选人, 接收简历, 不合适, 约面试]
tools:
  inherit: false
  allowed: [boss_filter, boss_clear_filter, boss_goto, boss_greet, boss_accept_resume, boss_reject_current, boss_interview_demo]
skills:
  allowed: []
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---
```

body 系统提示词要点：
1. 授权规则（设计 §14）：用户在当前对话明确说出动作和数量即本次授权；**greet 单次最多 3 人、accept 单次 1 份、reject 固定当前 1 人**；数量模糊必须澄清，授权不跨对话、不扩大。
2. 组合链路：筛选并打招呼 `boss_goto(recommend)→boss_filter→boss_greet`；接收简历 `boss_goto(chat)→boss_accept_resume`；拒绝 `boss_goto(chat)→boss_reject_current`；面试演示 `boss_goto(chat)→boss_interview_demo`。
3. 失败处理：DEVICE_UNAVAILABLE → 引导用户到「本地工具」页面配对/启动 Runtime；CHROME_UNAVAILABLE/NOT_LOGGED_IN/WRONG_PAGE/PAYWALL/UI_CHANGED/BUSY/DESKTOP_NOT_INTERACTIVE/unknown → **停止并向用户说明原因与人工操作引导，禁止换用其他工具重试**；effect=unknown 绝不自动重试。
4. 面试演示绝不发送（工具本身只填不发送）。

**URL 路由**：`/chat/recruiting-operator`（dir_name 匹配）。

**注意**：检查子智能体 DB 定义（subagent_definitions 表）与文件加载的关系——现有子智能体是从 subagents/ 目录加载还是 DB？先读 src/subagents/loader.py 与 registry.py 确认加载链路，若管理后台 DB 也需登记，在规格执行时同步（记录决策）。

## 7. 测试

`tests/unit/local_tools/test_proxy_tool.py`（mock repository）：
- 设备闸门各分支（无设备/未选/离线/未配对 provider）→ 明确引导，不建 invocation。
- 授权上限：greet limit=4 → Pydantic 校验拒绝。
- 终态映射：succeeded/failed/unknown/cancelled→工具结果；unknown 结果 message 含「禁止重试」。
- 事件→进度队列：events 按 seq 转成 progress 文本。

`tests/integration/test_local_tool_proxy_flow.py`（真实 DB + skip；直接驱动 proxy，不经 LLM）：
- 全链路：建设备（模拟 Runtime heartbeat 在线）→ proxy.execute(boss_goto) → fake Runtime 线程走 claim/started/progress/result → proxy 返回成功且 progress 队列收到事件。
- 取消：proxy 执行中 request_cancel → fake Runtime 感知 → 终态 cancelled → proxy 返回。
- 超时路径（缩短 timeout 注入）。

`tests/unit/test_agent_local_tool_registration.py`：
- master agent 注册表无 boss 工具；inherit=true 子智能体无 boss 工具；recruiting-operator 配置下 7 个工具齐全且只有这 7 个。
- SUBAGENT.md 加载：frontmatter 解析、system_prompt 取 body（防 architecture.md 记录的静默失败陷阱）。

## 8. 不做（本阶段）

- Web 前端（M0.6）。
- schema_digest 强校验（设备 manifest_digest 仅记录，不参与 available 判定）。
- WSS transport、动态 tool 可用性过滤。
