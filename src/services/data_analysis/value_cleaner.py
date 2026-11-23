"""
数据分析 - 数据值级确定性清洗与列质量报告

设计见 docs/subagent/data-analysis/excel-upload-validation-design.md §7。

定位：加载后、给 LLM 前的确定性清洗层。脏数据是企业数据常态，不做上传拦截，
统一在此转换可确定性转换的形态（单位数/缺失标记/杂空格日期等），一切转换
写进 column_quality 报告，绝不静默。清洗只作用于"整格匹配"的值，
`预算10万`、`约30` 等嵌入文本保持原样交 LLM 说明。

与 excel_validator 共享常量（缺失标记表、单位倍率表、日期格式清单），
避免两处口径漂移。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# ============== 共享常量（excel_validator 同步引用） ==============

# 整格匹配即视为缺失值（中文业务表惯例）
MISSING_MARKERS = {"-", "—", "–", "/", "\\", "无", "N/A", "NA", "n/a", "null", "NULL", "None"}

# 单位倍率（整格匹配 "数字+单位" 时换算）
UNIT_MULTIPLIERS = {
    "万": 10_000,
    "亿": 100_000_000,
    "千": 1_000,
}

# 货币符号（去除后按数值解析）
CURRENCY_PREFIXES = ("¥", "￥", "$", "€", "£", "RMB", "CNY", "USD")

# 百分比换算口径：50% -> 0.5（报告中显式声明）
PERCENT_SUFFIX = "%"

# 全角数字/符号 -> 半角
_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９．，－＋％",
    "0123456789.,-+%",
)

# 日期格式候选清单（顺序即优先级；strptime 自带真实日历校验，
# '2023-2-30' 类非法日期在此自然失败）
# 注意：清洗前先去所有空白字符，'2021 -1-20' -> '2021-1-20'
DATE_FORMATS = [
    ("%Y-%m-%d", None),
    ("%Y/%m/%d", None),
    ("%Y.%m.%d", None),
    ("%Y年%m月%d日", None),
    ("%Y-%m", "month"),
    ("%Y/%m", "month"),
    ("%Y年%m月", "month"),
    ("%Y%m%d", None),
]

# 日期形态粗判（用于区分"非法日期"与"普通文本"：
# 形态匹配但 strptime 失败 -> 视为非法日期置空并报告，而非保留文本）
# 覆盖：2023-10-01 / 2023/10/1 / 2023.10.01 / 2023年10月1日 / 2023年10月 / 2023-10
_DATE_LIKE_RE = re.compile(
    r"^(\d{4})\s*[-/年.]\s*(\d{1,2})(?:\s*[-/月.]\s*(\d{1,2}))?\s*日?(?:月)?$"
)

# 数值形态粗判（货币符号 + 千分位 + 小数 + 可选单位/百分比）
_NUMBER_LIKE_RE = re.compile(
    r"^[¥￥$€£]?\s*-?[0-9,．，]+(?:\.[0-9]+)?\s*(?:万|亿|千|元|%|％)?$"
)

# 列类型转换阈值（与 sheet_parser._infer_type 口径一致）
NUMERIC_COLUMN_THRESHOLD = 0.8
DATE_COLUMN_THRESHOLD = 0.5

# 报告中失败样例最多保留条数
MAX_EXAMPLES = 5


def _normalize_cell(value: Any) -> Any:
    """基础归一化：字符串去首尾空白、全角转半角。非字符串原样返回。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value:
            value = value.translate(_FULLWIDTH_TRANS).strip()
    return value


def _try_parse_date(raw: str) -> Tuple[Optional[Any], Optional[str]]:
    """尝试解析日期，返回 (date_value|None, granularity|None)。

    返回 (None, None) 表示不是日期形态或解析失败，由调用方区分处理。
    """
    from datetime import datetime

    compact = re.sub(r"\s+", "", raw)
    # 先按"年月日"形态尝试 strptime（自带日历校验）
    for fmt, granularity in DATE_FORMATS:
        try:
            dt = datetime.strptime(compact, fmt)
            return dt.date(), granularity
        except ValueError:
            continue
    return None, None


