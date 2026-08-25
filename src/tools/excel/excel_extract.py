"""
Excel ETL 抽取层（M3 LLM 抽取 + M4 校验修复回路 + 同人多条标注，D2 纯库不注册工具）

管线（Phase 2 库层，Phase 3 skill 的 pipeline.py 直接编排本模块）：

    render_llm_view（M1）→ mask（Q3，调用方持有 MaskSession）
    → run_extraction 全程脱敏域：extract_records → validate_records（D10 分级）
      → repair_records（D11 修复回路）→ annotate_same_person（D12 同人标注）
    → unmask（Phase 3 管线还原 records / manual_review 中的占位符）→ 填充交付

⚠️ unmask 必须在**所有 LLM 阶段（extract / repair / schema）之后**执行：
修复回喂会把整条已抽记录 JSON 嵌进 prompt，若先还原再修复，真实证件号会随
记录进入修复 prompt，违反 Q3（LLM 全程不见真实证件号/手机号）。校验位等真值
复检由 Phase 3 在 unmask 后用 validate_record 纯函数复查，仍 error 进人工清单。
（validate 对占位符 [ID_n]/[TEL_n] 与 * 掩码身份证同等放行 + warning。）

关键决议对齐（docs/tools/excel/excel-etl-gap-analysis.md §7）：
- D7 分块：50 数据行/块，块字符数 ≤ 24k（超则减半行数重切）；表头区随每块重复
- D8 输出契约：JSON 数组（外层 {"records": [...]} 包装，复用 _extract_json 容错解析），
  每条记录 18 字段（缺省 null）+ _source
- D9 失败路径：单块解析失败/空结果重试 1 次（重试 prompt 附失败原因），仍败 → 任务级失败
- D10 校验分级：error 阻断（修复回路），warning 不阻断（照写 + 进报告）
- D11 修复回路：仅 error 行回喂（原行文本 + 已抽记录 + 错误清单），最多 2 轮；
  仍 error 剔除进 manual_review（附原始行文本与错误）
- D12 同人多条：不合并不去重，按业务键分组标注，备注追加【同人多条：另见 X】

计量：on_usage(usage, stage) 回调在每次 LLM 调用（含重试）时上报（stage =
"extract"/"repair"/"schema"）；库层只回调不落库，落库由调用方接
session_record.record_skill_llm_usage（Phase 3 pipeline）。
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import openpyxl
from loguru import logger

from src.tools.excel.excel_mask import PLACEHOLDER_RE
from src.tools.excel.excel_template_ai import _default_llm, _extract_json

# ------------------------------------------------------------
# 常量
# ------------------------------------------------------------

# D7：单块最大数据行数与块字符上限（超限减半行数重切）
CHUNK_MAX_ROWS = 50
CHUNK_MAX_CHARS = 24000

# markdown 表分隔行（render_llm_view 输出的 | --- | --- | 行）
_SEPARATOR_LINE_RE = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+$")

# ------------------------------------------------------------
# schema 资产
# ------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).resolve().parent / "assets" / "schema.json"


def load_default_schema() -> Dict[str, Any]:
    """加载默认 18 字段 schema 资产（社保增减员标准模板）"""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _normalize_header(header: Any) -> str:
    """表头规范化：去所有空白（换行/空格），用于指纹哈希与模板表头匹配"""
    return re.sub(r"\s+", "", str(header if header is not None else ""))


def template_fingerprint_from_headers(headers: Sequence[Any], sheet_name: str) -> str:
    """按固定规则计算模板指纹（纯函数，schema 资产与 xlsx 实测共用同一规则）

    计算式：sha256("{sheet_name}\\n" + "\\n".join(去空白表头)) 的 hexdigest，
    表头顺序即 schema.fields 顺序（template_header[0]）。schema.json 中写死的
    template_fingerprint 即按此式由 18 个标准表头 + "Sheet1" 预计算而来。
    """
    payload = str(sheet_name) + "\n" + "\n".join(_normalize_header(h) for h in headers)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_first_sheet_headers(xlsx_path: Any) -> Tuple[List[Any], str]:
    """openpyxl 读首个 sheet 的首行表头（值形态），返回 (表头列表, sheet 名)"""
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        sheet_name = ws.title
        headers: List[Any] = []
        for row in ws.iter_rows(min_row=1, max_row=1):
            headers = [c.value for c in row]
            break
        return headers, sheet_name
    finally:
        wb.close()


def template_fingerprint(xlsx_path: Any) -> str:
    """实测 xlsx 首个 sheet 表头指纹（与 schema 同规则哈希，纯函数零 LLM）"""
    headers, sheet_name = _read_first_sheet_headers(xlsx_path)
    return template_fingerprint_from_headers(headers, sheet_name)


def schema_matches(template_path: Any, schema: Dict[str, Any]) -> bool:
    """模板表头是否与 schema 指纹匹配（D3/D14：匹配直接用，不匹配触发一次重抽）"""
    try:
        return template_fingerprint(template_path) == schema.get("template_fingerprint")
    except Exception as e:
        logger.warning(f"[excel_extract] 模板指纹计算失败，视为不匹配: {template_path}, {e}")
        return False


def extract_schema_from_template(
    template_path: Any,
    *,
    llm_callable: Optional[Callable[[str], Any]] = None,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> Dict[str, Any]:
    """schema 不匹配时，从模板表头一次性生成字段清单（D14：供 Phase 3 固化写回资产）

    LLM 只看表头（零 PII），产出 fields 数组；指纹仍按固定规则本地计算。
    """
    headers, sheet_name = _read_first_sheet_headers(template_path)
    header_list = [str(h) for h in headers if _normalize_header(h)]
    prompt = (
        "你是社保增减员报表的字段定义专家。下面是一个 Excel 模板的表头（首行，顺序排列）：\n"
        + "\n".join(f"{i + 1}. {h}" for i, h in enumerate(header_list))
        + "\n\n请为每个业务字段输出定义，JSON 对象格式：\n"
        '{"fields": [{"name": "字段名", "type": "string|date|ym|enum|money|ratio", '
        '"description": "给抽取 LLM 的格式约定（日期 YYYY-MM-DD、年月 YYYYMM、比例 N%:N%）", '
        '"template_header": ["标准表头", "别名..."], "required": false, '
        '"enum_values": ["..."]}]\n'
        "（enum 字段才需要 enum_values）\n"
        "标准 18 字段参考：增减类型(枚举 增员/减员)、姓名、身份证、参保地、手机号码、参保年月(YYYYMM)、"
        "基本工资、公积金基数、公积金比例(N%:N%)、劳动合同起始时间、劳动合同终止时间、岗位、学历、备注、"
        "离职方式、用工信息结束时间(YYYY-MM-DD)、社保最后缴纳月(YYYYMM)、公积金最后缴纳月(YYYYMM)。\n"
        "只返回 JSON。"
    )
    content, _ = _invoke_llm(prompt, llm_callable, on_usage=on_usage, stage="schema")
    parsed = _extract_json(content)
    fields = parsed.get("fields") if isinstance(parsed, dict) else parsed
    if not isinstance(fields, list) or not fields:
        raise ValueError(f"模板 schema 重抽输出无效: {type(fields).__name__}")
    # CR P2：逐元素形状校验——缺 name/type 或 template_header 非字符串数组的字段
    # 会让下游 _schema_field_lines/填充列绑定直接 KeyError，此处尽早报出元素下标
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise ValueError(f"fields[{i}] 不是 JSON 对象: {type(f).__name__}")
        if not isinstance(f.get("name"), str) or not f["name"].strip():
            raise ValueError(f"fields[{i}].name 缺失或非字符串")
        if not isinstance(f.get("type"), str) or not f["type"].strip():
            raise ValueError(f"fields[{i}].type 缺失或非字符串: {f['name']}")
        th = f.get("template_header")
        if th is not None and (
            not isinstance(th, list) or not th or not all(isinstance(a, str) for a in th)
        ):
            raise ValueError(f"fields[{i}].template_header 须为非空字符串数组: {f['name']}")
    return {
        "version": "1.0",
        "sheet_name": sheet_name,
        "template_fingerprint": template_fingerprint_from_headers(headers, sheet_name),
        "fields": fields,
    }


# ------------------------------------------------------------
# LLM 调用（复用 _default_llm 的 provider 分支，额外取回 usage）
# ------------------------------------------------------------


def _call_llm(prompt: str, *, disable_thinking: bool = True) -> Tuple[str, Dict[str, Any]]:
    """默认 LLM 调用：复用 excel_template_ai._default_llm（qwen/zhipu/deepseek 分支），
    return_usage=True 取回 usage 供计量（含 model / cached_tokens 归一键）。禁止在本模块复制 provider 逻辑。"""
    return _default_llm(prompt, disable_thinking=disable_thinking, return_usage=True)


def _invoke_llm(
    prompt: str,
    llm_callable: Optional[Callable[[str], Any]],
    *,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
    stage: str = "extract",
) -> Tuple[str, Dict[str, Any]]:
    """统一 LLM 入口：注入的 llm_callable 可返回 str 或 (content, usage) 元组（测试用）；
    未注入则走 _call_llm。每次调用（含重试）都触发 on_usage(usage, stage)。"""
    if llm_callable is not None:
        result = llm_callable(prompt)
        if isinstance(result, tuple) and len(result) == 2:
            content, usage = result
            usage = dict(usage or {})
        else:
            content, usage = result, {}
    else:
        content, usage = _call_llm(prompt)
    if on_usage is not None:
        try:
            on_usage(usage, stage)
        except Exception as e:  # 计量回调失败不影响抽取主流程
            logger.warning(f"[excel_extract] on_usage 回调异常(stage={stage}): {e}")
    return content, usage


# ------------------------------------------------------------
# 分块（D7）
# ------------------------------------------------------------


def _chunk_markdown_table(
    rendered_text: str,
    *,
    max_rows: int = CHUNK_MAX_ROWS,
    max_chars: int = CHUNK_MAX_CHARS,
) -> List[str]:
    """把 render_llm_view 的 markdown 表按数据行切块，表头区（## Sheet 行 + 表头行 + 分隔行）随每块重复。

    行数上限 max_rows；若块总字符超 max_chars 则减半行数重切（行数到 1 仍超只能接受，
    防御单行超长）。无数据行的文本（空表/模板）返回空列表（不触发 LLM 调用）。
    """
    lines = rendered_text.splitlines()
    sep_idx = None
    for i, line in enumerate(lines):
        if line.startswith("|") and _SEPARATOR_LINE_RE.match(line):
            sep_idx = i
            break
    if sep_idx is None:
        # 无 markdown 表（如 "(空工作表)"）：没有可抽取的数据行，不切块不触发 LLM 调用
        return []

    header_block = "\n".join(lines[: sep_idx + 1])
    # 分隔行之后所有 | 开头的行都是数据行（多段重复表头/标题/页脚原样保留，由 LLM 自行理解）
    data_lines = [line for line in lines[sep_idx + 1:] if line.startswith("|")]
    if not data_lines:
        return []

    rows_per_chunk = max_rows
    while True:
        chunks = [data_lines[i: i + rows_per_chunk] for i in range(0, len(data_lines), rows_per_chunk)]
        longest = max(len(header_block) + sum(len(row) + 1 for row in chunk) for chunk in chunks)
        if longest <= max_chars or rows_per_chunk <= 1:
            break
        rows_per_chunk = max(1, rows_per_chunk // 2)

    return [header_block + "\n" + "\n".join(chunk) for chunk in chunks if chunk]


# ------------------------------------------------------------
# 抽取（M3 / D8 / D9）
# ------------------------------------------------------------


def _schema_field_lines(schema: Dict[str, Any]) -> str:
    """schema 字段定义 → prompt 文本（字段名/类型/枚举/格式约定）"""
    lines = []
    for f in schema.get("fields", []):
        item = f"- {f['name']}（类型 {f['type']}）"
        if f.get("enum_values"):
            item += f"，枚举值：{'/'.join(str(v) for v in f['enum_values'])}"
        if f.get("required"):
            item += "，必填"
        item += f"：{f.get('description', '')}"
        lines.append(item)
    return "\n".join(lines)


def _build_extract_prompt(
    chunk_text: str,
    schema: Dict[str, Any],
    source_label: str,
    *,
    retry_reason: Optional[str] = None,
) -> str:
    """抽取 prompt：字段定义 + 格式约定 + 脏结构说明 + 输出契约 + 数据块"""
    prompt = (
        "你是社保增减员报表数据抽取器。从下面的 markdown 表中抽取每位增员/减员人员的一条记录。\n\n"
        "## 字段定义与格式约定\n"
        f"{_schema_field_lines(schema)}\n\n"
        "## 输入说明\n"
        "- 数据来自客户原始报表，可能存在脏结构：分段表头（数据行中间重复出现表头行）、"
        "两行表头（首行是说明/标题、第二行才是真表头）、标题/说明/备注页脚行、重复列名、空列。"
        "这些行原样存在，请自行理解，不要把它们输出为人员记录。\n"
        "- 金额保持数字；日期输出 YYYY-MM-DD；年月输出 YYYYMM；比例输出 N% 或 N%:N%；找不到的字段填 null。\n"
        "- 源报表可能是脱敏文本（身份证含 [ID_n] 占位符或 * 掩码），占位符/掩码原样照抄。\n\n"
        "## 输出契约\n"
        '只返回一个 JSON 对象：{"records": [{...18 个字段..., "_source": "..."}]}，'
        f"其中每条记录的 _source 固定为 {json.dumps(source_label, ensure_ascii=False)}。"
        "没有人员记录时返回 {\"records\": []}。不要输出任何其他文字。\n\n"
        "## 数据\n"
        f"{chunk_text}"
    )
    if retry_reason:
        prompt += (
            f"\n\n## 上次失败原因（请修正）\n{retry_reason}"
            "\n请严格按输出契约重新返回完整 JSON。"
        )
    return prompt


def extract_records(
    rendered_text: str,
    schema: Dict[str, Any],
    *,
    source_label: str,
    llm_callable: Optional[Callable[[str], Any]] = None,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> Dict[str, Any]:
    """对单个 sheet 的渲染文本分块抽取人员记录（D7/D8/D9）。

    Returns:
        {"success": True, "records": [...], "dropped": n}；records 元素 = 18 字段 +
        _source 的 dict；dropped = 输出中被丢弃的非 dict 项数（CR P2 计数进 stats）；
        任一块重试 1 次后仍解析失败 → {"success": False, "error": ...}（任务级失败，D9）。
    """
    chunks = _chunk_markdown_table(rendered_text)
    records: List[Dict[str, Any]] = []
    dropped = 0
    for idx, chunk in enumerate(chunks):
        prompt = _build_extract_prompt(chunk, schema, source_label)
        parsed: Optional[Dict[str, Any]] = None
        last_error: Optional[Exception] = None
        for attempt in (1, 2):  # 首调 + 失败重试 1 次（D9）
            try:
                content, _ = _invoke_llm(prompt, llm_callable, on_usage=on_usage, stage="extract")
                parsed = _extract_json(content)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("records"), list):
                    raise ValueError(
                        f"输出契约不符：期望 {{\"records\": [...]}}，得到 {type(parsed).__name__}"
                    )
                if not parsed["records"] and attempt == 1:
                    # 空结果可疑（块内含数据行），附原因重试 1 次；仍空接受（页脚-only 块合法）
                    raise ValueError("返回 records 为空，但该块包含数据行")
                break
            except Exception as e:
                last_error = e
                logger.warning(f"[excel_extract] 第 {idx + 1} 块第 {attempt} 次抽取失败: {e}")
                prompt = _build_extract_prompt(chunk, schema, source_label, retry_reason=str(e))
                parsed = None
        if parsed is None:
            return {
                "success": False,
                "error": f"第 {idx + 1}/{len(chunks)} 块抽取失败（重试 1 次后仍失败）: {last_error}",
            }
        for item in parsed["records"]:
            if isinstance(item, dict):
                record = dict(item)
                record["_source"] = source_label  # _source 由调用方钉死，不信任 LLM 回填
                records.append(record)
            else:
                # CR P2：非 dict 项（LLM 偶发输出字符串/null 元素）不静默丢弃，计数上报
                dropped += 1
                logger.warning(f"[excel_extract] 丢弃非 dict 记录项: {type(item).__name__}")
    return {"success": True, "records": records, "dropped": dropped}


# ------------------------------------------------------------
# 校验（M4 / D10，独立纯函数供 Phase 3 复用）
# ------------------------------------------------------------

# ISO 7064 MOD 11-2 身份证校验
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"
_ID_FULL_RE = re.compile(r"\d{17}[\dX]")
# 脱敏掩码：连续 * 或 ≥3 连续 X/x（单个 X 是合法校验位，不算脱敏）
_ID_MASK_STAR_RE = re.compile(r"\*")
_ID_MASK_X_RUN_RE = re.compile(r"[Xx]{3,}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_YM_RE = re.compile(r"\d{6}")
_RATIO_RE = re.compile(r"\d+(?:\.\d+)?%(:\d+(?:\.\d+)?%)?")


def _is_empty(value: Any) -> bool:
    """记录字段值视为空：None 或空白字符串（找不到的字段统一 null）"""
    return value is None or (isinstance(value, str) and not value.strip())


def is_masked_id_card(value: Any) -> bool:
    """是否为脱敏身份证：含 ``*`` 或 ``XXXX`` 掩码（如 340203********1234），
    或 Q3 占位符形式（``[ID_n]``/``[TEL_n]``，excel_mask.MaskSession 产出——
    mask 后 LLM 照抄占位符，校验必须同等放行 + warning，否则生产脱敏管线
    全量记录误报 error 进修复回路/人工清单）"""
    return isinstance(value, str) and (
        bool(_ID_MASK_STAR_RE.search(value))
        or bool(_ID_MASK_X_RUN_RE.search(value))
        or bool(PLACEHOLDER_RE.fullmatch(value))
    )


def id_card_checksum_ok(value: str) -> bool:
    """18 位身份证校验位校验（ISO 7064 MOD 11-2）；格式非法直接 False"""
    if not isinstance(value, str) or not _ID_FULL_RE.fullmatch(value):
        return False
    total = sum(int(c) * w for c, w in zip(value[:17], _ID_WEIGHTS))
    return _ID_CHECK_CHARS[total % 11] == value[17].upper()


def is_valid_date(value: Any) -> bool:
    """YYYY-MM-DD 且为真实日历日期"""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def is_valid_ym(value: Any) -> bool:
    """YYYYMM 且月份 01-12"""
    if not isinstance(value, str) or not _YM_RE.fullmatch(value):
        return False
    return 1 <= int(value[4:6]) <= 12


def is_valid_ratio(value: Any) -> bool:
    """公积金比例格式：N% 或 N%:N%"""
    return isinstance(value, str) and bool(_RATIO_RE.fullmatch(value))


def validate_record(record: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, List[str]]:
    """单条记录校验（D10 分级）。error 阻断进修复回路；warning 不阻断照写。

    error：必填为空（姓名/身份证/增减类型）、增减类型非枚举、身份证非 18 位/校验位错
    （脱敏形式除外）、date 字段非 YYYY-MM-DD、ym 字段非 YYYYMM。
    warning：身份证为脱敏形式、社保≠公积金最后缴纳月、参保年月/最后缴纳月为 null、
    公积金比例格式异常。
    """
    errors: List[str] = []
    warnings: List[str] = []

    for f in schema.get("fields", []):
        name = f["name"]
        value = record.get(name)
        if f.get("required") and _is_empty(value):
            errors.append(f"{name}为空")
        if _is_empty(value):
            continue
        ftype = f.get("type")
        if ftype == "enum" and value not in (f.get("enum_values") or []):
            errors.append(f"{name}非枚举值{'/'.join(str(v) for v in f.get('enum_values') or [])}: {value}")
        elif ftype == "date" and not is_valid_date(value):
            errors.append(f"{name}非 YYYY-MM-DD 日期: {value}")
        elif ftype == "ym" and not is_valid_ym(value):
            errors.append(f"{name}非 YYYYMM 年月: {value}")
        elif ftype == "ratio" and not is_valid_ratio(value):
            warnings.append(f"{name}比例格式异常（期望 N% 或 N%:N%）: {value}")

    # 身份证专项（string 类型字段，按字段名识别）
    id_card = record.get("身份证")
    if not _is_empty(id_card):
        if is_masked_id_card(id_card):
            warnings.append(f"身份证为脱敏形式: {id_card}")
        elif not isinstance(id_card, str) or len(id_card) != 18:
            errors.append(f"身份证非 18 位: {id_card}")
        elif not id_card_checksum_ok(id_card):
            errors.append(f"身份证校验位错误: {id_card}")

    # 年月 null / 社保≠公积金（D10 warning）
    for name in ("参保年月", "社保最后缴纳月", "公积金最后缴纳月"):
        if _is_empty(record.get(name)):
            warnings.append(f"{name}为 null")
    social_last = record.get("社保最后缴纳月")
    fund_last = record.get("公积金最后缴纳月")
    if not _is_empty(social_last) and not _is_empty(fund_last) and social_last != fund_last:
        warnings.append(f"社保最后缴纳月({social_last})≠公积金最后缴纳月({fund_last})")

    return {"errors": errors, "warnings": warnings}


def validate_records(
    records: Sequence[Dict[str, Any]], schema: Dict[str, Any]
) -> List[Dict[str, List[str]]]:
    """逐条校验，返回与 records 等长的 [{"errors": [], "warnings": []}]"""
    return [validate_record(r, schema) for r in records]


# ------------------------------------------------------------
# 修复回路（M4 / D11）
# ------------------------------------------------------------


def _find_source_row(rendered_text: str, record: Dict[str, Any]) -> str:
    """在渲染文本中定位记录的原始数据行：优先身份证（含占位符/掩码形式，原样可搜），
    降级姓名；找不到返回空串。

    CR P2：姓名降级时**精确匹配优先**——先找"某单元格与姓名全等"的行（按 markdown
    表 ``|`` 切分逐格比对），无命中再做子串匹配。纯子串会把"王强"误命中"王小强"
    所在行，回喂修复时给 LLM 错误的原始行上下文。
    """
    for key in ("身份证", "姓名"):
        value = record.get(key)
        if _is_empty(value):
            continue
        target = str(value)
        # 第一遍：精确匹配（markdown 表某单元格与目标值全等）
        # 第二遍：子串匹配兜底（身份证 18 位数字串基本唯一；姓名保底不再挑剔）
        for exact in (True, False):
            for line in rendered_text.splitlines():
                if target not in line:
                    continue
                if exact and target not in [c.strip() for c in line.split("|")]:
                    continue
                return line
    return ""


def _build_repair_prompt(
    row_text: str,
    record: Dict[str, Any],
    errors: Sequence[str],
    schema: Dict[str, Any],
    source_label: str,
) -> str:
    """修复 prompt：原行文本 + 已抽记录 + 错误清单，要求只输出修正后的单条记录 JSON"""
    return (
        "你是社保增减员数据修复器。下面一条抽取记录未通过校验，请根据原始行文本修正它。\n\n"
        "## 字段格式约定\n"
        f"{_schema_field_lines(schema)}\n\n"
        "## 原始数据行\n"
        f"{row_text or '(未能在源文本中定位原始行)'}\n\n"
        "## 当前抽取记录（JSON）\n"
        f"{json.dumps(record, ensure_ascii=False, indent=2)}\n\n"
        "## 校验错误清单（必须全部修正）\n"
        + "\n".join(f"- {e}" for e in errors)
        + '\n\n## 输出契约\n只返回修正后的这一条记录的 JSON 对象（18 字段 + "'
        '_source"，_source 固定为 ' + json.dumps(source_label, ensure_ascii=False)
        + "），null 表示确实找不到。不要输出任何其他文字。"
    )


def repair_records(
    records_with_errors: Sequence[Tuple[Dict[str, Any], Dict[str, List[str]]]],
    rendered_text: str,
    schema: Dict[str, Any],
    *,
    source_label: str,
    llm_callable: Optional[Callable[[str], Any]] = None,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> Dict[str, Any]:
    """D11 修复回路：仅 error 行回喂 LLM（原行文本 + 已抽记录 + 错误清单），最多 2 轮；
    修复后重新校验，仍 error 的行从结果剔除进 manual_review（附原始行文本与错误）；
    warning-only / 无错行原样保留不触碰。stage="repair" 计量。

    Args:
        records_with_errors: (record, validate_record 结果) 元组序列

    Returns:
        {"success", "records", "record_warnings"(与 records 等长对齐，供报告),
         "manual_review", "repaired"}
    """
    records_out: List[Dict[str, Any]] = []
    record_warnings: List[List[str]] = []
    manual_review: List[Dict[str, Any]] = []
    repaired = 0

    for record, validation in records_with_errors:
        errors = list(validation.get("errors") or [])
        if not errors:
            records_out.append(dict(record))  # warning 行照留，不进修复回路
            record_warnings.append(list(validation.get("warnings") or []))
            continue

        current = dict(record)
        row_text = _find_source_row(rendered_text, current)
        warnings = list(validation.get("warnings") or [])
        for round_no in (1, 2):
            prompt = _build_repair_prompt(row_text, current, errors, schema, source_label)
            try:
                content, _ = _invoke_llm(prompt, llm_callable, on_usage=on_usage, stage="repair")
                candidate = _extract_json(content)
                if not isinstance(candidate, dict):
                    raise ValueError(f"修复输出不是 JSON 对象: {type(candidate).__name__}")
            except Exception as e:
                logger.warning(f"[excel_extract] 修复第 {round_no} 轮输出解析失败: {e}")
                # CR P2：解析失败**不清空既有错误**——追加本轮失败原因后进下一轮；
                # 第 2 轮仍失败时 manual_review 里保留原始校验错误（修复目标不可丢）
                errors = errors + [f"第 {round_no} 轮修复输出解析失败: {e}"]
                continue
            candidate["_source"] = source_label
            current = candidate
            result = validate_record(current, schema)
            errors = result["errors"]
            warnings = result["warnings"]
            if not errors:
                break

        if errors:
            manual_review.append({
                "record": current,
                "errors": errors,
                "warnings": warnings,
                "source_row": row_text,
                "_source": source_label,
            })
        else:
            repaired += 1
            records_out.append(current)
            record_warnings.append(warnings)

    return {
        "success": True,
        "records": records_out,
        "record_warnings": record_warnings,
        "manual_review": manual_review,
        "repaired": repaired,
    }


# ------------------------------------------------------------
# 同人多条标注（D12）
# ------------------------------------------------------------

# 备注追加标记格式：【同人多条：另见 文件名、文件名】（多来源顿号连接）
_DUP_NOTE_TEMPLATE = "【同人多条：另见 {others}】"


def _person_key(record: Dict[str, Any]) -> Tuple[Tuple[str, str], str]:
    """同人分组业务键：完整 18 位身份证优先；脱敏/缺失降级为 姓名+参保地+增减类型（D12）"""
    id_card = record.get("身份证")
    if isinstance(id_card, str) and _ID_FULL_RE.fullmatch(id_card):
        return ("id", id_card), id_card
    parts = [str(record.get(k) or "") for k in ("姓名", "参保地", "增减类型")]
    display = "|".join(parts)
    return ("fallback", display), display


def annotate_same_person(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """D12 同人多条：不合并不去重，按业务键分组；命中同人多条的记录备注末尾追加
    【同人多条：另见 其他来源文件名】（多个来源顿号连接），其余记录备注原样。

    Returns:
        {"records": [...], "duplicate_groups": [{"key": 业务键, "members": [{"姓名", "_source"}]}]}
    """
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    order: List[Tuple[str, str]] = []
    for record in records:
        key, display = _person_key(record)
        if key not in groups:
            groups[key] = {"display": display, "members": []}
            order.append(key)
        groups[key]["members"].append(record)

    duplicate_groups: List[Dict[str, Any]] = []
    dup_keys = set()
    for key in order:
        group = groups[key]
        if len(group["members"]) >= 2:
            dup_keys.add(key)
            duplicate_groups.append({
                "key": group["display"],
                "members": [
                    {"姓名": m.get("姓名"), "_source": m.get("_source")}
                    for m in group["members"]
                ],
            })

    annotated: List[Dict[str, Any]] = []
    for record in records:
        out = dict(record)
        key, _ = _person_key(out)
        if key in dup_keys:
            own_file = str(out.get("_source") or "").split("#")[0]
            other_files: List[str] = []
            for member in groups[key]["members"]:
                if member is record:
                    continue
                file_name = str(member.get("_source") or "").split("#")[0]
                if file_name and file_name not in other_files:
                    other_files.append(file_name)
            note = _DUP_NOTE_TEMPLATE.format(others="、".join(other_files) or own_file)
            base = out.get("备注")
            out["备注"] = (base + note) if isinstance(base, str) and base.strip() else note
        annotated.append(out)

    return {"records": annotated, "duplicate_groups": duplicate_groups}


# ------------------------------------------------------------
# 编排（Phase 3 skill 的 pipeline.py 直接调用）
# ------------------------------------------------------------


def run_extraction(
    rendered_sheets: Sequence[Dict[str, Any]],
    schema: Dict[str, Any],
    *,
    llm_callable: Optional[Callable[[str], Any]] = None,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> Dict[str, Any]:
    """多 sheet 全管线编排：抽取 → 校验 → 修复回路 → 同人标注。

    Args:
        rendered_sheets: [{name, text(render_llm_view 输出), source_label(文件名#Sheet 名)}]

    Returns:
        {"success", "records", "record_warnings"(与 records 等长对齐，供 D16 报告明细),
         "manual_review", "duplicate_groups",
         "stats": {extracted, repaired, manual_review_count, dropped_items, llm_calls}}
    """
    llm_calls = [0]

    def _counting_on_usage(usage: Dict[str, Any], stage: str) -> None:
        llm_calls[0] += 1
        if on_usage is not None:
            on_usage(usage, stage)

    per_sheet_records: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    all_records: List[Dict[str, Any]] = []
    dropped_total = 0
    for sheet in rendered_sheets:
        result = extract_records(
            sheet["text"],
            schema,
            source_label=sheet["source_label"],
            llm_callable=llm_callable,
            on_usage=_counting_on_usage,
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error"),
                "records": [],
                "record_warnings": [],
                "manual_review": [],
                "duplicate_groups": [],
                "stats": {"extracted": 0, "repaired": 0, "manual_review_count": 0,
                          "dropped_items": dropped_total, "llm_calls": llm_calls[0]},
            }
        per_sheet_records.append((sheet, result["records"]))
        all_records.extend(result["records"])
        dropped_total += int(result.get("dropped") or 0)

    validations = validate_records(all_records, schema)

    final_records: List[Dict[str, Any]] = []
    record_warnings: List[List[str]] = []
    manual_review: List[Dict[str, Any]] = []
    repaired_total = 0
    offset = 0
    for sheet, sheet_records in per_sheet_records:
        count = len(sheet_records)
        pairs = list(zip(sheet_records, validations[offset: offset + count]))
        offset += count
        if any(v.get("errors") for _, v in pairs):
            repair = repair_records(
                pairs,
                sheet["text"],
                schema,
                source_label=sheet["source_label"],
                llm_callable=llm_callable,
                on_usage=_counting_on_usage,
            )
            final_records.extend(repair["records"])
            record_warnings.extend(repair["record_warnings"])
            manual_review.extend(repair["manual_review"])
            repaired_total += repair["repaired"]
        else:
            final_records.extend(sheet_records)
            record_warnings.extend(
                list(v.get("warnings") or []) for _, v in pairs
            )

    annotated = annotate_same_person(final_records)
    return {
        "success": True,
        "records": annotated["records"],
        "record_warnings": record_warnings,
        "manual_review": manual_review,
        "duplicate_groups": annotated["duplicate_groups"],
        "stats": {
            "extracted": len(all_records),
            "repaired": repaired_total,
            "manual_review_count": len(manual_review),
            "dropped_items": dropped_total,
            "llm_calls": llm_calls[0],
        },
    }
