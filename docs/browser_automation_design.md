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
  "action": "click" | "fill" | "select" | "navigate" | "done" | "ask_user",
  "target": "元素描述或URL",
  "value": "填写值（fill/select时）",
  "reason": "为什么这样做"
}
```

- `action=done`：任务完成，停止循环
- `action=ask_user`：信息不足，需要用户补充

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

### 10.1 进度回调

每步推送进度（语义快照生成除外），具体规则：

- **推送的步骤**：打开页面、填写输入、点击元素、选择选项、页面跳转、任务完成等
- **不推送**：语义快照生成（内部操作）
- **ask_user 中断**：内部 LLM 判断信息不足时，返回 `action=ask_user` + 截图 + 问题，中断工具执行。Agent 将问题转达用户后，以用户回复作为参数再次调用 `browser_automation`，orchestrator 通过 session_id 恢复浏览器会话状态继续执行
- **执行失败**：立即中断并返回错误信息给 agent

### 10.2 截图策略

每个阶段结束时截图一次。阶段结束的判定条件：

- 正常完成任务
- 异常退出（错误、超时）
- ask_user 中断等待用户输入

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
