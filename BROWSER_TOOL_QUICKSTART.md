# Browser工具快速开始指南

## 前言

Browser工具为AID Work Agent添加了强大的网页自动化能力，使智能体能够理解并操作网页，完成打开网页、点击元素、填写表单、收集信息等任务。

## 快速开始

### 1. 安装依赖

```bash
# 安装Playwright
pip install playwright

# 安装浏览器驱动（必需）
python -m playwright install
```

### 2. 基本使用

```python
import asyncio
from src.tools.browser import (
    BrowserOpenTool,
    BrowserFillTool,
    BrowserClickTool,
    BrowserGetContentTool,
    BrowserCloseTool
)

async def main():
    # 1. 打开网页
    open_tool = BrowserOpenTool()
    result = await open_tool.execute(
        url="https://www.baidu.com",
        headless=True  # 无头模式，不显示浏览器窗口
    )
    print(f"已打开: {result['title']}")

    # 2. 填写搜索框
    fill_tool = BrowserFillTool()
    result = await fill_tool.execute(
        selector="#kw",
        value="Python Playwright"
    )
    print(f"已填写: {result['selector']}")

    # 3. 点击搜索按钮
    click_tool = BrowserClickTool()
    result = await click_tool.execute(
        selector="#su"
    )
    print("已点击搜索按钮")

    # 4. 获取搜索结果
    await asyncio.sleep(2)  # 等待结果加载
    get_tool = BrowserGetContentTool()
    result = await get_tool.execute(
        selector="#content_left",
        format="text"
    )
    print(f"搜索结果: {result['content'][:500]}")

    # 5. 关闭浏览器
    close_tool = BrowserCloseTool()
    await close_tool.execute()

asyncio.run(main())
```

## 工具列表

### 1. browser_open - 打开网页

```python
await BrowserOpenTool().execute(
    url="https://www.example.com",
    session_id="my_session",  # 可选，默认'default'
    headless=True,  # 可选，默认true
    wait_for="load"  # 可选：load/domcontentloaded/networkidle/commit
)
```

### 2. browser_click - 点击元素

```python
await BrowserClickTool().execute(
    selector="button[type='submit']",
    session_id="my_session",
    timeout=10000  # 可选，超时时间（毫秒）
)
```

### 3. browser_fill - 填写表单

```python
await BrowserFillTool().execute(
    selector="input[name='username']",
    value="my_username",
    session_id="my_session",
    timeout=10000
)
```

### 4. browser_get_content - 获取页面内容

```python
# 获取整个页面的文本
result = await BrowserGetContentTool().execute(
    format="text",  # text/html/markdown
    session_id="my_session"
)

# 获取特定元素的HTML
result = await BrowserGetContentTool().execute(
    selector="#main-content",
    format="html",
    session_id="my_session"
)
```

### 5. browser_navigate - 页面导航

```python
# 后退
await BrowserNavigateTool().execute(action="back")

# 前进
await BrowserNavigateTool().execute(action="forward")

# 刷新
await BrowserNavigateTool().execute(action="reload")
```

### 6. browser_screenshot - 网页截图

```python
from src.tools.browser import BrowserScreenshotTool

# 截取当前可视区域
await BrowserScreenshotTool().execute(
    path="./screenshot.png",
    session_id="my_session",
    full_page=False
)

# 截取整个页面
await BrowserScreenshotTool().execute(
    path="./full_page.png",
    session_id="my_session",
    full_page=True
)
```

### 7. browser_close - 关闭浏览器

```python
# 关闭指定会话
await BrowserCloseTool().execute(session_id="my_session")

# 关闭所有会话
await BrowserCloseTool().execute()
```

## 常见使用场景

### 场景1: 自动登录

```python
async def auto_login():
    # 打开登录页面
    await BrowserOpenTool().execute(url="https://example.com/login")
    
    # 填写用户名
    await BrowserFillTool().execute(
        selector="input[name='username']",
        value="my_username"
    )
    
    # 填写密码
    await BrowserFillTool().execute(
        selector="input[name='password']",
        value="my_password"
    )
    
    # 点击登录按钮
    await BrowserClickTool().execute(
        selector="button[type='submit']"
    )
    
    # 等待登录完成
    await asyncio.sleep(3)
    
    # 获取登录后的页面
    result = await BrowserGetContentTool().execute(format="text")
    print(result['content'])
```

### 场景2: 网页信息收集

```python
async def collect_news():
    # 打开新闻网站
    await BrowserOpenTool().execute(
        url="https://news.ycombinator.com"
    )
    
    # 获取新闻列表
    result = await BrowserGetContentTool().execute(
        selector="tr.athing",
        format="text"
    )
    
    # 处理新闻内容
    news_content = result['content']
    print(f"获取到 {result['content_length']} 字符的新闻内容")
    
    # 智能体可以进一步分析和提取信息
    # ...
```

### 场景3: 表单自动填写

