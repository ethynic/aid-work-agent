"""excel_to_md 大文件守门测试（2026-11-12 agent2 压测整改）

1M 行 Excel 走 to_md 整簿转换时 MarkItDown 全量加载顶死 1GiB cgroup；
守门必须在 convert 之前 fail-fast，引导 read + range 分块。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_xlsx(path: Path, rows: int = 20, cols: int = 3) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            ws.cell(row=r, column=c, value=f"r{r}c{c}")
    wb.save(path)
    wb.close()
    return path


@pytest.fixture
def small_xlsx(tmp_path):
    return _make_xlsx(tmp_path / "small.xlsx")


def test_small_file_passes_guard_and_converts(small_xlsx):
    """小文件放行并正常转换"""
    from src.tools.excel.excel_to_md import excel_to_markdown

    result = excel_to_markdown(str(small_xlsx))
    assert result["success"] is True
    assert "r1c1" in result["markdown"]
    assert result["total_rows"] > 0


def test_guard_blocks_over_threshold_before_convert(small_xlsx, monkeypatch):
    """超阈值拦截发生在 MarkItDown convert 之前"""
    from src.tools.excel import excel_to_md

    monkeypatch.setattr(excel_to_md, "TO_MD_MAX_CELLS", 10)  # 20×3=60 cells > 10

    with patch("markitdown.MarkItDown") as mock_md:
        result = excel_to_md.excel_to_markdown(str(small_xlsx))

    assert result["success"] is False
    assert result["cells_est"] == 60
    assert "read" in result["error"] and "range" in result["error"]
    mock_md.assert_not_called()


def test_guard_error_suggests_read_range_not_sheet_name(small_xlsx, monkeypatch):
    """错误文案引导 read+range 分块，不引导 sheet_name（整簿加载照旧不省内存）"""
    from src.tools.excel import excel_to_md

    monkeypatch.setattr(excel_to_md, "TO_MD_MAX_CELLS", 10)
    result = excel_to_md.excel_to_markdown(str(small_xlsx))

    assert result["success"] is False
    assert "500 行" in result["error"]
    assert "sheet_name" not in result["error"]


def test_guard_allows_inflated_dimensions_when_bounded(tmp_path, monkeypatch):
    """虚标声明维度但真实数据小（空尾确认）→ 探测证伪后放行"""
    from src.tools.excel import excel_to_md

    real_rows = 5
    declared_rows = 1_000_000

    mock_wb = MagicMock()
    mock_ws = MagicMock()
    mock_ws.max_row = declared_rows
    mock_ws.max_column = 3
    ws_iter = iter([tuple(f"v{r}c{c}" for c in range(1, 4)) for r in range(1, real_rows + 1)])

    def fake_iter_rows(values_only=True):
        for row in ws_iter:
            yield row
        for _ in range(300):  # 空尾 ≥ STREAM_EMPTY_TAIL(200) → 边界确认
            yield (None, None, None)

    mock_ws.iter_rows = fake_iter_rows
    mock_wb.worksheets = [mock_ws]
    # MarkItDown 内部经 pandas 也以 read_only=True 调 load_workbook，无法按参数
    # 分流 mock；守门放行判定只看探测结果，MarkItDown 转换正确性由小文件用例覆盖
    monkeypatch.setattr(openpyxl, "load_workbook", lambda path, **kw: mock_wb)

    mock_converter = MagicMock()
    mock_converter.convert.return_value.text_content = "| a |\n|---|\n| 1 |"
    with patch("markitdown.MarkItDown", return_value=mock_converter):
        result = excel_to_md.excel_to_markdown(str(_make_xlsx(tmp_path / "tiny.xlsx")))

    assert result["success"] is True
    mock_converter.convert.assert_called_once()


def test_probe_sheet_missing_dimensions_goes_streaming(tmp_path):
    """声明维度缺失（write_only 文件 read_only 下 max_row=None）→ 走流式探测，触顶不确认"""
    from src.tools.excel.excel_to_md import _probe_sheet

    mock_ws = MagicMock()
    mock_ws.max_row = None
    mock_ws.max_column = None
    mock_ws.iter_rows = lambda values_only=True: iter(
        (f"d{r}",) for r in range(1, 30000)  # 数据密集，触顶 STREAM_MAX_SCAN_ROWS(20000)
    )
    rows, cols, confirmed = _probe_sheet(mock_ws)
    assert rows == 20000
    assert confirmed is False


def test_probe_sheet_missing_dimensions_small_data_confirmed():
    """声明缺失但真实数据小（空尾确认）→ 探测证伪放行"""
    from src.tools.excel.excel_to_md import _probe_sheet
    from src.tools.file.excel_reader import STREAM_EMPTY_TAIL

    mock_ws = MagicMock()
    mock_ws.max_row = None
    mock_ws.max_column = None

    def fake_iter_rows(values_only=True):
        for r in range(1, 6):
            yield (f"v{r}", f"w{r}", f"x{r}")
        for _ in range(STREAM_EMPTY_TAIL):
            yield (None, None, None)

    mock_ws.iter_rows = fake_iter_rows
    rows, cols, confirmed = _probe_sheet(mock_ws)
    assert rows == 5
    assert cols == 3
    assert confirmed is True


def test_guard_blocks_dense_file_at_scan_limit(tmp_path, monkeypatch):
    """探测触顶扫描上限仍无空尾（数据密集大文件）→ 必须拦截，防止真大文件漏拦"""
    from src.tools.excel import excel_to_md

    mock_wb = MagicMock()
    mock_ws = MagicMock()
    mock_ws.max_row = 1_000_000
    mock_ws.max_column = 5
    mock_ws.iter_rows = lambda values_only=True: iter(
        tuple(f"d{r}c{c}" for c in range(1, 6)) for r in range(1, 20001)
    )
    mock_wb.worksheets = [mock_ws]
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **k: mock_wb)

    result = excel_to_md.excel_to_markdown(str(_make_xlsx(tmp_path / "big.xlsx")))
    assert result["success"] is False
    assert "read" in result["error"]
    # 触顶未确认时按探测下限拦截，即使 probe cells(20000×5=10万) 低于阈值
    assert result["cells_est"] == 100_000


def test_guard_fail_open_on_non_xlsx(tmp_path):
    """探测异常（csv 等非 openpyxl 管控格式）fail-open 放行，按原路径处理"""
    from src.tools.excel.excel_to_md import excel_to_markdown

    csv_path = tmp_path / "plain.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")

    mock_converter = MagicMock()
    mock_converter.convert.return_value.text_content = "| a | b |\n|---|---|\n| 1 | 2 |"
    with patch("markitdown.MarkItDown", return_value=mock_converter):
        result = excel_to_markdown(str(csv_path))

    # 探测 openpyxl 打不开 csv → fail-open，正常走 MarkItDown 转换
    assert result["success"] is True
    assert "| a | b |" in result["markdown"]
