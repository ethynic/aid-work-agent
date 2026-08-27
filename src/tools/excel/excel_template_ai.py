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
from openpyxl.styles import Alignment
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
    # 所有将写入单元格的值必须是标量（per_capita 的值同理）；按列/档位分列的
    # 合计没有嵌套表达方式，须拆成独立标量键（_validate_cell_scalars 前置拦截）


def _validate_cell_scalars(fill_data: "FillData") -> Optional[str]:
    """校验所有将写入单元格的值均为标量（openpyxl 只接受 str/int/float/bool/None）。

    嵌套 dict/list 直达 cell.value 会抛 "Cannot convert ... to Excel"（线上事故：
    交叉表合计按列分档传了嵌套 dict）。在 LLM 结构分析前拦截，返回带拆平指引的
    错误消息，调用方 Agent 按提示修正 data 即可重试自愈。返回 None 表示通过。
    """
    def check(path: str, value) -> Optional[str]:
        if value is None or isinstance(value, (str, int, float, bool)):
            return None
        kind = "dict" if isinstance(value, dict) else type(value).__name__
        return (
            f"data.{path} 的值必须是标量（单元格只能填 str/int/float/bool），实际是 {kind}。"
            "请拆平后重试：按列/档位分列的数值拆成独立标量键"
            "（如 合计总价:{'40人':9520,'45人':10560} 拆成 合计总价_40人:9520、合计总价_45人:10560），"
            "多值明细转为多行放入 rows"
        )

    for k, v in (fill_data.meta or {}).items():
        err = check(f"meta.{k}", v)
        if err:
            return err
    for idx, row in enumerate(fill_data.rows, 1):
        for k, v in row.items():
            err = check(f"rows[{idx}].{k}", v)
            if err:
                return err
    for k, v in (fill_data.group_subtotals or {}).items():
        err = check(f"group_subtotals.{k}", v)
        if err:
            return err
    for k, v in (fill_data.totals or {}).items():
        if k == "per_capita" and isinstance(v, dict):
            for pk, pv in v.items():
                err = check(f"totals.per_capita.{pk}", pv)
                if err:
                    return err
        else:
            err = check(f"totals.{k}", v)
            if err:
                return err
    return None



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
    if llm_callable is not None:
        raw = llm_callable(prompt)
    else:
        # 结构分析是本链路成败关键且模板版式多变，默认开思考（预算见 _default_llm）；
        # M3 抽取层（excel_extract）/技能管线等确定性场景不传参保持默认关闭
        raw = _default_llm(prompt, enable_thinking=True)
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
2. **groups（分区小计版式必填）+ 小计形态判定（关键，极易错）**：
   先判定小计是「行式」还是「列式」，二者处理方式完全不同：
   - **行式小计**：每组明细**下方有独立一行**"XX小计"（如"房餐车小计""门票小计"）。每个小计行对应一个分组：detail_first_row/detail_last_row 是该小计行**上方**紧邻的明细示例数据首末行，subtotal_row/subtotal_col 是小计行及其合计值所在列，match 用"明细行可用的键"指明哪些值归该组（常用 category）。detail_template_row 取该组明细首行。
   - **⚠️ 列式小计**：小计与明细**同行**——某列跨行竖向合并显示，如"商品类别总计/分类小计"列每组仅首行有值、下方合并居中。**此类列严禁设 subtotal_row/subtotal_col**，必须作为普通 columns 绑定列（bind=对应键名，如"商品类别总计"），组内非首行该键留空（竖向合并会自动居中显示）。误把列式小计设成 subtotal_row，会在明细行数变化时让小计落到相邻分组的残留行，触发"样例残留"报错。
   **无分区小计的简单版式，groups 填空数组，改填 detail_first_row/detail_last_row。**
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


