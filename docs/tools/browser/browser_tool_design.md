# Browser 工具优化设计文档

## 1. 概述

### 1.1 背景与问题

当前 Browser 工具基于 Playwright 实现，提供基础的网页操作能力（打开、点击、填写、获取内容）。但在复杂网页场景下存在以下问题：

**问题 1：元素定位困难**
- 当前依赖 CSS 选择器定位，但大模型难以准确生成复杂页面的 CSS 选择器
- 动态渲染页面的元素结构不稳定，选择器容易失效
- 复杂页面（多表单、多折叠区域）难以正确定位目标元素

**问题 2：缺少语义理解**
- `browser_get_content` 只能获取原始文本内容，大模型难以理解页面结构
- 无法区分可交互元素（按钮、输入框）与装饰性元素
- 文本内容缺少语义标签（如"这是导航菜单"、"这是提交按钮"）

**问题 3：无法处理复杂交互流程**
- 折叠菜单（如"财务管理 → 报销申请"）需要多次探索才能找到目标
- 页面状态变化后无法自动重新定位元素
- 没有路径追踪能力，不知道"我在哪里"和"如何到达目标"

### 1.2 设计目标

参考 OpenClaw 和 DeerFlow 的实现，设计新一代 Browser 工具：

| 目标 | 描述 |
|------|------|
| **语义快照** | 生成 LLM 可理解的页面结构化表示，包含元素语义标签 |
| **稳定定位** | 通过语义引用而非 CSS 选择器定位元素，提高稳定性 |
| **智能探索** | 支持复杂网页的多步导航，自动发现折叠菜单 |
| **状态追踪** | 记录操作历史，支持路径回溯 |

---

## 2. 核心功能设计

### 2.1 语义快照 (Semantic Snapshot)

**核心概念**：将网页 DOM 结构转换为 LLM 可理解的语义化表示。

**实现方案**：

```python
class SemanticSnapshotTool(BaseTool):
    """获取页面语义快照"""

    name = "browser_snapshot"
    description = """获取当前页面的语义快照，返回结构化的页面表示。
    快照包含页面上所有可交互元素的语义信息，便于大模型理解页面结构并定位元素。
    适用于：
    - 了解页面整体布局和功能区域
    - 定位需要点击或填写的元素
    - 发现折叠菜单中的隐藏内容"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "mode": {
                "type": "string",
                "enum": ["standard", "interactive", "compact"],
                "description": "快照模式：standard标准模式，interactive交互模式(仅显示可交互元素)，compact紧凑模式"
            },
            "max_depth": {
                "type": "integer",
                "description": "DOM遍历最大深度，默认6"
            },
            "include_hidden": {
                "type": "boolean",
                "description": "是否包含隐藏元素（如display:none），默认false"
            }
        }
    }
```

**输出格式**：

```json
{
  "success": true,
  "session_id": "default",
  "url": "https://example.com/finance",
  "title": "企业管理系统 - 财务模块",
  "mode": "interactive",
  "regions": [
    {
      "id": "r1",
      "type": "navigation",
      "label": "主导航菜单",
      "children": [
        {"id": "r1-1", "type": "menu_item", "label": "首页", "ref": "e1", "action": "click"},
        {"id": "r1-2", "type": "menu_item", "label": "财务管理", "ref": "e2", "action": "click", "has_submenu": true},
        {"id": "r1-3", "type": "menu_item", "label": "人力资源", "ref": "e3", "action": "click", "has_submenu": true}
      ]
    },
    {
      "id": "r2",
      "type": "form",
      "label": "报销申请表单",
      "children": [
        {"id": "r2-1", "type": "input", "label": "申请人", "ref": "e10", "value_type": "text"},
        {"id": "r2-2", "type": "input", "label": "报销金额", "ref": "e11", "value_type": "number"},
        {"id": "r2-3", "type": "button", "label": "提交申请", "ref": "e12", "action": "click"}
      ]
    }
  ],
  "interactive_elements": [
    {"ref": "e1", "tag": "a", "label": "首页", "href": "/home", "visible": true},
    {"ref": "e2", "tag": "div", "label": "财务管理", "role": "menuitem", "has_popup": true, "visible": true},
    {"ref": "e3", "tag": "div", "label": "人力资源", "role": "menuitem", "has_popup": true, "visible": true},
    {"ref": "e10", "tag": "input", "label": "申请人", "type": "text", "visible": true},
    {"ref": "e11", "tag": "input", "label": "报销金额", "type": "number", "visible": true},
    {"ref": "e12", "tag": "button", "label": "提交申请", "type": "submit", "visible": true}
  ],
  "submenu_snapshots": [
    {
      "trigger_ref": "e2",
      "trigger_label": "财务管理",
      "items": [
        {"ref": "e2-1", "label": "报销申请", "action": "click"},
        {"ref": "e2-2", "label": "付款申请", "action": "click"},
        {"ref": "e2-3", "label": "财务报表", "action": "click"}
      ]
    }
  ]
}
```

**关键特性**：

1. **ref 引用系统**：每个可交互元素都有唯一引用（如 `e1`, `e2`），大模型通过 ref 而非 CSS 选择器操作元素
2. **语义标签**：自动识别元素类型（navigation, form, button, input 等）
3. **区域划分**：按功能区域组织元素，便于大模型理解页面布局
4. **子菜单快照**：自动检测并展开折叠菜单，返回隐藏内容

