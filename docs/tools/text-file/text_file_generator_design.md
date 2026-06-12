# 文本文件生成工具设计文档

> ⚠️ **本文档已被取代**。`file_write` 工具已重命名为 `write` 并重构，详见 [file_tools_redesign_v2.md](file_tools_redesign_v2.md)。
> 本文档仅作历史参考。

> 版本: v1.1 | 创建: 2026-05-19 | 状态: 待审核

## 1. 概述

新增 `file_write` 工具，支持 Agent 生成文本类文件（Markdown、HTML、TXT、CSV、JSON、XML、CSS、JS 等）。工具接收文本内容和目标文件路径，写入磁盘后自动注册到下载系统，返回可下载 URL。

**与现有工具的关系**：
- `file_read` — 读取文件（已有）
- `file_list` — 列出文件（已有）
- `file_write` — 生成文本文件（本工具）
- `register_download_file` — 注册到下载系统（已有，`file_write` 内部自动调用）

## 2. 现状分析

### 2.1 当前能力缺口

Agent 可以生成 Word（python-docx）、Excel（openpyxl）、PPT（python-pptx）、PDF（fpdf2/pandoc）等二进制格式文件，但无法直接生成文本类文件。常见场景：

| 场景 | 需要的文件格式 | 当前状态 |
|------|---------------|---------|
| 生成会议纪要 | Markdown (.md) | 需要手动拼路径再调 register_download_file |
| 生成 HTML 报告 | HTML (.html) | 同上 |
| 导出 CSV 数据 | CSV (.csv) | 同上 |
| 生成配置文件 | JSON / XML / YAML | 同上 |
| 生成 CSS / JS 代码文件 | .css / .js | 同上 |

**问题**：LLM 需要两步操作（写文件 → 注册下载），且写文件没有专用工具，只能通过临时方案处理。缺少一个统一的文本文件生成入口。

### 2.2 设计原则

1. **一个工具覆盖所有文本格式**：通过文件后缀名自动处理，不按格式拆分工具
2. **自动注册下载**：写完文件后自动注册到下载系统，LLM 不需要再调 `register_download_file`
3. **自动创建目录**：目标路径的父目录不存在时自动创建
4. **路径可选**：`file_path` 为可选参数，不提供时自动写入系统临时目录
5. **跨平台兼容**：路径处理同时支持 Windows（反斜杠 `C:\`）和 Linux（正斜杠 `/home/`），统一使用 `pathlib.Path` 避免平台差异
6. **安全限制**：限制可写的目录范围和文件大小

## 3. 功能设计

### 3.1 支持的文件类型

| 后缀 | 类型 | 说明 |
|------|------|------|
| `.md` | Markdown | Markdown 文档 |
| `.html` / `.htm` | HTML | HTML 页面 |
| `.txt` | 纯文本 | 无格式文本 |
| `.csv` | CSV | 逗号分隔值文件 |
| `.json` | JSON | JSON 数据文件 |
| `.xml` | XML | XML 文档 |
| `.yaml` / `.yml` | YAML | YAML 配置文件 |
| `.css` | CSS | 样式表 |
| `.js` | JavaScript | 脚本文件 |
| `.ts` | TypeScript | TypeScript 脚本 |
| `.py` | Python | Python 脚本 |
| `.sh` / `.bat` | Shell/Batch | 脚本文件 |
| `.sql` | SQL | SQL 脚本 |
| `.log` | Log | 日志文件 |
| `.ini` / `.cfg` / `.conf` | Config | 配置文件 |
| `.svg` | SVG | SVG 矢量图（本质是 XML 文本） |
| `.rtf` | RTF | 富文本（本质是文本格式） |

不在白名单中的后缀默认以纯文本写入，但记录 warning 日志。

### 3.2 工具接口

```python
class FileWriteInput(BaseModel):
    """文本文件生成参数"""
    content: str = Field(
        ...,
        description="文件内容文本。需符合对应文件类型的语法规范（如 Markdown、HTML、JSON 等）"
    )
    file_path: Optional[str] = Field(
        None,
        description="目标文件路径（含文件名和后缀），如 'output/report.md'。"
                    "支持绝对路径和相对路径，Windows 和 Linux 路径均可（如 'data\\report.md' 或 'data/report.md'）。"
                    "不提供时自动写入系统临时目录"
    )
    file_extension: Optional[str] = Field(
        None,
        description="文件后缀名（不含点号），如 'md'、'html'、'json'。"
                    "仅在 file_path 未提供时使用，默认 'txt'。"
                    "如果 file_path 已提供则忽略此参数"
    )
    encoding: Optional[str] = Field(
        None,
        description="文件编码，默认 UTF-8"
    )
    overwrite: Optional[bool] = Field(
        False,
        description="是否覆盖已存在的文件，默认 False（已存在时返回错误）"
    )
    register_download: Optional[bool] = Field(
        True,
        description="是否自动注册到下载系统（生成下载链接），默认 True"
    )
    display_name: Optional[str] = Field(
        None,
        description="注册下载时的显示文件名（可选），默认使用 file_path 中的文件名。"
                    "file_path 未提供时建议设置此参数，否则使用自动生成的临时文件名"
    )