def _subtotal_hits_residue(final_r: int, plan) -> bool:
    """小计最终行 final_r 是否撞上某分组**最终**明细行区（列式小计误判为行式 subtotal_row 的症状）。

    fill_template 防御用：合法行式小计在本组明细下方独立成行，经 _final_row 重映射后
    不可能落进任何分组的最终明细区；列式小计（小计与明细同行、靠竖向合并显示）被误判
    成 subtotal_row 时，其"小计行"本就是明细行，重映射后必然与（本组或别组的）明细行
    重叠，写入会覆盖明细数据并触发 _verify_render"渲染不一致"硬报错。命中即由调用方
    跳过写入（该格已是明细数据或已清空）。

    注意不能拿"收缩组被删尾行的最终坐标"做判据：删行后下方内容整体上移，被删行的
    最终坐标恰好被合法小计行本身占据——按最终坐标比对会误杀所有收缩组的合法小计。
    """
    for g, rows, _delta in plan:
        if not rows:
            continue
        first_final = _final_row(g.detail_first_row, plan)
        last_final = first_final + len(rows) - 1
        if first_final <= final_r <= last_final:
            return True
    return False


def _compute_final_merges(saved, plan, groups) -> List[Tuple[int, int, int, int]]:
    """行增删后计算合并区的最终坐标（按 _final_row 平移）。
    返回 [(min_col, min_row, max_col, max_row), ...] 最终坐标。

    明细区内的合并（横向单行如备注 G4:J4、竖向跨多行如类别 A4:A7）行数随数据增删而变化、
    无法逐个平移，这里一律丢弃，改由填充后的 _apply_detail_row_merges（横向按列模式应用到
    每个最终明细行，含插入的新行）与 _apply_vertical_merges（竖向按相邻相同值合并）重建。
    """
    out = []
    for min_col, min_row, max_col, max_row in saved:
        if _is_intra_detail(min_row, max_row, groups):
            continue
        nr1 = _final_row(min_row, plan)
        nr2 = _final_row(max_row, plan)
        if nr1 <= 0 or nr2 <= 0:
            continue
        out.append((min_col, nr1, max_col, nr2))
    return out


def _apply_merges(ws, final_merges):
    """按最终坐标重新合并"""
    for min_col, min_row, max_col, max_row in final_merges:
        a = f"{get_column_letter(min_col)}{min_row}"
        b = f"{get_column_letter(max_col)}{max_row}"
        try:
            ws.merge_cells(f"{a}:{b}")
        except Exception as e:
            logger.debug(f"[excel_template_ai] 合并重锚定跳过 {a}:{b}: {e}")


def _mergeable_value(v) -> Any:
    """归一化单元格值用于"相邻相同"比较：None/空串视为 None（空值不参与合并）。"""
    if v is None or v == "":
        return None
    return v


def _collect_vmerge_cols(active_merges, groups) -> set:
    """识别样例中"明细区内竖向合并"的列：这类合并表示该列同组值相同、需合并居中显示
    （如分组类别列 A4:A5 合并成"住宿"）。条件：单列(min_col==max_col) + 跨多行
    (min_row<max_row) + 完全落在某分组明细区内。这些合并在 _compute_final_merges
    里会被丢弃（明细区行数变化无法平移），改由 _apply_vertical_merges 填充后按值合并。"""
    cols = set()
    for min_col, min_row, max_col, max_row in active_merges:
        if min_col != max_col or min_row >= max_row:
            continue
        if _is_intra_detail(min_row, max_row, groups):
            cols.add(min_col)
    return cols


def _normalize_vmerge_styles(ws, vmerge_cols: set, saved_merges, groups):
    """竖向合并列（如成本类别 A 列）样式归一：把整列每个明细格统一成"类别锚点"样式。

    样例里竖向合并的锚点格（每组首行）是粗体/居中的类别样式，续行格是默认样式（合并后
    不可见所以无所谓）。但填充时数据值可能落到续行格上，导致同列类别值样式不一致——
    有的行继承了锚点样式（粗体）、有的继承了续行样式（普通），视觉上乱。这里在增删行前
    把每个明细格都复制"该列首个竖向合并锚点"的样式，保证整列统一。必须在 unmerge 之后、
    增删行之前调用：unmerge 后单元格样式可写；此时还在原始行号上，锚点行号有效。
    """
    if not vmerge_cols:
        return
    # 每列取首个竖向合并锚点行作为类别样式基准
    anchor_by_col = {}
    for col in vmerge_cols:
        for min_col, min_row, max_col, max_row in saved_merges:
            if min_col == max_col == col and min_row < max_row and _is_intra_detail(min_row, max_row, groups):
                anchor_by_col[col] = min_row
                break
    for col, anchor_row in anchor_by_col.items():
        src = ws.cell(row=anchor_row, column=col)
        if not src.has_style:
            continue
        for g in groups:
            for r in range(g.detail_first_row, g.detail_last_row + 1):
                if r == anchor_row:
                    continue
                copy_cell_style(src, ws.cell(row=r, column=col))


