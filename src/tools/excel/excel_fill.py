"""
Excel ETL 填充层（M5 后半：纯代码模板填充 + 校验报告生成，D15/D16/D17）

Phase 3 库层（D2：通用能力进 tools/excel，不注册 Agent 工具），skill 的
pipeline.py 编排调用。三个入口均为**纯代码零 LLM**：

- fill_template（D15）：克隆模板 → 规范化表头匹配 schema.template_header 别名
  得列绑定 → D13 排序（增员在前减员在后，组内按来源文件名+行号）→ 逐行写值
  → 数据行样式复制表头行（边框/字体/居中）→ 另存 → 终检
- generate_report（D16）：独立 .md 校验报告（摘要/明细/同人多条/计量小计），
  与 xlsx 一起交付；不加第二 sheet（防下游机读）
- output_names（D17）：`{模板名}_汇总_YYYYMMDD-HHMM.xlsx` / `{模板名}_校验报告_YYYYMMDD-HHMM.md`
"""

import re
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import openpyxl
from openpyxl.utils import get_column_letter
from loguru import logger

# D13 排序：增员在前减员在后；其他/缺失类型排最后
_TYPE_ORDER = {"增员": 0, "减员": 1}
_DEFAULT_TYPE_ORDER = 2


def _normalize_header(header: Any) -> str:
    """表头规范化：去所有空白（换行/空格），与 excel_extract 同规则"""
    return re.sub(r"\s+", "", str(header if header is not None else ""))


# ------------------------------------------------------------
# D15 填充
# ------------------------------------------------------------


def _bind_columns(ws, schema: Dict[str, Any]) -> Tuple[Dict[str, int], List[str]]:
    """首行表头与 schema.template_header 别名数组匹配，返回 (字段->列号绑定, 未匹配表头列表)。

    模板表头可能含 ``\\n``（如"社保缴至月份\\n（如6月继续缴纳…）"），规范化去空白后
    再比对；同一字段多个别名命中时取首个命中的列；同一列只绑定一个字段（先到先得）。
    """
    alias_to_field: Dict[str, str] = {}
    for f in schema.get("fields", []):
        for alias in f.get("template_header") or []:
            norm = _normalize_header(alias)
            if norm and norm not in alias_to_field:
                alias_to_field[norm] = f["name"]

    bindings: Dict[str, int] = {}
    used_cols = set()
    unmatched: List[str] = []
    for cell in ws[1]:
        norm = _normalize_header(cell.value)
        if not norm:
            continue
        field = alias_to_field.get(norm)
        if field and field not in bindings and cell.column not in used_cols:
            bindings[field] = cell.column
            used_cols.add(cell.column)
        else:
            unmatched.append(str(cell.value))
    return bindings, unmatched


def _source_sort_key(record: Dict[str, Any]) -> Tuple[int, str, int]:
    """D13 组内排序键：来源文件名 + 行号（``_source`` = ``文件名#Sheet名#行号``）"""
    parts = str(record.get("_source") or "").split("#")
    file_name = parts[0] if parts else ""
    row_no = 0
    if len(parts) >= 3 and str(parts[2]).isdigit():
        row_no = int(parts[2])
    return file_name, row_no