```

**参数优先级说明**：

| 场景 | file_path | file_extension | 行为 |
|------|-----------|----------------|------|
| 指定完整路径 | `"output/report.md"` | 忽略 | 写入 `storage/output/report.md` |
| 仅指定后缀 | `None` | `"md"` | 写入临时文件 `tmpXXXXXX.md` |
| 都不指定 | `None` | `None` | 写入临时文件 `tmpXXXXXX.txt` |

**LLM 使用建议**：
- 需要控制文件名和路径时传 `file_path`
- 只关心文件格式、让系统决定存储位置时传 `file_extension`
- 两种方式都会自动注册下载，用户都能在前端下载

### 3.3 工具元数据

```python
class FileWriteTool(BaseTool):
    name = "file_write"
    description = """生成文本文件并注册到下载系统。支持 Markdown、HTML、TXT、CSV、JSON、XML、YAML、CSS、JS 等文本格式。
根据文件后缀名自动处理编码和格式。生成后自动创建下载链接，用户可在前端下载。
file_path 可选：不提供时自动写入系统临时目录并通过 file_extension 指定格式。
如果目标目录不存在会自动创建。默认不覆盖已存在的文件。支持 Windows 和 Linux 路径格式。"""
    display_name = "生成文本文件"
    category = "file"
    InputModel = FileWriteInput
```

### 3.4 核心处理流程

```
LLM 调用 file_write(content, file_path?, file_extension?, ...)
  │
  ├─ 1. 参数验证
  │     ├─ content 非空
  │     ├─ content 大小检查（上限 10MB）
  │     └─ file_path 提供时进行安全路径检查（禁止路径穿越）
  │
  ├─ 2. 路径解析（跨平台）
  │     ├─ file_path 已提供:
  │     │     ├─ 统一分隔符 → Path(file_path) 自动处理（Windows 反斜杠 / Linux 正斜杠）
  │     │     ├─ 禁止路径穿越（ ".." 检查）
  │     │     ├─ 相对路径解析到 storage/output 目录
  │     │     └─ 绝对路径直接使用
  │     │
  │     └─ file_path 未提供:
  │           ├─ 从 file_extension 获取后缀（默认 .txt）
  │           ├─ 调用 tempfile.mkstemp(suffix=ext) 生成临时文件
  │           └─ 临时文件在系统临时目录（Windows: %TEMP%, Linux: /tmp/）
  │
  ├─ 3. 后缀检查
  │     ├─ 禁止列表检查（.exe .bat 等）
  │     └─ 不在白名单中的后缀记录 warning
  │
  ├─ 4. 内容预处理（按后缀名）
  │     ├─ .json: 验证 JSON 合法性（json.loads 校验）
  │     ├─ .csv: 无特殊处理
  │     ├─ .html/.htm: 无特殊处理（LLM 保证语法）
  │     └─ 其他: 无特殊处理
  │
  ├─ 5. 写入文件
  │     ├─ 自动创建父目录（parents=True, exist_ok=True）
  │     ├─ 使用指定编码（默认 UTF-8）
  │     └─ 写入后验证文件确实存在
  │
  ├─ 6. 注册下载（register_download=True 时）
  │     ├─ 复制文件到上传目录（storage/uploads/{tenant}/{user}/）
  │     ├─ 注册到 Redis（带 24h TTL）
  │     └─ 返回 download_url
  │
  └─ 7. 返回结果
        └─ {success, file_path, file_size, download_url, ...}