def _apply_vertical_merges(ws, vmerge_cols: set, plan):
    """对需竖向合并的列，在每个分组明细最终范围内，把上下相邻且值相同(非空)的单元格
    合并并居中。只在组内合并、不跨组；空值不合并；单行不合并。

    样例的竖向合并只起"指示作用"——告诉渲染器"这列要按相邻相同值合并"；合并的实际
    行范围由填充后的真实数据决定（M 行可能比样例 K 行多/少），不照搬样例合并范围。
    """
    if not vmerge_cols:
        return
    for g, rows, _delta in plan:
        if not rows:
            continue
        first_final = _final_row(g.detail_first_row, plan)
        last_final = first_final + len(rows) - 1
        for col in sorted(vmerge_cols):
            seg_start = first_final
            for r in range(first_final, last_final + 1):
                cur = _mergeable_value(ws.cell(row=r, column=col).value)
                nxt = _mergeable_value(ws.cell(row=r + 1, column=col).value) if r < last_final else None
                if cur is not None and cur == nxt:
                    continue  # 仍在同一段相同值
                # 段 [seg_start, r] 结束（cur 为该段值）
                if r > seg_start:  # 至少 2 行才合并
                    a = f"{get_column_letter(col)}{seg_start}"
                    b = f"{get_column_letter(col)}{r}"
                    try:
                        ws.merge_cells(f"{a}:{b}")
                        anchor = ws.cell(row=seg_start, column=col)
                        prev_wrap = anchor.alignment.wrap_text if anchor.alignment else False
                        anchor.alignment = Alignment(
                            horizontal="center", vertical="center", wrap_text=prev_wrap,
                        )
                    except Exception as e:
                        logger.debug(f"[excel_template_ai] 竖向合并跳过 {a}:{b}: {e}")
                seg_start = r + 1


def _collect_hmerge_patterns(saved_merges, groups) -> set:
    """识别明细区内"横向单行合并"的列模式（如备注列 G4:J4 = (7,10)）。
    这类合并是**每行的版式模式**：明细区每行都该这样跨列（备注文字横跨到 J）。
    行数随数据增删变化（M>K 会插入新行），无法逐个平移，改为按列模式应用到每个最终明细行。"""
    patterns = set()
    for min_col, min_row, max_col, max_row in saved_merges:
        if min_row == max_row and min_col < max_col and _is_intra_detail(min_row, max_row, groups):
            patterns.add((min_col, max_col))
    return patterns


def _apply_detail_row_merges(ws, patterns: set, plan):
    """对每个最终明细行（含插入的新行）应用横向合并列模式（备注跨列等）。
    样例的横向合并表示"这一列范围在该行要合并"，每个明细行——无论来自样例还是新插入——
    都套用同一模式，保证版式一致。值已在锚点（最左列），merge_cells 保留锚点值。"""
    if not patterns:
        return
    for g, rows, _delta in plan:
        if not rows:
            continue
        first_final = _final_row(g.detail_first_row, plan)
        last_final = first_final + len(rows) - 1
        for r in range(first_final, last_final + 1):
            for c1, c2 in sorted(patterns):
                a = f"{get_column_letter(c1)}{r}"
                b = f"{get_column_letter(c2)}{r}"
                try:
                    ws.merge_cells(f"{a}:{b}")
                except Exception as e:
                    logger.debug(f"[excel_template_ai] 横向合并跳过 {a}:{b}: {e}")


