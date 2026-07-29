# 浏览器自动化工具重构设计文档

> 创建日期: 2026-04-30 | 状态: 已实现

## 1. 问题背景

当前浏览器工具由 12 个子工具组成（browser_open、browser_snapshot、browser_click 等），LLM 需要手动编排 `open → snapshot → 分析 → click → snapshot → 分析...` 循环。

**核心问题**：
- 一次简单操作消耗 4-6 轮 LLM 对话（每轮 = LLM 推理 + 工具调用）
- LLM 需要"学会"复杂的浏览器操作流程，usage_guide 约 40 行才说清楚
- 编排逻辑在 LLM 侧，不可控、不稳定

**目标**：用一个 `browser_automation` 工具完全替换 12 个子工具。参数只有任务描述，工具内部通过 LLM 自主完成所有浏览器操作。

## 2. 核心接口

```python
class BrowserAutomationInput(BaseModel):
    task: str = Field(..., description=(
        "要在浏览器中完成的任务描述。"
        "如：「打开 example.com 并登录，用户名 admin 密码 123456」"
        "「访问淘宝搜索"机械键盘"并按销量排序，提取前10个商品名称和价格」"
    ))
```

一个参数，一切尽在其中。

## 3. 内部架构

```
browser_automation(task="登录xxx网站")
    │
    ▼
BrowserOrchestrator（编排器）
    │
    ├─ Phase 1: 解析任务 → LLM 生成操作计划
    │   llm_gateway.chat("分析任务，生成浏览器操作步骤列表")
    │
    ├─ Phase 2: 循环执行
    │   while 未完成 and 步骤数 < max_steps:
    │     ├─ browser_session.open(url) / page 操作
    │     ├─ SemanticSnapshotGenerator.generate(page)
    │     ├─ 将当前页面状态 + 剩余步骤 → LLM
    │     ├─ LLM 返回下一步操作 (click/fill/select/type)
    │     ├─ NaturalMatcher 匹配元素
    │     ├─ 执行操作
    │     └─ wait_for_page_stable()
    │
    └─ Phase 3: 返回结果
        返回 { success, result, steps_taken, screenshots }
```

## 4. 文件结构

```
src/tools/browser/
├── __init__.py                    # 导出 BrowserAutomationTool
├── automation_tool.py             # 新：BrowserAutomationTool（入口）
├── orchestrator.py                # 新：BrowserOrchestrator（编排逻辑）
├── session.py                     # 新：BrowserSession（从 browser_tool.py 提取）
├── page_ops.py                    # 新：PageOps（页面操作：click/fill/select）
│
├── browser_tool.py                # 保留（兼容旧代码，BrowserSession 已迁移到 session.py）
├── tools_semantic.py              # 保留（旧工具兼容，操作逻辑已在 page_ops.py 重新实现）
├── tools_snapshot.py              # 保留（旧工具兼容）
├── tools_find.py                  # 保留（旧工具兼容）
├── tools_path.py                  # 保留（旧工具兼容）
│
└── semantic/                      # 保留不变
    ├── snapshot_generator.py
    ├── natural_matcher.py
    ├── element_classifier.py
    ├── semantic_tagger.py
    ├── ref_mapper.py
    ├── submenu_detector.py
    └── iframe_handler.py
```

## 5. 关键实现

### 5.1 Orchestrator 编排逻辑

```python
class BrowserOrchestrator:
    def __init__(self, session: BrowserSession, llm=None):
        self.session = session
        self.llm = llm or llm_gateway
        self.page_ops = PageOps(session)
        self.max_steps = 20

    async def execute(self, task: str) -> dict:
        # 1. LLM 解析任务，确定初始 URL
        # 2. 循环：snapshot → LLM 决策 → 执行操作
        # 3. 返回结果
```

**LLM 决策 Prompt（每轮）**：

```
你是一个浏览器自动化助手。当前页面状态：
URL: {url}
标题: {title}
可操作元素: {interactive_elements}

用户任务: {task}
已执行步骤: {completed_steps}

请决定下一步操作，输出 JSON：
{
  "action": "click" | "fill" | "select" | "navigate" | "done" | "request_human",
  "target": "元素描述或URL",
  "value": "填写值（fill/select时）",
  "reason": "为什么这样做",
  "reason_code": "request_human 时必填的白名单原因",
  "instruction_code": "request_human 时必填的服务端指引模板",
  "completion_mode": "auto_or_confirm | confirm_only"
}
```

- `action=done`：任务完成，停止循环
- `action=request_human`：进入 v2.5 `HumanAssistanceRequest + ToolSuspension`，不是普通工具返回；完成条件必须由服务端白名单验证

### 5.2 PageOps 页面操作

从 tools_semantic.py 提取核心操作逻辑，作为类方法而非独立工具：

```python
class PageOps:
    async def click(self, description: str) -> dict
    async def fill(self, field: str, value: str) -> dict
    async def select(self, field: str, option: str) -> dict
    async def navigate(self, url: str) -> dict
    async def get_content(self) -> str
    async def take_snapshot(self) -> SemanticSnapshot
```

每个方法：获取 ref_mapper → NaturalMatcher 匹配 → Playwright 操作 → wait_for_page_stable → 返回结果

### 5.3 BrowserSession

从 browser_tool.py 提取，逻辑不变，独立文件管理。

## 6. 返回结果格式

```json
{
    "success": true,
    "task": "登录xxx网站",
    "result": "已完成登录，当前页面：xxx仪表盘",
    "steps_taken": 5,
    "steps": [
        {"action": "open", "target": "https://xxx.com", "result": "页面加载完成"},
        {"action": "fill", "target": "用户名", "value": "admin", "result": "填写成功"},
        {"action": "fill", "target": "密码", "value": "***", "result": "填写成功"},
        {"action": "click", "target": "登录按钮", "result": "点击成功，页面跳转"},
        {"action": "done", "result": "登录成功"}
    ],
    "final_url": "https://xxx.com/dashboard",
    "message": "任务完成：已成功登录xxx网站"
}
```