```

## 4. 安全设计

### 4.1 跨平台路径安全

使用 `pathlib.Path` 统一处理路径，自动兼容 Windows（`\`）和 Linux（`/`）：

```python
import tempfile
import re
from pathlib import PurePosixPath, PureWindowsPath

def _resolve_and_validate_path(self, file_path: str) -> Path:
    """
    解析并验证文件路径，兼容 Windows 和 Linux。

    - pathlib.Path 自动处理平台分隔符差异
    - resolve() 消除符号链接和相对路径组件
    - 统一禁止路径穿越
    """
    # pathlib 在当前平台自动选择正确的路径解析器
    # Windows 输入 "data\\report.md" 或 "data/report.md" 都能正确解析
    # Linux 输入 "data/report.md" 正常解析
    p = Path(file_path)

    # 禁止路径穿越：检查 resolve 前后的路径组件
    # resolve() 前先检查原始字符串中的 ".."
    if ".." in p.parts:
        raise ValueError("文件路径不允许包含 '..'")

    # 相对路径解析到 storage/output 目录
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        p = project_root / "storage" / "output" / p

    # resolve() 消除任何残余的相对组件，获取绝对路径
    p = p.resolve()

    # 文件名安全检查（Windows 和 Linux 保留字符）
    filename = p.name
    # Windows 保留字符: < > : " | ? *
    # Linux 保留字符: / (已由 pathlib 处理) 和 null
    forbidden_chars = set('<>:"|?*\0')
    if any(c in forbidden_chars for c in filename):
        raise ValueError(f"文件名包含非法字符: {filename}")

    return p

def _generate_temp_path(self, file_extension: Optional[str]) -> Path:
    """
    生成临时文件路径。兼容 Windows (%TEMP%) 和 Linux (/tmp)。

    使用 tempfile.mkstemp 而非手动拼接路径，确保：
    - Windows 上使用 %TEMP% 或 %TMP% 环境变量
    - Linux 上使用 /tmp 或 $TMPDIR
    - 文件名不含冲突字符
    """
    ext = file_extension or "txt"
    # 规范化后缀：确保以点号开头
    if not ext.startswith('.'):
        ext = '.' + ext

    fd, temp_path = tempfile.mkstemp(suffix=ext, prefix="agent_")
    os.close(fd)  # 关闭文件描述符，后续用 write_text 写入

    return Path(temp_path)
```

### 4.2 内容大小限制

- 文本内容上限：**10 MB**（约 1000 万字符）
- 超出限制时返回 `{"success": False, "error": "文件内容过大，上限 10MB"}`

### 4.3 后缀白名单

不在白名单中的后缀仍可写入（作为纯文本），但记录 warning 日志。**以下后缀禁止写入**（安全黑名单）：

```python
FORBIDDEN_EXTENSIONS = {
    ".exe", ".msi", ".dll", ".bat", ".cmd", ".ps1",  # 可执行文件
    ".sh",                                           # Shell 脚本（Linux）
    ".py", ".pyc",                                   # Python 脚本（防止注入）
}
```

> 实际上 `.py` 和 `.sh` 在白名单中也有，需要根据部署环境决定。安全起见，Phase 1 先把 `.py`、`.sh`、`.bat` 从白名单移除，只保留纯数据/文档格式。

### 4.4 文件名安全

- 文件名仅允许：字母、数字、中文、`-`、`_`、`.`
- 禁止特殊字符：`/`、`\`、`:`、`*`、`?`、`"`、`<`、`>`、`|`

## 5. 详细设计

### 5.1 类结构

```python
import os
import re
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "..."
    display_name = "生成文本文件"
    category = "file"
    InputModel = FileWriteInput

    # 文本文件后缀白名单
    TEXT_EXTENSIONS = {
        '.md', '.markdown', '.mdx',
        '.html', '.htm',
        '.txt', '.text',
        '.csv', '.tsv',
        '.json', '.jsonl',
        '.xml', '.xsd', '.xsl',
        '.yaml', '.yml',
        '.css', '.scss', '.less',
        '.js', '.mjs',
        '.ts',
        '.sql',
        '.log',
        '.ini', '.cfg', '.conf', '.toml',
        '.svg',
        '.rtf',
    }

    # 禁止的后缀（可执行/脚本）
    FORBIDDEN_EXTENSIONS = {
        '.exe', '.msi', '.dll', '.com',
        '.bat', '.cmd', '.ps1',
        '.py', '.pyc', '.pyo',
        '.sh', '.bash', '.zsh',
        '.php', '.jsp', '.asp', '.aspx',
    }

    def __init__(self):
        self._user_id: Optional[str] = None
        self._tenant_id: Optional[str] = None

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_tenant_id(self, tenant_id: str):
        self._tenant_id = tenant_id

    def get_display_name(self, tool_args=None) -> str:
        base = self.display_name
        if tool_args:
            path = tool_args.get("file_path") or ""
            if path:
                filename = Path(path).name  # pathlib 跨平台取文件名
                return f"{base}「{filename}」"
        return base
```

