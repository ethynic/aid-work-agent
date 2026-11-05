"""
Excel文档处理工具

提供Excel文档的读取功能，支持提取工作表、单元格、表格等数据
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# ── used range 膨胀防御（2026-11-05 TPG80509 事故：整列格式化使 4 个 Sheet 的
# max_row 全部膨胀到 ~104.8 万行，普通模式逐格遍历冻结 worker 50 分钟）──
# 超过该行数视为膨胀嫌疑，改走 read_only 流式 + 真实数据边界探测
MAX_NORMAL_ROWS = 2000
# 已见数据后连续全空行达到该数即判定真实数据边界（膨胀文件的空行是连续的）
STREAM_EMPTY_TAIL = 200
# 流式扫描硬上限（数据真在尾部且超过此行数的文件，提示用 range 精确读取）
STREAM_MAX_SCAN_ROWS = 20000
# 流式路径单 Sheet 返回的数据行上限
STREAM_MAX_DATA_ROWS = 500


def _stream_real_rows(ws):
    """read_only 流式迭代，返回 (headers, data_rows, true_max_row, truncated)。

    早期停止：已有数据后连续 STREAM_EMPTY_TAIL 行全空 → 数据边界；未见过数据时
    扫到 STREAM_MAX_SCAN_ROWS 兜底。values_only 流式逐行 yield，不做逐格构造。
    """
    headers = None
    data_rows = []
    true_max_row = 0
    empty_streak = 0
    truncated = False
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        values = ["" if v is None else v for v in row]
        if any(v != "" for v in values):
            if headers is None:
                headers = [str(v) if v != "" else "" for v in values]
            else:
                data_rows.append(values)
                if len(data_rows) >= STREAM_MAX_DATA_ROWS:
                    truncated = True
                    break
            true_max_row = row_idx
            empty_streak = 0
        elif headers is not None:
            empty_streak += 1
            if empty_streak >= STREAM_EMPTY_TAIL:
                break
        if row_idx >= STREAM_MAX_SCAN_ROWS:
            truncated = True
            break
    return headers, data_rows, true_max_row, truncated


def _format_rows_to_text_doc(headers, data_rows) -> str:
    """与 _format_sheet_to_text 同构的固定列宽文本，输入为流式提取的行数据。"""
    all_rows = ([list(headers)] if headers else []) + [list(r) for r in data_rows]
    if not all_rows:
        return "(空工作表)"
    col_count = max(len(r) for r in all_rows)
    col_widths = []
    for col in range(col_count):
        max_width = 0
        for r in all_rows:
            cell_str = str(r[col]) if col < len(r) and r[col] is not None else ""
            max_width = max(max_width, len(cell_str))
        col_widths.append(min(max_width + 2, 50))

    text_lines = []
    for r in all_rows:
        row_parts = []
        for col in range(col_count):
            cell_str = str(r[col]) if col < len(r) and r[col] is not None else ""
            row_parts.append(cell_str.ljust(col_widths[col]))
        text_lines.append(" | ".join(row_parts))
    return "\n".join(text_lines)


class ExcelReader:
    """Excel文档读取器"""

    def __init__(self):
        """初始化Excel读取器"""
        self.openpyxl = None
        self._init_openpyxl()

    def _init_openpyxl(self):
        """初始化openpyxl库"""
        try:
            import openpyxl
            self.openpyxl = openpyxl
            logger.info("openpyxl库初始化成功")
        except ImportError:
            logger.error("openpyxl库未安装，请运行: pip install openpyxl")
            self.openpyxl = None

    def read_excel_document(self, file_path: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
        """
        读取Excel文档内容

        Args:
            file_path: Excel文档路径
            sheet_name: 工作表名称（可选），如果不指定则读取第一个工作表

        Returns:
            包含文档内容的字典
        """
        if self.openpyxl is None:
            return {
                "success": False,
                "error": "openpyxl库未安装，无法读取Excel文档"
            }

        workbook = None
        try:
            path = Path(file_path)
            if not path.exists():
                return {
                    "success": False,
                    "error": f"文件不存在: {file_path}"
                }

            # 检查文件扩展名
            if path.suffix.lower() not in ['.xlsx', '.xls']:
                return {
                    "success": False,
                    "error": f"不支持的文件格式: {path.suffix}，仅支持.xlsx和.xls格式"
                }

            # 检查.xls格式（需要转换）
            if path.suffix.lower() == '.xls':
                return {
                    "success": False,
                    "error": "目前仅支持.xlsx格式，请将.xls文件转换为.xlsx格式"
                }

            # 膨胀探测：read_only 模式读声明维度代价极低（仅解析 dimension 元素），
            # 命中膨胀文件直接走流式路径，避免普通模式把百万空行全部解析进内存
            inflated = self._is_inflated_workbook(path)
            if inflated:
                return self._read_document_stream(path, sheet_name)

            # 读取Excel文档
            workbook = self.openpyxl.load_workbook(path, data_only=True)

            # 提取所有工作表信息
            sheet_names = workbook.sheetnames
            sheet_count = len(sheet_names)

            # 获取工作表信息
            sheets_info = []
            for sheet_name_item in sheet_names:
                sheet = workbook[sheet_name_item]
                sheets_info.append({
                    "name": sheet_name_item,
                    "title": sheet.title,
                    "max_row": sheet.max_row,
                    "max_column": sheet.max_column,
                    "row_count": sheet.max_row,
                    "column_count": sheet.max_column
                })

            # 读取指定工作表或第一个工作表
            target_sheet_name = sheet_name if sheet_name else sheet_names[0]
            if target_sheet_name not in sheet_names:
                return {
                    "success": False,
                    "error": f"工作表不存在: {target_sheet_name}，可用工作表: {', '.join(sheet_names)}"
                }

            sheet_data = self._extract_sheet_data(workbook[target_sheet_name])

            # 提取文档信息
            doc_info = self._extract_document_info(workbook, path)

            # 合并所有工作表内容（用于完整内容输出）
            all_sheets_content = []
            for sheet_name_item in sheet_names:
                sheet = workbook[sheet_name_item]
                sheet_text = self._format_sheet_to_text(sheet)
                all_sheets_content.append(f"=== 工作表: {sheet_name_item} ===\n{sheet_text}")

            full_content = '\n\n'.join(all_sheets_content)
            total_lines = len(full_content.splitlines())

            logger.info(f"成功读取Excel文档: {path} (工作表: {sheet_count})")

            return {
                "success": True,
                "message": "成功读取Excel文档",
                "file_path": str(path),
                "file_type": "xlsx",
                "sheet_count": sheet_count,
                "sheet_names": sheet_names,
                "sheets_info": sheets_info,
                "current_sheet": target_sheet_name,
                "sheet_data": sheet_data,
                "document_info": doc_info,
                "content": full_content,
                "total_lines": total_lines
            }

        except Exception as e:
            logger.error(f"读取Excel文档失败: {e}")
            return {
                "success": False,
                "error": f"读取Excel文档失败: {str(e)}"
            }
        finally:
            if workbook is not None:
                workbook.close()

    def _is_inflated_workbook(self, path: Path) -> bool:
        """read_only 模式读声明维度（仅解析 dimension 元素，代价极低）判断膨胀。"""
        wb = None
        try:
            wb = self.openpyxl.load_workbook(str(path), data_only=True, read_only=True)
            return any((ws.max_row or 0) > MAX_NORMAL_ROWS for ws in wb.worksheets)
        except Exception as e:
            logger.opt(exception=True).warning(f"[ExcelReader] 膨胀探测失败，按普通路径读取: {e}")
            return False
        finally:
            if wb is not None:
                wb.close()

    def _read_document_stream(self, path: Path, sheet_name: Optional[str]) -> Dict[str, Any]:
        """膨胀文件的流式读取：逐 Sheet 探测真实数据边界，返回结构与普通路径一致。"""
        wb = None
        try:
            wb = self.openpyxl.load_workbook(str(path), data_only=True, read_only=True)
            sheet_names = list(wb.sheetnames)
            if sheet_name and sheet_name not in sheet_names:
                return {
                    "success": False,
                    "error": f"工作表不存在: {sheet_name}，可用工作表: {', '.join(sheet_names)}"
                }
            target_sheet_name = sheet_name or sheet_names[0]

            sheets_info = []
            all_sheets_content = []
            sheet_data = None
            truncated_any = False
            for name in sheet_names:
                headers, data_rows, true_rows, truncated = _stream_real_rows(wb[name])
                truncated_any = truncated_any or truncated
                col_count = max(
                    [len(headers) if headers else 0] + [len(r) for r in data_rows]
                ) if (headers or data_rows) else 0
                sheets_info.append({
                    "name": name,
                    "title": name,
                    "max_row": true_rows,
                    "max_column": col_count,
                    "row_count": true_rows,
                    "column_count": col_count,
                })
                if name == target_sheet_name:
                    sheet_data = {
                        "name": name,
                        "max_row": true_rows,
                        "max_column": col_count,
                        "headers": headers or [],
                        "rows": ([list(headers)] if headers else []) + data_rows,
                        "data": data_rows,
                    }
                sheet_text = _format_rows_to_text_doc(headers, data_rows)
                all_sheets_content.append(f"=== 工作表: {name} ===\n{sheet_text}")

            full_content = '\n\n'.join(all_sheets_content)
            total_lines = len(full_content.splitlines())

            doc_info = {
                "file_name": path.name,
                "file_size": path.stat().st_size,
                "sheet_count": len(sheet_names),
                "active_sheet": wb.active.title if wb.active else None,
            }
            try:
                props = getattr(wb, "properties", None)
                if props:
                    doc_info.update({
                        "title": props.title or "",
                        "author": props.creator or "",
                        "created": str(props.created) if props.created else "",
                        "modified": str(props.modified) if props.modified else "",
                    })
            except Exception as e:
                logger.warning(f"提取文档信息失败: {e}")

            message = "成功读取Excel文档"
            if truncated_any:
                message += "（部分工作表数据超过流式扫描上限已截断，需要完整数据请改用 range 参数精确读取）"

            return {
                "success": True,
                "message": message,
                "file_path": str(path),
                "file_type": "xlsx",
                "sheet_count": len(sheet_names),
                "sheet_names": sheet_names,
                "sheets_info": sheets_info,
                "current_sheet": target_sheet_name,
                "sheet_data": sheet_data,
                "document_info": doc_info,
                "content": full_content,
                "total_lines": total_lines,
                "truncated": truncated_any,
            }
        except Exception as e:
            logger.opt(exception=True).error(f"读取Excel文档失败: {e}")
            return {
                "success": False,
                "error": f"读取Excel文档失败: {str(e)}"
            }
        finally:
            if wb is not None:
                wb.close()

    def _extract_sheet_data(self, sheet) -> Dict[str, Any]:
        """
        提取工作表数据

        Args:
            sheet: openpyxl的Worksheet对象

        Returns:
            工作表数据字典
        """
        max_row, max_column = sheet.max_row, sheet.max_column
        data = {
            "name": sheet.title,
            "max_row": max_row,
            "max_column": max_column,
            "rows": [],
            "headers": [],
            "data": []
        }

        # 获取第一行作为表头
        if max_row >= 1:
            headers = []
            for col in range(1, max_column + 1):
                cell_value = sheet.cell(1, col).value
                headers.append(str(cell_value) if cell_value is not None else "")
            data["headers"] = headers

        # 获取所有数据行
        rows = []
        for row in range(1, max_row + 1):
            row_data = []
            for col in range(1, max_column + 1):
                cell_value = sheet.cell(row, col).value
                row_data.append(cell_value)
            rows.append(row_data)

        data["rows"] = rows

        # 提取数据（跳过表头）
        if max_row > 1:
            data["data"] = rows[1:]
        else:
            data["data"] = []

        return data

    def _format_sheet_to_text(self, sheet) -> str:
        """
        将工作表格式化为文本

        Args:
            sheet: openpyxl的Worksheet对象

        Returns:
            格式化的文本内容
        """
        text_lines = []

        # openpyxl 的动态维度会扫描全部已加载单元格；不能在每列循环中重复计算。
        max_row, max_column = sheet.max_row, sheet.max_column
        if max_row == 0:
            return "(空工作表)"

        # 获取每列的最大宽度（用于格式化）
        col_widths = []
        for col in range(1, max_column + 1):
            max_width = 0
            for row in range(1, max_row + 1):
                cell_value = sheet.cell(row, col).value
                cell_str = str(cell_value) if cell_value is not None else ""
                max_width = max(max_width, len(cell_str))
            col_widths.append(min(max_width + 2, 50))  # 限制最大宽度

        # 格式化每一行
        for row in range(1, max_row + 1):
            row_parts = []
            for col in range(1, max_column + 1):
                cell_value = sheet.cell(row, col).value
                cell_str = str(cell_value) if cell_value is not None else ""
                width = col_widths[col - 1]
                row_parts.append(cell_str.ljust(width))
            text_lines.append(" | ".join(row_parts))

        return '\n'.join(text_lines)

    def _extract_document_info(self, workbook, path: Path) -> Dict[str, Any]:
        """
        提取文档元信息

        Args:
            workbook: openpyxl的Workbook对象
            path: 文件路径

        Returns:
            文档信息字典
        """
        info = {
            "file_name": path.name,
            "file_size": path.stat().st_size,
            "sheet_count": len(workbook.sheetnames),
            "active_sheet": workbook.active.title if workbook.active else None,
        }

        try:
            # 尝试获取工作簿属性（如果有的话）
            if hasattr(workbook, 'properties'):
                props = workbook.properties
                if props:
                    info.update({
                        "title": props.title or "",
                        "author": props.creator or "",
                        "created": str(props.created) if props.created else "",
                        "modified": str(props.modified) if props.modified else "",
                    })
        except Exception as e:
            logger.warning(f"提取文档信息失败: {e}")

        return info


def is_excel_document(file_path: str) -> bool:
    """
    判断文件是否为Excel文档

    Args:
        file_path: 文件路径

    Returns:
        是否为Excel文档
    """
    path = Path(file_path)
    return path.suffix.lower() in ['.xlsx', '.xls']
