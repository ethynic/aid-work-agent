"""
Excel 工具核心库

共享的基础功能：
- 常量映射（字体、数字格式）
- 文件操作（ExcelFileHandler）
- 范围解析、自动列宽等工具函数
"""

import uuid
from copy import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from loguru import logger

# 常见中文字体映射
FONT_MAP = {
    "微软雅黑": "Microsoft YaHei",
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
    "Microsoft YaHei": "Microsoft YaHei",
    "SimSun": "SimSun",
    "SimHei": "SimHei",
    "KaiTi": "KaiTi",
    "FangSong": "FangSong",
}

# 数字格式映射
NUMBER_FORMATS = {
    "千分位": "#,##0.00",
    "百分比": "0.00%",
    "整数": "#,##0",
    "日期": "yyyy-mm-dd",
    "货币": "¥#,##0.00",
    "文本": "@",
}

# 默认表头样式
DEFAULT_HEADER_FONT = Font(name="Microsoft YaHei", bold=True, color="FFFFFF", size=11)
DEFAULT_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
DEFAULT_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def resolve_font_name(name: str) -> str:
    if not name:
        return name
    return FONT_MAP.get(name, name)


def _resolve_path_via_redis(file_id: str) -> Optional[str]:
    """[已废弃] 薄包装，调用 src.core.storage.resolve_path_via_redis

    保留是为了向后兼容（test 直接从此模块导入 _resolve_path_via_redis）。
    新代码请直接 from src.core.storage import resolve_path_via_redis。
    """
    from src.core.storage import resolve_path_via_redis
    return resolve_path_via_redis(file_id)


def parse_color(color_str: str) -> Optional[str]:
    """解析颜色字符串，返回 6 位 hex（不含 #）"""
    if not color_str:
        return None
    color_str = color_str.lstrip("#")
    if len(color_str) == 6:
        try:
            int(color_str, 16)
            return color_str
        except ValueError:
            return None
    return None


def parse_range(range_str: str) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """
    解析 Excel 范围字符串为行列索引元组。

    "A1:D10" → ((1, 1), (4, 10))  即 (min_col, min_row), (max_col, max_row)
    "A1" → ((1, 1), (1, 1))
    """
    from openpyxl.utils import range_boundaries

    if ":" in range_str:
        min_col, min_row, max_col, max_row = range_boundaries(range_str)
    else:
        col, row = range_boundaries(range_str)
        min_col = max_col = col
        min_row = max_row = row

    return (min_col, min_row), (max_col, max_row)


def auto_column_width(ws, min_width: int = 8, max_width: int = 50, sample_rows: int = 100):
    """自动调整列宽，考虑中文字符宽度（CJK 字符算 2 倍宽度）"""
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in list(col_cells)[:sample_rows]:
            if cell.value is None:
                continue
            val = str(cell.value)
            length = sum(2 if ord(c) > 127 else 1 for c in val)
            max_len = max(max_len, length)

        adjusted = min(max(max_len + 2, min_width), max_width)
        ws.column_dimensions[col_letter].width = adjusted