### 5.2 execute 方法

```python
async def execute(self, **kwargs) -> Dict[str, Any]:
    content = kwargs.get("content", "")
    file_path = kwargs.get("file_path")
    file_extension = kwargs.get("file_extension")
    encoding = kwargs.get("encoding") or "utf-8"
    overwrite = kwargs.get("overwrite", False)
    register_download = kwargs.get("register_download", True)
    display_name = kwargs.get("display_name")

    # 1. 参数校验
    if not content:
        return {"success": False, "error": "未提供文件内容"}

    # 2. 内容大小检查
    content_size = len(content.encode(encoding))
    if content_size > 10 * 1024 * 1024:
        return {"success": False, "error": f"文件内容过大 ({content_size} 字节)，上限 10MB"}

    try:
        # 3. 路径解析
        if file_path:
            # 指定了文件路径：解析并验证
            path = self._resolve_and_validate_path(file_path)
        else:
            # 未指定路径：生成临时文件
            # tempfile.mkstemp 在 Windows 上用 %TEMP%，Linux 上用 /tmp
            ext = file_extension or "txt"
            if ext.startswith('.'):
                ext = ext[1:]  # 去掉前导点号，mkstemp 的 suffix 参数已含点号
            fd, temp_path = tempfile.mkstemp(suffix=f'.{ext}', prefix='agent_')
            os.close(fd)
            path = Path(temp_path)

        # 4. 后缀检查
        suffix = path.suffix.lower()
        if suffix in self.FORBIDDEN_EXTENSIONS:
            # 清理临时文件
            if not file_path and path.exists():
                path.unlink(missing_ok=True)
            return {"success": False, "error": f"不允许生成 {suffix} 类型的文件"}

        if suffix not in self.TEXT_EXTENSIONS:
            logger.warning(f"文件后缀 {suffix} 不在文本白名单中，将以纯文本写入")

        # 5. 覆盖检查（仅对非临时文件生效，临时文件不存在冲突问题）
        if file_path and path.exists() and not overwrite:
            return {"success": False, "error": f"文件已存在: {path}。如需覆盖请设置 overwrite=True"}

        # 6. JSON 格式校验
        if suffix == '.json':
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                if not file_path and path.exists():
                    path.unlink(missing_ok=True)
                return {"success": False, "error": f"JSON 格式错误: {e}"}

        # 7. 创建目录并写入文件
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)

        file_size = path.stat().st_size
        logger.info(f"文件已生成: {path} ({file_size} bytes)")

        # 8. 构建返回结果
        result = {
            "success": True,
            "file_path": str(path),  # 返回实际写入的绝对路径
            "file_name": path.name,
            "file_size": file_size,
            "encoding": encoding,
            "is_temp": not bool(file_path),  # 标记是否为临时文件
            "message": f"文件已生成: {path.name}",
        }

        # 9. 注册到下载系统
        if register_download:
            download_info = self._register_download(path, display_name)
            if download_info.get("success"):
                result["download_url"] = download_info["download_url"]
                result["file_id"] = download_info["file_id"]
                result["download_file_name"] = download_info.get("file_name", path.name)
            else:
                logger.warning(f"注册下载失败: {download_info.get('error')}")
                result["download_warning"] = "文件已生成但注册下载失败"

        return result

    except Exception as e:
        logger.error(f"生成文件失败: {e}")
        return {"success": False, "error": f"生成文件失败: {str(e)}"}
```

### 5.3 路径解析方法（跨平台）

