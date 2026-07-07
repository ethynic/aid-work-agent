# 大内容落盘检索闭环 + grep 工具设计

> 配套：[`tool-overall-optimization-design.md`](./tool-overall-optimization-design.md) #2、[`file-tools-claude-code-parity-design.md`](./file-tools-claude-code-parity-design.md)
>
> 解决问题：工具（http_api/pdf/ocr）截断大内容后**完整内容永久丢失**，agent 无法回读被截断部分，形成「信息黑洞」。本设计建立「截断 → 落盘 → read/grep 回读」闭环，对齐 Claude Code harness 模式。

## 1. 问题根因

Phase 2 试点只做了截断，没做落盘：

| 工具 | 截断点 | 截断后完整内容去向 |
|------|--------|------|
| http_api | `http_api.py:273` JSON 序列化 >5000 字符截断 | **丢弃**，无落盘 |
| http_api | `http_api.py:281` 纯文本 >5000 字符截断 | **丢弃**，无落盘 |
| paddleocr | `ocr_tool.py:298` full_text >5000 截断 | **丢弃** |
| pdf_process | `_merge_results` 各字段截断 | **丢弃** |

agent 看到 `truncated: True` 后，**没有任何手段**拿回被截断内容。若用户要的信息恰在截断处之后，agent 永远答不出。

## 2. 设计目标

建立与 Claude Code harness 一致的三段式闭环：

```
工具产生大内容
   ├─ 小内容（≤阈值）→ 直接返回，不落盘（零额外开销）
   └─ 大内容（>阈值）→ 落盘到临时文件 + 返回截断预览 + file_path + truncated:true
                              ↓
                    agent 用 read（分页）/ grep（关键词）按需回读完整内容
```

**核心原则**：
1. **小内容不落盘**——避免给每次 API 调用都加文件 IO 开销。阈值内直接返回，agent 一次拿到全量。
2. **大内容落临时文件**——不是 `cp` 的「交付产物」下载目录（那是给用户的），而是 agent 工作用的临时文件，消费完即可清理。
3. **agent 自主决策回读**——agent 看 truncated 标记 + file_path，判断是否需要 read/grep 深入。不强制回读，避免无谓 token。

## 3. 落盘机制设计

### 3.1 临时文件管理器 `src/tools/_spill.py`

新增公共模块，统一管理大内容落盘。避免每个工具各写一套。

```python
# src/tools/_spill.py 核心接口
def spill_large_content(
    content: str,
    *,
    prefix: str = "tool_response_",
    suffix: str = ".txt",
    meta: dict | None = None,
) -> dict:
    """
    将大文本内容落盘到临时文件，返回供 LLM 回读的结构。

    Returns:
        {
            "file_path": "<临时文件绝对路径，read/grep 可读>",
            "full_size": <完整内容字符数>,
            "preview": "<前 N 字符预览>",
            "truncated": True
        }
    """
```

**落盘目录**：`tempfile.gettempdir()` 下的 `aid_agent_spill/` 子目录。原因：
- `read` 工具的路径白名单（`read_tool.py:182-192`）已允许临时目录，无需改 read
- 与 write 的 `storage/output/`（用户产物）、cp 的 `UPLOAD_DIR`（下载注册）区分，避免污染
- OS 自动清理临时目录，无需自建 GC（可加启动时清理增强）

**文件命名**：`{prefix}{timestamp}_{short_uuid}{suffix}`，如 `httpapi_response_20260706_a1b2c3.json`。prefix 标识来源工具，便于排查。

**meta 携带**：可选元信息（如原 URL、OCR 页数）写入 `.<same>.meta.json`，供 grep 旁路参考，不进返回值。

### 3.2 工具改造点（按统一阈值决策）

**阈值统一**：内容序列化后 > 5000 字符才落盘（与现有截断阈值一致，避免小响应也落盘）。

| 工具 | 改造 |
|------|------|
| **http_api** | `_parse_response`：JSON/text 序列化 >5000 时，调 `spill_large_content` 落盘，返回 `{data: preview, file_path, truncated:true, full_size}` |
| **paddleocr** | full_text >5000 时落盘 |
| **pdf_process** | read/ocr/pdf_to_md 截断字段超阈值时落盘 |

**返回结构约定**（落盘场景）：
```python
{
    "success": True,
    "data": "<前 5000 字符预览>",      # 截断预览，agent 先看摘要
    "truncated": True,
    "file_path": "/tmp/aid_agent_spill/httpapi_response_xxx.json",  # 完整内容落盘路径
    "full_size": 50000                  # 完整内容大小，agent 据此判断是否回读
}
```

**非落盘场景**（小响应）保持原样：`{success, data}`，无 file_path/truncated 字段，零额外开销。

## 4. grep 工具设计

### 4.1 为什么必须有

无 grep，agent 要在落盘的 50KB JSON 里找一个字段，只能 read 分页逐段读——每页 2000 行、要读几十轮，token 爆炸且低效。grep 一次定位，是闭环的关键一环。

### 4.2 实现：调 ripgrep 二进制

