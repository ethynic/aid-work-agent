"""通用 Excel 导入服务（UUID 智能匹配）

将 ``travel_quote`` 中的 UUID 导入逻辑抽象为可复用服务，支持：

1. 同租户覆盖 —— UUID + tenant_id 命中 → UPDATE
2. 跨租户复制 —— UUID 命中其他租户 → 审计日志 + 新生 UUID → INSERT
3. 首次入库 —— UUID 合法但未命中 → 沿用 Excel UUID → INSERT
4. 全新数据 —— UUID 为空 / 不合法 → 新生 UUID → INSERT

调用方只需提供 ``ImportTableConfig``，无需关心智能判断细节。
"""
from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from src.db.database import get_db_connection


_UUID_RE = re.compile(r"^[a-zA-Z]+_[a-z0-9]{12}$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# 敏感字段正则（与项目后端接口错误处理规范对齐）
_SENSITIVE_PATTERNS = [
    re.compile(r'password["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'passwd["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'secret["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'token["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'api[_-]?key["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'access[_-]?key["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'private[_-]?key["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'auth[_-]?token["\s:=]+\S+', re.IGNORECASE),
]


def _default_sanitize(error_msg: str) -> str:
    """默认的敏感信息过滤实现，调用方可注入自己的版本。"""
    if not error_msg:
        return error_msg
    for pattern in _SENSITIVE_PATTERNS:
        error_msg = pattern.sub(
            lambda m: m.group(0).split("=")[0] + "=***", error_msg
        )
    return error_msg


def is_valid_uuid(value: Any, prefix: str) -> bool:
    """校验 UUID 是否符合 ``{prefix}_xxxxxxxxxxxx`` 格式（12 位小写 hex）。

    不合法时返回 False，导入流程将强制生成新 UUID。
    """
    if not isinstance(value, str):
        return False
    if not value.startswith(f"{prefix}_"):
        return False
    return bool(_UUID_RE.match(value))


def generate_uuid(prefix: str) -> str:
    """生成符合 ``{prefix}_xxxxxxxxxxxx`` 格式的 UUID。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _validate_identifier(value: str, kind: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise ValueError(f"非法{kind}: {value}")


def _validate_config(cfg: "ImportTableConfig") -> None:
    _validate_identifier(cfg.table, "表名")
    for col_name in cfg.columns:
        _validate_identifier(col_name, "列名")
    for col_name in cfg.numeric_cols | cfg.bool_cols | cfg.json_cols:
        _validate_identifier(col_name, "列名")
    for col_name in cfg.fixed_values.keys():
        _validate_identifier(col_name, "列名")


def _generate_unique_uuid(cursor: Any, table: str, prefix: str, max_attempts: int = 5) -> str:
    for _ in range(max_attempts):
        new_uuid = generate_uuid(prefix)
        cursor.execute(f"SELECT id FROM {table} WHERE uuid = %s", (new_uuid,))
        if not cursor.fetchone():
            return new_uuid
    raise RuntimeError("生成唯一 UUID 失败")


@dataclass
class ImportTableConfig:
    """单表导入配置。

    Attributes:
        table: 数据库表名
        columns: 期望从 Excel 中读取的列（首列通常为 uuid）
        numeric_cols: 需转为数值的列名集合
        bool_cols: 需转为布尔的列名集合
        json_cols: 需按 JSON 解析的列名集合
        uuid_prefix: UUID 前缀（如 ``"tqv"``），用于合法性和新生
        fixed_values: 导入时强制写入的固定字段值
    """

    table: str
    columns: List[str]
    numeric_cols: Set[str] = field(default_factory=set)
    bool_cols: Set[str] = field(default_factory=set)
    json_cols: Set[str] = field(default_factory=set)
    uuid_prefix: str = ""
    fixed_values: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportResult:
    """导入结果统计。"""

    imported: int = 0
    updated: int = 0
    skipped: int = 0
    cross_tenant: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "imported": self.imported,
            "updated": self.updated,
            "skipped": self.skipped,
            "cross_tenant": self.cross_tenant,
            "errors": self.errors[:20],
        }


def _coerce_value(col_name: str, value: Any, cfg: ImportTableConfig) -> Any:
    """将 Excel 读取的原始值转换为数据库兼容类型。

    空值返回 None；数值列转 int / float；布尔列转 bool。
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None

    if col_name in cfg.bool_cols:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "y", "是")
        return False

    if col_name in cfg.numeric_cols:
        try:
            val = float(value)
            return int(val) if val == int(val) else val
        except (ValueError, TypeError):
            return None

    if col_name in cfg.json_cols:
        if isinstance(value, str):
            return json.loads(value.strip())
        return value

    if isinstance(value, str):
        return value.strip()
    return value


def import_table_by_uuid(
    cfg: ImportTableConfig,
    tenant_id: str,
    file_content: bytes,
    operator: Optional[str] = None,
    sanitize: Optional[Any] = None,
) -> ImportResult:
    """通过 UUID 智能匹配导入 Excel 数据到业务表。

    Args:
        cfg: 表导入配置
        tenant_id: 当前租户 ID
        file_content: Excel 文件二进制内容（.xlsx）
        operator: 操作人标识（写入审计日志），可空
        sanitize: 错误信息过滤回调（接受 str，返回 str），默认使用本地实现

    Returns:
        ``ImportResult``，含 inserted / updated / skipped / cross_tenant / errors
    """
    import openpyxl

    sanitize = sanitize or _default_sanitize
    result = ImportResult()
    _validate_config(cfg)

    wb = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
    try:
        ws = wb.active

        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = next(rows_iter)
        except StopIteration:
            result.errors.append("Excel 文件为空")
            return result

        header_list = [str(h).strip() if h else "" for h in header]

        col_indices: Dict[str, int] = {}
        for col_name in cfg.columns:
            if col_name in header_list:
                col_indices[col_name] = header_list.index(col_name)

        business_cols = [
            c for c in cfg.columns if c not in ("uuid", "id", "tenant_id", "user_id") and c in col_indices
        ]
        has_uuid = "uuid" in col_indices

        with get_db_connection() as conn:
            cursor = conn.cursor()

            for row_num, row in enumerate(rows_iter, start=2):
                if not row or all(
                    v is None or (isinstance(v, str) and v.strip() == "") for v in row
                ):
                    continue

                row_data: Dict[str, Any] = {}
                for col_name, col_idx in col_indices.items():
                    if col_idx < len(row):
                        row_data[col_name] = _coerce_value(col_name, row[col_idx], cfg)

                row_uuid = row_data.get("uuid") if has_uuid else None
                if isinstance(row_uuid, str):
                    row_uuid = row_uuid.strip() or None

                business_data: Dict[str, Any] = {}
                for col_name in business_cols:
                    val = row_data.get(col_name)
                    if val is not None:
                        business_data[col_name] = val
                business_data.update(cfg.fixed_values)

                if not business_data:
                    result.skipped += 1
                    continue

                savepoint = f"import_row_{row_num}"
                cursor.execute(f"SAVEPOINT {savepoint}")
                cross_tenant_row = False
                try:
                    if row_uuid and is_valid_uuid(row_uuid, cfg.uuid_prefix):
                        cursor.execute(
                            f"SELECT id, tenant_id FROM {cfg.table} WHERE uuid = %s",
                            (row_uuid,),
                        )
                        existing = cursor.fetchone()

                        if existing and existing.get("tenant_id") == tenant_id:
                            set_clause = ", ".join(
                                f"{k} = %s" for k in business_data.keys()
                            )
                            vals = list(business_data.values()) + [
                                existing["id"],
                                tenant_id,
                            ]
                            cursor.execute(
                                f"UPDATE {cfg.table} SET {set_clause} "
                                f"WHERE id = %s AND tenant_id = %s",
                                tuple(vals),
                            )
                            result.updated += 1
                            cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                            continue

                        if existing and existing.get("tenant_id") != tenant_id:
                            logger.warning(
                                f"[UUIDImport-CrossTenant] {cfg.table} uuid={row_uuid} "
                                f"原属 tenant={existing.get('tenant_id')} "
                                f"-> 新租户 tenant={tenant_id}, 操作人={operator or 'unknown'}"
                            )
                            row_uuid = None
                            cross_tenant_row = True

                    business_data["tenant_id"] = tenant_id
                    if operator:
                        business_data["user_id"] = operator
                    if row_uuid and is_valid_uuid(row_uuid, cfg.uuid_prefix):
                        business_data["uuid"] = row_uuid
                    else:
                        business_data["uuid"] = _generate_unique_uuid(cursor, cfg.table, cfg.uuid_prefix)

                    cols = list(business_data.keys())
                    vals = list(business_data.values())
                    placeholders = ", ".join(["%s"] * len(cols))
                    col_names = ", ".join(cols)

                    cursor.execute(
                        f"INSERT INTO {cfg.table} ({col_names}) VALUES ({placeholders})",
                        tuple(vals),
                    )
                    result.imported += 1
                    if cross_tenant_row:
                        result.cross_tenant += 1
                    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")

                except Exception as e:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                    result.skipped += 1
                    error_msg = sanitize(str(e))
                    result.errors.append(f"第{row_num}行: {error_msg}")
                    logger.warning(
                        f"[UUIDImport] {cfg.table} row {row_num}: {error_msg}"
                    )

            conn.commit()
    finally:
        wb.close()
    logger.info(
        f"[UUIDImport] {cfg.table} tenant={tenant_id} "
        f"imported={result.imported} updated={result.updated} "
        f"skipped={result.skipped} cross_tenant={result.cross_tenant}"
    )
    return result
