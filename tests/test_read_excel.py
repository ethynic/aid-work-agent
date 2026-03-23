"""
测试Excel文档读取功能
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.tools.file.file_reader_tool import FileReaderTool
from loguru import logger


async def test_read_excel():
    """测试读取Excel文档"""

    file_reader = FileReaderTool()

    logger.info("\n=== 测试: 读取示例Excel文档 ===")

    # 读取示例Excel文档
    result = await file_reader.execute(file_path="sample_excel.xlsx")

    if result["success"]:
        print("\n[成功] Excel文档读取成功!\n")
        print(f"文件路径: {result['file_path']}")
        print(f"文件类型: {result['file_type']}")
        print(f"工作表数量: {result['sheet_count']}")
        print(f"工作表列表: {', '.join(result['sheet_names'])}")
        print(f"当前工作表: {result['current_sheet']}")
        print(f"总行数: {result['total_lines']}")
        print(f"文件大小: {result['file_size']} 字节")

        print("\n=== 文档信息 ===")
        doc_info = result.get('document_info', {})
        if doc_info:
            print(f"文件名: {doc_info.get('file_name', 'N/A')}")
            print(f"活跃工作表: {doc_info.get('active_sheet', 'N/A')}")

        print("\n=== 当前工作表数据 ===")
        sheet_data = result.get('sheet_data', {})
        print(f"工作表名称: {sheet_data.get('name', 'N/A')}")
        print(f"行数: {sheet_data.get('max_row', 0)}")
        print(f"列数: {sheet_data.get('max_column', 0)}")

        print("\n表头:")
        headers = sheet_data.get('headers', [])
        if headers:
            print(f"  {headers}")

        print("\n前3行数据:")
        rows = sheet_data.get('data', [])[:3]
        for i, row in enumerate(rows, 1):
            print(f"  行{i}: {row}")

        print("\n=== 完整内容 (前800字符) ===")
        print(result['content'][:800] + '...')
    else:
        print(f"\n[失败] Excel文档读取失败: {result['error']}")


if __name__ == "__main__":
    asyncio.run(test_read_excel())