```python
def _resolve_and_validate_path(self, file_path: str) -> Path:
    """
    解析并验证文件路径，兼容 Windows 和 Linux。

    pathlib.Path 自动处理平台差异：
    - Windows: "data\\report.md" 和 "data/report.md" 都能正确解析
    - Linux: "data/report.md" 正常解析
    - resolve() 返回平台原生的绝对路径（Windows 用反斜杠，Linux 用正斜杠）
    """
    p = Path(file_path)

    # 安全检查：禁止路径穿越
    if ".." in p.parts:
        raise ValueError("文件路径不允许包含 '..'")

    # 相对路径解析到 storage/output 目录
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        p = project_root / "storage" / "output" / p

    # resolve() 消除残余相对组件，返回绝对路径
    p = p.resolve()

    # 文件名安全检查（Windows 保留字符 + NULL）
    filename = p.name
    forbidden_chars = set('<>:"|?*\0')
    if any(c in forbidden_chars for c in filename):
        raise ValueError(f"文件名包含非法字符: {filename}")

    return p
```

### 5.4 下载注册方法

```python
def _register_download(self, file_path: Path, display_name: Optional[str] = None) -> Dict[str, Any]:
    """将生成的文件注册到下载系统"""
    from src.tools.file.register_download_tool import _resolve_upload_dir
    from src.core.redis_client import redis_client
    import shutil
    import uuid

    file_id = f"file_{uuid.uuid4().hex[:12]}"
    suffix = file_path.suffix.lower()

    if not display_name:
        display_name = file_path.name

    # 确保显示名带后缀
    if not display_name.lower().endswith(suffix):
        display_name += suffix

    # MIME 类型映射
    mime_map = {
        '.md': 'text/markdown', '.markdown': 'text/markdown',
        '.html': 'text/html', '.htm': 'text/html',
        '.txt': 'text/plain', '.text': 'text/plain',
        '.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
        '.json': 'application/json', '.jsonl': 'application/jsonl',
        '.xml': 'application/xml',
        '.yaml': 'text/yaml', '.yml': 'text/yaml',
        '.css': 'text/css',
        '.js': 'text/javascript', '.mjs': 'text/javascript',
        '.svg': 'image/svg+xml',
    }
    mime_type = mime_map.get(suffix, 'text/plain')

    upload_dir = _resolve_upload_dir(self._tenant_id, self._user_id)
    dest_path = upload_dir / f"{file_id}{suffix}"
    shutil.copy2(str(file_path), str(dest_path))

    file_size = dest_path.stat().st_size

    file_info = {
        "file_id": file_id,
        "name": display_name,
        "path": str(dest_path.absolute()),
        "size": file_size,
        "mime_type": mime_type,
        "type": "file",
    }

    key = redis_client.make_key("uploaded_file", file_id)
    for field, value in file_info.items():
        redis_client.hset(key, field, value)
    redis_client.expire(key, 86400)

    download_url = f"/api/files/{file_id}/download"

    return {
        "success": True,
        "file_id": file_id,
        "file_name": display_name,
        "file_size": file_size,
        "download_url": download_url,
    }
```

## 6. 注册与集成

### 6.1 工具注册

**修改文件**: `src/core/agent.py`

在 `_register_builtin_tools()` 方法中添加：

```python
from src.tools.file.text_file_writer import FileWriteTool

self.tool_registry.register(FileWriteTool())
```

### 6.2 用户上下文注入

**修改文件**: `src/core/agent.py`

在现有的 `set_user_id` 注入点附近添加：

```python
# 注入用户信息到文本文件生成工具
file_write_tool = self.tool_registry.get_tool("file_write")
if file_write_tool:
    if hasattr(file_write_tool, 'set_user_id') and user:
        file_write_tool.set_user_id(user.user_id)
    if hasattr(file_write_tool, 'set_tenant_id') and tenant_id:
        file_write_tool.set_tenant_id(tenant_id)
```

### 6.3 模块导出

**修改文件**: `src/tools/file/__init__.py`

添加 `FileWriteTool` 到导出列表。

## 7. LLM 使用指引

工具的 `description` 和 `usage_guide` 需要引导 LLM 正确使用：

```python
usage_guide = """生成文本文件时：
方式一（推荐，简单）：只传内容和格式
  file_write(content="完整内容", file_extension="md")
  → 自动生成临时文件，用户可直接下载

方式二（控制路径）：指定完整路径
  file_write(file_path="output/report.md", content="完整内容")
  → 文件保存到 storage/output/report.md

注意：
- 两种方式都会自动注册下载，用户都能在前端下载，无需再调 register_download_file
- 确保 content 符合文件后缀对应的语法（如 JSON 格式正确、HTML 标签闭合等）
- 如需覆盖已有文件，设置 overwrite=True
- 路径支持 Windows 和 Linux 格式"""
```

