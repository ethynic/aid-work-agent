# Browser Tools 语义工具

基于 Playwright 的新一代浏览器自动化工具，通过语义快照和自然语言驱动实现稳定的网页操作。

## 核心特性

- **语义快照**：将网页 DOM 转换为 LLM 可理解的语义结构
- **自然语言驱动**：通过描述性语言操作元素，无需 CSS 选择器
- **智能匹配**：支持 Embedding 语义相似度匹配
- **路径追踪**：记录操作历史，支持状态回溯
- **iframe 支持**：处理页面嵌套的 iframe 内容
- **动态子菜单检测**：使用 MutationObserver 实时检测子菜单

## 快速开始

### 1. 打开网页

```python
from src.tools.browser import BrowserOpenTool

tool = BrowserOpenTool()
result = await tool.execute(url="https://www.example.com")
session_id = result["session_id"]
```

### 2. 获取语义快照

```python
from src.tools.browser import BrowserSnapshotTool

snapshot_tool = BrowserSnapshotTool()
snapshot = await snapshot_tool.execute(
    session_id=session_id,
    mode="interactive"  # standard / interactive / compact
)
```

返回示例：

```json
{
  "success": true,
  "url": "https://example.com",
  "title": "示例页面",
  "interactive_elements": [
    {"ref": "e1", "label": "用户名", "tag": "input", "type": "text"},
    {"ref": "e2", "label": "密码", "tag": "input", "type": "password"},
    {"ref": "e3", "label": "登录", "tag": "button"}
  ],
  "submenu_snapshots": [
    {
      "trigger_ref": "e4",
      "trigger_label": "财务管理",
      "items": [
        {"ref": "e4-1", "label": "报销申请", "action": "click"},
        {"ref": "e4-2", "label": "付款申请", "action": "click"}
      ]
    }
  ],
  "iframe_snapshots": [
    {"ref": "iframe1", "src": "https://embed.example.com", "sandboxed": false}
  ]
}
```

### 3. 语义操作

```python
# 点击元素
from src.tools.browser import BrowserClickTool

click_tool = BrowserClickTool()
await click_tool.execute(
    description="登录",
    session_id=session_id
)

# 填写表单
from src.tools.browser import BrowserFillTool

fill_tool = BrowserFillTool()
await fill_tool.execute(
    field="用户名",
    value="zhangsan",
    session_id=session_id
)

# 选择选项
from src.tools.browser import BrowserSelectTool

select_tool = BrowserSelectTool()
await select_tool.execute(
    field="部门",
    option="技术研发部",
    session_id=session_id
)
```

### 4. 完整工作流示例

```python
import asyncio
from src.tools.browser import (
    BrowserOpenTool,
    BrowserSnapshotTool,
    BrowserClickTool,
    BrowserFillTool,
)

async def login_and_navigate():
    session_id = "my_session"

    # 1. 打开登录页
    await BrowserOpenTool().execute(
        url="https://erp.company.com",
        session_id=session_id
    )

    # 2. 获取语义快照
    snapshot = await BrowserSnapshotTool().execute(session_id=session_id)

    # 3. 填写登录表单
    await BrowserFillTool().execute(
        field="用户名",
        value="admin",
        session_id=session_id
    )
    await BrowserFillTool().execute(
        field="密码",
        value="***",
        session_id=session_id
    )

    # 4. 点击登录按钮
    await BrowserClickTool().execute(
        description="登录",
        session_id=session_id
    )

    # 5. 获取新页面的快照
    snapshot = await BrowserSnapshotTool().execute(session_id=session_id)

    # 6. 点击子菜单中的选项
    await BrowserClickTool().execute(
        description="报销申请",
        session_id=session_id
    )
```

## 工具列表

### 导航类工具

| 工具 | 说明 |
|------|------|
| `browser_open` | 打开网页 |
| `browser_snapshot` | 获取语义快照（**必须首先调用**） |
| `browser_navigate` | 前进/后退/刷新 |
| `browser_close` | 关闭浏览器 |

