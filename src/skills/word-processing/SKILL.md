---
name: word-processing
description: Word文档处理技能，用于分析.docx文档结构、创建全新Word文档、修改已有文档内容和调整文档格式排版。当用户上传.docx文件、要求创建Word文档、修改Word内容、调整文档格式/字体/排版、生成报告或合同模板时使用此技能。即使用户只是提到'文档处理'、'排版'、'写文档'而没有明确说Word，也应使用此技能。
metadata:
  version: "1.0.0"
  author: aid-work-agent
---

# Word 文档处理技能

## 适用场景

- **分析文档**：用户上传了 Word 文件，想了解其结构或内容
- **创建文档**：用户需要生成新的 Word 文档（报告、会议纪要、合同模板等）
- **修改内容**：用户需要修改已有文档中的文字、段落或表格
- **调整格式**：用户需要改变文档的字体、段落格式、页面设置

如果用户只是想阅读文档内容，使用 `file_read` 工具即可。

## 工作流程

### 场景 A：分析文档

```bash
skill_execute(skill="word-processing", command='python scripts/word_ops.py analyze --file-path "<文件路径>"')
```

输出包含段落索引/样式/字体、表格数据、页眉页脚。修改文档前先分析，因为需要段落索引才能准确定位修改目标。

### 场景 B：创建新文档（必须用 Markdown）

创建新 Word 文档**只能**通过 Markdown 转 Word 的方式，不支持 JSON 格式输入。Markdown 更自然、更适合表达大段内容。

**步骤：**

1. **生成 Markdown 内容**：用 `content_generate` 工具生成 Markdown 格式的文档内容
2. **转为 Word**：将 Markdown 内容通过 `content` 参数（纯文本）传给 `skill_execute`
3. **注册下载**：调用 `register_download_file` 工具注册文件，用户即可下载

```bash
# 步骤 2：将 Markdown 纯文本内容转为 Word
skill_execute(
    skill="word-processing",
    command="python scripts/word_ops.py create-from-md --title '文档标题'",
    content="这里直接放 content_generate 生成的 Markdown 纯文本内容"
)

# 步骤 3：注册到下载系统（用输出的 file_path）
register_download_file(file_path="<上一步输出的file_path>", display_name="会议纪要.docx")
```

**关键：使用 `content` 参数传 Markdown 文本，不要用 `files` 参数做 base64 编码！**

`create-from-md` 支持的 Markdown 元素：标题（# ~ ######）、粗体/斜体、表格、有序/无序列表、代码块、引用、分隔线（转为分页符）。中文默认使用宋体正文、黑体标题。

### 场景 C：修改或格式化已有文档

1. **先 analyze**：获取段落索引和当前格式
2. **执行修改**：用 `modify` 改内容，用 `format` 改格式
3. **注册下载**：调用 `register_download_file` 注册输出文件

**修改内容**（modify）：
```bash
skill_execute(
    skill="word-processing",
    command='python scripts/word_ops.py modify --file-path "<路径>" --instructions \'<JSON>\''
)
```

**调整格式**（format）：
```bash
skill_execute(
    skill="word-processing",
    command='python scripts/word_ops.py format --file-path "<路径>" --instructions \'<JSON>\''
)
```

modify 操作（详见 `references/modify_schema.md`）：`replace_text`、`insert_paragraph`、`delete_paragraph`、`replace_paragraph`、`insert_table`、`delete_table`、`modify_table_cell`、`add_page_break`

format 操作（详见 `references/format_schema.md`）：`set_font`（scope: all/paragraphs/heading1/heading2/table）、`set_paragraph_format`、`set_page_margins`、`set_page_size`、`set_header`、`set_footer`、`set_table_style`

## 输出文件处理

所有子命令输出包含 `file_path`（文件在服务器上的绝对路径）。生成文件后，必须调用 `register_download_file` 工具注册到下载系统：

```bash
register_download_file(file_path="输出中的file_path值", display_name="用户看到的文件名.docx")
```

该工具返回 `download_url`（如 `/api/files/xxx/download`），将此 URL 告知用户即可下载。不调用此工具，用户无法下载文件。

## 中文字体参考

| 中文名 | font_name | 适用场景 |
|--------|-----------|---------|
| 宋体 | SimSun | 正文（正式公文） |
| 黑体 | SimHei | 标题、强调 |
| 仿宋 | FangSong | 公文正文 |
| 楷体 | KaiTi | 引用、注释 |
| 微软雅黑 | Microsoft YaHei | 现代文档正文 |

## 注意事项

- **创建新文档只用 create-from-md + content 参数**：不接受 JSON 格式，不要使用 `files` 参数做 base64 编码，命令中也不需要任何文件路径参数
- **段落索引**：modify 操作的段落索引来自 analyze 输出。多次修改同一文件时，每次都重新 analyze
- **所有操作基于副本**：原始文件不会被修改
- **JSON 转义**：命令行中 JSON 用单引号包裹，内部用双引号

## 示例

**示例 1：创建会议纪要**

```
1. content_generate：生成会议纪要的 Markdown 内容
2. skill_execute：将 Markdown 转为 Word
   skill="word-processing"
   command="python scripts/word_ops.py create-from-md --title '会议纪要'"
   content="上面 content_generate 返回的 Markdown 纯文本内容"
3. register_download_file(file_path="输出路径", display_name="会议纪要.docx")
4. 告知用户下载链接
```

**示例 2：修改合同中的公司名**

```
1. skill_execute：analyze --file-path "<合同路径>"
2. skill_execute：modify --file-path "<路径>" --instructions '{"operations":[{"op":"replace_text","target":"XX科技有限公司","replacement":"YY智能技术有限公司"}]}'
3. register_download_file(file_path="输出路径", display_name="合同_已修改.docx")
4. 告知用户下载链接
```

**示例 3：统一文档格式**

```
1. skill_execute：analyze --file-path "<路径>"
2. skill_execute：format --file-path "<路径>" --instructions '{"operations":[{"op":"set_font","scope":"paragraphs","font_name":"SimSun","font_size":12},{"op":"set_font","scope":"heading1","font_name":"SimHei","font_size":16,"color":"1F4E79"}]}'
3. register_download_file(file_path="输出路径", display_name="报告_已排版.docx")
4. 告知用户下载链接
```