def _anchor_for(row: int, col: int, final_merges) -> Tuple[int, int]:
    """(row,col) 所在合并区的锚点（左上格）；不在任何合并区则返回自身。
    值必须写到锚点，否则合并区显示锚点值、非锚点格只读。"""
    for min_col, min_row, max_col, max_row in final_merges:
        if min_row <= row <= max_row and min_col <= col <= max_col:
            return (min_row, min_col)
    return (row, col)


def _set_value(ws, row: int, col: int, value, final_merges):
    """写值到 (row,col) 所在合并区的锚点（避免 MergedCell 只读报错 + 值落在非锚点被隐藏）。"""
    r, c = _anchor_for(row, col, final_merges)
    ws.cell(row=r, column=c).value = value


def _meta_value_col(mf: MetaField, final_merges) -> int:
    """meta 值单元格列：显式 value_col 优先；否则 = 标题视觉范围右侧 +1
    （标题本身是合并区时取合并区 max_col+1，否则标题 col+1）。
    避免 col+1 落到"合并标题"的非锚点格上导致值覆盖标题。"""
    if mf.value_col:
        return mf.value_col
    end = mf.col
    for min_col, min_row, max_col, max_row in final_merges:
        if min_row <= mf.row <= max_row and min_col <= mf.col <= max_col:
            end = max_col
            break
    return end + 1


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
                      bound_by_col: Dict[int, Optional[str]], row_data: Dict[str, Any],
                      final_merges):
    """写一行明细：跨度内每列按绑定写值或清空（bind 为 None / 键缺失 → 清空）。
    通过 _set_value 写到合并区锚点，避免 MergedCell 只读 + 值落非锚点被隐藏。"""
    for col in range(min_col, max_col + 1):
        bind = bound_by_col.get(col)
        value = row_data[bind] if (bind and bind in row_data) else None
        _set_value(ws, row, col, value, final_merges)


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

    # 1. 快照合并 + 行维度。**只解除"明细区及以下"的合并**（这些受增删影响或需要写入）；
    #    明细区以上（标题/meta）的横向合并若没内容要填，保持不动——避免被解除后没还原。
    #    阈值 = 首个分组明细首行；max_row < 阈值的合并在标题/meta 区，一律不碰。
    saved_merges = _snapshot_merges(ws)
    threshold = min((g.detail_first_row for g in groups), default=1)
    active_merges = [m for m in saved_merges if m[3] >= threshold]  # m=(min_col,min_row,max_col,max_row)
    # 同步快照行维度：openpyxl insert/delete_rows 不平移 row_dimensions，
    # 导致插入/删除点下方已存在的行（小计/合计等）行高丢失——这里先存后恢复。
    saved_dims = [(r, dim.height, dim.hidden)
                  for r, dim in ws.row_dimensions.items()
                  if dim.height is not None or dim.hidden]
    for mr in list(ws.merged_cells.ranges):
        if mr.max_row >= threshold:
            ws.unmerge_cells(str(mr))

    # 1.5 竖向合并列样式归一（在原始行号上、增删行前）：成本类别列整列统一成锚点样式，
    #     否则数据值落到样例续行格（默认样式）上会让同列类别值有的粗体有的不粗体。
    vmerge_cols = _collect_vmerge_cols(saved_merges, groups)
    _normalize_vmerge_styles(ws, vmerge_cols, saved_merges, groups)

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

    # 3. 合并区最终坐标：
    #    final_merges = 全部合并（含未解除的标题/meta 合并）按 _final_row 平移——供 _set_value/
    #      _meta_value_col 定位锚点/值列（meta 区合并没有解除，但查它才能把值写到正确锚点）。
    #    active_final = 仅"被解除过的"明细区及以下合并——只有这些需要重新应用（标题/meta 未解除，
    #      不能再 merge 一次，否则 openpyxl 报已合并）。
    final_merges = _compute_final_merges(saved_merges, plan, groups)
    active_final = _compute_final_merges(active_merges, plan, groups)

    # 4-7. 填数据（此时所有合并已解除、单元格可写；用 _set_value 把值写到合并区锚点，
    #     这样合并重新应用后值显示在锚点，不会落到只读的非锚点格）
    # 4. 填各分组明细
    for g, rows, _delta in plan:
        first_final = _final_row(g.detail_first_row, plan)
        for i, row_data in enumerate(rows):
            _write_detail_row(ws, first_final + i, min_col, max_col, bound_by_col, row_data, final_merges)

    # 5. 填各分组小计（位于该组明细下方，按 _final_row 重映射；无值则清空防残留）
    for g, rows, _delta in plan:
        if g.subtotal_row and g.subtotal_col:
            r = _final_row(g.subtotal_row, plan)
            # 防御：小计最终行若撞上某分组最终明细区（列式小计误判为行式 subtotal_row 的典型症状），
            # 跳过写入避免覆盖明细数据、触发"渲染不一致"硬报错（该格已是明细行数据）
            if _subtotal_hits_residue(r, plan):
                logger.warning(
                    f"[excel_template_ai] 跳过分组 {g.name!r} 小计：最终行 R{r} 落入收缩组残留区，"
                    f"疑似列式小计被误判为 subtotal_row"
                )
                continue
            _set_value(ws, r, g.subtotal_col, _resolve_subtotal(g, data, rows, bound_by_col), final_merges)

    # 6. 填顶部 meta（左右结构：标题在 mf.col，值写到标题视觉范围右侧；标题合并时取合并区右侧+1）
    for mf in structure.meta_fields:
        if not mf.bind:
            continue
        value_col = _meta_value_col(mf, final_merges)
        _set_value(ws, mf.row, value_col, data.meta.get(mf.bind), final_merges)

    # 7. 填合计区（位于所有分组下方，按 _final_row 重映射；无值 → 清空防样例残留）
    for t in structure.totals:
        r = _final_row(t.row, plan)
        _set_value(ws, r, t.col, _resolve_total_value(t.bind, data.totals), final_merges)

    # 8. 最后才重新合并——只重应用"被解除过的"明细区及以下合并（标题/meta 区合并从未解除，不动）
    _apply_merges(ws, active_final)

    # 9. 竖向合并：样例明细区内"同组值列"（如分组类别列 A4:A5）按相邻相同值合并居中。
    #    这类合并在第3步被 _compute_final_merges 丢弃（明细区行数变化无法平移），
    #    改为填充后按实际值重新合并——结果即"同组相邻相同值合并居中显示"。
    #    vmerge_cols 已在 1.5 步算过（样式归一用），这里直接复用。
    _apply_vertical_merges(ws, vmerge_cols, plan)

    # 10. 横向合并：样例明细区内"每行跨列"模式（如备注列 G:J）应用到每个最终明细行。
    #     同样在第3步被丢弃，这里按列模式重建——含插入的新行，保证每行版式一致。
    hmerge_patterns = _collect_hmerge_patterns(saved_merges, groups)
    _apply_detail_row_merges(ws, hmerge_patterns, plan)

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


