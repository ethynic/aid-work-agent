# Word文档读取功能使用说明

## 概述

`file_read`工具已扩展支持Word文档（.docx格式）的读取功能。该功能可以提取Word文档中的文本段落和表格内容。

## 功能特性

- ✅ 读取Word文档（.docx格式）
- ✅ 提取文本段落
- ✅ 提取表格内容
- ✅ 提取文档元信息（标题、作者、创建时间等）
- ✅ 支持行号范围读取
- ✅ 自动文档格式识别

## 安装依赖

在安装之前，请确保已安装python-docx库：

```bash
pip install python-docx
```

或者通过项目依赖安装：

```bash
pip install -r requirements.txt
```

## 使用方法

### 基本用法

```python
import asyncio
from src.tools.file.file_reader_tool import FileReaderTool

async def read_word_doc():
    file_reader = FileReaderTool()

    # 读取Word文档
    result = await file_reader.execute(file_path="document.docx")

    if result["success"]:
        print("文档内容:")
        print(result["content"])

        print("\n段落数量:", result["paragraph_count"])
        print("表格数量:", result["table_count"])
        print("文档信息:", result["document_info"])
    else:
        print("读取失败:", result["error"])

asyncio.run(read_word_doc())
```

### 指定行号范围

```python
# 只读取前10行
result = await file_reader.execute(
    file_path="document.docx",
    start_line=1,
    end_line=10
)
```

### 返回值说明

成功读取Word文档后，返回的字典包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| success | bool | 是否成功 |
| message | str | 消息描述 |
| file_path | str | 文件路径 |
| file_type | str | 文件类型（"docx"） |
| encoding | str | 编码格式（"utf-8"） |
| content | str | 完整文档内容（文本+表格） |
| total_lines | int | 总行数 |
| read_lines | int | 读取的行数 |
| start_line | int | 起始行号 |
| end_line | int | 结束行号 |
| file_size | int | 文件大小（字节） |
| paragraphs | List[str] | 段落列表 |
| paragraph_count | int | 段落数量 |
| tables | List[str] | 表格内容列表 |
| table_count | int | 表格数量 |
| document_info | Dict[str, Any] | 文档元信息 |

### 文档元信息

`document_info`字段包含的元信息：

- title: 文档标题
- author: 作者
- subject: 主题
- created: 创建时间
- modified: 修改时间
- last_modified_by: 最后修改者
- keywords: 关键词
- comments: 注释

## 限制说明

1. **仅支持.docx格式**：目前仅支持.docx格式的Word文档，不支持旧的.doc格式
2. **依赖库**：需要安装python-docx库
3. **文件大小**：默认最大文件大小限制为10MB
4. **格式限制**：
   - 表格格式化为文本表格，保留表格结构
   - 图片、图表等复杂元素不会被提取
   - 特殊格式（如页眉页脚、批注等）不会被提取

## 内容格式说明

提取的文档内容格式如下：

```
=== 文档段落 ===
第一段内容
第二段内容
第三段内容

=== 文档表格 ===

表格 1:
表头1 | 表头2 | 表头3
-------------------------------
数据1 | 数据2 | 数据3
数据4 | 数据5 | 数据6

表格 2:
...
```

## 错误处理

### 常见错误及解决方案

1. **错误**: `python-docx库未安装，无法读取Word文档`
   - 解决: 运行 `pip install python-docx`

2. **错误**: `不支持的文件格式: .doc，仅支持.docx和.doc格式`
   - 解决: 将.doc文件转换为.docx格式

3. **错误**: `文件过大`
   - 解决: 增加max_size参数或压缩文件

4. **错误**: `文件不存在`
   - 解决: 检查文件路径是否正确

## 集成说明

Word读取功能已集成到`FileReaderTool`中，当检测到文件扩展名为.docx或.doc时，自动使用WordReader处理。

### 判断逻辑

```python
if is_word_document(str(path)):
    return self._read_word_document(path, start_line, end_line)
```

### 独立使用

也可以单独使用WordReader类：

```python
from src.tools.file.word_reader import WordReader

reader = WordReader()
result = reader.read_word_document("document.docx")
```

## 测试

运行测试脚本：

```bash
python test_word_reader.py
```

测试脚本包含以下测试用例：
- 读取不存在的Word文档
- 读取文本文件（非Word文档）
- Word文档判断功能
- 依赖库检查

## 未来扩展

可能的改进方向：

1. 支持.doc格式（需要使用antiword或libreoffice）
2. 提取图片和图表
3. 支持批注和修订模式
4. 支持页眉页脚
5. 支持样式和格式保留

## 技术实现

- 使用python-docx库读取Word文档
- 遍历文档段落和表格
- 格式化输出为可读文本
- 保留表格结构（使用分隔符）
- 提取文档核心属性