---

### 2.2 元素引用操作 (Element Reference Actions)

**核心概念**：通过语义 ref 而非 CSS 选择器执行操作，提高稳定性。

**工具设计**：

```python
class BrowserClickByRefTool(BaseTool):
    """通过语义引用点击元素"""

    name = "browser_click_ref"
    description = """点击页面上的指定元素。使用语义引用(ref)定位，比CSS选择器更稳定。
    适用于：
    - 点击按钮、链接
    - 展开折叠菜单
    - 选择下拉选项"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "元素的语义引用，来自browser_snapshot的interactive_elements"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "timeout": {"type": "integer", "description": "超时时间(毫秒)"}
        },
        "required": ["ref"]
    }


class BrowserFillByRefTool(BaseTool):
    """通过语义引用填写表单"""

    name = "browser_fill_ref"
    description = """填写表单字段。使用语义引用定位，比CSS选择器更稳定。
    适用于：
    - 填写文本输入框
    - 设置日期选择器
    - 选择下拉选项"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "表单字段的语义引用，来自browser_snapshot的interactive_elements"
            },
            "value": {
                "type": "string",
                "description": "要填写的值"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "timeout": {"type": "integer", "description": "超时时间(毫秒)"}
        },
        "required": ["ref", "value"]
    }


class BrowserSelectByRefTool(BaseTool):
    """通过语义引用选择选项"""

    name = "browser_select_ref"
    description = """在下拉列表或选项中选择。使用语义引用定位。
    适用于：
    - 选择下拉菜单选项
    - 选择单选/复选框选项"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "下拉选择框的语义引用"
            },
            "option_ref": {
                "type": "string",
                "description": "要选择的选项引用，来自browser_snapshot的子菜单snapshot"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "timeout": {"type": "integer", "description": "超时时间(毫秒)"}
        },
        "required": ["ref", "option_ref"]
    }
```

---

### 2.3 智能元素查找 (Smart Element Finder)

**核心概念**：根据语义描述自动查找匹配元素，无需手动定位。

```python
class BrowserFindElementTool(BaseTool):
    """根据语义描述查找元素"""

    name = "browser_find_element"
    description = """根据语义描述查找页面元素。通过自然语言描述目标特征，自动在页面中寻找匹配元素。
    适用于：
    - 不知道具体ref，根据描述查找
    - 动态页面，ref可能变化
    - 需要在当前视口或整页中搜索"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "元素的语义描述，如'登录按钮'、'报销金额输入框'、'财务管理菜单'"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "scope": {
                "type": "string",
                "enum": ["viewport", "page"],
                "description": "搜索范围：viewport只在当前视口，page在整页搜索"
            },
            "match_hint": {
                "type": "string",
                "description": "额外的匹配提示，如'type=submit'、'class=primary'"
            }
        },
        "required": ["description"]
    }
```

**输出格式**：

```json
{
  "success": true,
  "ref": "e15",
  "label": "报销申请",
  "tag": "a",
  "role": "menuitem",
  "matched_by": "description",
  "confidence": 0.95,
  "alternatives": [
    {"ref": "e16", "label": "付款申请", "confidence": 0.7},
    {"ref": "e17", "label": "差旅报销", "confidence": 0.6}
  ],
  "action": "click"
}
```

---

### 2.4 路径追踪 (Path Tracker)

**核心概念**：记录操作历史，支持回溯和多步导航。

```python
class BrowserPathTracker:
    """追踪浏览器操作路径"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.history: List[PathStep] = []
        self.current_ref: Optional[str] = None

    def record_step(self, action: str, ref: str, snapshot_ref: str, result: str):
        """记录操作步骤"""
        self.history.append(PathStep(
            action=action,
            ref=ref,
            snapshot_ref=snapshot_ref,
            result=result,
            timestamp=datetime.now()
        ))


class BrowserGetPathTool(BaseTool):
    """获取当前操作路径"""

    name = "browser_get_path"
    description = """获取当前的浏览器操作路径历史，用于了解如何到达当前位置或进行回溯。"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "format": {
                "type": "string",
                "enum": ["text", "json"],
                "description": "输出格式"
            }
        }
    }


class BrowserBacktrackTool(BaseTool):
    """回溯到之前的页面状态"""

    name = "browser_backtrack"
    description = """回溯到之前的页面状态。如果当前操作导致页面异常，可以回退到之前的状态。"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "steps": {
                "type": "integer",
                "description": "回溯的步数，默认为1"
            }
        }
    }
```

---