```python
async def fill_form():
    # 打开表单页面
    await BrowserOpenTool().execute(
        url="https://example.com/form"
    )
    
    # 填写多个字段
    form_data = {
        "input[name='name']": "张三",
        "input[name='email']": "zhangsan@example.com",
        "input[name='phone']": "13800138000",
    }
    
    for selector, value in form_data.items():
        await BrowserFillTool().execute(
            selector=selector,
            value=value
        )
    
    # 提交表单
    await BrowserClickTool().execute(
        selector="button[type='submit']"
    )
```

### 场景4: 多页面操作

```python
async def multi_page_workflow():
    # 打开第一个页面
    await BrowserOpenTool().execute(
        url="https://www.example.com"
    )
    
    # 获取第一个页面信息
    result = await BrowserGetContentTool().execute(format="text")
    print(f"页面1: {result['content'][:200]}")
    
    # 点击链接跳转
    await BrowserClickTool().execute(selector="a[href='/page2']")
    
    # 获取第二个页面信息
    result = await BrowserGetContentTool().execute(format="text")
    print(f"页面2: {result['content'][:200]}")
    
    # 返回第一个页面
    await BrowserNavigateTool().execute(action="back")
```

## CSS选择器技巧

### 基本选择器
```python
# 通过ID选择
selector="#username"

# 通过类名选择
selector=".submit-btn"

# 通过标签名选择
selector="button"
```

### 属性选择器
```python
# 属性等于指定值
selector="input[type='text']"

# 属性以指定值开头
selector="a[href^='https']"

# 属性包含指定值
selector="div[class*='container']"
```

### 组合选择器
```python
# 容器内的元素
selector="div.container p"

# 直接子元素
selector="div > p"

# 多条件选择
selector="button.submit-btn[type='submit']"
```

### 伪类选择器
```python
# 第一个子元素
selector="li:first-child"

# 最后一个子元素
selector="li:last-child"

# 第n个子元素
selector="li:nth-child(3)"
```

## 重要提示

### 1. 会话管理
- 使用不同的`session_id`可以同时运行多个浏览器会话
- 相同的`session_id`会复用已打开的浏览器
- 完成任务后记得关闭浏览器释放资源

### 2. 等待策略
- `load`: 等待load事件触发（默认）
- `domcontentloaded`: 等待DOM加载完成
- `networkidle`: 等待网络空闲（适合动态网页）
- `commit`: 等待文档提交

### 3. 无头模式
- `headless=True`: 不显示浏览器窗口（适合服务器环境）
- `headless=False`: 显示浏览器窗口（适合调试）

### 4. 超时设置
- 默认超时时间为10000毫秒（10秒）
- 根据网页加载速度合理调整

### 5. 内容格式
- `text`: 纯文本，适合LLM分析
- `html`: HTML源码，适合解析
- `markdown`: Markdown格式，适合文档生成

## 集成到智能体

### 注册工具

```python
from src.tools.browser import create_browser_tools
from src.tools import tool_registry

# 创建并注册浏览器工具
browser_tools = create_browser_tools()
for tool in browser_tools:
    tool_registry.register(tool)
```

### 在智能体中使用

智能体现在可以通过工具调用自动完成以下任务：

1. **网页信息收集**
   - 打开指定网页
   - 获取页面内容
   - 提取关键信息

2. **网页操作**
   - 点击按钮和链接
   - 填写表单
   - 提交数据

3. **多页面工作流**
   - 浏览多个页面
   - 收集和整理信息
   - 返回结果给用户

## 测试

运行测试文件验证工具功能：

```bash
python test_browser_tool.py
```

运行示例文件查看使用示例：

```bash
python browser_tool_examples.py
```

## 故障排除

### 问题1: 未安装浏览器驱动
```
错误: 未安装Playwright，请运行: pip install playwright && python -m playwright install
```
解决方法:
```bash
pip install playwright
python -m playwright install
```

### 问题2: 元素未找到
```
错误: 点击元素失败: Timeout exceeded
```
解决方法:
- 使用浏览器开发者工具检查元素选择器
- 增加timeout参数的值
- 确保元素已加载（增加等待时间）

### 问题3: 网页加载超时
```
错误: 打开网页失败: Timeout exceeded
```
解决方法:
- 增加wait_for时间或改为'networkidle'
- 检查网络连接
- 尝试使用其他URL

## 下一步

- 查看完整文档: `src/tools/browser/README.md`
- 查看实现总结: `docs/browser_tool_implementation.md`
- 运行测试和示例: `test_browser_tool.py`, `browser_tool_examples.py`

## 总结

Browser工具为AID Work Agent提供了强大的网页自动化能力，使智能体能够：

✅ 理解和操作网页
✅ 代替用户完成网页任务
✅ 自动化信息收集和处理
✅ 支持复杂的网页交互

开始使用Browser工具，让智能体更加强大！