def _try_parse_number(raw: str) -> Optional[float]:
    """尝试解析数值（货币符号/千分位/单位倍率/百分比），失败返回 None。"""
    compact = re.sub(r"\s+", "", raw)

    percent = False
    if compact.endswith(PERCENT_SUFFIX) or compact.endswith("％"):
        percent = True
        compact = compact.rstrip("%％")

    # 去货币前缀
    for prefix in CURRENCY_PREFIXES:
        if compact.startswith(prefix):
            compact = compact[len(prefix):]
            break

    # 单位倍率（后缀单字符）
    multiplier = 1
    if compact and compact[-1] in UNIT_MULTIPLIERS:
        multiplier = UNIT_MULTIPLIERS[compact[-1]]
        compact = compact[:-1]
    elif compact.endswith("元"):
        compact = compact[:-1]

    compact = compact.replace(",", "").replace("，", "")
    if not compact or compact in ("-", "+"):
        return None
    try:
        num = float(compact)
    except ValueError:
        return None
    num *= multiplier
    if percent:
        num = num / 100
    return num


def _is_date_like(raw: str) -> bool:
    """形态上是否像日期（含被空白打断的形态，如 '2021 -1-20'）。"""
    compact = re.sub(r"\s+", "", raw)
    return bool(_DATE_LIKE_RE.match(compact))


def clean_cell(value: Any) -> Tuple[Any, str]:
    """清洗单个单元格值。

    Returns:
        (cleaned_value, action)：action ∈
        ''              未变化
        'missing'       缺失标记/空串 -> None
        'date'          日期解析成功
        'date_month'    中文年月等月粒度日期
        'invalid_date'  形态像日期但非法（'2023-2-30'）-> None
        'number'        数值换算成功（含单位/货币/百分比）
    """
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return None, ""
    if not isinstance(value, str):
        # 数值/日期类型原样返回（pandas 读出 datetime/float 的场景无需清洗）
        return value, ""

    normalized = _normalize_cell(value)
    if normalized is None or normalized == "":
        return None, "missing"
    if normalized in MISSING_MARKERS:
        return None, "missing"

    if _is_date_like(normalized):
        parsed, granularity = _try_parse_date(normalized)
        if parsed is not None:
            return parsed, ("date_month" if granularity == "month" else "date")
        return None, "invalid_date"

    if _NUMBER_LIKE_RE.match(normalized):
        num = _try_parse_number(normalized)
        if num is not None:
            return num, "number"

    return normalized, ""