## 3. 语义快照生成算法

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    SemanticSnapshotGenerator                │
├─────────────────────────────────────────────────────────────┤
│  1. DOM Traversal        2. Element Classifier             │
│  ┌─────────────────┐     ┌─────────────────┐                │
│  │ 使用 Playwright │ ──▶ │  元素类型识别   │                │
│  │ 遍历 DOM 树     │     │  button/input   │                │
│  │ 深度优先/广度   │     │  nav/header     │                │
│  └─────────────────┘     └─────────────────┘                │
│           │                       │                          │
│           ▼                       ▼                          │
│  3. Semantic Tagger           4. Ref Assigner                │
│  ┌─────────────────┐     ┌─────────────────┐                │
│  │ 语义标签生成    │     │  生成唯一引用   │                │
│  │ label/role/    │     │  e1, e2, e3...  │                │
│  │ aria-* 属性    │     │                 │                │
│  └─────────────────┘     └─────────────────┘                │
│           │                       │                          │
│           ▼                       ▼                          │
│  5. Submenu Detector                                      │
│  ┌─────────────────────────────────────────┐                │
│  │  检测并展开折叠菜单，生成子菜单快照      │                │
│  │  hover/focus 触发 → 获取弹出内容 → 还原 │                │
│  └─────────────────────────────────────────┘                │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────┐                │
│  │  Output: SemanticSnapshot JSON           │                │
│  └─────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 元素分类器

```python
class ElementClassifier:
    """元素类型分类器"""

    INTERACTIVE_TAGS = {'a', 'button', 'input', 'select', 'textarea', 'details'}
    NAVIGATION_TAGS = {'nav', 'header', 'aside', 'menu', 'ul', 'ol'}
    FORM_TAGS = {'form', 'fieldset'}
    CONTENT_TAGS = {'article', 'section', 'main', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}

    @classmethod
    def classify(cls, element) -> ElementType:
        """分类元素类型"""
        tag = element.tag_name.lower()

        if tag in cls.INTERACTIVE_TAGS:
            return ElementType.INTERACTIVE
        elif tag in cls.NAVIGATION_TAGS:
            return ElementType.NAVIGATION
        elif tag in cls.FORM_TAGS:
            return ElementType.FORM
        elif tag in cls.CONTENT_TAGS:
            return ElementType.CONTENT
        else:
            return ElementType.OTHER
```

### 3.3 语义标签生成器

```python
class SemanticTagger:
    """生成元素的语义标签"""

    def generate_label(self, element) -> str:
        """生成元素语义标签"""
        # 优先级：aria-label > aria-labelledby > text content > placeholder > title

        # 1. 尝试 aria-label
        aria_label = element.get_attribute('aria-label')
        if aria_label:
            return aria_label.strip()

        # 2. 尝试 aria-labelledby (需要解析关联文本)
        aria_labelledby = element.get_attribute('aria-labelledby')
        if aria_labelledby:
            label = self._resolve_aria_labelledby(aria_labelledby)
            if label:
                return label

        # 3. 尝试有意义的文本内容
        text = self._extract_meaningful_text(element)
        if text and len(text) <= 50:  # 限制长度
            return text

        # 4. 尝试 placeholder
        placeholder = element.get_attribute('placeholder')
        if placeholder:
            return f"输入框: {placeholder}"

        # 5. 尝试 title 属性
        title = element.get_attribute('title')
        if title:
            return title

        return ""

    def _extract_meaningful_text(self, element) -> str:
        """提取有意义的文本内容"""
        # 排除脚本、样式、隐藏元素
        # 优先提取直接文本，忽略子元素文本
        direct_text = element.evaluate("""
            node => {
                if (node.style.display === 'none' || node.style.visibility === 'hidden') {
                    return '';
                }
                // 获取直接文本节点
                let text = '';
                for (let child of node.childNodes) {
                    if (child.nodeType === Node.TEXT_NODE) {
                        text += child.textContent;
                    }
                }
                return text.trim();
            }
        """)
        return direct_text if direct_text else ""
```

### 3.4 子菜单检测器

```python
class SubmenuDetector:
    """检测并处理折叠菜单/子菜单"""

    def detect_and_snapshot(self, page, element) -> Optional[SubmenuSnapshot]:
        """检测元素是否有子菜单，如有则展开并获取快照"""
        # 检查是否有子菜单特征
        has_popup = element.get_attribute('aria-haspopup')
        has_submenu = element.get_attribute('data-submenu') or 'dropdown' in element.get_attribute('class', '')

        if not (has_popup or has_submenu):
            return None

        try:
            # 悬停触发子菜单
            await element.hover()
            await page.wait_for_timeout(300)  # 等待动画

            # 获取弹出的菜单内容
            popup_selector = element.evaluate("""
                node => {
                    // 查找关联的菜单面板
                    const menuId = node.getAttribute('aria-controls') ||
                                   node.getAttribute('data-target') ||
                                   node.getAttribute('id') + '-menu';

                    if (menuId) {
                        return document.getElementById(menuId) ||
                               document.querySelector(`[aria-labelledby="${menuId}"]`);
                    }

                    // 查找 DOM 中的下一个兄弟菜单
                    let sibling = node.nextElementSibling;
                    while (sibling) {
                        if (sibling.matches('[role="menu"], [role="dropdown"], .dropdown-menu, .submenu')) {
                            return sibling;
                        }
                        sibling = sibling.nextElementSibling;
                    }
                    return null;
                }
            """)

            if popup_selector:
                items = await self._extract_menu_items(popup_selector)
                return SubmenuSnapshot(
                    trigger_ref=element.ref,
                    trigger_label=element.label,
                    items=items
                )

        except Exception:
            pass

        return None

    def _extract_menu_items(self, menu_element) -> List[MenuItem]:
        """提取菜单项列表"""
        items = []
        menu_items = await menu_element.query_selector_all(
            '[role="menuitem"], [role="menuitemradio"], a, button'
        )

        for idx, item in enumerate(menu_items):
            items.append(MenuItem(
                ref=f"{self.parent_ref}-{idx + 1}",
                label=await self._get_item_label(item),
                action="click",
                visible=await item.is_visible()
            ))

        return items
```

