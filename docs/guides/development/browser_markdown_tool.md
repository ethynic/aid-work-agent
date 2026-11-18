# Browser Markdown 工具使用说明（整合版）

## 功能概述

已将Markdown获取功能整合到 `browser_get_content` 工具中，**默认返回Markdown格式**，并返回最终的URL和内容给工具调用方。

## 工具说明

### browser_get_content

**描述**: 获取网页的内容，支持多种格式输出（**默认Markdown格式**）。可获取整个页面或特定元素的内容，并返回最终URL

**参数**:
- `selector` (可选): CSS选择器，如果为空则获取整个页面的内容
- `format` (可选): 返回格式，**默认markdown**
  - `markdown`: Markdown格式（默认）
  - `text`: 纯文本
  - `html`: HTML源码
- `session_id` (可选): 浏览器会话ID，默认为'default'
- `clean_content` (可选): 是否清理内容（移除导航、广告等非正文内容），仅在format为markdown时有效，默认true

**返回值**:
```json
{
  "success": true,
  "message": "成功获取页面内容",
  "url": "https://example.com/",       // 最终URL（可能经过重定向）
  "title": "Example Domain",           // 页面标题
  "format": "markdown",                // 内容格式
  "content": "# Example Domain...",    // 内容（markdown/text/html）
  "markdown": "# Example Domain...",   // markdown格式时的兼容字段
  "content_length": 167,               // 内容长度（字符数）
  "truncated": false                   // 是否被截断
}
```

## 使用示例

### 基本用法（默认Markdown）

```python
from src.tools.browser.browser_tool import (
    BrowserOpenTool,
    BrowserGetContentTool,
    BrowserCloseTool
)

async def get_page_content():
    # 打开网页
    open_tool = BrowserOpenTool()
    await open_tool.execute(url="https://www.example.com")
    
    # 获取内容（默认markdown格式）
    content_tool = BrowserGetContentTool()
    result = await content_tool.execute()  # 不指定format，默认markdown
    
    if result["success"]:
        print(f"最终URL: {result['url']}")
        print(f"Markdown内容:\n{result['content']}")
    
    # 关闭浏览器
    close_tool = BrowserCloseTool()
    await close_tool.execute()
```

### 指定其他格式

```python
# 获取纯文本
result = await content_tool.execute(format="text")

# 获取HTML
result = await content_tool.execute(format="html")
```

### 使用选择器提取特定内容

```python
result = await content_tool.execute(
    selector=".article-content",
    clean_content=True  # 清理内容
)
```

## 技术特性

### 1. 默认Markdown格式

不再需要手动指定format参数，默认就返回高质量的Markdown内容：
- 标题（h1-h6）
- 链接（转换为 `[text](url)` 格式）
- 粗体、斜体
- 列表（有序和无序）
- 代码块和行内代码
- 段落和换行

### 2. 智能内容提取

当 `clean_content=true` 时（默认），工具会自动尝试识别页面的主要内容区域，按以下优先级查找：

1. `<article>` 标签
2. `[role="main"]` 属性
3. `<main>` 标签
4. `.post-content`, `.article-content` 等常见内容类
5. `.content`, `#content`
6. `.post`, `.article`

如果找不到主要内容区域，则返回整个页面的body内容。

### 3. 高质量转换

使用 `markdownify` 库进行专业的HTML到Markdown转换，自动移除不影响内容的标签：
- `script`, `style` - 脚本和样式
- `nav`, `footer`, `header`, `aside` - 导航和页脚

### 4. 最终URL追踪

自动处理URL重定向，返回最终URL：

```python
{
  "url": "https://example.com/"  # 最终URL（可能经过重定向）
}
```

### 5. 性能优化

- 限制内容长度（markdown格式50000字符，其他格式10000字符）
- 自动清理多余空行
- 内容过长时自动截断并提示

## 依赖项

已添加到 `requirements.txt`:

```txt
playwright>=1.40.0  # Browser automation framework
markdownify>=0.11.6  # HTML to Markdown conversion
```

安装依赖：

```bash
# 在venv环境中安装
.\venv\Scripts\pip install markdownify
```

## 工作流程

### 标准工作流程

```python
# 1. 打开浏览器
open_tool = BrowserOpenTool()
await open_tool.execute(url="https://example.com")

# 2. 获取内容（默认markdown）
content_tool = BrowserGetContentTool()
result = await content_tool.execute()

# 3. 处理内容
if result["success"]:
    print(f"URL: {result['url']}")
    print(f"内容: {result['content']}")

# 4. 关闭浏览器
close_tool = BrowserCloseTool()
await close_tool.execute()
```

### 多步骤操作流程

```python
# 适用于需要多步操作的场景
# 1. 打开网页
await open_tool.execute(url="https://example.com")

# 2. 点击按钮或填写表单
await click_tool.execute(selector=".submit-btn")

# 3. 获取新页面的内容
result = await content_tool.execute(format="markdown")

# 4. 继续其他操作...
```

## 最佳实践

### 1. 获取新闻文章内容

```python
await open_tool.execute(url="https://news-site.com/article/123")
result = await content_tool.execute(clean_content=True)
```

### 2. 获取技术文档

```python
await open_tool.execute(url="https://docs.python.org/3/library/asyncio.html")
result = await content_tool.execute(
    selector=".body-content",
    clean_content=True
)
```

### 3. 处理动态内容页面

```python
# 使用networkidle等待页面完全加载
await open_tool.execute(
    url="https://spa-website.com",
    wait_for="networkidle"
)
result = await content_tool.execute()
```

### 4. 提取特定区域内容

```python
await open_tool.execute(url="https://example.com")
result = await content_tool.execute(
    selector="#main-article",
    clean_content=False  # 指定了选择器，通常不需要再清理
)
```

## 错误处理

工具会返回详细的错误信息：

```python
{
  "success": false,
  "error": "获取内容失败: timeout exceeded"
}
```

常见错误：
- 浏览器未启动：需要先使用 `browser_open` 打开网页
- 会话不存在：检查 session_id 是否正确
- 选择器无效：CSS选择器语法错误或元素不存在
- 网络超时：页面加载超时

## 测试

运行测试文件验证功能：

```bash
.\venv\Scripts\python test_browser_markdown.py
```

测试覆盖：
- ✅ 默认markdown格式获取
- ✅ 最终URL返回
- ✅ 智能内容提取
- ✅ 内容清理功能
- ✅ 选择器使用
- ✅ 多种格式切换

## 更新日志

### v2.0（当前版本）
- ✨ 整合Markdown获取功能到 `browser_get_content` 工具
- ✨ 默认返回markdown格式（无需手动指定）
- ✨ 新增智能内容提取（自动识别主要内容区域）
- ✨ 新增内容清理功能（移除导航、广告等）
- ✨ 返回最终URL（支持重定向追踪）
- 🔧 使用markdownify库替代简单正则转换
- 🔧 提升内容限制（markdown格式50000字符）
- 📝 完善文档和测试

### v1.0
- 初始版本，支持基本的text/html/markdown格式
