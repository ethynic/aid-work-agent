import io
from contextlib import contextmanager

import openpyxl

from src.services.import_service import ImportTableConfig, import_table_by_uuid


def make_excel(rows: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@contextmanager
def fail_if_db_called(monkeypatch):
    def _raise():
        raise AssertionError("不匹配列头不应连接数据库")

    monkeypatch.setattr("src.services.import_service.get_db_connection", _raise)
    yield


def test_fixed_values_do_not_create_rows_when_headers_do_not_match(monkeypatch):
    excel = make_excel([
        ["酒店名称", "开业时间", "地址", "房型"],
        ["嘎百福客栈", "2019年", "观景台附近", "标间"],
    ])
    cfg = ImportTableConfig(
        table="documents",
        columns=[
            "uuid", "title", "file_type", "file_path", "file_size",
            "total_chunks", "embedding_model", "summary", "metadata",
        ],
        numeric_cols={"file_size", "total_chunks"},
        json_cols={"metadata"},
        uuid_prefix="doc",
        fixed_values={"source_type": "hotel_resource"},
    )

    with fail_if_db_called(monkeypatch):
        result = import_table_by_uuid(cfg, "tenant_9eb3e45cab83", excel)

    assert result.imported == 0
    assert result.updated == 0
    assert result.skipped == 0
    assert result.errors == ["Excel 列头不匹配，未找到可导入的业务字段"]