---

## 4. 完整工作流示例

### 场景：在企业系统中填写报销申请

**传统方式（有问题）**：
1. 用户："帮我打开财务管理系统，填写报销申请"
2. Agent 尝试直接点击报销申请 → 失败（报销申请在折叠菜单下）
3. Agent 尝试猜测 CSS 选择器 → 不准确

**新方式（语义快照）**：

```
用户：帮我打开财务管理系统，填写报销申请

Agent 流程：
┌────────────────────────────────────────────────────────────────┐
│ Step 1: browser_open                                          │
│ URL: https://erp.company.com                                  │
│ Result: 成功打开登录页面                                       │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 2: browser_snapshot (获取登录页快照)                      │
│ Output:                                                       │
│ {                                                             │
│   "interactive_elements": [                                   │
│     {"ref": "e1", "label": "用户名", "tag": "input"},         │
│     {"ref": "e2", "label": "密码", "tag": "input"},          │
│     {"ref": "e3", "label": "登录", "tag": "button"}          │
│   ]                                                           │
│ }                                                             │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 3: browser_fill_ref(ref="e1", value="zhangsan")         │
│ Step 4: browser_fill_ref(ref="e2", value="***")              │
│ Step 5: browser_click_ref(ref="e3")                           │
│ Result: 登录成功，进入首页                                     │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 6: browser_snapshot (获取首页快照)                        │
│ Output:                                                       │
│ {                                                             │
│   "regions": [                                                │
│     {                                                        │
│       "id": "r1",                                            │
│       "type": "navigation",                                  │
│       "label": "主导航",                                     │
│       "children": [                                          │
│         {"id": "r1-1", "label": "首页", "ref": "e10"},       │
│         {"id": "r1-2", "label": "财务管理", "ref": "e11",    │
│          "has_submenu": true},                               │
│         ...                                                  │
│       ]                                                      │
│     }                                                        │
│   ],                                                          │
│   "submenu_snapshots": [  // 自动检测到折叠菜单              │
│     {                                                        │
│       "trigger_ref": "e11",                                  │
│       "trigger_label": "财务管理",                          │
│       "items": [                                             │
│         {"ref": "e11-1", "label": "报销申请", "action": "click"},│
│         {"ref": "e11-2", "label": "付款申请", "action": "click"},│
│         {"ref": "e11-3", "label": "财务报表", "action": "click"} │
│       ]                                                      │
│     }                                                        │
│   ]                                                           │
│ }                                                             │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 7: browser_click_ref(ref="e11")  // 点击财务管理        │
│ Note: 系统会自动展开子菜单                                     │
│ Result: 财务管理子菜单已展开                                  │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 8: browser_click_ref(ref="e11-1")  // 点击报销申请       │
│ Result: 进入报销申请页面                                       │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 9: browser_snapshot (获取报销申请页快照)                  │
│ Output:                                                       │
│ {                                                             │
│   "regions": [                                                │
│     {                                                        │
│       "id": "r1",                                            │
│       "type": "form",                                        │
│       "label": "报销申请表单",                               │
│       "children": [                                          │
│         {"ref": "e20", "label": "申请人", "type": "input"},  │
│         {"ref": "e21", "label": "部门", "type": "select"},   │
│         {"ref": "e22", "label": "报销类型", "type": "select"},│
│         {"ref": "e23", "label": "报销金额", "type": "input"}, │
│         {"ref": "e24", "label": "事由", "type": "textarea"}, │
│         {"ref": "e25", "label": "提交", "type": "button"}    │
│       ]                                                      │
│     }                                                        │
│   ]                                                           │
│ }                                                             │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Step 10-14: 填写表单                                          │
│ browser_fill_ref(ref="e20", value="张三")                    │
│ browser_fill_ref(ref="e21", value="技术研发部")              │
│ browser_fill_ref(ref="e22", value="差旅费")                  │
│ browser_fill_ref(ref="e23", value="1500.00")                 │
│ browser_fill_ref(ref="e24", value="客户拜访差旅费用")         │
│ Step 15: browser_click_ref(ref="e25")  // 提交               │
└────────────────────────────────────────────────────────────────┘
```

---

## 5. 工具完整列表

### 5.1 工具命名规范（重要）

**核心原则**：工具名称必须简洁、语义明确，便于 LLM 理解和调用。

| 新工具名 | 旧工具名 | 说明 |
|----------|----------|------|
| `browser_snapshot` | - | 获取语义快照（**必须首先调用**） |
| `browser_click` | - | 点击元素（**语义驱动，废弃 CSS 选择器版本**） |
| `browser_fill` | - | 填写表单（**语义驱动，废弃 CSS 选择器版本**） |
| `browser_select` | - | 选择选项 |
| `browser_find` | - | 语义查找元素 |
| `browser_open` | `browser_open` | 打开网页（保留） |
| `browser_get_content` | `browser_get_content` | 获取页面内容（保留） |
| `browser_navigate` | `browser_navigate` | 页面导航（保留） |
| `browser_screenshot` | `browser_screenshot` | 网页截图（保留） |
| `browser_close` | `browser_close` | 关闭浏览器（保留） |
| `browser_get_path` | - | 获取操作路径 |
| `browser_backtrack` | - | 回溯操作 |