## 8. 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/tools/file/text_file_writer.py` | 文本文件生成工具实现 |

### 修改文件

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `src/core/agent.py` | 小 | 注册工具 + 注入 user_id/tenant_id |
| `src/tools/file/__init__.py` | 小 | 导出 `FileWriteTool` |

## 9. 验收标准

### 功能验收

- [ ] 指定路径：`file_write(file_path="report.md", content="# Report\n...")` 生成 Markdown 文件到 `storage/output/`
- [ ] 临时文件：`file_write(content="# Report\n...", file_extension="md")` 自动生成临时文件
- [ ] 无路径无后缀：`file_write(content="hello")` 自动生成 `.txt` 临时文件
- [ ] 生成后自动注册到下载系统，返回 `download_url`
- [ ] 前端显示下载卡片，点击可下载
- [ ] 相对路径自动解析到 `storage/output/` 目录
- [ ] 目标目录不存在时自动创建
- [ ] JSON 内容格式校验：非法 JSON 返回错误

### 跨平台验收

- [ ] Windows 上路径 `data\\report.md` 和 `data/report.md` 都能正确解析
- [ ] Linux 上路径 `data/report.md` 正确解析
- [ ] 临时文件在 Windows 上写入 `%TEMP%` 目录
- [ ] 临时文件在 Linux 上写入 `/tmp` 目录
- [ ] 返回的 `file_path` 使用平台原生分隔符

### 安全验收

- [ ] 路径包含 `..` 时拒绝写入
- [ ] `.exe`、`.bat` 等可执行文件后缀拒绝写入
- [ ] 文件已存在且 `overwrite=False` 时拒绝覆盖
- [ ] 内容超过 10MB 时拒绝写入
- [ ] 文件名包含非法字符时拒绝写入

### 兼容性验收

- [ ] 不影响现有 `file_read`、`file_list`、`register_download_file` 工具
- [ ] 现有的 Word/Excel/PPT/PDF 生成流程不受影响
- [ ] 前端下载卡片复用现有的 `DownloadFileCard` 组件

## 10. 测试计划

| 测试类型 | 测试文件 | 测试内容 |
|----------|----------|----------|
| 单元测试 | `tests/unit/tools/test_text_file_writer.py` | 参数校验、路径安全、格式校验、文件写入 |
| 集成测试 | `tests/integration/test_file_write_flow.py` | 完整流程：生成 → 注册 → 下载 URL 返回 |

关键测试用例：

```python
class TestFileWriteTool:
    @pytest.mark.asyncio
    async def test_write_with_file_path(self):
        """指定路径生成 Markdown 文件"""

    @pytest.mark.asyncio
    async def test_write_to_temp_with_extension(self):
        """不指定路径，通过 file_extension 生成临时文件"""

    @pytest.mark.asyncio
    async def test_write_to_temp_default_txt(self):
        """不指定路径和后缀，默认生成 .txt 临时文件"""

    @pytest.mark.asyncio
    async def test_write_json_invalid(self):
        """JSON 格式错误时返回失败"""

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self):
        """路径穿越被阻止"""

    @pytest.mark.asyncio
    async def test_forbidden_extension_blocked(self):
        """可执行文件后缀被拒绝"""

    @pytest.mark.asyncio
    async def test_overwrite_existing_file(self):
        """overwrite=False 时拒绝覆盖已存在文件"""

    @pytest.mark.asyncio
    async def test_content_too_large(self):
        """超大内容被拒绝"""

    @pytest.mark.asyncio
    async def test_auto_register_download(self):
        """自动注册到下载系统"""

    @pytest.mark.asyncio
    async def test_skip_register_download(self):
        """register_download=False 时不注册"""

    @pytest.mark.asyncio
    async def test_cross_platform_path_windows(self):
        """Windows 风格路径解析（反斜杠）"""

    @pytest.mark.asyncio
    async def test_cross_platform_path_posix(self):
        """Linux 风格路径解析（正斜杠）"""

    @pytest.mark.asyncio
    async def test_temp_file_cleanup_on_error(self):
        """错误时清理临时文件"""
```