def apply_table_style(ws, header_row: int = 1, data_start_row: int = 2, data_end_row: int = None):
    """应用默认表格样式：表头加粗蓝色背景、数据区域边框"""
    if data_end_row is None:
        data_end_row = ws.max_row

    max_col = ws.max_column

    # 表头样式
    for col in range(1, max_col + 1):
        cell = ws.cell(row=header_row, column=col)
        cell.font = DEFAULT_HEADER_FONT
        cell.fill = DEFAULT_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # 数据区域边框
    for row in range(data_start_row, data_end_row + 1):
        for col in range(1, max_col + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = DEFAULT_BORDER
            cell.alignment = Alignment(vertical="center")


def copy_cell_style(src_cell, dst_cell):
    """复制单元格的所有样式"""
    if src_cell.has_style:
        dst_cell.font = copy(src_cell.font)
        dst_cell.border = copy(src_cell.border)
        dst_cell.fill = copy(src_cell.fill)
        dst_cell.number_format = src_cell.number_format
        dst_cell.protection = copy(src_cell.protection)
        dst_cell.alignment = copy(src_cell.alignment)


class ExcelFileHandler:
    """Excel 文件操作管理"""

    @staticmethod
    def save_temp(wb: openpyxl.Workbook, file_name: Optional[str] = None,
                  output_dir: Optional[str] = None) -> Dict[str, Any]:
        """将 Workbook 保存到指定目录或用户会话目录"""
        if output_dir:
            save_dir = Path(output_dir)
        else:
            save_dir = ExcelFileHandler.get_session_dir()

        save_dir.mkdir(parents=True, exist_ok=True)

        if not file_name:
            file_name = f"excel_{uuid.uuid4().hex[:8]}.xlsx"
        elif not file_name.endswith(".xlsx") and not file_name.endswith(".csv"):
            file_name += ".xlsx"

        output_path = save_dir / file_name
        if output_path.exists():
            output_path = save_dir / f"{output_path.stem}_{uuid.uuid4().hex[:8]}{output_path.suffix}"
        wb.save(str(output_path))

        file_size = output_path.stat().st_size
        return {
            "file_path": str(output_path.absolute()),
            "file_size": file_size,
        }

    @staticmethod
    def get_session_dir() -> Path:
        """获取当前用户会话的文件存储目录（遵循租户附件存储规范）

        路径: storage/tenants/{tenant_id}/conversation/
        无租户时: storage/tenants/_anonymous/conversation/
        """
        try:
            from src.core.storage import ensure_tenant_storage_dir
            from src.saas.context import get_current_tenant_id, get_current_user_id
            tenant_id = get_current_tenant_id()
            # user_id 不进路径，仅作元数据
            _ = get_current_user_id()
            tid = tenant_id or "_anonymous"
            return Path(ensure_tenant_storage_dir(tid, "conversation"))
        except Exception as e:
            logger.warning(f"[ExcelFileHandler] 获取会话目录失败，使用临时目录: {e}")
            import tempfile
            return Path(tempfile.mkdtemp(prefix="excel_"))

    @staticmethod
    def copy_and_open(file_path: str) -> Tuple[openpyxl.Workbook, Dict[str, Any]]:
        """打开文件（不修改原文件，返回 Workbook 对象）"""
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        wb = openpyxl.load_workbook(str(src))
        info = {
            "original_path": str(src.absolute()),
            "original_name": src.name,
            "original_size": src.stat().st_size,
        }
        return wb, info

    @staticmethod
    def resolve_path(file_path: str) -> str:
        """解析文件路径（支持相对路径或 file_id）

        查找顺序：
        1. 原路径直接命中（含绝对路径）
        2. Redis 元数据命中（file_id -> uploaded_file:{file_id}.path，最可靠）
        3. 磁盘全场景扫描兜底（storage/tenants/{tenant}/{scene}/，命中后回写 Redis 自愈）
        """
        p = Path(file_path)
        # 防路径穿越：含 .. 的相对路径不得进行 exists 检查或路径拼接
        # （Path.exists() 和 Path()/.. 都会自动 resolve 后命中项目外系统文件）
        if not p.is_absolute() and ".." in p.parts:
            return str(p.absolute())
        if p.exists():
            return str(p.absolute())

        # Redis 元数据 + 磁盘全场景扫描兜底：file_id 上传时写了 uploaded_file:{file_id}
        # 元数据；元数据丢失（迁移/过期）时按文件名主干全场景扫描磁盘并自愈回写
        try:
            from src.core.storage import resolve_uploaded_file_path
            resolved = resolve_uploaded_file_path(file_path)
            if resolved:
                return resolved
        except (ImportError, AttributeError):
            pass
        return str(p.absolute())

    @staticmethod
    def detect_file_type(file_path: str) -> str:
        """检测文件类型（xlsx/csv/json）"""
        path = Path(file_path)
        ext = path.suffix.lower()
        if ext in (".xlsx", ".xlsm"):
            return "xlsx"
        elif ext == ".csv":
            return "csv"
        elif ext == ".json":
            return "json"
        elif ext == ".xls":
            return "xls"
        return ext.lstrip(".") or "unknown"