### 5.2 工具废弃计划

#### 废弃范围

以下旧工具将在 **Phase 2** 完成时标记为 deprecated，**Phase 3** 完成时删除：

| 旧工具 | 废弃阶段 | 替代工具 | 废弃原因 |
|--------|----------|----------|----------|
| `browser_click` (CSS版) | Phase 2 标记 deprecated | `browser_click` (语义版) | CSS 选择器不稳定，大模型难以准确生成 |
| `browser_fill` (CSS版) | Phase 2 标记 deprecated | `browser_fill` (语义版) | CSS 选择器不稳定，大模型难以准确生成 |

#### 废弃规则

**重要**：`browser_open` 成功后，**必须调用** `browser_snapshot` 获取当前页面的语义快照，之后所有操作**必须**通过快照中的 `ref` 进行。

```
# ✅ 正确流程
browser_open(url="https://example.com")
    ↓
browser_snapshot()  # 必须先获取快照
    ↓
browser_click(ref="e5")  # 通过 ref 点击
browser_fill(ref="e10", value="test")  # 通过 ref 填写

# ❌ 错误流程 - 直接使用 CSS 选择器
browser_open(url="https://example.com")
    ↓
browser_click(selector="#submit-btn")  # 禁止使用 CSS 选择器
```

### 5.3 工具统一性要求

**语义快照驱动的完整工具链**：

所有 Browser 工具按照使用场景分为两类：

#### A. 导航类工具（独立使用）

| 工具 | 描述 | 使用时机 |
|------|------|----------|
| `browser_open` | 打开网页 | 启动浏览器时调用 |
| `browser_snapshot` | 获取语义快照 | **每次页面变化后必须调用** |
| `browser_navigate` | 前进/后退/刷新 | 需要导航时调用 |
| `browser_close` | 关闭浏览器 | 任务结束时调用 |

#### B. 操作类工具（依赖快照）

| 工具 | 描述 | 依赖 |
|------|------|------|
| `browser_click` | 点击元素 | 必须先 `browser_snapshot` |
| `browser_fill` | 填写表单 | 必须先 `browser_snapshot` |
| `browser_select` | 选择选项 | 必须先 `browser_snapshot` |
| `browser_find` | 语义查找 | 可选，通常配合快照使用 |

#### C. 调试类工具（可选）

| 工具 | 描述 |
|------|------|
| `browser_get_content` | 获取页面原始内容 |
| `browser_screenshot` | 截图 |
| `browser_get_path` | 获取操作历史 |
| `browser_backtrack` | 回溯状态 |

### 5.4 工具优先级

| 优先级 | 工具 | 说明 |
|--------|------|------|
| **P0** | `browser_snapshot` | 核心功能，必须首先实现 |
| **P0** | `browser_click` (语义版) | 核心操作，必须实现 |
| **P0** | `browser_fill` (语义版) | 核心操作，必须实现 |
| **P1** | `browser_select` | 表单操作增强 |
| **P1** | `browser_find` | 语义查找增强 |
| **P2** | `browser_get_path` | 路径追踪 |
| **P2** | `browser_backtrack` | 状态回溯 |

---

## 6. 自然语言驱动设计

### 6.1 设计原则

**核心目标**：让 LLM 能够通过自然语言描述来完成网页操作，无需手动构造参数。

| 原则 | 描述 |
|------|------|
| **意图驱动** | 工具接受自然语言描述作为输入，解析为具体操作 |
| **上下文感知** | 工具能理解当前页面快照的语义信息 |
| **自动映射** | 自然语言描述自动映射到快照中的 `ref` 引用 |
| **多候选** | 当匹配多个元素时，返回候选列表供选择 |

### 6.2 工具自然语言接口设计

#### browser_click - 自然语言点击

```python
class BrowserClickTool(BaseTool):
    """点击页面元素（自然语言驱动）"""

    name = "browser_click"
    description = """点击页面上的元素。通过自然语言描述要点击的元素，系统自动在快照中查找匹配元素并点击。

    使用方式：
    - "点击登录按钮"
    - "点击报销申请菜单"
    - "点击提交按钮"
    - "点击确定"

    重要：必须先调用 browser_snapshot 获取当前页面的语义快照！"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "要点击元素的自然语言描述，如'登录按钮'、'报销申请'"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "timeout": {"type": "integer", "description": "超时时间(毫秒)"}
        },
        "required": ["description"]
    }
```

#### browser_fill - 自然语言填写

