# Browser 工具使用文档

## 概述

Browser工具是基于Playwright实现的浏览器自动化工具集，允许智能体理解并操作网页，完成打开网页、点击元素、填写表单、收集信息等任务。

## 安装依赖

```bash
# 安装Playwright
pip install playwright

# 安装浏览器驱动
python -m playwright install
```

## 工具列表

### 1. browser_open - 打开网页

**功能**: 打开指定URL的网页

**参数**:
- `url` (必需): 网页URL，必须以http://或https://开头
- `session_id` (可选): 浏览器会话ID，用于管理多个会话，默认为'default'
- `headless` (可选): 是否无头模式运行，默认true
- `wait_for` (可选): 等待页面加载完成的策略，默认load

**示例**:
```python
await browser_open(
    url="https://www.baidu.com",
    session_id="my_session",
    headless=False
)
```

### 2. browser_click - 点击元素

**功能**: 点击网页中的指定元素（按钮、链接等）

**参数**:
- `selector` (必需): CSS选择器，如'.submit-btn', '#submit'
- `session_id` (可选): 浏览器会话ID
- `timeout` (可选): 等待元素出现的超时时间（毫秒），默认10000

**示例**:
```python
await browser_click(
    selector="button[type='submit']",
    session_id="my_session"
)
```

### 3. browser_fill - 填写表单

**功能**: 填写网页表单中的输入框、文本域等元素

**参数**:
- `selector` (必需): CSS选择器
- `value` (必需): 要填写的值
- `session_id` (可选): 浏览器会话ID
- `timeout` (可选): 超时时间（毫秒），默认10000

**示例**:
```python
await browser_fill(
    selector="input[name='username']",
    value="my_username",
    session_id="my_session"
)
```

### 4. browser_get_content - 获取页面内容

**功能**: 获取网页的文本内容、HTML结构或特定元素的内容

**参数**:
- `selector` (可选): CSS选择器，如果为空则获取整个页面的内容
- `format` (可选): 返回格式（text/html/markdown），默认text
- `session_id` (可选): 浏览器会话ID

**示例**:
```python
# 获取整个页面的文本
await browser_get_content(
    format="text",
    session_id="my_session"
)

# 获取特定元素的HTML
await browser_get_content(
    selector="#main-content",
    format="html",
    session_id="my_session"
)
```

### 5. browser_navigate - 页面导航

**功能**: 在当前页面进行导航操作：前进、后退、刷新

**参数**:
- `action` (必需): 导航动作（back/forward/reload）
- `session_id` (可选): 浏览器会话ID

**示例**:
```python
await browser_navigate(
    action="back",
    session_id="my_session"
)
```

### 6. browser_close - 关闭浏览器

**功能**: 关闭浏览器或特定会话

**参数**:
- `session_id` (可选): 要关闭的会话ID，如果为空则关闭所有会话

**示例**:
```python
# 关闭指定会话
await browser_close(session_id="my_session")

# 关闭所有会话
await browser_close()
```

### 7. browser_screenshot - 网页截图

**功能**: 对当前网页进行截图并保存

**参数**:
- `path` (可选): 截图保存路径，默认'./screenshot.png'
- `session_id` (可选): 浏览器会话ID
- `full_page` (可选): 是否截取整个页面，默认false

**示例**:
```python
await browser_screenshot(
    path="./screenshot.png",
    full_page=True,
    session_id="my_session"
)
```

## 使用场景

### 场景1: 网页信息收集

```python
# 1. 打开网页
await browser_open(url="https://news.ycombinator.com")

# 2. 获取页面内容
result = await browser_get_content(format="text")
content = result["content"]

# 3. 解析并提取信息
# 智能体可以根据需求处理content内容

# 4. 关闭浏览器
await browser_close()
```

### 场景2: 网页表单填写

```python
# 1. 打开登录页面
await browser_open(url="https://example.com/login")

# 2. 填写用户名
await browser_fill(
    selector="input[name='username']",
    value="my_username"
)

# 3. 填写密码
await browser_fill(
    selector="input[name='password']",
    value="my_password"
)

# 4. 点击登录按钮
await browser_click(selector="button[type='submit']")

# 5. 等待并获取结果
await asyncio.sleep(2)
result = await browser_get_content(format="text")
```

### 场景3: 网页自动化任务

```python
# 1. 打开起始页面
await browser_open(url="https://example.com")

# 2. 点击导航到特定页面
await browser_click(selector="a[href='/products']")

# 3. 等待页面加载
await asyncio.sleep(1)

# 4. 截图保存当前状态
await browser_screenshot(path="./products.png")

# 5. 获取产品列表信息
result = await browser_get_content(
    selector=".product-list",
    format="text"
)
```

## CSS选择器技巧

### 基本选择器
- `#id`: 通过ID选择
- `.class`: 通过类名选择
- `tag`: 通过标签名选择

### 属性选择器
- `[attribute]`: 包含指定属性
- `[attribute='value']`: 属性等于指定值
- `input[type='text']`: 类型为text的输入框

### 组合选择器
- `div.container p`: 容器内的所有p标签
- `div > p`: 直接子元素的p标签

### 伪类选择器
- `:first-child`: 第一个子元素
- `:last-child`: 最后一个子元素
- `:nth-child(n)`: 第n个子元素

## 注意事项

1. **会话管理**: 使用不同的session_id可以同时运行多个浏览器会话
2. **等待策略**: 对于动态加载的网页，建议使用`wait_for="networkidle"`确保内容完全加载
3. **超时设置**: 根据网页加载速度合理设置timeout参数
4. **选择器准确性**: 使用浏览器开发者工具检查元素的选择器
5. **错误处理**: 工具执行失败时会返回错误信息，需要适当处理
6. **资源清理**: 完成任务后记得关闭浏览器释放资源

## 测试

运行测试文件验证工具功能：

```bash
python test_browser_tool.py
```

## 常见问题

### Q: 如何处理需要登录的网页？
A: 使用browser_fill填写用户名密码，然后使用browser_click点击登录按钮。

### Q: 如何处理弹窗？
A: Playwright会自动处理大部分弹窗。如果需要特殊处理，可以在browser_get_content中获取弹窗内容。

### Q: 如何获取页面的所有链接？
A: 使用browser_get_content获取HTML，然后解析提取所有`<a>`标签的href属性。

### Q: 无头模式和有头模式有什么区别？
A: 无头模式（headless=true）不显示浏览器窗口，适合服务器环境；有头模式可以看到浏览器操作过程，适合调试。

## 未来扩展

- 支持文件上传/下载
- 支持JavaScript执行
- 支持多标签页管理
- 支持Cookie管理
- 支持代理配置