服务器/本地均用 `rg` 二进制（已确认本地 rg 13.0.0 可用，Dockerfile 需补装）。理由：
- **性能**：MB 级文件毫秒级，Python re 逐行慢一个数量级
- **功能成熟**：rg 原生支持 `-A/-B/-C` 上下文、`-i` 忽略大小写、`-w` 全词、`-l` 仅文件名、`-c` 计数、glob 过滤、二进制跳过
- **与 Claude Code 一致**：Claude Code Grep 也是调 rg

**Dockerfile 补装**（在 `Dockerfile` apt-get 段新增）：
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep && rm -rf /var/lib/apt/lists/*
```

### 4.3 工具定义 `src/tools/file/grep_tool.py`

```
name: grep
description: 在文件或目录中搜索文本（正则匹配）。用于在工具落盘的大响应文件、
             代码、日志中定位内容。返回匹配行+行号+上下文。
```

**入参**（对齐 Claude Code Grep）：

| 参数 | 必填 | 说明 |
|------|------|------|
| `pattern` | 是 | 正则表达式 |
| `path` | 是 | 搜索目标：文件路径或目录。支持 read 白名单内的路径 |
| `glob` | 否 | 文件名过滤，如 `*.json`、`*.py`。目录搜索时有效 |
| `output_mode` | 否 | `content`（默认，带行号内容）/ `files_with_matches`（仅文件名）/ `count`（计数） |
| `-i` `ignore_case` | 否 | 忽略大小写，默认 false |
| `context` | 否 | 上下文行数（同时设前后），默认 0 |
| `before_context` | 否 | 匹配行前 N 行，默认 0 |
| `after_context` | 否 | 匹配行后 N 行，默认 0 |
| `max_matches` | 否 | 最大返回匹配数，默认 50（防巨量匹配撑爆上下文） |

**返回**（content 模式）：
```python
{
    "success": True,
    "matches": [
        {"file": "<路径>", "line": <行号 1-based>, "content": "<匹配行>"},
        ...
    ],
    "count": <实际匹配数>,
    "truncated": <bool>  # 超过 max_matches 时 true
}
```

**行号 1-based**——与 read 显示的 cat -n 行号一致，agent 能直接用行号去 read 定位。

**安全**：
- 复用 read 的路径白名单（项目根 + 临时目录）
- `pattern` 用 `subprocess` 参数数组传给 rg（不经 shell），防注入
- rg 超时（如 10s）兜底，防恶意大目录搜索
- 二进制文件 rg 自动跳过

## 5. agent 闭环工作流（设计目标）

改造后，agent 处理大响应的标准路径：

```
1. agent 调 http_api → 返回 {data: 预览5000字, truncated:true, file_path:"/tmp/.../xxx.json"}
2. agent 判断：用户问题能否从预览回答？
   ├─ 能 → 直接答，结束（省 token）
   └─ 不能，用户要的字段在截断处后 →
3. agent 调 grep(pattern="目标字段", path="file_path") → 拿到匹配行号
4. agent 调 read(file_path="file_path", offset=行号, limit=50) → 精准读取上下文
5. agent 综合回答
```

这是 Claude Code harness 的「Read/Grep/Edit 闭环」在本项目的落地。agent 智能体本身已具备这个推理能力（主智能体 prompt 已支持工具编排），缺的只是 read/grep 能读到大内容——本设计补齐这个缺口。

## 6. 影响范围

### 新增
| 文件 | 内容 |
|------|------|
| `src/tools/_spill.py` | 落盘管理器 `spill_large_content` |
| `src/tools/file/grep_tool.py` | grep 工具（调 rg） |
| `tests/unit/tools/test_spill.py` | 落盘单测 |
| `tests/unit/tools/test_grep_tool.py` | grep 单测 |

### 修改
| 文件 | 改动 |
|------|------|
| `src/tools/network/http_api.py` | `_parse_response` 落盘逻辑 |
| `src/tools/ocr/ocr_tool.py` | full_text 落盘 |
| `src/tools/pdf/pdf_process_tool.py` | 截断字段落盘 |
| `src/core/agent.py` | 注册 grep 工具 |
| `Dockerfile` | 补装 ripgrep |

### 不改
- read 工具：路径白名单已允许临时目录，无需改动即可读落盘文件
- cp/transfer_to_human 等：与本闭环无关

## 7. 验收标准

- [ ] 小响应（<5000 字符）不落盘，返回结构与改造前一致（零回归）
- [ ] 大响应（≥5000 字符）落盘，返回 file_path + truncated + 预览
- [ ] agent 能用 read 读落盘文件的任意分页
- [ ] agent 能用 grep 在落盘文件中定位关键词，拿到 1-based 行号
- [ ] grep 在 1MB 文件下 <1s
- [ ] Dockerfile 构建后 rg 可用
- [ ] 临时目录文件不无限增长（启动清理 + 老化）

## 8. 非目标

- 不改 read 工具的路径白名单（已够用）
- 不改 cp 的下载注册机制（那是用户产物交付，与本闭环不同域）
- 不引入全文搜索引擎/索引（grep + read 分页足够，不过度设计）
- 不做老文件的 GC daemon（启动时清理 + 依赖 OS tmp 清理即可）
