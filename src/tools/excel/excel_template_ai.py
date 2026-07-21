#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excel 智能模板填充（无状态）

设计文档：docs/tools/excel/excel-template-ai-design.md
开发计划：docs/plans/plan-excel-template-ai.md

核心契约：
- 无状态。调用方每次必传样例文件路径，工具读样例 → 数据感知分析结构 → 按样例版式填充数据。
- 行数不匹配：样例明细区 K 行、本次 data.rows M 行，多/少/相等都正确处理，
  **输出明细区不得残留任何样例示例数据**（行级 + 单元格级强校验）。
- 样式保留：以克隆样例为基底，填充只写 .value 不重建样式；插入行复制模板行样式 + 行高；
  行增删后显式重锚定合并单元格范围（openpyxl insert/delete_rows 不自动平移）。
"""

import json
import re
from copy import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.utils import get_column_letter
from loguru import logger

from src.tools.excel.excel_lib import ExcelFileHandler, copy_cell_style


# ============================================================
# 数据契约 / 结构模型（Pydantic）
# ============================================================

from pydantic import BaseModel, Field, model_validator


class _NoneTolerant(BaseModel):
    """LLM 返回的 JSON 常带 null（如无表头的列 header=null）。
    构造前剥离 None 值，让字段走各自默认，避免 pydantic v2 对 str 字段拒收 None。"""

    @model_validator(mode="before")
    @classmethod
    def _drop_none_values(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


class ColumnBinding(_NoneTolerant):
    """明细列绑定：列号 + 表头 + 绑定的 data.rows 键（None 表示该列不绑键，渲染时清空）"""

    col: int
    header: Optional[str] = None
    bind: Optional[str] = None



class MetaField(_NoneTolerant):
    """顶部信息字段（左右结构：标题在左、值在右）。
    (row, col) 是**标题单元格**（标题文字所在格）；值由渲染器写到标题右侧相邻格。
    LLM 找标题文字天然可靠；让它挑"右侧值格"易错（会把值写进标题格覆盖标题）。
    若值不在标题紧邻右侧（隔列/合并区），用 value_col 显式指定值格列。"""

    row: int
    col: int  # 标题单元格列
    label: Optional[str] = None
    bind: Optional[str] = None
    value_col: Optional[int] = None  # 值单元格列；None 时取 col+1（标题右侧相邻）



class TotalCell(_NoneTolerant):
    """合计区单元格"""

    row: int
    col: int
    label: Optional[str] = None
    bind: Optional[str] = None  # grand_total 或 per_capita 的键；None=未绑定，渲染时清空



class GroupRegion(_NoneTolerant):
    """一个分组明细区 + 其小计行（Phase B）。match 按 data 行字段值归属"""

    name: Optional[str] = None  # 小计行标签，如"房餐车小计"；用作 group_subtotals 的键
    detail_first_row: int = 0
    detail_last_row: int = 0
    detail_template_row: int = 0
    subtotal_row: int = 0  # 0 = 无小计行
    subtotal_col: int = 0
    match: Dict[str, List[str]] = Field(default_factory=dict)  # {field: [values]}


class SheetStructure(BaseModel):
    """analyze_structure 产出的样例结构（瞬态，不持久化）"""

    title: Optional[Dict[str, Any]] = None  # {row, col, merge?}
    meta_fields: List[MetaField] = Field(default_factory=list)
    columns: List[ColumnBinding] = Field(default_factory=list)
    # 单明细区（Phase A 兼容 / 无分组时 fallback）
    detail_first_row: int = 0
    detail_last_row: int = 0  # 样例明细区末行（含），K = last - first + 1
    detail_template_row: int = 0  # 插入新行时复制样式的模板行
    # 多分组（Phase B）：非空时优先于单明细区
    groups: List[GroupRegion] = Field(default_factory=list)
    totals: List[TotalCell] = Field(default_factory=list)



class FillData(BaseModel):
    """fill_with_sample 的 data 参数：领域无关"""

    rows: List[Dict[str, Any]] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
    columns: Optional[List[Dict[str, Any]]] = None  # 显式列绑定（可选，优先于 LLM 推断）
    group_subtotals: Dict[str, Any] = Field(default_factory=dict)
    totals: Dict[str, Any] = Field(default_factory=dict)
    # totals 形如 {"grand_total": 10230, "per_capita": {"成人人均": 2280}}



# ============================================================
# 样例读取
# ============================================================


def read_sample_grid(file_path: str, max_rows: int = 200, max_cols: int = 40) -> Dict[str, Any]:
    """读取样例文件的结构化网格（供 LLM 分析），不改原文件。

    返回：
      cells: [{row, col, value, bold, fill_rgb}]
      merges: [(min_col, min_row, max_col, max_row)]
      max_row / max_col
      row_heights: {row: height}（仅记录有自定义行高的行）
    """
    wb, _ = ExcelFileHandler.copy_and_open(file_path)
    try:
        ws = wb.active
        cells: List[Dict[str, Any]] = []
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, max_rows),
                                min_col=1, max_col=min(ws.max_column, max_cols)):
            for cell in row:
                if cell.value is None:
                    continue
                bold = bool(cell.font and cell.font.bold)
                fill_rgb = ""
                try:
                    if cell.fill and cell.fill.start_color and cell.fill.start_color.rgb:
                        rgb = str(cell.fill.start_color.rgb)
                        if rgb and rgb != "00000000":
                            fill_rgb = rgb
                except Exception:
                    fill_rgb = ""
                cells.append({
                    "row": cell.row,
                    "col": cell.column,
                    "value": str(cell.value),
                    "bold": bold,
                    "fill": fill_rgb,
                })

        merges = []
        for mr in ws.merged_cells.ranges:
            merges.append((mr.min_col, mr.min_row, mr.max_col, mr.max_row))

        row_heights = {}
        for r, dim in ws.row_dimensions.items():
            if dim.height is not None:
                row_heights[r] = dim.height

        return {
            "sheet_name": ws.title,
            "cells": cells,
            "merges": merges,
            "max_row": ws.max_row,
            "max_col": ws.max_column,
            "row_heights": row_heights,
        }
    finally:
        wb.close()


def _grid_to_text(grid: Dict[str, Any]) -> str:
    """把网格渲染成 LLM 易读的文本（带坐标 + 样式标记 + 合并区）"""
    lines = []
    lines.append(f"工作表: {grid['sheet_name']}  最大行={grid['max_row']}  最大列={grid['max_col']}")
    lines.append("单元格（格式 R行C列: 值 [粗体] [填充色]）:")
    for c in grid["cells"]:
        marks = []
        if c["bold"]:
            marks.append("粗体")
        if c["fill"]:
            marks.append(f"填充={c['fill']}")
        tag = f" [{','.join(marks)}]" if marks else ""
        lines.append(f"  R{c['row']}C{c['col']}: {c['value']}{tag}")
    if grid["merges"]:
        lines.append("合并单元格（min_col,min_row,max_col,max_row）:")
        for mc, mr, xc, xr in grid["merges"]:
            a = f"{get_column_letter(mc)}{mr}"
            b = f"{get_column_letter(xc)}{xr}"
            lines.append(f"  {a}:{b}")
    return "\n".join(lines)


# ============================================================
# AI 结构分析（数据感知）
# ============================================================


def analyze_structure(
    sample_file_path: str,
    data: FillData,
    *,
    llm_callable: Optional[Callable[[str], str]] = None,
) -> SheetStructure:
    """数据感知地分析样例结构。

    用 data.rows 的键、data.meta 的键辅助 LLM 判定绑定。
    llm_callable 为 None 时走内部默认 LLM（同步 SDK）；测试可注入 mock。
    """
    grid = read_sample_grid(sample_file_path)
    grid_text = _grid_to_text(grid)

    row_keys = sorted({k for row in data.rows for k in row.keys()})[:50]
    per_capita = data.totals.get("per_capita") or {}
    total_keys = sorted(set(list(data.totals.keys()) + list(per_capita.keys())))
    data_keys = {
        "row_keys": row_keys,
        "meta_keys": sorted(data.meta.keys()),
        "total_keys": total_keys,
    }

    prompt = _build_analyze_prompt(grid_text, data_keys, data)
    raw = (llm_callable or _default_llm)(prompt)
    parsed = _extract_json(raw)
    structure = _coerce_structure(parsed, grid)

    has_single = (structure.detail_first_row > 0
                  and structure.detail_last_row >= structure.detail_first_row)
    if not structure.groups and not has_single:
        raise ValueError(
            "结构分析未能识别明细行区（需 groups 或 detail_first_row/detail_last_row）"
        )
    if not structure.columns:
        raise ValueError("结构分析未能识别明细列（columns 为空）")
    return structure


def _build_analyze_prompt(grid_text: str, data_keys: Dict[str, List[str]], data: FillData) -> str:
    return f"""你是 Excel 表格结构分析助手。分析下面的"样例表格"结构，输出 JSON 描述哪些单元格填什么。