def _effective_value(ws, row: int, col: int):
    """读单元格"有效值"：合并区内的非锚点格读锚点值（openpyxl 合并后非锚点 value=None，
    但语义上它显示的是锚点值）。_verify_render 用它避免把"合并隐藏的非锚点格"误判为残留。"""
    for mr in ws.merged_cells.ranges:
        if mr.min_row <= row <= mr.max_row and mr.min_col <= col <= mr.max_col:
            return ws.cell(row=mr.min_row, column=mr.min_col).value
    return ws.cell(row=row, column=col).value


def _is_non_anchor_merged(ws, row: int, col: int) -> bool:
    """(row,col) 是否落在某个合并区内但不是锚点（左上格）。这些格的显示值归锚点，
    verify 不应独立校验——否则横向合并（如备注 G:J）跨进列跨度时，非锚点格读到的
    锚点值会被误判为"该未绑定列的残留"。锚点格自身会被单独校验。"""
    for mr in ws.merged_cells.ranges:
        if mr.min_row <= row <= mr.max_row and mr.min_col <= col <= mr.max_col:
            return not (row == mr.min_row and col == mr.min_col)
    return False


def _verify_render(ws, structure: SheetStructure, data: FillData):
    """断言输出明细区严格等于 data（→ 无样例残留）。

    逐分组、按 _final_row 定位，跨度内每个明细单元格必须等于 data 值或为空。
    合并区单元格用 _effective_value 读有效值（锚点值），避免竖向合并后非锚点格
    value=None 被误判为不一致；合并区内的非锚点格直接跳过（值归锚点，锚点格单独校验）。
    """
    groups, assigned, plan, _unmatched, min_col, max_col, bound_by_col = _compute_plan(structure, data)
    if min_col == 0 or not groups:
        return
    for g, rows, _delta in plan:
        first_final = _final_row(g.detail_first_row, plan)
        for i, row_data in enumerate(rows):
            for col in range(min_col, max_col + 1):
                rcell = first_final + i
                if _is_non_anchor_merged(ws, rcell, col):
                    continue  # 合并区非锚点格：显示值归锚点，锚点格会在自身位置被校验
                cell_val = _effective_value(ws, rcell, col)
                bind = bound_by_col.get(col)
                expected = row_data.get(bind) if bind else None
                if expected is None:
                    if cell_val not in (None, ""):
                        raise AssertionError(
                            f"样例残留: R{rcell}C{col} 应为空，实际={cell_val!r}"
                        )
                elif cell_val != expected:
                    raise AssertionError(
                        f"渲染不一致: R{rcell}C{col} 期望={expected!r} 实际={cell_val!r}"
                    )