```python
class BrowserFillTool(BaseTool):
    """填写表单字段（自然语言驱动）"""

    name = "browser_fill"
    description = """填写表单字段。通过自然语言描述要填写的字段和值，系统自动查找匹配元素并填写。

    使用方式：
    - "在用户名输入框填写张三"
    - "在密码框输入123456"
    - "填写报销金额1500元"
    - "在备注框填写客户拜访差旅"

    重要：必须先调用 browser_snapshot 获取当前页面的语义快照！"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "要填写的字段的自然语言描述，如'用户名'、'报销金额'"
            },
            "value": {
                "type": "string",
                "description": "要填写的值"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "timeout": {"type": "integer", "description": "超时时间(毫秒)"}
        },
        "required": ["field", "value"]
    }
```

#### browser_select - 自然语言选择

```python
class BrowserSelectTool(BaseTool):
    """选择下拉选项（自然语言驱动）"""

    name = "browser_select"
    description = """在下拉列表中选择选项。通过自然语言描述要选择的选项，系统自动查找并选择。

    使用方式：
    - "选择部门为技术研发部"
    - "选择报销类型为差旅费"
    - "选择审批人为张三"
    - "选择日期为今天"

    重要：必须先调用 browser_snapshot 获取当前页面的语义快照！"""

    parameters_schema = {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "下拉选择框的描述，如'部门'、'报销类型'"
            },
            "option": {
                "type": "string",
                "description": "要选择的选项，如'技术研发部'、'差旅费'"
            },
            "session_id": {"type": "string", "description": "浏览器会话ID"},
            "timeout": {"type": "integer", "description": "超时时间(毫秒)"}
        },
        "required": ["field", "option"]
    }
```

### 6.3 自然语言匹配引擎

```python
class NaturalLanguageMatcher:
    """自然语言描述匹配器"""

    def __init__(self, snapshot: SemanticSnapshot):
        self.snapshot = snapshot
        self.elements = snapshot.interactive_elements

    def match_click(self, description: str) -> MatchResult:
        """匹配点击目标

        Args:
            description: 自然语言描述，如"登录按钮"、"报销申请"

        Returns:
            MatchResult: 匹配结果，包含 ref、置信度、备选列表
        """
        # 1. 预处理描述
        normalized_desc = self._normalize(description)

        # 2. 多策略匹配
        candidates = []

        # 策略1：精确标签匹配
        for elem in self.elements:
            if self._exact_match(normalized_desc, elem.label):
                candidates.append((elem, 1.0))

        # 策略2：包含匹配
        if not candidates:
            for elem in self.elements:
                if self._partial_match(normalized_desc, elem.label):
                    candidates.append((elem, 0.8))

        # 策略3：语义相似度匹配
        if not candidates:
            for elem in self.elements:
                similarity = self._semantic_similarity(normalized_desc, elem.label)
                if similarity > 0.6:
                    candidates.append((elem, similarity))

        # 3. 排序并返回结果
        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            return MatchResult(success=False, error="未找到匹配元素")

        best_match, confidence = candidates[0]
        alternatives = [
            {"ref": e.ref, "label": e.label, "confidence": c}
            for e, c in candidates[1:5]  # 最多返回4个备选
        ]

        return MatchResult(
            success=True,
            ref=best_match.ref,
            label=best_match.label,
            confidence=confidence,
            alternatives=alternatives,
            action="click"
        )

    def match_fill(self, field: str, value: str) -> MatchResult:
        """匹配填写目标"""
        # 类似 match_click，但专注于 input/textarea/select 元素
        pass

    def match_select(self, field: str, option: str) -> MatchResult:
        """匹配选择目标"""
        # 需要先展开下拉框获取选项列表
        pass

    def _normalize(self, text: str) -> str:
        """标准化描述文本"""
        import re
        # 移除标点、转小写、去除多余空格
        text = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
        return text.lower().strip()

    def _exact_match(self, desc: str, label: str) -> bool:
        """精确匹配"""
        return desc == self._normalize(label)

    def _partial_match(self, desc: str, label: str) -> bool:
        """部分匹配"""
        desc_norm = self._normalize(desc)
        label_norm = self._normalize(label)
        return desc_norm in label_norm or label_norm in desc_norm

    def _semantic_similarity(self, desc: str, label: str) -> float:
        """语义相似度（简化版，实际可用 embedding）"""
        # 简单实现：基于关键词共现
        desc_words = set(desc)
        label_words = set(label)

        if not desc_words or not label_words:
            return 0.0

        intersection = desc_words & label_words
        union = desc_words | label_words

        return len(intersection) / len(union)
```

### 6.4 LLM 调用示例

#### 传统方式（有问题）

```
用户：帮我打开财务系统，填写报销申请

LLM 决策：
1. browser_open(url="https://erp.company.com")
2. browser_click(selector=".menu-item[data-id='finance']")  ← CSS选择器不可靠
3. browser_click(selector="#expense-reimburse")  ← 猜测选择器
4. browser_fill(selector="input[name='amount']", value="1500")  ← 选择器不准确
```

#### 自然语言驱动方式（推荐）