## 7. 安全与限制

| 限制 | 值 | 说明 |
|------|------|------|
| max_steps | 20 | 防止无限循环 |
| 总超时 | 5 分钟 | 单次任务上限 |
| 密码脱敏 | 返回结果 | 密码字段显示为 *** |
| LLM 调用 | ~200-500 tokens/步 | 20 步约 10000 tokens |

## 8. Agent 集成变更

**agent.py 变更**：
- 删除 12 个浏览器工具注册（约 40 行）→ 替换为 1 个 `BrowserAutomationTool()`
- 删除 browser 相关的 progress 回调、display name 特殊处理
- 不再需要 browser 的 usage_guide

## 9. 实施步骤

1. 创建 `session.py`：从 browser_tool.py 提取 BrowserSession
2. 创建 `page_ops.py`：从 tools_semantic.py 提取操作逻辑
3. 创建 `orchestrator.py`：实现 LLM 驱动的编排循环
4. 创建 `automation_tool.py`：实现 BrowserAutomationTool 入口
5. 更新 `__init__.py`：导出新工具
6. 更新 `agent.py`：替换工具注册
7. 删除旧文件
8. 更新设计文档和测试

## 10. 已确认的设计决策

> 2026-07-20：本节原有会话与 `ask_user` 设计已由 [浏览器执行架构、可视化与人工接管设计 v2.7](./browser_visualization_design.md) 扩展并约束。新的强制规则是：服务端默认 headless；完成/失败/取消/超时/shutdown 必须关闭；人工等待仅能在 5 分钟租约内保留；原工具调用必须持久化 suspend/resume，用户完成后由页面条件或“完成并继续”事件自动唤醒，不依赖用户再次发消息或 LLM 重调工具；Agent 只公开完整任务边界；桌面执行只作为 Agent Desktop 内置可选 browser runtime，复用主应用技术栈、认证、installation identity、签名安装包、更新和生命周期，不形成第二个客户端；服务端模式免安装，Agent Web 跨应用拉起复用 `aidagent://browser-launch` 一次性 ticket；`auto` 必须先执行服务端 headless，只有确定的本地能力预检失败或 HeadlessFailureDetector 高置信失败才能升级 desktop runtime；实时视图、人工接管、多租户和多 worker 以 v2.7 为准。如本文件冲突，以 v2.7 为准。

### 可信本地可见模式（2026-07-28）

`BrowserAutomationInput.headless` 恢复为可选参数。`None` 继续使用部署配置；
`true` 可显式要求无头；`false` 仅在执行器注入进程内不可序列化的本地交互能力令牌，
且服务进程确认当前处于本机交互桌面时允许。该能力令牌不进入 Agent 工具 schema，
JSON 参数中的同名布尔值不能伪造信任，普通远程用户不能据公开参数在服务端弹窗。
同进程桌面宿主通过 `BrowserAutomationTool.execute_local_interactive()` 调用，不直接
持有或传递能力令牌；普通 Agent 调用仍只能进入 `execute()` 的公开参数边界。
最终值必须写入 `BrowserRunSpec.headless`，仍保留每次 run 隔离和终态关闭。

Windows 检测不依赖可能缺失的 `SESSIONNAME`：先用 `ProcessIdToSessionId` 拒绝
Session 0，再用 `OpenInputDesktop(DESKTOP_SWITCHDESKTOP)` 验证当前进程可访问
input desktop，并用 `CloseDesktop` 释放句柄；任一 API 失败均拒绝可见模式。

页面快照若确定性命中 403/Access Denied/异常访问等拒绝页，Orchestrator 在调用 LLM
决策前返回 `success=false,status=blocked,error_code=ACCESS_BLOCKED`；LLM 的
`done` 或 reason 不能覆盖页面阻断信号。

### 10.1 进度回调

每步推送进度（语义快照生成除外），具体规则：

- **推送的步骤**：打开页面、填写输入、点击元素、选择选项、页面跳转、任务完成等
- **不推送**：语义快照生成（内部操作）
- **旧 ask_user 恢复方式（废弃）**：不得再要求用户通过聊天提供 `user_response`，也不得依赖 LLM 使用相同 `session_id` 重新调用 `browser_automation`。v2.5 使用 `HumanAssistanceRequest + ToolSuspension + browser_resume_job` 从同一 run/page/context 恢复。
- **执行失败**：立即中断并返回错误信息给 agent

### 10.2 截图策略

每个阶段结束时截图一次。阶段结束的判定条件：

- 正常完成任务
- 异常退出（错误、超时）
- `HumanAssistanceRequest` 挂起时只保留实时内存画面，不把敏感截图落盘；用户完成后由 resume job 续跑原工具

每个阶段只返回一张截图，不返回中间步骤截图。

### 10.3 错误恢复

分层处理策略：

- **可恢复错误**（元素匹配失败、页面未加载完、操作超时等）：内部 LLM 重新分析页面状态，尝试换方式操作，最多重试 3 次
- **不可恢复错误**（页面 404/5xx、网络断开、浏览器崩溃等）：直接返回失败信息 + 截图，不重试

### 10.4 内容提取

统一提取为 Markdown 格式：

- 内部 LLM 在 `action=done` 时从页面提取内容，输出为 Markdown（支持文本、表格、列表等格式）
- 如需结构化数据（表格、列表），由后续其他工具从 Markdown 中解析提取
- `browser_automation` 工具职责单一：操作浏览器 + 返回 Markdown 内容