def sort_records(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """D13 输出排序：增员在前减员在后，组内按（来源文件名, 行号）稳定排序"""
    return sorted(
        records,
        key=lambda r: (
            _TYPE_ORDER.get(str(r.get("增减类型") or ""), _DEFAULT_TYPE_ORDER),
            *_source_sort_key(r),
        ),
    )


def _copy_cell_style(src_cell, dst_cell) -> None:
    """表头行单元格样式复制到数据行（边框/字体/居中/填充，copy.copy 逐属性）"""
    dst_cell.font = copy(src_cell.font)
    dst_cell.border = copy(src_cell.border)
    dst_cell.fill = copy(src_cell.fill)
    dst_cell.alignment = copy(src_cell.alignment)
    dst_cell.number_format = src_cell.number_format


def _final_check(output_path: Path, record_count: int, schema: Dict[str, Any]) -> Dict[str, bool]:
    """终检（D15）：写入行数=records 数、schema 字段表头完整（别名任一命中）、无残留行

    - row_count_ok：首行之后**有值行**数 == records 数（纯样式行不计）
    - no_residual_rows：有值行全部落在预期 1+n 行内（克隆自空模板，超出即残留占位/空模板行）
    """
    wb = openpyxl.load_workbook(str(output_path))
    try:
        ws = wb.worksheets[0]

        header_norms = {_normalize_header(c.value) for c in ws[1]} - {""}
        # 表头完整：每个 schema 字段至少一个别名出现在模板首行
        headers_complete = all(
            any(_normalize_header(alias) in header_norms
                for alias in (f.get("template_header") or [f.get("name")]))
            for f in schema.get("fields", [])
        )

        value_rows = [
            row[0].row
            for row in ws.iter_rows(min_row=2)
            if any(cell.value is not None for cell in row)
        ]
        return {
            "row_count_ok": len(value_rows) == record_count,
            "headers_complete": headers_complete,
            "no_residual_rows": all(r <= 1 + record_count for r in value_rows),
        }
    finally:
        wb.close()


def fill_template(
    template_path: Any,
    records: Sequence[Dict[str, Any]],
    schema: Dict[str, Any],
    *,
    output_path: Any,
) -> Dict[str, Any]:
    """D15 纯代码填充（零 LLM）：克隆模板写入 records 并终检。

    Args:
        template_path: 标准（或别名兼容的）空表模板 xlsx
        records: 抽取管线输出的记录（18 字段 + _source + 备注同人标记）
        schema: 字段定义（template_header 别名数组）
        output_path: 输出 xlsx 路径（不存在则创建，存在则覆盖）

    Returns:
        {"success", "output_path", "written", "bindings"(字段->列字母),
         "unmatched_columns", "warnings", "checks"(终检三项)}
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # 1. 克隆模板（普通模式加载保留样式；模板是表头空表，无公式/大数据量）
    wb = openpyxl.load_workbook(str(template_path))
    try:
        ws = wb.worksheets[0]

        # 1.5 合并单元格预处理：任何延伸到数据区（第 2 行及以下）的合并先拆开。
        # 不拆则写值/样式会命中 MergedCell（value 只读）直接 AttributeError 崩溃；
        # 纵向合并若只跳过写入还会静默丢值（合州区各行只保留首行值）。
        unmerged = 0
        for rng in list(ws.merged_cells.ranges):
            if rng.max_row >= 2:
                ws.unmerge_cells(str(rng))
                unmerged += 1

        # 2. 列绑定
        bindings, unmatched = _bind_columns(ws, schema)
        warnings: List[str] = []
        if unmerged:
            warnings.append(
                f"模板含 {unmerged} 处跨数据区合并单元格，已自动拆分后按行写入（原合并仅保留左上角值）"
            )
        if unmatched:
            warnings.append(f"模板列未匹配到 schema 字段: {', '.join(unmatched)}")
        missing_fields = [f["name"] for f in schema.get("fields", []) if f["name"] not in bindings]
        if missing_fields:
            warnings.append(f"schema 字段未在模板中找到对应列: {', '.join(missing_fields)}")

        # 3. D13 排序 + 4. 逐行写值（数据行样式复制表头行）
        ordered = sort_records(records)
        header_cells = list(ws[1])
        for i, record in enumerate(ordered):
            row_idx = 2 + i
            for field, col_idx in bindings.items():
                ws.cell(row=row_idx, column=col_idx, value=record.get(field))
            for header_cell in header_cells:
                _copy_cell_style(header_cell, ws.cell(row=row_idx, column=header_cell.column))

        wb.save(str(out))
    finally:
        wb.close()

    # 5. 终检（另存后重读，防"看似写入"）
    checks = _final_check(out, len(records), schema)
    success = all(checks.values())
    if not success:
        warnings.append(f"终检未通过: {checks}")
        logger.warning(f"[excel_fill] 终检未通过: {out}, checks={checks}")

    return {
        "success": success,
        "output_path": str(out),
        "written": len(records),
        "bindings": {field: get_column_letter(col) for field, col in sorted(bindings.items(), key=lambda kv: kv[1])},
        "unmatched_columns": unmatched,
        "warnings": warnings,
        "checks": checks,
    }


# ------------------------------------------------------------
# D16 校验报告
# ------------------------------------------------------------


def _fmt_value(value: Any) -> str:
    """报告展示值：None -> 空串，其余 str 化"""
    return "" if value is None else str(value)


def generate_report(result_bundle: Dict[str, Any], *, output_path: Any) -> str:
    """D16 生成独立 .md 校验报告并返回全文。

    Args:
        result_bundle: 管线汇总结果，键：
          - template_name / xlsx_path / report_path（可选）
          - sources: [{file, extracted, written, skipped}] 各来源抽取/写入/跳过数
          - records / record_warnings: 写入模板的最终记录与逐条 warning（等长对齐）
          - manual_review: 人工处理清单（record/errors/warnings/source_row/_source）
          - duplicate_groups: 同人多条分组（D12）
          - metering: 计量小计（llm_calls/tokens/credit，不可用时缺省）
          - fill_warnings: 填充层 warning（未匹配列等）
          - schema_note: 指纹不匹配重抽提示（D14 降级，可选）
        output_path: 报告输出路径

    Returns:
        报告全文（str）
    """
    records = result_bundle.get("records") or []
    record_warnings = result_bundle.get("record_warnings") or []
    manual_review = result_bundle.get("manual_review") or []
    duplicate_groups = result_bundle.get("duplicate_groups") or []
    sources = result_bundle.get("sources") or []
    metering = result_bundle.get("metering")

    lines: List[str] = []
    lines.append(f"# 校验报告 — {result_bundle.get('template_name', '标准模板')}")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if result_bundle.get("xlsx_path"):
        lines.append(f"- 汇总文件：{result_bundle.get('xlsx_path')}")
    lines.append(f"- 写入记录：{len(records)} 条；人工处理清单：{len(manual_review)} 条；"
                 f"同人多条组：{len(duplicate_groups)} 组")
    if result_bundle.get("schema_note"):
        lines.append(f"- ⚠️ {result_bundle['schema_note']}")
    lines.append("")

    # ---- 摘要 ----
    lines.append("## 一、摘要（各来源抽取 / 写入 / 跳过）")
    lines.append("")
    lines.append("| 来源文件 | 抽取 | 写入 | 跳过(人工清单) |")
    lines.append("| --- | --- | --- | --- |")
    total = [0, 0, 0]
    for s in sources:
        extracted, written, skipped = (int(s.get(k) or 0) for k in ("extracted", "written", "skipped"))
        total[0] += extracted
        total[1] += written
        total[2] += skipped
        lines.append(f"| {s.get('file', '')} | {extracted} | {written} | {skipped} |")
    lines.append(f"| **合计** | **{total[0]}** | **{total[1]}** | **{total[2]}** |")
    lines.append("")

    # ---- 明细：逐行 warning ----
    warn_rows = [
        (rec, record_warnings[i] if i < len(record_warnings) else [])
        for i, rec in enumerate(records)
        if i < len(record_warnings) and record_warnings[i]
    ]
    lines.append("## 二、明细（逐行 warning / error）")
    lines.append("")
    if not warn_rows and not manual_review:
        lines.append("全部记录校验通过，无 warning / error。")
    for rec, warns in warn_rows:
        lines.append(f"- **{_fmt_value(rec.get('姓名'))}**（{_fmt_value(rec.get('_source'))}）：")
        for w in warns:
            lines.append(f"  - warning：{w}")
    for item in manual_review:
        rec = item.get("record") or {}
        lines.append(f"- **{_fmt_value(rec.get('姓名'))}**（{_fmt_value(item.get('_source'))}）→ 人工处理：")
        for e in item.get("errors") or []:
            lines.append(f"  - error：{e}")
        for w in item.get("warnings") or []:
            lines.append(f"  - warning：{w}")
        if item.get("source_row"):
            lines.append(f"  - 原始行：`{item['source_row']}`")
    lines.append("")

    # ---- 同人多条 ----
    lines.append("## 三、同人多条（D12：不去重不合并，逐条标注出处）")
    lines.append("")
    if not duplicate_groups:
        lines.append("无同人多条记录。")
    for group in duplicate_groups:
        lines.append(f"- 业务键 `{group.get('key')}`：")
        for m in group.get("members") or []:
            lines.append(f"  - {_fmt_value(m.get('姓名'))}（{_fmt_value(m.get('_source'))}）")
    lines.append("")

    # ---- 填充层提示 ----
    fill_warnings = result_bundle.get("fill_warnings") or []
    if fill_warnings:
        lines.append("## 四、填充提示")
        lines.append("")
        for w in fill_warnings:
            lines.append(f"- {w}")
        lines.append("")

    # ---- 计量小计 ----
    if metering:
        lines.append("## 五、计量小计")
        lines.append("")
        lines.append(f"- LLM 调用次数：{metering.get('llm_calls', 0)}")
        lines.append(f"- 累计 tokens：prompt {metering.get('prompt_tokens', 0)} + "
                     f"completion {metering.get('completion_tokens', 0)} = "
                     f"{metering.get('total_tokens', 0)}")
        credit = metering.get("credit")
        if credit is not None:
            lines.append(f"- 积分消耗（估算）：{credit:.4f}")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return content


# ------------------------------------------------------------
# D17 命名
# ------------------------------------------------------------


def output_names(template_name: str, *, now: Optional[datetime] = None) -> Tuple[str, str]:
    """D17 交付命名：`{模板名}_汇总_YYYYMMDD-HHMM.xlsx` / `{模板名}_校验报告_YYYYMMDD-HHMM.md`

    模板名去 .xlsx 后缀；now 参数供测试注入固定时间。
    """
    base = re.sub(r"\.xlsx$", "", str(template_name or "模板"), flags=re.IGNORECASE)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"{base}_汇总_{stamp}.xlsx", f"{base}_校验报告_{stamp}.md"