## 样例表格（含示例数据）
{grid_text}

## 本次要填入的数据键（用于辅助判断绑定）
- 明细行可用的键: {data_keys['row_keys']}
- 顶部信息(meta)可用的键: {data_keys['meta_keys']}
- 合计(totals)可用的键: {data_keys['total_keys']}

## 任务
识别样例的结构，输出 JSON（只返回 JSON，不要其他文字）：
{{
  "title": {{"row": 1, "col": 1, "merge": "A1:G1"}},
  "meta_fields": [
    {{"row": 2, "col": 3, "label": "日期：", "bind": "date"}}
  ],
  "columns": [
    {{"col": 1, "header": "成本类别", "bind": "category"}},
    {{"col": 6, "header": "费用小计", "bind": "amount"}}
  ],
  "groups": [
    {{"name": "房餐车小计", "detail_first_row": 4, "detail_last_row": 7, "detail_template_row": 4, "subtotal_row": 8, "subtotal_col": 6, "match": {{"category": ["住宿", "餐饮", "用车"]}}}},
    {{"name": "门票小计", "detail_first_row": 9, "detail_last_row": 11, "detail_template_row": 9, "subtotal_row": 12, "subtotal_col": 6, "match": {{"category": ["门票/项目"]}}}}
  ],
  "totals": [
    {{"row": 13, "col": 6, "label": "合计", "bind": "grand_total"}},
    {{"row": 13, "col": 4, "label": "成人人均", "bind": "成人人均"}}
  ]
}}

