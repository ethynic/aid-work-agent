# 文件工具使用指南

## 概述

本指南介绍如何使用 AID Work Agent 中的文件读取工具。该工具提供通用的文本文件读取功能，支持自动编码检测，适用于各种文本文件格式。

## 工具列表

### 1. file_read - 文件读取工具

读取文本文件的内容，支持自动检测文件编码。

#### 功能特性

- ✅ 自动检测文件编码（UTF-8、GBK、GB2312等）
- ✅ 支持指定编码读取
- ✅ 支持读取指定行范围
- ✅ 文件大小限制保护（默认10MB）
- ✅ 错误处理和友好提示

#### 参数说明

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| file_path | string | 是 | 要读取的文件路径（绝对或相对路径） |
| encoding | string | 否 | 文件编码，不指定则自动检测 |
| start_line | integer | 否 | 起始行号，默认为1 |
| end_line | integer | 否 | 结束行号，默认读取到文件末尾 |
| max_size | integer | 否 | 最大读取字节数，默认10MB |

#### 使用示例

**示例1：读取整个文件**

```python
# LLM 会调用：
file_read(file_path="README.md")
```

**示例2：指定编码读取**

```python
file_read(
    file_path="data.txt",
    encoding="gbk"
)
```

**示例3：读取部分行**

```python
file_read(
    file_path="large_file.txt",
    start_line=10,
    end_line=50
)
```

**示例4：限制读取大小**

```python
file_read(
    file_path="big_file.log",
    max_size=1048576  # 1MB
)
```

#### 返回结果

```json
{
    "success": true,
    "message": "成功读取文件内容",
    "file_path": "/path/to/file.txt",
    "encoding": "utf-8",
    "total_lines": 100,
    "read_lines": 100,
    "start_line": 1,
    "end_line": 100,
    "file_size": 2048,
    "content": "文件内容..."
}
```

### 2. file_list - 文件列表工具

列出指定目录下的文件和子目录。

#### 功能特性

- ✅ 支持通配符模式匹配
- ✅ 支持递归遍历
- ✅ 可选择是否显示隐藏文件
- ✅ 返回文件详细信息（大小、修改时间等）

#### 参数说明

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| directory | string | 否 | 要列出的目录路径，默认当前目录 |
| pattern | string | 否 | 文件名匹配模式，支持通配符（*.py, *.txt等） |
| recursive | boolean | 否 | 是否递归遍历子目录，默认False |
| show_hidden | boolean | 否 | 是否显示隐藏文件，默认False |

#### 使用示例

**示例1：列出当前目录**

```python
file_list(directory=".")
```

**示例2：查找Python文件**

```python
file_list(
    directory="src",
    pattern="*.py"
)
```

**示例3：递归查找所有文件**

```python
file_list(
    directory="project",
    pattern="*",
    recursive=True
)
```

**示例4：显示隐藏文件**

```python
file_list(
    directory=".",
    show_hidden=True
)
```

#### 返回结果

```json
{
    "success": true,
    "message": "成功列出目录内容",
    "directory": "/path/to/dir",
    "files": [
        {
            "name": "file.txt",
            "path": "/path/to/dir/file.txt",
            "size": 1024,
            "modified": 1703001234.567
        }
    ],
    "directories": [
        {
            "name": "subdir",
            "path": "/path/to/dir/subdir"
        }
    ],
    "file_count": 1,
    "dir_count": 1
}
```

## 编码检测机制

文件读取工具使用多种策略自动检测文件编码：

### 检测优先级

1. **BOM标记检测**
   - UTF-8 BOM: `\xef\xbb\xbf`
   - UTF-16 LE BOM: `\xff\xfe`
   - UTF-16 BE BOM: `\xfe\xff`

2. **chardet库检测**（如果已安装）
   - 置信度 > 0.7 时使用

3. **启发式检测**
   - 尝试UTF-8解码
   - 尝试GBK解码（检查中文字符）

4. **编码列表遍历**
   - utf-8
   - gbk
   - gb2312
   - gb18030
   - utf-16
   - utf-16-le
   - utf-16-be
   - ascii
   - latin-1
   - cp1252

### 推荐安装

为了更好的编码检测效果，建议安装 `chardet` 库：

```bash
pip install chardet
```

## 错误处理

工具会返回详细的错误信息：

- **文件不存在**: `文件不存在: /path/to/file`
- **路径不是文件**: `路径不是文件: /path/to/dir`
- **文件过大**: `文件过大 (X 字节)，超过最大限制 Y 字节`
- **编码错误**: 会尝试其他编码或使用替换字符

## 最佳实践

### 1. 读取大文件

对于大文件，建议使用行范围限制：

```python
# 分批读取
file_read(file_path="large.log", start_line=1, end_line=100)
file_read(file_path="large.log", start_line=101, end_line=200)
```

### 2. 处理未知编码

优先使用自动检测，仅在检测失败时指定编码：

```python
# 先尝试自动检测
result = file_read(file_path="unknown.txt")
if not result["success"]:
    # 失败时指定编码
    file_read(file_path="unknown.txt", encoding="gbk")
```

### 3. 查找特定文件

结合 file_list 和 file_read：

```python
# 1. 先列出文件
files = file_list(directory="docs", pattern="*.md")
# 2. 逐个读取
for file in files["files"]:
    content = file_read(file_path=file["path"])
```

## 与智能体集成

文件工具已注册到智能体工具注册表，LLM 可以自动调用：

**用户**: "帮我读取 README.md 文件"

**智能体**: 会自动调用 `file_read` 工具并返回内容

**用户**: "列出 src 目录下的所有 Python 文件"

**智能体**: 会自动调用 `file_list` 工具并返回文件列表

## 注意事项

1. **文件大小限制**: 默认最大10MB，可通过 `max_size` 参数调整
2. **路径处理**: 支持绝对路径和相对路径，会自动从项目根目录查找
3. **编码兼容性**: 自动检测适用于大多数场景，特殊编码建议手动指定
4. **性能考虑**: 递归遍历大量文件时可能耗时较长

## 技术实现

### 文件位置

- 工具实现: `src/tools/file/file_reader_tool.py`
- 工具注册: `src/core/agent.py` 中的 `_register_builtin_tools` 方法

### 扩展开发

如需扩展文件工具功能，可以继承 `BaseTool` 类：

```python
from src.tools.base import BaseTool

class MyFileTool(BaseTool):
    name = "my_file_tool"
    description = "自定义文件工具"
    category = "file"
    parameters_schema = { ... }
    
    async def execute(self, **kwargs):
        # 实现逻辑
        return {"success": True, ...}
```

## 常见问题

**Q: 为什么读取的文件内容是乱码？**

A: 可能是编码检测失败。尝试手动指定编码：
```python
file_read(file_path="file.txt", encoding="gbk")
```

**Q: 如何读取二进制文件？**

A: 当前工具仅支持文本文件。二进制文件需要开发新的工具。

**Q: 文件路径支持哪些格式？**

A: 支持绝对路径和相对路径，会自动从项目根目录查找相对路径文件。

**Q: 如何处理超大文件？**

A: 使用 `start_line` 和 `end_line` 参数分批读取，或增大 `max_size` 参数。

## 更新日志

### v1.0.0 (2026-03-17)
- ✨ 新增 file_read 工具
- ✨ 新增 file_list 工具
- ✨ 支持自动编码检测
- ✨ 支持行范围读取
- ✨ 支持文件大小限制
- ✨ 支持通配符匹配和递归遍历