# ============================================================
# 覆盖分析（供调用方提示用户：哪些数据没用上、哪些模板字段没给值）
# ============================================================


def _analyze_coverage(structure: SheetStructure, data: FillData) -> Tuple[List[str], List[Dict[str, Any]]]:
    """分析"用户数据 ↔ 模板字段"的覆盖关系，供调用方提示用户。

    返回 (unused_data_keys, missing_fields)：
    - unused_data_keys：用户提供了但模板没有对应位置的数据键。这些键不会被填（避免硬塞到错列），
      告诉用户"这些没填进去"，免得用户以为数据丢了。
    - missing_fields：模板里要填但用户没给值的字段。这些会被清空（不残留样例示例值），
      告诉用户"模板还需要这些"以便补全。
    """
    used_row = {c.bind for c in structure.columns if c.bind}
    used_meta = {m.bind for m in structure.meta_fields if m.bind}
    used_total = {t.bind for t in structure.totals if t.bind}

    # 未使用的数据键（用户给了但模板没位置）
    unused: List[str] = []
    row_keys = {k for row in data.rows for k in row.keys()}
    unused.extend(sorted(row_keys - used_row))
    unused.extend(f"meta:{k}" for k in sorted(set(data.meta or {}) - used_meta))
    total_provided = set(data.totals or {})
    per_capita = (data.totals or {}).get("per_capita")
    if isinstance(per_capita, dict):
        total_provided |= set(per_capita.keys())
    for k in sorted(total_provided - used_total - {"per_capita"}):
        unused.append(f"totals:{k}")

    # 缺失的模板字段（模板要填但没给值 → 会被清空）
    missing: List[Dict[str, Any]] = []
    for m in structure.meta_fields:
        if m.bind and (not data.meta or data.meta.get(m.bind) in (None, "")):
            missing.append({"type": "meta", "bind": m.bind, "label": m.label})
    for t in structure.totals:
        if t.bind and _resolve_total_value(t.bind, data.totals or {}) is None:
            missing.append({"type": "total", "bind": t.bind, "label": t.label})
    for c in structure.columns:
        # 该列绑定键在所有数据行中都不存在 → 整列空白
        if c.bind and not any(c.bind in row for row in data.rows):
            missing.append({"type": "column", "bind": c.bind, "label": c.header})
    return unused, missing


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
        {success, file_path, file_name, file_size, rows_rendered, inferred_structure,
         unused_data_keys, missing_fields, message}
    """
    src = Path(sample_file_path)
    if not src.exists():
        return {"success": False, "error": f"样例文件不存在: {sample_file_path}"}

    fill_data = data if isinstance(data, FillData) else FillData(**(data or {}))
    if not fill_data.rows:
        return {"success": False, "error": "data.rows 为空，无需填充"}

    err = _validate_cell_scalars(fill_data)
    if err:
        return {"success": False, "error": err}

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

    unused, missing = _analyze_coverage(structure, fill_data)
    notes = []
    if unused:
        notes.append(f"以下数据未找到模板对应位置，已忽略：{', '.join(unused)}")
    if missing:
        labels = [f"{m.get('label') or m.get('bind')}" for m in missing]
        notes.append(f"模板中以下字段未提供数据，已清空：{', '.join(labels)}")

    save_result.update({
        "success": True,
        "rows_rendered": rows_rendered,
        "inferred_structure": _serialize_structure(structure),
        "unused_data_keys": unused,
        "missing_fields": missing,
        "message": "；".join(notes) if notes else f"已填充 {rows_rendered} 行",
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


def _default_llm(
    prompt: str,
    *,
    enable_thinking: bool = False,
    return_usage: bool = False,
):
    """默认 LLM 调用（deepseek-v4-pro 等思考模型）。

    思考开关统一命名为 enable_thinking（与网关 Provider、settings.llm 同名），
    本函数默认 False（关思考，供 M3 抽取层等确定性场景）；结构分析
    （analyze_structure）因成败关键且版式多变，显式传 True 开启思考。
    思考模型的思考 token 也计入 max_tokens，故 enable_thinking=True 时
    预算自动放大到 16384、超时放宽到 240s，避免截断。

    return_usage=True 时返回 ``(content, usage_dict)`` 而非仅 content（Excel ETL M3
    抽取层需要 usage 做计量上报）；默认 False 完全向后兼容。usage_dict 键与
    OpenAI 兼容接口一致：prompt_tokens / completion_tokens / total_tokens（缺失键补 0），
    并附加归一键：cached_tokens（prompt_tokens_details.cached_tokens /
    prompt_cache_hit_tokens 两种形态统一，缓存计费扣减用）与 model（实际调用
    模型名，计量落库取单价用——provider 可能是 qwen/zhipu，不能假设 deepseek）。
    """
    from src.config.settings import settings
    provider = settings.llm.provider
    # 关思考时这是纯输出预算，结构 JSON 很短，4096 足够；
    # 开思考时思考 token 也计入 max_tokens（deepseek 计费口径），需放大预算并放宽超时
    max_tokens = 16384 if enable_thinking else 4096
    timeout = 240.0 if enable_thinking else 120.0

    if provider == "qwen":
        import httpx
        keys = settings.llm.qwen.get_effective_keys()
        if not keys:
            raise ValueError("QWEN API key 未配置")
        model = getattr(settings.llm.qwen, "model", None) or "qwen3.7-flash"
        base_url = getattr(settings.llm.qwen, "base_url", None) or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_url = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        # qwen3.x 系列走 OpenAI 兼容接口（原生 Generation 端点不适用），思考开关用 enable_thinking
        if not enable_thinking:
            payload["enable_thinking"] = False
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if return_usage:
            usage = _normalize_usage(data.get("usage"))
            usage["model"] = model
            return content, usage
        return content

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
        if provider == "deepseek" and not enable_thinking:
            payload["thinking"] = {"type": "disabled"}
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if return_usage:
            usage = _normalize_usage(data.get("usage"))
            usage["model"] = model
            return content, usage
        return content

    raise ValueError(f"不支持的 LLM 提供商: {provider}")


def _normalize_usage(raw_usage) -> Dict[str, Any]:
    """OpenAI 兼容接口的 usage dict 容错归一（计量落库用）

    保证三个基础键存在且为 int；缓存命中 token 统一归一到 ``cached_tokens``
    （兼容 qwen/OpenAI 的 ``prompt_tokens_details.cached_tokens`` 与 deepseek 的
    ``prompt_cache_hit_tokens`` 两种形态，计量按缓存单价扣减）。
    """
    usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cached = usage.get("cached_tokens")
    if not cached:
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if not cached:
        cached = usage.get("prompt_cache_hit_tokens")
    usage["cached_tokens"] = int(cached or 0)
    usage["prompt_tokens"] = prompt_tokens
    usage["completion_tokens"] = completion_tokens
    usage["total_tokens"] = int(usage.get("total_tokens", 0) or 0) or (prompt_tokens + completion_tokens)
    return usage