```
用户：帮我打开财务系统，填写报销申请

LLM 决策：
1. browser_open(url="https://erp.company.com")
   → 打开登录页面

2. browser_snapshot()
   → 返回登录页快照：
     {
       "interactive_elements": [
         {"ref": "e1", "label": "用户名", "tag": "input"},
         {"ref": "e2", "label": "密码", "tag": "input"},
         {"ref": "e3", "label": "登录", "tag": "button"}
       ]
     }

3. browser_fill(field="用户名", value="zhangsan")
   → 匹配到 e1，自动填写

4. browser_fill(field="密码", value="***")
   → 匹配到 e2，自动填写

5. browser_click(description="登录按钮")
   → 匹配到 e3，点击登录

6. browser_snapshot()
   → 返回首页快照（包含财务管理菜单）

7. browser_click(description="财务管理")
   → 匹配到 e11（has_submenu=true）

8. browser_snapshot()
   → 自动检测子菜单，返回：
     {
       "submenu_snapshots": [
         {"trigger_ref": "e11", "items": [
           {"ref": "e11-1", "label": "报销申请"},
           {"ref": "e11-2", "label": "付款申请"}
         ]}
       ]
     }

9. browser_click(description="报销申请")
   → 匹配到 e11-1，点击进入报销页面

10. browser_snapshot()
    → 返回报销申请表单快照

11. browser_fill(field="报销金额", value="1500")
12. browser_fill(field="事由", value="客户拜访差旅")
13. browser_click(description="提交")
```

### 6.5 系统提示词集成

在 Agent 的系统提示词中添加工具使用规范：

```markdown
## Browser 工具使用规范

### 必须遵循的流程

1. **打开网页后**：必须立即调用 `browser_snapshot` 获取语义快照
2. **页面变化后**（点击、导航等）：必须再次调用 `browser_snapshot`
3. **操作元素时**：必须使用 `browser_click`、`browser_fill` 等语义工具，传入自然语言描述

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

### 语义快照的作用

`browser_snapshot` 返回的 JSON 包含：
- `interactive_elements`: 页面上所有可交互元素的 `ref` 和语义标签
- `regions`: 按功能区域组织的元素结构
- `submenu_snapshots`: 折叠菜单的隐藏内容

LLM 应该分析快照内容，理解页面结构，然后通过自然语言描述要操作的目标。
```

---

## 7. 实现计划

### Phase 1: 核心功能 (P0) ✅ 已完成

**目标**：完成语义快照驱动的核心工具集，废弃旧版 CSS 选择器工具。

#### 1.1 SemanticSnapshotGenerator 类 ✅
- DOM 遍历
- 元素分类
- 语义标签生成
- ref 引用分配
- 子菜单检测

#### 1.2 新工具实现 ✅

| 工具 | 实现内容 | 状态 |
|------|----------|------|
| `browser_snapshot` | 标准模式 + 交互模式 + 子菜单检测 | ✅ |
| `browser_click` | 自然语言描述 → ref 映射 → 点击 | ✅ |
| `browser_fill` | 自然语言字段描述 + 值 → ref 映射 → 填写 | ✅ |
| `browser_select` | 自然语言选择框描述 + 选项 → ref 映射 → 选择 | ✅ |
| `browser_find` | 语义描述匹配 → ref 返回 → 置信度排序 | ✅ |

#### 1.3 旧工具标记 Deprecated ✅

| 旧工具 | 处理方式 |
|--------|----------|
| `browser_click` (CSS版) | 在 description 中标记 `@deprecated`，指向新版 |
| `browser_fill` (CSS版) | 在 description 中标记 `@deprecated`，指向新版 |

### Phase 2: 增强与正式废弃 (P1) ✅ 已完成

#### 2.1 功能增强 ✅

| 功能 | 说明 | 状态 |
|------|------|------|
| PathTracker 集成 | 操作历史自动记录 | ✅ |
| 快照缓存优化 | 内容哈希 + TTL 缓存 | ✅ |
| Embedding 语义匹配 | sentence-transformers 支持 | ✅ |

#### 2.2 旧工具正式废弃 ✅

| 旧工具 | 处理方式 |
|--------|----------|
| `browser_click` (CSS版) | 从 `create_browser_tools()` 移除，仅保留语义版 |
| `browser_fill` (CSS版) | 从 `create_browser_tools()` 移除，仅保留语义版 |

**实现文件**：
- `src/tools/browser/tools_path.py` - PathTracker 操作历史记录
- `src/tools/browser/semantic/snapshot_generator.py` - 快照缓存（`SnapshotCache` 类）
- `src/tools/browser/semantic/natural_matcher.py` - Embedding 语义匹配（`_get_embedding` 方法）

### Phase 3: 高级功能 (P2) ✅ 已完成

#### 3.1 高级特性
- `browser_backtrack` 状态回溯 ✅ 已实现
- 动态子菜单实时检测 ✅ 已优化（MutationObserver + 智能等待）
- iframe 嵌套处理 ✅ 已实现（IFrameHandler + 递归内容提取）

#### 3.2 清理工作
- [x] 删除 CSS 选择器版本从 `create_browser_tools()`
- [x] 更新 `__init__.py` 导出
- [x] 更新测试用例（`test_browser_semantic.py`）
- [x] 更新设计文档

### Phase 1 时间估算

| 任务 | 预估工时 |
|------|----------|
| SemanticSnapshotGenerator 实现 | 4-6 小时 |
| browser_snapshot 工具 | 2-3 小时 |
| browser_click/browse_fill/browse_select 工具 | 3-4 小时 |
| browser_find 工具 | 2-3 小时 |
| 自然语言匹配引擎 | 3-4 小时 |
| 测试与调试 | 4-6 小时 |
| **Phase 1 合计** | **18-26 小时** |