### 操作类工具

| 工具 | 说明 | 依赖 |
|------|------|------|
| `browser_click` | 点击元素 | `browser_snapshot` |
| `browser_fill` | 填写表单 | `browser_snapshot` |
| `browser_select` | 选择选项 | `browser_snapshot` |
| `browser_find` | 语义查找元素 | 可选 |

### 调试类工具

| 工具 | 说明 |
|------|------|
| `browser_get_content` | 获取页面原始内容 |
| `browser_screenshot` | 截图 |
| `browser_get_path` | 获取操作历史 |
| `browser_backtrack` | 回溯状态 |

## 重要规范

### 必须遵循的流程

1. **打开网页后**：必须立即调用 `browser_snapshot` 获取语义快照
2. **页面变化后**：必须再次调用 `browser_snapshot`
3. **操作元素时**：必须使用 `browser_click`、`browser_fill` 等语义工具

### 禁止事项

- ❌ 禁止使用 CSS 选择器（如 `selector="#submit-btn"`）
- ❌ 禁止在 `browser_open` 后直接操作元素（必须先快照）
- ❌ 禁止跳过 `browser_snapshot` 直接猜测 ref

### 正确示例

```
browser_open(url="https://example.com")
browser_snapshot()  ← 必须
browser_click(description="登录按钮")  ← 使用自然语言描述
```

### 错误示例

```
browser_open(url="https://example.com")
browser_click(selector="#login")  ← 禁止使用 CSS 选择器
```

## 语义快照输出说明

### interactive_elements

页面上所有可交互元素的语义信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ref` | string | 元素唯一引用（用于后续操作） |
| `label` | string | 语义标签（LLM 可理解） |
| `tag` | string | HTML 标签名 |
| `role` | string | ARIA 角色 |
| `type` | string | input 类型（仅 input 元素） |
| `visible` | bool | 是否可见 |
| `disabled` | bool | 是否禁用 |
| `has_popup` | bool | 是否有弹出菜单 |

### submenu_snapshots

折叠菜单的隐藏内容（自动检测）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trigger_ref` | string | 触发元素 ref |
| `trigger_label` | string | 触发元素标签 |
| `items` | array | 子菜单项列表 |

### iframe_snapshots

页面中的 iframe 信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ref` | string | iframe 引用 |
| `src` | string | iframe src 属性 |
| `title` | string | iframe title |
| `visible` | bool | 是否可见 |
| `sandboxed` | bool | 是否沙箱化（跨域） |
| `element_count` | int | 同源 iframe 内部元素数量 |
| `nested_iframes` | array | 嵌套的 iframe |

## 缓存机制

语义快照支持缓存，5分钟 TTL：

```python
# 启用缓存（默认）
snapshot = await snapshot_tool.execute(session_id=session_id, use_cache=True)

# 禁用缓存
snapshot = await snapshot_tool.execute(session_id=session_id, use_cache=False)
```

## 路径追踪

操作会自动记录到 PathTracker：

```python
# 获取操作路径
from src.tools.browser import BrowserGetPathTool

path_tool = BrowserGetPathTool()
path_result = await path_tool.execute(session_id=session_id, format="text")

# 回溯状态
from src.tools.browser import BrowserBacktrackTool

backtrack_tool = BrowserBacktrackTool()
await backtrack_tool.execute(session_id=session_id, steps=2)
```

## 测试

运行语义工具测试：

```bash
python -m tests.test_browser_semantic
```

## 文件结构

```
src/tools/browser/
├── semantic/
│   ├── snapshot_generator.py   # 语义快照生成器
│   ├── submenu_detector.py     # 子菜单检测器
│   ├── natural_matcher.py      # 自然语言匹配器
│   ├── iframe_handler.py       # iframe 处理器
│   └── ...
├── tools_snapshot.py           # browser_snapshot 工具
├── tools_semantic.py           # browser_click/fill/select 工具
└── ...
```