## 关键规则
1. **columns 必须覆盖明细区所有列**：样例明细表头有几列就列几列；能绑到上面"明细行可用的键"的填 bind，绑不上的列 bind 填 null（渲染时清空，避免残留样例数据）。多个分组共用同一组 columns。
2. **groups（分区小计版式必填）**：样例若出现"XX小计"行（如"房餐车小计""门票小计"），每个小计行对应一个分组：detail_first_row/detail_last_row 是该小计行**上方**紧邻的明细示例数据首末行，subtotal_row/subtotal_col 是小计行及其合计值所在列，match 用"明细行可用的键"指明哪些值归该组（常用 category）。detail_template_row 取该组明细首行。**无分区小计的简单版式，groups 填空数组，改填 detail_first_row/detail_last_row。**
3. **meta_fields 的 (row,col) 是"标题单元格"（标题文字所在格），不是值格**。这类是左右结构：标题在左格、值要填到它**右侧相邻格**。渲染器会自动把值写到 col+1，所以你只需标注标题格。例如"日期："在 C2（col=3）、值要填到 D2，则 meta_fields 写 {{row:2,col:3,bind:"date"}}（col=3 是标题格 C2，渲染器自动写到 D2）。**千万不要把 col 写成值格或写进标题格的 bind——否则值会覆盖标题。** 若某字段值不在标题紧邻右侧（如隔一列、或在合并区右端），加 value_col 显式指定值格列。
4. **totals**：合计行 + 人均/标量单元格。bind 用上面"合计可用的键"（grand_total 或 per_capita 的键名，如"成人人均"）。
5. **bind 优先用上面给出的键**；键里没有的不要编造。
6. title/meta/totals 没有对应内容时填 null / 空数组。
7. 只返回 JSON。"""


def _extract_json(raw: str) -> Dict[str, Any]:
    """从 LLM 输出提取 JSON（兼容 ```json 代码块）"""
    if not raw:
        raise ValueError("LLM 返回空内容")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    raw = raw.strip()
    # 截取最外层花括号
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 输出 JSON 解析失败: {e}; 原文片段: {raw[:200]}")


def _coerce_record(model_cls, d: Any):
    """单条 LLM 记录 → model。剥离 None 后构造；缺必填/类型错等不规范记录返回 None（跳过）而非整体崩溃。"""
    if not isinstance(d, dict):
        return None
    try:
        return model_cls(**d)
    except Exception as e:
        logger.debug(f"[excel_template_ai] 丢弃一条 {model_cls.__name__}（LLM 数据不规范）: {e}; raw={d}")
        return None


def _coerce_structure(parsed: Dict[str, Any], grid: Dict[str, Any]) -> SheetStructure:
    """把 LLM JSON 整理为 SheetStructure，做基本校验"""
    title = parsed.get("title")
    if title and not isinstance(title, dict):
        title = None

    meta_fields = [m for m in (_coerce_record(MetaField, mf) for mf in (parsed.get("meta_fields") or [])) if m]
    columns = [c for c in (_coerce_record(ColumnBinding, c) for c in (parsed.get("columns") or [])) if c]
    totals = [t for t in (_coerce_record(TotalCell, t) for t in (parsed.get("totals") or [])) if t]
    groups = [g for g in (_coerce_record(GroupRegion, g) for g in (parsed.get("groups") or [])) if g]

    # 分组行号一致性校验：detail_first_row 必须 <= detail_last_row，否则 K<=0 会插入超额行到错误位置
    for g in groups:
        if g.detail_first_row <= 0 or g.detail_last_row < g.detail_first_row:
            raise ValueError(
                f"分组 '{g.name}' 行号非法：detail_first_row={g.detail_first_row}, "
                f"detail_last_row={g.detail_last_row}"
            )

    first = int(parsed.get("detail_first_row") or 0)
    last = int(parsed.get("detail_last_row") or 0)
    tmpl = int(parsed.get("detail_template_row") or first)

    return SheetStructure(
        title=title,
        meta_fields=meta_fields,
        columns=columns,
        groups=groups,
        detail_first_row=first,
        detail_last_row=last,
        detail_template_row=tmpl if tmpl > 0 else first,
        totals=totals,
    )


# ============================================================
# 渲染（行数不匹配 + 样式保留 + 合并重锚定）
# ============================================================


def _snapshot_merges(ws) -> List[Tuple[int, int, int, int]]:
    return [(mr.min_col, mr.min_row, mr.max_col, mr.max_row) for mr in ws.merged_cells.ranges]


def _copy_row_format(ws, src_row: int, dst_row: int, max_col: int):
    """复制整行格式：单元格样式 + 行高 + 隐藏状态（用于插入的新行）"""
    for col in range(1, max_col + 1):
        src_cell = ws.cell(row=src_row, column=col)
        dst_cell = ws.cell(row=dst_row, column=col)
        if src_cell.has_style:
            copy_cell_style(src_cell, dst_cell)
    src_dim = ws.row_dimensions[src_row]
    dst_dim = ws.row_dimensions[dst_row]
    if src_dim.height is not None:
        dst_dim.height = src_dim.height
    dst_dim.hidden = src_dim.hidden


def _effective_groups(structure: SheetStructure) -> List[GroupRegion]:
    """生效的分组列表：有 groups 用 groups；否则用单明细区合成一个无小计的组"""
    if structure.groups:
        return structure.groups
    if structure.detail_first_row > 0 and structure.detail_last_row >= structure.detail_first_row:
        return [GroupRegion(
            name="_default",
            detail_first_row=structure.detail_first_row,
            detail_last_row=structure.detail_last_row,
            detail_template_row=structure.detail_template_row or structure.detail_first_row,
        )]
    return []


def _row_matches_group(row: Dict[str, Any], g: GroupRegion) -> bool:
    """行是否归属该分组：row['group']==name 或 match 中某字段值命中"""
    if g.name and row.get("group") == g.name:
        return True
    for field, values in (g.match or {}).items():
        if row.get(field) in values:
            return True
    return False


def _assign_rows(rows: List[Dict[str, Any]], groups: List[GroupRegion]) -> Tuple[Dict[str, List], List]:
    """把 data.rows 按分组规则分配。返回 (assigned, unmatched)。
    单组且无 match 规则时全部归该组。"""
    assigned = {g.name: [] for g in groups}
    unmatched = []
    single_no_match = len(groups) == 1 and not groups[0].match and groups[0].name == "_default"
    for row in rows:
        if single_no_match:
            assigned[groups[0].name].append(row)
            continue
        placed = False
        for g in groups:
            if _row_matches_group(row, g):
                assigned[g.name].append(row)
                placed = True
                break
        if not placed:
            unmatched.append(row)
    if unmatched and groups:
        # 不丢弃：未匹配行并入第一个分组（避免静默丢数据）
        for row in unmatched:
            assigned[groups[0].name].append(row)
    return assigned, unmatched


def _compute_plan(structure: SheetStructure, data: FillData):
    """计算渲染计划。返回 (groups, assigned, plan, unmatched, min_col, max_col, bound_by_col)。
    plan = [(group, rows, delta)]，delta = M_group - K_group。"""
    groups = _effective_groups(structure)
    assigned, unmatched = _assign_rows(data.rows, groups)
    plan = []
    for g in groups:
        K = g.detail_last_row - g.detail_first_row + 1
        rows = assigned.get(g.name, [])
        plan.append((g, rows, len(rows) - K))
    min_col, max_col, bound_by_col = _column_span(structure)
    return groups, assigned, plan, unmatched, min_col, max_col, bound_by_col


def _final_row(orig_row: int, plan) -> int:
    """原始行号 → 渲染后行号：累加所有"末行在 orig_row 上方"的分组的 delta。
    因为分组自下而上增删，某分组的增删只影响其下方的行。"""
    return orig_row + sum(delta for (g, _rows, delta) in plan if g.detail_last_row < orig_row)


def _is_intra_detail(min_row: int, max_row: int, groups: List[GroupRegion]) -> bool:
    """合并范围是否完全落在某分组的明细区内（这类合并在行数变化后无法简单平移，本期丢弃避免错乱）"""
    for g in groups:
        if g.detail_first_row <= min_row and max_row <= g.detail_last_row:
            return True
    return False


def _row_was_deleted(orig_row: int, plan) -> bool:
    """orig_row 是否落在某个收缩分组被删除的尾部区间（这些行已不存在，恢复行高时跳过）"""
    for g, rows, delta in plan:
        if delta < 0:
            m_g = len(rows)
            if g.detail_first_row + m_g <= orig_row <= g.detail_last_row:
                return True
    return False


def _remap_merges(ws, saved: List[Tuple[int, int, int, int]], plan, groups: List[GroupRegion]):
    """行增删后重锚定合并范围：明细区内的丢弃，其余按 _final_row 平移"""
    for min_col, min_row, max_col, max_row in saved:
        if _is_intra_detail(min_row, max_row, groups):
            continue
        nr1 = _final_row(min_row, plan)
        nr2 = _final_row(max_row, plan)
        if nr1 <= 0 or nr2 <= 0:
            continue
        a = f"{get_column_letter(min_col)}{nr1}"
        b = f"{get_column_letter(max_col)}{nr2}"
        try:
            ws.merge_cells(f"{a}:{b}")
        except Exception as e:
            logger.debug(f"[excel_template_ai] 合并重锚定跳过 {a}:{b}: {e}")


def _resolve_subtotal(g: GroupRegion, data: FillData, rows: List[Dict[str, Any]],
                      bound_by_col: Dict[int, Optional[str]]) -> Any:
    """分组小计值：优先 data.group_subtotals[name]；否则按 subtotal_col 对应键数值求和；都没有返回 None（清空）"""
    if g.name and g.name in (data.group_subtotals or {}):
        return data.group_subtotals[g.name]
    if g.subtotal_col:
        bind = bound_by_col.get(g.subtotal_col)
        if bind:
            total, found = 0.0, False
            for r in rows:
                v = r.get(bind)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    total += v
                    found = True
            if found:
                return round(total, 2)
    return None


def _column_span(structure: SheetStructure) -> Tuple[int, int, Dict[int, Optional[str]]]:
    """明细列跨度 + 每列的绑定键。未列出的列（跨度内空隙）bind 为 None → 渲染时清空。
    保证整个明细列范围无残留，即使 structure.columns 只列了部分列。"""
    if not structure.columns:
        return (0, 0, {})
    cols = [c.col for c in structure.columns]
    bound_by_col = {c.col: c.bind for c in structure.columns}
    return min(cols), max(cols), bound_by_col


def _write_detail_row(ws, row: int, min_col: int, max_col: int,
                      bound_by_col: Dict[int, Optional[str]], row_data: Dict[str, Any]):
    """写一行明细：跨度内每列按绑定写值或清空（bind 为 None / 键缺失 → 清空），只动 .value"""
    for col in range(min_col, max_col + 1):
        cell = ws.cell(row=row, column=col)
        bind = bound_by_col.get(col)
        if bind and bind in row_data:
            cell.value = row_data[bind]
        else:
            cell.value = None  # 清空，杜绝样例残留


def _render(ws, structure: SheetStructure, data: FillData) -> int:
    """渲染到 ws（已克隆样例）。支持多分组，每组独立行数不匹配处理。
    分组自下而上增删，行号按 _final_row 重映射。返回填充的明细总行数。"""
    groups, assigned, plan, unmatched, min_col, max_col, bound_by_col = _compute_plan(structure, data)
    if unmatched:
        logger.warning(f"[excel_template_ai] {len(unmatched)} 行未匹配任何分组，并入首组: "
                       f"{[r.get('group') or r.get('category') for r in unmatched[:3]]}")
    if not groups:
        raise ValueError("结构无明细区（既无 groups 也无 detail_first_row）")

    fmt_max_col = max([c.col for c in structure.columns], default=ws.max_column)

    # 1. 快照并解除所有合并（避免增删时 openpyxl 合并范围错乱）
    saved_merges = _snapshot_merges(ws)
    # 同步快照行维度：openpyxl insert/delete_rows 不平移 row_dimensions，
    # 导致插入/删除点下方已存在的行（小计/合计等）行高丢失——这里先存后恢复。
    saved_dims = [(r, dim.height, dim.hidden)
                  for r, dim in ws.row_dimensions.items()
                  if dim.height is not None or dim.hidden]
    for mr in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(mr))

    # 2. 分组自下而上增删（用原始坐标；下方分组已处理不影响上方分组坐标）
    for g, rows, delta in reversed(plan):
        if delta > 0:
            ws.insert_rows(g.detail_last_row + 1, amount=delta)
            tmpl = g.detail_template_row or g.detail_first_row
            for i in range(delta):
                _copy_row_format(ws, tmpl, g.detail_last_row + 1 + i, fmt_max_col)
        elif delta < 0:
            M_g = len(rows)
            ws.delete_rows(g.detail_first_row + M_g, amount=-delta)  # 删除该组明细末尾 |delta| 行

    # 2.5 恢复行维度到偏移后位置（跳过被删除的样例行）
    for orig_row, height, hidden in saved_dims:
        if _row_was_deleted(orig_row, plan):
            continue
        new_row = _final_row(orig_row, plan)
        if new_row <= 0:
            continue
        dim = ws.row_dimensions[new_row]
        if height is not None:
            dim.height = height
        if hidden:
            dim.hidden = hidden

    # 3. 重锚定合并范围（明细区内的丢弃，其余按 _final_row 平移）
    _remap_merges(ws, saved_merges, plan, groups)

    # 4. 填各分组明细（明细区现按 _final_row 定位）
    for g, rows, _delta in plan:
        first_final = _final_row(g.detail_first_row, plan)
        for i, row_data in enumerate(rows):
            _write_detail_row(ws, first_final + i, min_col, max_col, bound_by_col, row_data)

    # 5. 填各分组小计（位于该组明细下方，按 _final_row 重映射；无值则清空防残留）
    for g, rows, _delta in plan:
        if g.subtotal_row and g.subtotal_col:
            r = _final_row(g.subtotal_row, plan)
            ws.cell(row=r, column=g.subtotal_col).value = _resolve_subtotal(g, data, rows, bound_by_col)

    # 6. 填顶部 meta（左右结构：标题在 mf.col，值写到右侧 value_col/col+1，标题格不动）
    for mf in structure.meta_fields:
        if not mf.bind:
            continue
        value_col = mf.value_col if mf.value_col else mf.col + 1
        ws.cell(row=mf.row, column=value_col).value = data.meta.get(mf.bind)

    # 7. 填合计区（位于所有分组下方，按 _final_row 重映射；无值 → 清空防样例残留）
    for t in structure.totals:
        r = _final_row(t.row, plan)
        ws.cell(row=r, column=t.col).value = _resolve_total_value(t.bind, data.totals)

    return len(data.rows)


def _resolve_total_value(bind: str, totals: Dict[str, Any]) -> Any:
    """从 data.totals 解析合计值。bind='grand_total' 取标量；否则当 per_capita 键"""
    if not bind:
        return None
    if bind in totals:
        return totals[bind]
    per_capita = totals.get("per_capita") or {}
    if isinstance(per_capita, dict) and bind in per_capita:
        return per_capita[bind]
    return None


# ============================================================
# 不变式校验
# ============================================================


def _verify_render(ws, structure: SheetStructure, data: FillData):
    """断言输出明细区严格等于 data（→ 无样例残留）。

    逐分组、按 _final_row 定位，跨度内每个明细单元格必须等于 data 值或为空。
    """
    groups, assigned, plan, _unmatched, min_col, max_col, bound_by_col = _compute_plan(structure, data)
    if min_col == 0 or not groups:
        return
    for g, rows, _delta in plan:
        first_final = _final_row(g.detail_first_row, plan)
        for i, row_data in enumerate(rows):
            for col in range(min_col, max_col + 1):
                cell_val = ws.cell(row=first_final + i, column=col).value
                bind = bound_by_col.get(col)
                expected = row_data.get(bind) if bind else None
                if expected is None:
                    if cell_val not in (None, ""):
                        raise AssertionError(
                            f"样例残留: R{first_final + i}C{col} 应为空，实际={cell_val!r}"
                        )
                elif cell_val != expected:
                    raise AssertionError(
                        f"渲染不一致: R{first_final + i}C{col} 期望={expected!r} 实际={cell_val!r}"
                    )


# ============================================================
# 对外入口
# ============================================================


def fill_with_sample(
    sample_file_path: str,
    data: Any,
    output_name: Optional[str] = None,
    output_dir: Optional[str] = None,
    *,
    llm_callable: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """无状态：样例 + data → 按样例版式填好的 Excel。

    Args:
        sample_file_path: 样例 xlsx 路径（调用方提供并负责存储；工具只读不改原件）
        data: FillData 或 dict（meta/rows/columns?/group_subtotals?/totals?）
        output_name: 输出文件名
        output_dir: 输出目录（不传走 ExcelFileHandler 会话目录）
        llm_callable: 注入 LLM（测试用）

    Returns:
        {success, file_path, file_name, file_size, rows_rendered, inferred_structure}
    """
    src = Path(sample_file_path)
    if not src.exists():
        return {"success": False, "error": f"样例文件不存在: {sample_file_path}"}

    fill_data = data if isinstance(data, FillData) else FillData(**(data or {}))
    if not fill_data.rows:
        return {"success": False, "error": "data.rows 为空，无需填充"}

    try:
        structure = analyze_structure(sample_file_path, fill_data, llm_callable=llm_callable)
        # 显式 columns 优先于 LLM 推断（调用方已知 schema 时钉死列绑定）
        if fill_data.columns:
            structure.columns = [ColumnBinding(**c) for c in fill_data.columns]
    except Exception as e:
        logger.opt(exception=True).error(f"[excel_template_ai] 结构分析失败: {e}")
        return {"success": False, "error": f"样例结构分析失败: {e}"}

    wb, _ = ExcelFileHandler.copy_and_open(sample_file_path)
    try:
        ws = wb.active
        rows_rendered = _render(ws, structure, fill_data)
        _verify_render(ws, structure, fill_data)  # fail-loud：残留即 bug

        output_name = output_name or (src.stem + "_filled.xlsx")
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name, output_dir=output_dir)
    finally:
        wb.close()

    save_result.update({
        "success": True,
        "rows_rendered": rows_rendered,
        "inferred_structure": _serialize_structure(structure),
    })
    return save_result


def _serialize_structure(s: SheetStructure) -> Dict[str, Any]:
    return {
        "title": s.title,
        "meta_fields": [m.model_dump() if hasattr(m, "model_dump") else m.dict() for m in s.meta_fields],
        "columns": [c.model_dump() if hasattr(c, "model_dump") else c.dict() for c in s.columns],
        "detail_first_row": s.detail_first_row,
        "detail_last_row": s.detail_last_row,
        "detail_template_row": s.detail_template_row,
        "groups": [g.model_dump() if hasattr(g, "model_dump") else g.dict() for g in s.groups],
        "totals": [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in s.totals],
    }


# ============================================================
# 默认 LLM（同步 SDK，子进程安全；测试注入 mock 不走这里）
# ============================================================


def _default_llm(prompt: str, *, disable_thinking: bool = True) -> str:
    """默认 LLM 调用（deepseek-v4-pro 等思考模型）。

    结构分析**默认关闭思考**：与 travel-quote/attraction.py 一致——思考模型的思考 token
    也计入 max_tokens，开启时小 max_tokens 会截断输出 JSON；关闭后输出确定、不截断、几秒返回。
    复杂模板若分析不准，可传 disable_thinking=False 开启思考（届时需更大 max_tokens 与超时）。
    """
    from src.config.settings import settings
    provider = settings.llm.provider
    # 关闭思考时这是纯输出预算；结构 JSON 很短，4096 足够
    max_tokens = 4096

    if provider == "qwen":
        import dashscope
        keys = settings.llm.qwen.get_effective_keys()
        if not keys:
            raise ValueError("QWEN API key 未配置")
        dashscope.api_key = keys[0]
        model = getattr(settings.llm.qwen, "model", None) or "qwen-plus"
        kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            result_format="message",
            temperature=0.0,
            max_tokens=max_tokens,
        )
        if disable_thinking:
            kwargs["extra_body"] = {"enable_thinking": False}
        resp = dashscope.Generation.call(**kwargs)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM 调用失败: {resp.message}")
        return resp.output.choices[0].message.content

    if provider in ("zhipu", "deepseek"):
        import httpx
        cfg = getattr(settings.llm, provider)
        keys = cfg.get_effective_keys()
        if not keys:
            raise ValueError(f"{provider} API key 未配置")
        model = getattr(cfg, "model", None) or ("glm-4-flash" if provider == "zhipu" else "deepseek-chat")
        base_url = getattr(cfg, "base_url", None)
        if provider == "zhipu":
            base_url = base_url or "https://open.bigmodel.cn/api/paas/v4"
        else:
            base_url = base_url or "https://api.deepseek.com"
        api_url = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        if provider == "deepseek" and disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    raise ValueError(f"不支持的 LLM 提供商: {provider}")