### 迁移指南

#### 从旧工具迁移到新工具

**旧代码**：
```python
# ❌ 旧方式 - 已废弃
await BrowserClickTool().execute(selector="#submit-btn")
await BrowserFillTool().execute(selector="input[name='amount']", value="1500")
```

**新代码**：
```python
# ✅ 新方式 - 语义驱动
await BrowserClickTool().execute(description="提交按钮")
await BrowserFillTool().execute(field="报销金额", value="1500")
```

#### Agent 迁移检查清单

- [ ] `browser_open` 后是否立即调用 `browser_snapshot`
- [ ] 是否使用自然语言描述替代 CSS 选择器
- [ ] 页面变化后是否重新调用 `browser_snapshot`
- [ ] 是否移除了所有 `selector=` 参数

---

## 8. 文件结构

```
src/tools/browser/
├── __init__.py                      # ✅ 工具导出（已更新）
├── browser_tool.py                  # ✅ 基础工具
├── semantic/
│   ├── __init__.py                  # ✅ 包含 iframe_handler 导出
│   ├── snapshot_generator.py        # ✅ 语义快照生成器（已添加缓存 + iframe 检测）
│   ├── element_classifier.py        # ✅ 元素分类器
│   ├── semantic_tagger.py           # ✅ 语义标签生成器
│   ├── submenu_detector.py         # ✅ 子菜单检测器（已优化：MutationObserver）
│   ├── natural_matcher.py           # ✅ 自然语言匹配引擎（已添加Embedding）
│   ├── ref_mapper.py                # ✅ ref 映射器
│   └── iframe_handler.py            # ✅ iframe 嵌套处理器（Phase 3 新增）
├── tools_snapshot.py                # ✅ browser_snapshot 工具
├── tools_semantic.py                # ✅ browser_click/fill/select 语义工具（已集成PathTracker）
├── tools_find.py                    # ✅ browser_find 工具
├── tools_path.py                    # ✅ PathTracker + browser_get_path/backtrack
└── README.md                        # 待创建：完整使用文档
```

**关键变更（Phase 3）**：

1. **iframe 嵌套处理** (`iframe_handler.py`)
   - 新增 `IFrameHandler` 类
   - 新增 `get_page_iframes()` 快捷函数
   - 支持同源 iframe 内容递归提取
   - 在快照中返回 iframe 列表及其内部元素

2. **动态子菜单检测优化** (`submenu_detector.py`)
   - 新增 `wait_for_submenu_appearance()` 方法
   - 使用 MutationObserver 替代固定 `asyncio.sleep()`
   - 智能等待子菜单出现，超时可配置

3. **快照生成器更新** (`snapshot_generator.py`)
   - 新增 `_detect_iframes()` 方法
   - `SemanticSnapshot` 新增 `iframe_snapshots` 字段
   - 集成 iframe_handler 进行 iframe 检测

---

## 9. 附录：OpenClaw 实现参考

### 9.1 OpenClaw 核心命令

| 命令 | 功能 |
|------|------|
| `browser snapshot` | 获取 AI 快照（aria-ref 数字引用） |
| `browser snapshot --interactive` | 获取交互快照（ARIA 角色引用） |
| `browser snapshot --compact` | 紧凑输出 |
| `browser snapshot --depth N` | 调整遍历深度 |
| `browser click <ref>` | 点击指定引用 |
| `browser type <ref> <text>` | 输入文本 |
| `browser highlight <ref>` | 高亮显示元素 |
| `browser wait <selector>` | 等待元素出现 |

### 9.2 OpenClaw 快照输出示例

```
# AI 快照（数字 ref）
Page: 12 elements
  [1] Link: "首页" -> /
  [2] Link: "财务管理" -> /finance (has popup)
    [2.1] Link: "报销申请" -> /finance/expense
    [2.2] Link: "付款申请" -> /finance/payment
  [3] Form: "登录表单"
    [3.1] Input: "用户名" [text]
    [3.2] Input: "密码" [password]
    [3.3] Button: "登录" [submit]

# 交互快照（角色 ref）
e1: link "首页" href="/"
e2: link "财务管理" has_popup=true
e3: textbox "用户名"
e4: passwordbox "密码"
e5: button "登录"
```

---

## 10. 变更日志

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-03-31 | v1.0 | 初始设计文档创建 |
| 2026-03-31 | v1.1 | 补充旧工具废弃计划，明确 CSS 选择器工具删除时间表 |
| 2026-03-31 | v1.2 | 新增自然语言驱动设计，定义 browser_click/fill/select 的语义接口 |
| 2026-03-31 | v1.3 | 完善实现计划，增加迁移指南和检查清单 |
| 2026-04-01 | v1.4 | Phase 2 实现完成：PathTracker 操作历史集成、快照缓存优化、Embedding 语义匹配增强、旧工具正式标记 deprecated |
| 2026-04-01 | v1.5 | Phase 2 清理完成：从 `create_browser_tools()` 移除 CSS 选择器版本工具 |
| 2026-04-01 | v1.6 | Phase 3 完成：动态子菜单实时检测优化（MutationObserver）、iframe 嵌套处理实现、测试用例更新 |