def clean_dataframe(df: "pd.DataFrame") -> Tuple["pd.DataFrame", Dict[str, Any]]:
    """清洗 DataFrame 并生成列质量报告。

    清洗规则：
    1. 单元格级：仅转换整格匹配的确定性形态（见 clean_cell）；
    2. 列级重推断类型：非空值中数值占比 >= NUMERIC_COLUMN_THRESHOLD 转 numeric，
       日期占比 >= DATE_COLUMN_THRESHOLD 转 datetime，与 sheet_parser._infer_type
       阈值口径一致；转换失败置 NaN 并计入 to_null。

    Returns:
        (df_cleaned, column_quality)：column_quality 每列形如
        {"dtype": str, "converted": int, "to_null": int,
         "examples_failed": [...], "granularity": str|None, "note": str|None}
        仅 converted/to_null 为 0 且无备注的列只保留 dtype。
    """
    import pandas as pd

    df = df.copy()
    column_quality: Dict[str, Any] = {}

    for col in df.columns:
        converted = 0
        to_null = 0
        month_cells = 0
        invalid_examples: List[str] = []
        has_percent = False
        actions: List[str] = []  # 与 df.index 对齐的逐格动作

        new_values: List[Any] = []
        for v in df[col]:
            cleaned, action = clean_cell(v)
            if action == "missing" or action == "invalid_date":
                to_null += 1
                if action == "invalid_date":
                    if len(invalid_examples) < MAX_EXAMPLES:
                        invalid_examples.append(str(v))
            elif action in ("date", "date_month", "number"):
                converted += 1
                if action == "date_month":
                    month_cells += 1
                if action == "number" and isinstance(v, str) and ("%" in v or "％" in v):
                    has_percent = True
            new_values.append(cleaned)
            actions.append(action)

        df[col] = pd.Series(new_values, index=df.index, dtype=object)

        # 列级重推断类型
        dtype = "text"
        note: Optional[str] = None
        granularity: Optional[str] = "month" if month_cells > 0 else None

        non_null = pd.Series(new_values, index=df.index).dropna()
        if len(non_null) > 0:
            numeric_vals = pd.to_numeric(non_null, errors="coerce")
            numeric_ratio = numeric_vals.notna().mean()
            date_vals = pd.to_datetime(non_null, errors="coerce")
            date_ratio = date_vals.notna().mean()

            if numeric_ratio >= NUMERIC_COLUMN_THRESHOLD:
                coerced = pd.to_numeric(df[col], errors="coerce")
                extra_null = int(coerced.isna().sum() - df[col].isna().sum())
                if extra_null > 0:
                    to_null += extra_null
                df[col] = coerced
                dtype = "integer" if (numeric_vals.dropna() == numeric_vals.dropna().astype("int64")).all() else "decimal"
                if has_percent:
                    note = "百分比已换算为小数"
            elif date_ratio >= DATE_COLUMN_THRESHOLD:
                coerced = pd.to_datetime(df[col], errors="coerce")
                extra_null = int(coerced.isna().sum() - df[col].isna().sum())
                if extra_null > 0:
                    to_null += extra_null
                    # 补充失败样例（列级转换中失败的值）
                    failed_mask = coerced.isna() & df[col].notna()
                    for v in df[col][failed_mask].head(MAX_EXAMPLES - len(invalid_examples)):
                        invalid_examples.append(str(v))
                df[col] = coerced
                dtype = "date"
            else:
                dtype = "text"

        entry: Dict[str, Any] = {"dtype": dtype}
        if converted:
            entry["converted"] = converted
        if to_null:
            entry["to_null"] = to_null
        if invalid_examples:
            entry["examples_failed"] = invalid_examples
        if granularity:
            entry["granularity"] = granularity
        if note:
            entry["note"] = note
        column_quality[str(col)] = entry

    return df, column_quality


def column_warnings_from_quality(column_quality: Dict[str, Any]) -> List[str]:
    """把列质量报告压缩为面向用户的 warning 文案列表（上传校验事件用）。"""
    warnings: List[str] = []
    for col, q in column_quality.items():
        converted = q.get("converted", 0)
        to_null = q.get("to_null", 0)
        if not converted and not to_null:
            continue
        parts = []
        if converted:
            parts.append(f"{converted} 个值已自动换算（单位/货币/百分比/日期）")
        if to_null:
            parts.append(f"{to_null} 个值无法识别已置空")
        granularity = q.get("granularity")
        if granularity == "month":
            parts.append("含仅到月份的日期（按当月 1 日处理）")
        warnings.append(f"「{col}」列：{'，'.join(parts)}")
    return warnings


def quality_summary_for_llm(column_quality: Dict[str, Any]) -> str:
    """把列质量报告压缩为给分析 LLM 的一段说明（随 load_table 元信息返回）。"""
    lines: List[str] = []
    for col, q in column_quality.items():
        bits: List[str] = []
        if q.get("converted"):
            bits.append(f"{q['converted']} 个值已确定性换算")
        if q.get("to_null"):
            bits.append(f"{q['to_null']} 个值无法识别已置空")
        examples = q.get("examples_failed")
        if examples:
            bits.append(f"失败样例: {examples}")
        if q.get("granularity") == "month":
            bits.append("该列含月粒度日期")
        if q.get("note"):
            bits.append(q["note"])
        if bits:
            lines.append(f"- {col}（{q.get('dtype', 'text')}）: {'; '.join(bits)}")
    if not lines:
        return ""
    return "值级清洗报告（转换均已应用，回答时如涉及请如实说明）:\n" + "\n".join(lines)


# 模块级惰性校验 pandas 可用（本模块依赖 pandas，延迟 import 由调用方保证）
try:
    import pandas as _pd  # noqa: F401
except ImportError as _e:  # pragma: no cover
    logger.error(f"value_cleaner 依赖 pandas 但导入失败: {_e}")
