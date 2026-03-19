# Excel文档读取功能使用说明

## 概述

`file_read`工具已扩展支持Excel文档（.xlsx格式）的读取功能。该功能可以提取Excel文档中的工作表、单元格和表格数据。

## 功能特性

- ✅ 读取Excel文档（.xlsx格式）
- ✅ 提取所有工作表
- ✅ 提取单元格数据
- ✅ 提取表头和数据行
- ✅ 提取文档元信息
- ✅ 支持行号范围读取
- ✅ 自动文档格式识别

## 安装依赖

在安装之前，请确保已安装openpyxl库：

```bash
pip install openpyxl
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

async def read_excel_doc():
    file_reader = FileReaderTool()

    # 读取Excel文档
    result = await file_reader.execute(file_path="data.xlsx")

    if result["success"]:
        print("文档内容:")
        print(result["content"])

        print("\n工作表数量:", result["sheet_count"])
        print("工作表列表:", result["sheet_names"])
        print("当前工作表:", result["current_sheet"])
        print("工作表数据:", result["sheet_data"])
    else:
        print("读取失败:", result["error"])

asyncio.run(read_excel_doc())
```

### 指定行号范围

```python
# 只读取前10行
result = await file_reader.execute(
    file_path="data.xlsx",
    start_line=1,
    end_line=10
)
```

## 返回值说明

成功读取Excel文档后，返回的字典包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| success | bool | 是否成功 |
| message | str | 消息描述 |
| file_path | str | 文件路径 |
| file_type | str | 文件类型（"xlsx"） |
| encoding | str | 编码格式（"utf-8"） |
| content | str | 完整文档内容（所有工作表） |
| total_lines | int | 总行数 |
| read_lines | int | 读取的行数 |
| start_line | int | 起始行号 |
| end_line | int | 结束行号 |
| file_size | int | 文件大小（字节） |
| sheet_count | int | 工作表数量 |
| sheet_names | List[str] | 工作表名称列表 |
| current_sheet | str | 当前工作表名称 |
| sheet_data | Dict[str, Any] | 当前工作表数据 |
| document_info | Dict[str, Any] | 文档元信息 |

### sheet_data字段说明

`sheet_data`字段包含当前工作表的详细信息：

```python
{
    "name": "工作表名称",
    "max_row": 行数,
    "max_column": 列数,
    "headers": ["表头1", "表头2", ...],  # 第一行数据
    "rows": [[单元格1, 单元格2, ...], ...],  # 所有行
    "data": [[单元格1, 单元格2, ...], ...]   # 数据行（不包含表头）
}
```

### document_info字段说明

`document_info`字段包含文档元信息：

- file_name: 文件名
- file_size: 文件大小
- sheet_count: 工作表数量
- active_sheet: 活跃工作表
- title: 文档标题
- author: 作者
- created: 创建时间
- modified: 修改时间

## 内容格式说明

提取的文档内容按以下格式组织：

```
=== 工作表: 工作表1 ===
表头1 | 表头2 | 表头3
------------------------
数据1 | 数据2 | 数据3
数据4 | 数据5 | 数据6

=== 工作表: 工作表2 ===
表头1 | 表头2
---------------
数据1 | 数据2
```

## 限制说明

1. **仅支持.xlsx格式**：目前仅支持.xlsx格式的Excel文档，不支持旧的.xls格式
2. **依赖库**：需要安装openpyxl库
3. **文件大小**：默认最大文件大小限制为10MB
4. **格式限制**：
   - 图表、公式等复杂元素不会被提取
   - 样式和格式（颜色、字体等）不会被保留
   - 只提取单元格的值（使用data_only=True）

## 错误处理

### 常见错误及解决方案

1. **错误**: `openpyxl库未安装，无法读取Excel文档`
   - 解决: 运行 `pip install openpyxl`

2. **错误**: `不支持的文件格式: .xls，仅支持.xlsx和.xls格式`
   - 解决: 将.xls文件转换为.xlsx格式

3. **错误**: `文件过大`
   - 解决: 增加max_size参数或压缩文件

4. **错误**: `工作表不存在`
   - 解决: 检查工作表名称是否正确，使用sheet_names查看可用工作表

## 集成说明

Excel读取功能已集成到`FileReaderTool`中，当检测到文件扩展名为.xlsx或.xls时，自动使用ExcelReader处理。

### 判断逻辑

```python
if is_excel_document(str(path)):
    return self._read_excel_document(path, start_line, end_line)
```

### 独立使用

也可以单独使用ExcelReader类：

```python
from src.tools.file.excel_reader import ExcelReader

reader = ExcelReader()
result = reader.read_excel_document("data.xlsx")

# 读取指定工作表
result = reader.read_excel_document("data.xlsx", sheet_name="Sheet2")
```

## 测试

运行测试脚本：

```bash
# 生成示例Excel文档
python create_sample_excel.py

# 运行读取测试
python test_read_excel.py

# 运行演示
python demo_excel_reader.py
```

## 与Word文档的对比

| 特性 | Word文档 | Excel文档 |
|------|----------|-----------|
| 文件格式 | .docx | .xlsx |
| 读取库 | python-docx | openpyxl |
| 主要内容 | 段落、表格 | 工作表、单元格 |
| 数据结构 | 段落列表、表格列表 | 工作表、行、列 |
| 表头提取 | 需要手动识别 | 自动识别第一行 |
| 多个部分 | 表格独立 | 多个工作表 |

## 技术实现

- 使用openpyxl库读取Excel文档
- 遍历所有工作表
- 提取单元格值（data_only=True）
- 自动识别表头（第一行）
- 格式化输出为易读文本
- 提取文档元信息
- 支持多个工作表
