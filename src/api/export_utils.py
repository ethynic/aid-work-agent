"""通用 Excel 导出工具。"""
from typing import List, Optional

from fastapi.responses import StreamingResponse

from src.services.export_service import ExportTableConfig
from src.services.export_service import export_table_to_excel as _export_table_to_excel


def export_table_to_excel(
    table_name: str,
    tenant_id: str,
    columns: List[str],
    filename: str,
    where_clause: str = "",
    where_params: Optional[tuple] = None,
    db_label: Optional[str] = None,
) -> StreamingResponse:
    cfg = ExportTableConfig(
        table=table_name,
        columns=columns,
        filename=filename,
        where_clause=where_clause,
        where_params=where_params or (),
        db_label=db_label,
    )
    return _export_table_to_excel(cfg, tenant_id)
