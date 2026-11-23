"""
数据分析 - Excel 上传结构校验、表头检测与规范化

设计见 docs/subagent/data-analysis/excel-upload-validation-design.md。

三档判定（逐 sheet 独立，互不连坐）：
- ok：第一行即表头且表头行无合并；
- fixable：表头在第 2~10 行可识别（确认后跳过前 N 行）或数据区存在合并单元格
  （语义明确，自动展开填充）；
- error：无法识别表头 / 表头行存在合并单元格（列归属歧义必须用户取消）/ 全空。

校验全部确定性代码（openpyxl/pandas），无 LLM 参与。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# 与 value_cleaner 共享口径（缺失标记表等常量在其模块内）

MAX_HEADER_SCAN_ROWS = 10  # 表头检测扫描前 N 行
SAMPLE_PREVIEW_ROWS = 5  # fixable 预览行数

# 表头行候选得分阈值（用 tests/fixtures 回归样本校准；见设计 §5）
_HEADER_SCORE_THRESHOLD = 2.0
_HEADER_NON_EMPTY_RATIO = 0.6

VERDICT_OK = "ok"
VERDICT_FIXABLE = "fixable"
VERDICT_ERROR = "error"


def _cell_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _is_number_like(v: str) -> bool:
    if not v:
        return False
    try:
        float(v.replace(",", "").replace("，", ""))
        return True
    except ValueError:
        return False


def _row_stats(cells: List[str]) -> Dict[str, float]:
    non_empty = [c for c in cells if c]
    n = len(non_empty)
    if n == 0:
        return {"non_empty_ratio": 0.0, "non_empty_count": 0, "text_ratio": 0.0, "unique_ratio": 0.0}
    texts = [c for c in non_empty if not _is_number_like(c)]
    return {
        "non_empty_ratio": n / max(len(cells), 1),
        "non_empty_count": n,
        "text_ratio": len(texts) / n,
        "unique_ratio": len(set(non_empty)) / n,
    }


def _numeric_ratio(cells: List[str]) -> float:
    non_empty = [c for c in cells if c]
    if not non_empty:
        return 0.0
    nums = [c for c in non_empty if _is_number_like(c)]
    return len(nums) / len(non_empty)


def detect_header_row(rows: List[List[str]]) -> Optional[int]:
    """在原始行列表（0 起）中检测表头行，返回 0 起索引；无候选返回 None。

    打分特征（设计 §5）：非空占比、文本占比、唯一性、下一行数值占比跃升；
    否决项：整行大部分为空且只有 1~2 个散落文字（典型标题/说明行）。
    """
    n_rows = len(rows)
    if n_rows == 0:
        return None

    norm = [[_cell_str(c) for c in r] for r in rows]
    width = max(len(r) for r in norm)
    norm = [r + [""] * (width - len(r)) for r in norm]

    best_idx: Optional[int] = None
    best_score = -1.0
    scored: List[Tuple[int, float]] = []
    for i in range(min(MAX_HEADER_SCAN_ROWS, n_rows)):
        stats = _row_stats(norm[i])
        # 否决项：标题/说明行
        if stats["non_empty_count"] <= 2 and stats["non_empty_ratio"] < 0.5:
            continue
        jump = 0.0
        if i + 1 < n_rows:
            jump = max(0.0, _numeric_ratio(norm[i + 1]) - _numeric_ratio(norm[i]))
        score = (
            2.0 * stats["non_empty_ratio"]
            + stats["text_ratio"]
            + stats["unique_ratio"]
            + jump
        )
        scored.append((i, score))
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx is None:
        return None
    stats = _row_stats(norm[best_idx])
    if best_score < _HEADER_SCORE_THRESHOLD or stats["non_empty_ratio"] < _HEADER_NON_EMPTY_RATIO:
        return None
    # 表头几乎总在数据行之前：接近最高分（±1.0）的候选中取最早行，
    # 避免数据行凭 unique_ratio 反超含重复/合并列名的真表头
    return next(i for i, s in scored if s >= best_score - 1.0)


def _read_rows_pandas(file_path: str, sheet_name: str, nrows: int, header: Optional[int] = None) -> List[List[str]]:
    """读原始值矩阵（dtype=str），用于检测与预览。"""
    import pandas as pd

    df = pd.read_excel(file_path, sheet_name=sheet_name, header=header, dtype=str, nrows=nrows)
    return [[("" if pd.isna(v) else str(v).strip()) for v in row] for row in df.values.tolist()]


def _read_csv_rows(file_path: str, nrows: int) -> List[List[str]]:
    import pandas as pd

    df = pd.read_csv(file_path, header=None, dtype=str, nrows=nrows, encoding="utf-8-sig")
    return [[("" if pd.isna(v) else str(v).strip()) for v in row] for row in df.values.tolist()]


def _get_all_merged_ranges(file_path: str) -> Dict[str, List[Tuple[int, int, int, int]]]:
    """读取全部 sheet 的合并单元格区域，返回 {sheet_name: [(min_row, min_col, max_row, max_col)]}，均 1 起。

    .xlsx 走 openpyxl（整簿只加载一次）；.xls 尝试 xlrd，不可用时返回空表
    （由调用方降级：合并检查不可见，表头检测照常）。
    """
    ext = Path(file_path).suffix.lower()
    try:
        if ext == ".xls":
            import xlrd

            book = xlrd.open_workbook(file_path)
            return {
                book.sheet_by_index(i).name: [
                    (r_lo + 1, c_lo + 1, r_hi + 1, c_hi + 1)
                    for (r_lo, r_hi, c_lo, c_hi) in getattr(book.sheet_by_index(i), "merged_cells", [])
                ]
                for i in range(book.nsheets)
            }
        from openpyxl import load_workbook

        # 注意：read_only 模式的 worksheet 没有 merged_cells 属性，须常规加载
        wb = load_workbook(file_path, data_only=True)
        try:
            return {
                name: [
                    (rng.min_row, rng.min_col, rng.max_row, rng.max_col)
                    for rng in wb[name].merged_cells.ranges
                ]
                for name in wb.sheetnames
            }
        finally:
            wb.close()
    except Exception as e:
        logger.warning(f"[excel_validator] 读取合并单元格失败 {file_path}: {e}")
        return {}


def validate_workbook(file_path: str) -> Dict[str, Any]:
    """校验工作簿全部 sheet，返回逐 sheet 判定（含列级值质量 warning 与预览）。"""
    import pandas as pd

    ext = Path(file_path).suffix.lower()
    if ext == ".csv":
        rows = _read_csv_rows(file_path, MAX_HEADER_SCAN_ROWS + SAMPLE_PREVIEW_ROWS)
        sheets = [_validate_sheet_rows("Sheet1", rows, [])]
    else:
        sheet_names = list(pd.ExcelFile(file_path).sheet_names)
        merged_all = _get_all_merged_ranges(file_path)
        sheets = []
        for name in sheet_names:
            rows = _read_rows_pandas(file_path, name, MAX_HEADER_SCAN_ROWS + SAMPLE_PREVIEW_ROWS)
            merged = merged_all.get(name, [])
            sheets.append(_validate_sheet_rows(name, rows, merged))
    return {"sheets": sheets}


def _validate_sheet_rows(
    sheet_name: str,
    rows: List[List[str]],
    merged_ranges: List[Tuple[int, int, int, int]],
) -> Dict[str, Any]:
    """对单个 sheet 的原始行做三档判定。merged_ranges 为 1 起坐标。"""
    issues: List[str] = []

    header_idx = detect_header_row(rows)  # 0 起
    if header_idx is None:
        return {
            "sheet_name": sheet_name,
            "verdict": VERDICT_ERROR,
            "header_row": None,
            "issues": [
                "无法识别表头行：前 10 行中没有一行符合表头特征"
                "（一行中大部分单元格有内容、多为文本、基本不重复）。"
                "请将字段名称整理到第一行后重新上传。"
            ],
            "column_warnings": [],
        }
    header_row = header_idx + 1  # 1 起，用户口径

    # 表头行合并单元格（列归属歧义）-> error；跨表头行与数据区的合并同判
    header_merge = [
        r for r in merged_ranges if r[0] <= header_row and r[2] >= header_row
    ]
    if header_merge:
        locs = ", ".join(f"第{r[0]}行" for r in header_merge[:3])
        return {
            "sheet_name": sheet_name,
            "verdict": VERDICT_ERROR,
            "header_row": header_row,
            "issues": [
                f"表头行（第 {header_row} 行）存在合并单元格（{locs}）。"
                "合并单元格会让 AI 无法判断该列归属，数据会算错。"
                "请取消该行合并，每列填写独立的表头名称后重新上传。"
            ],
            "column_warnings": [],
        }

    # 数据区合并 -> fixable（自动展开填充）
    data_merges = [r for r in merged_ranges if r[0] > header_row]
    if data_merges:
        issues.append(
            f"数据区存在 {len(data_merges)} 处合并单元格，"
            "导入时将自动把合并值填充到覆盖的每个单元格（不会丢失数据）。"
        )

    if header_idx > 0:
        issues.append(f"第 1~{header_idx} 行将被视为说明行丢弃，以第 {header_row} 行为表头。")

    # 无数据行 -> error
    data_rows = rows[header_idx + 1:]
    has_data = any(any(c for c in r) for r in data_rows)
    if not has_data and len(rows) > header_idx + 1:
        pass  # 只有预览范围内无数据，不能据此判全空（只读了前 N 行），交给后续 parse
    if len(rows) <= header_idx + 1:
        return {
            "sheet_name": sheet_name,
            "verdict": VERDICT_ERROR,
            "header_row": header_row,
            "issues": ["该工作表除表头外没有任何数据行，请检查内容后重新上传。"],
            "column_warnings": [],
        }

    # 值级质量 warning（对表头 + 预览行样本清洗，不阻断）
    column_warnings, _quality = _sample_column_warnings(rows, header_idx)

    verdict = VERDICT_FIXABLE if issues else VERDICT_OK

    result: Dict[str, Any] = {
        "sheet_name": sheet_name,
        "verdict": verdict,
        "header_row": header_row if verdict == VERDICT_FIXABLE else 1,
        "issues": issues,
        "column_warnings": column_warnings,
    }
    if verdict == VERDICT_FIXABLE:
        result["preview"] = _build_preview(rows, header_idx)
    return result


def _sample_column_warnings(rows: List[List[str]], header_idx: int) -> Tuple[List[str], Dict[str, Any]]:
    """对表头 + 预览行样本跑值清洗，产出列级 warning（轻量，仅前几行）。"""
    from src.services.data_analysis.value_cleaner import (
        clean_dataframe,
        column_warnings_from_quality,
    )

    header = rows[header_idx]
    sample_rows = rows[header_idx + 1 : header_idx + 1 + SAMPLE_PREVIEW_ROWS]
    width = len(header)
    import pandas as pd

    data = [(r + [""] * width)[:width] for r in sample_rows]
    if not data:
        return [], {}
    df = pd.DataFrame(data, columns=[h or f"列{i + 1}" for i, h in enumerate(header)])
    _, quality = clean_dataframe(df)
    return column_warnings_from_quality(quality), quality


def _build_preview(rows: List[List[str]], header_idx: int) -> Dict[str, Any]:
    header = rows[header_idx]
    data_rows = rows[header_idx + 1 : header_idx + 1 + SAMPLE_PREVIEW_ROWS]
    return {"columns": header, "rows": data_rows}


# ============== 规范化（用户确认后执行） ==============


def normalize_workbook(
    file_path: str,
    decisions: List[Dict[str, Any]],
) -> str:
    """按用户决策生成规范化派生文件，返回派生文件路径。

    decisions: [{"sheet_name": str, "header_row": int(1起), "selected": bool}]
    - .xlsx：openpyxl 展开 data 区合并 -> 删除表头前说明行 -> 仅保留选中 sheet；
    - .csv：跳行重写；
    - .xls：pandas + xlrd 合并信息尽力展开（xlrd 不可用时不展开合并，仅跳行）。
    原始文件不动。重复调用覆盖重建（幂等）。
    """
    ext = Path(file_path).suffix.lower()
    stem = Path(file_path).stem
    parent = Path(file_path).parent
    out_path = parent / f"{stem}_n{'.csv' if ext == '.csv' else '.xlsx'}"

    selected = [d for d in decisions if d.get("selected")]

    if ext == ".csv":
        import pandas as pd

        header_row = int(selected[0].get("header_row", 1)) if selected else 1
        df = pd.read_csv(file_path, header=header_row - 1, dtype=str)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        return str(out_path)

    if ext == ".xls":
        return _normalize_xls(file_path, out_path, selected)

    return _normalize_xlsx(file_path, out_path, selected)


def _normalize_xlsx(file_path: str, out_path: Path, selected: List[Dict[str, Any]]) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(file_path, data_only=True)
    keep = {d["sheet_name"] for d in selected}
    for name in list(wb.sheetnames):
        if name not in keep:
            del wb[name]

    for d in selected:
        ws = wb[d["sheet_name"]]
        header_row = int(d.get("header_row", 1))
        # 先展开数据区合并（值填充到每个覆盖单元格），再移除合并、删说明行；
        # 注意 MergedCell 的 value 只读，必须先 unmerge 再填充；
        # 若仍有覆盖表头行的合并（不应出现，confirm 传入决策固定），fail-fast
        for rng in list(ws.merged_cells.ranges):
            if rng.min_row > header_row:
                anchor = ws.cell(row=rng.min_row, column=rng.min_col).value
                merge_bounds = (rng.min_row, rng.min_col, rng.max_row, rng.max_col)
                ws.unmerge_cells(str(rng))
                for row in range(merge_bounds[0], merge_bounds[2] + 1):
                    for col in range(merge_bounds[1], merge_bounds[3] + 1):
                        ws.cell(row=row, column=col).value = anchor
            elif rng.min_row <= header_row <= rng.max_row:
                raise ValueError(
                    f"sheet「{d['sheet_name']}」表头行合并单元格未处理，无法规范化"
                )
        if header_row > 1:
            ws.delete_rows(1, header_row - 1)

    wb.save(out_path)
    return str(out_path)


def _normalize_xls(file_path: str, out_path: Path, selected: List[Dict[str, Any]]) -> str:
    """.xls 规范化：pandas 读原始矩阵 + xlrd 合并信息展开（尽力），重写为 .xlsx。"""
    import pandas as pd

    wb_out = None
    try:
        from openpyxl import Workbook

        wb_out = Workbook()
        wb_out.remove(wb_out.active)

        merge_info: Dict[str, list] = {}
        try:
            import xlrd

            book = xlrd.open_workbook(file_path)
            for d in selected:
                sh = book.sheet_by_name(d["sheet_name"])
                merge_info[d["sheet_name"]] = [
                    (r_lo + 1, c_lo + 1, r_hi + 1, c_hi + 1)
                    for (r_lo, r_hi, c_lo, c_hi) in getattr(sh, "merged_cells", [])
                ]
        except Exception as e:
            logger.warning(f"[excel_validator] .xls 合并信息读取失败（跳过合并展开）: {e}")

        for d in selected:
            name = d["sheet_name"]
            header_row = int(d.get("header_row", 1))
            raw = pd.read_excel(file_path, sheet_name=name, header=None, dtype=str)
            values = [[("" if pd.isna(v) else str(v).strip()) for v in row] for row in raw.values.tolist()]

            for (r1, c1, r2, c2) in merge_info.get(name, []):
                if r1 > header_row and len(values) >= r1 and len(values[r1 - 1]) >= c1:
                    anchor = values[r1 - 1][c1 - 1]
                    for r in range(r1, min(r2, len(values)) + 1):
                        for c in range(c1, c2 + 1):
                            if r - 1 < len(values) and c - 1 < len(values[r - 1]):
                                values[r - 1][c - 1] = anchor

            body = values[header_row:]
            cols = values[header_row - 1] if values else []
            width = len(cols)
            df = pd.DataFrame([(r + [""] * width)[:width] for r in body], columns=cols)
            ws = wb_out.create_sheet(title=name[:31])
            for row in [cols] + df.values.tolist():
                ws.append(row)

        wb_out.save(out_path)
        return str(out_path)
    finally:
        if wb_out is not None:
            wb_out.close()
