#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excel 模板填充提速与提示的单测

覆盖 2026-11-18 聊易 trace tr_runner_2f6d4b579c2f4811813b287e053fbb2d 后的三项改动：
- 结构分析缓存：同模板 + 同 data 键集合命中（跳过 LLM），模板/键集合变化不命中
- 未映射数值行提示：模板其他档位/区块的旧示例数据在渲染后列入 unmapped_numeric_rows
- ExcelRouter 读取优先快路径：「读取…检查修改结果」不再落入 LLM 路由
- ExcelProcessTool._merge_results 透传 message/unmapped_numeric_rows 给调用方
"""

import json
import sys
from pathlib import Path

import openpyxl
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 样例：A 档明细区（可填充）+ B 档示例区块（未映射，含数值）
# ============================================================

def _build_multi_block_sample(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A档报价单"

    ws["A1"] = "报价单"
    ws["A2"] = "客户："
    ws["B2"] = "（示例客户）"
    for i, h in enumerate(["序号", "项目", "单价", "金额"], 1):
        ws.cell(row=3, column=i, value=h)
    for r, (name, price, amount) in enumerate(
        [("课程", 168, 6720), ("午餐", 30, 1200)], start=4
    ):
        ws.cell(row=r, column=1, value=r - 3)
        ws.cell(row=r, column=2, value=name)
        ws.cell(row=r, column=3, value=price)
        ws.cell(row=r, column=4, value=amount)
    ws["A6"] = "合计"
    ws["D6"] = 7920

    # B 档示例区块：结构分析未识别，若 data 不覆盖会原样残留
    ws["A8"] = "B档（30-39人）专属报价"
    ws["A9"] = "课程"
    ws["C9"] = 198
    ws["D9"] = 7920
    ws["A10"] = "包车"
    ws["C10"] = 1000
    ws["D10"] = 1000

    wb.save(str(path))
    return path


SAMPLE_STRUCTURE = {
    "title": {"row": 1, "col": 1},
    "meta_fields": [{"row": 2, "col": 1, "bind": "customer"}],
    "columns": [
        {"col": 1, "header": "序号", "bind": "no"},
        {"col": 2, "header": "项目", "bind": "item"},
        {"col": 3, "header": "单价", "bind": "price"},
        {"col": 4, "header": "金额", "bind": "amount"},
    ],
    "detail_first_row": 4,
    "detail_last_row": 5,
    "detail_template_row": 4,
    "totals": [{"row": 6, "col": 4, "bind": "grand_total"}],
}


def _mock_llm(structure_dict=None):
    payload = json.dumps(structure_dict or SAMPLE_STRUCTURE, ensure_ascii=False)
    return lambda prompt: payload


def _fill_data():
    from src.tools.excel.excel_template_ai import FillData

    return FillData(
        rows=[
            {"no": 1, "item": "课程", "price": 168, "amount": 6720},
            {"no": 2, "item": "午餐", "price": 30, "amount": 1200},
        ],
        meta={"customer": "张三"},
        totals={"grand_total": 7920},
    )


@pytest.fixture
def sample_path(tmp_path):
    return _build_multi_block_sample(tmp_path / "sample.xlsx")


# ============================================================
# 未映射数值行提示
# ============================================================

class TestUnmappedNumericRows:
    def test_reports_unmapped_block_rows(self, sample_path, tmp_path):
        from src.tools.excel.excel_template_ai import fill_with_sample

        res = fill_with_sample(
            str(sample_path), _fill_data(),
            output_dir=str(tmp_path), llm_callable=_mock_llm(),
        )
        assert res["success"], res
        # B 档区块第 9、10 行各含 2 个数值，且未被结构映射
        assert res["unmapped_numeric_rows"] == [9, 10]
        assert "9-10" in res["message"]
        assert "未映射" in res["message"]

    def test_no_warning_when_all_numeric_rows_bound(self, tmp_path):
        """模板仅含已绑定明细/合计 → 不误报。"""
        from src.tools.excel.excel_template_ai import fill_with_sample

        path = tmp_path / "clean.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "报价单"
        ws["A2"] = "客户："
        ws["B2"] = "示例"
        for i, h in enumerate(["序号", "项目", "单价", "金额"], 1):
            ws.cell(row=3, column=i, value=h)
        for r, (name, price, amount) in enumerate(
            [("课程", 168, 6720), ("午餐", 30, 1200)], start=4
        ):
            ws.cell(row=r, column=1, value=r - 3)
            ws.cell(row=r, column=2, value=name)
            ws.cell(row=r, column=3, value=price)
            ws.cell(row=r, column=4, value=amount)
        ws["A6"] = "合计"
        ws["D6"] = 7920
        wb.save(str(path))

        res = fill_with_sample(
            str(path), _fill_data(), output_dir=str(tmp_path), llm_callable=_mock_llm(),
        )
        assert res["success"], res
        assert res["unmapped_numeric_rows"] == []
        assert "未映射" not in res["message"]

    def test_format_row_ranges(self):
        from src.tools.excel.excel_template_ai import _format_row_ranges

        assert _format_row_ranges([9, 10]) == "9-10"
        assert _format_row_ranges([3, 5, 6, 7, 10]) == "3、5-7、10"
        assert _format_row_ranges([]) == ""

    def test_unmapped_rows_follow_row_shrink(self, tmp_path):
        """M<K 收缩 2 行：未映射行坐标随渲染计划上移（9、10 -> 7、8）。"""
        from src.tools.excel.excel_template_ai import FillData, fill_with_sample

        path = tmp_path / "shrink.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "报价单"
        ws["A2"] = "客户："
        ws["B2"] = "示例"
        for i, h in enumerate(["序号", "项目", "单价", "金额"], 1):
            ws.cell(row=3, column=i, value=h)
        for r, (name, price, amount) in enumerate(
            [("课程", 168, 6720), ("午餐", 30, 1200), ("包车", 1400, 1400)], start=4
        ):
            ws.cell(row=r, column=1, value=r - 3)
            ws.cell(row=r, column=2, value=name)
            ws.cell(row=r, column=3, value=price)
            ws.cell(row=r, column=4, value=amount)
        ws["A7"] = "合计"
        ws["D7"] = 9320
        ws["A9"] = "B档"
        ws["C9"] = 198
        ws["D9"] = 7920
        ws["A10"] = "C档"
        ws["C10"] = 1000
        ws["D10"] = 1000
        wb.save(str(path))

        structure = dict(SAMPLE_STRUCTURE)
        structure["detail_first_row"] = 4
        structure["detail_last_row"] = 6
        structure["totals"] = [{"row": 7, "col": 4, "bind": "grand_total"}]
        data = FillData(
            rows=[{"no": 1, "item": "课程", "price": 168, "amount": 6720}],
            meta={"customer": "张三"},
            totals={"grand_total": 6720},
        )
        res = fill_with_sample(
            str(path), data, output_dir=str(tmp_path), llm_callable=_mock_llm(structure)
        )
        assert res["success"], res
        assert res["unmapped_numeric_rows"] == [7, 8]

    def test_non_numeric_title_row_does_not_break_fill(self, sample_path, tmp_path):
        """LLM 返回 title.row 非数字时检测函数静默跳过，填充本身不失败。"""
        from src.tools.excel.excel_template_ai import fill_with_sample

        structure = dict(SAMPLE_STRUCTURE)
        structure["title"] = {"row": "A1", "col": 1}
        res = fill_with_sample(
            str(sample_path), _fill_data(), output_dir=str(tmp_path), llm_callable=_mock_llm(structure)
        )
        assert res["success"], res
        assert res["unmapped_numeric_rows"] == [9, 10]


# ============================================================
# 结构分析缓存
# ============================================================

class _FakeRedis:
    """模拟 redis_client：get 反序列化 / set 序列化，make_key 拼前缀，贴近真实往返。"""

    def __init__(self):
        self.store = {}
        self.set_calls = []
        self._key_prefix = ""

    def is_available(self):
        return True

    def make_key(self, prefix, identifier=""):
        parts = [p for p in (self._key_prefix, prefix, identifier) if p]
        return ":".join(parts)

    def get(self, key):
        raw = self.store.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, key, value, ex=None):
        self.set_calls.append((key, ex))
        self.store[key] = json.dumps(value, ensure_ascii=False, default=str)

    def delete(self, key):
        return self.store.pop(key, None) is not None


class TestStructureCache:
    def test_hit_skips_llm_and_reuses_structure(self, sample_path, monkeypatch):
        import src.core.redis_client as redis_mod
        import src.tools.excel.excel_template_ai as tpl

        fake = _FakeRedis()
        monkeypatch.setattr(redis_mod, "redis_client", fake, raising=False)

        calls = {"n": 0}
        structure_payload = json.dumps(SAMPLE_STRUCTURE, ensure_ascii=False)

        def fake_analyze(sample_file_path, fill_data, *, llm_callable=None):
            calls["n"] += 1
            from src.tools.excel.excel_template_ai import _coerce_structure, read_sample_grid

            grid = read_sample_grid(sample_file_path)
            return _coerce_structure(json.loads(structure_payload), grid)

        monkeypatch.setattr(tpl, "analyze_structure", fake_analyze)

        first = tpl.analyze_structure_safe(str(sample_path), _fill_data())
        assert first["success"], first
        assert first["structure_cached"] is False
        assert calls["n"] == 1
        assert len(fake.set_calls) == 1
        assert fake.set_calls[0][1] == tpl.STRUCTURE_CACHE_TTL_SECONDS

        second = tpl.analyze_structure_safe(str(sample_path), _fill_data())
        assert second["success"], second
        assert second["structure_cached"] is True
        assert calls["n"] == 1  # 第二次未调用 LLM
        assert second["structure"]["columns"][2]["bind"] == "price"
        # JSON 往返不丢字段（title/merge/totals 均来自序列化缓存）
        assert second["structure"]["title"]["row"] == 1
        assert second["structure"]["totals"][0]["bind"] == "grand_total"

    def test_cache_stores_raw_structure_not_explicit_columns(self, sample_path, monkeypatch):
        """缓存键不含 data.columns，缓存里必须是 LLM 原始绑定，不能继承别人的显式列。"""
        import src.core.redis_client as redis_mod
        import src.tools.excel.excel_template_ai as tpl

        fake = _FakeRedis()
        monkeypatch.setattr(redis_mod, "redis_client", fake, raising=False)

        def fake_analyze(sample_file_path, fill_data, *, llm_callable=None):
            from src.tools.excel.excel_template_ai import _coerce_structure, read_sample_grid

            return _coerce_structure(
                json.loads(json.dumps(SAMPLE_STRUCTURE)), read_sample_grid(sample_file_path)
            )

        monkeypatch.setattr(tpl, "analyze_structure", fake_analyze)

        # 先带显式 columns 触发写缓存：本次返回用显式绑定
        first = tpl.analyze_structure_safe(
            str(sample_path),
            tpl.FillData(
                rows=_fill_data().rows,
                meta={"customer": "张三"},
                totals={"grand_total": 7920},
                columns=[{"col": 1, "header": "序号", "bind": "custom_no"}],
            ),
        )
        assert first["structure"]["columns"][0]["bind"] == "custom_no"

        # 再不带 columns → 命中缓存，拿到 LLM 原始绑定而非 custom_no
        second = tpl.analyze_structure_safe(str(sample_path), _fill_data())
        assert second["structure_cached"] is True
        assert second["structure"]["columns"][0]["bind"] == "no"

    def test_different_data_keys_miss_cache(self, sample_path, monkeypatch):
        import src.core.redis_client as redis_mod
        import src.tools.excel.excel_template_ai as tpl
        from src.tools.excel.excel_template_ai import FillData

        fake = _FakeRedis()
        monkeypatch.setattr(redis_mod, "redis_client", fake, raising=False)

        calls = {"n": 0}

        def fake_analyze(sample_file_path, fill_data, *, llm_callable=None):
            calls["n"] += 1
            structure = dict(SAMPLE_STRUCTURE)
            structure["columns"] = [dict(c) for c in SAMPLE_STRUCTURE["columns"]]
            from src.tools.excel.excel_template_ai import _coerce_structure, read_sample_grid

            return _coerce_structure(structure, read_sample_grid(sample_file_path))

        monkeypatch.setattr(tpl, "analyze_structure", fake_analyze)

        tpl.analyze_structure_safe(str(sample_path), _fill_data())
        # 新增列键 → 键签名变化 → 必须重新分析（不能复用旧列绑定）
        wider = FillData(
            rows=[
                {"no": 1, "item": "课程", "price": 168, "amount": 6720, "remark": "含材料"},
                {"no": 2, "item": "午餐", "price": 30, "amount": 1200, "remark": ""},
            ],
            meta={"customer": "张三"},
            totals={"grand_total": 7920},
        )
        res = tpl.analyze_structure_safe(str(sample_path), wider)
        assert res["success"], res
        assert res["structure_cached"] is False
        assert calls["n"] == 2

    def test_llm_callable_bypasses_cache(self, sample_path, monkeypatch):
        import src.core.redis_client as redis_mod
        import src.tools.excel.excel_template_ai as tpl

        fake = _FakeRedis()
        monkeypatch.setattr(redis_mod, "redis_client", fake, raising=False)

        res = tpl.analyze_structure_safe(
            str(sample_path), _fill_data(), llm_callable=_mock_llm()
        )
        assert res["success"], res
        assert fake.set_calls == []  # 测试注入 LLM 时不写缓存

    def test_redis_unavailable_falls_back_to_llm(self, sample_path, monkeypatch):
        import src.core.redis_client as redis_mod
        import src.tools.excel.excel_template_ai as tpl

        class _DownRedis:
            def is_available(self):
                return False

            def get(self, key):  # pragma: no cover - 不应被调用
                raise AssertionError("Redis 不可用时不应读写")

            def set(self, key, value, ex=None):  # pragma: no cover
                raise AssertionError("Redis 不可用时不应读写")

        monkeypatch.setattr(redis_mod, "redis_client", _DownRedis(), raising=False)
        calls = {"n": 0}
        structure_payload = json.dumps(SAMPLE_STRUCTURE, ensure_ascii=False)

        def fake_analyze(sample_file_path, fill_data, *, llm_callable=None):
            calls["n"] += 1
            from src.tools.excel.excel_template_ai import _coerce_structure, read_sample_grid

            return _coerce_structure(json.loads(structure_payload), read_sample_grid(sample_file_path))

        monkeypatch.setattr(tpl, "analyze_structure", fake_analyze)
        res = tpl.analyze_structure_safe(str(sample_path), _fill_data())
        assert res["success"], res
        assert calls["n"] == 1

    def test_invalidate_removes_cached_structure(self, sample_path, monkeypatch):
        """渲染失败时必须能主动失效缓存，避免 TTL 内复用坏结构。"""
        import src.core.redis_client as redis_mod
        import src.tools.excel.excel_template_ai as tpl

        fake = _FakeRedis()
        monkeypatch.setattr(redis_mod, "redis_client", fake, raising=False)

        def fake_analyze(sample_file_path, fill_data, *, llm_callable=None):
            from src.tools.excel.excel_template_ai import _coerce_structure, read_sample_grid

            return _coerce_structure(
                json.loads(json.dumps(SAMPLE_STRUCTURE)), read_sample_grid(sample_file_path)
            )

        monkeypatch.setattr(tpl, "analyze_structure", fake_analyze)

        tpl.analyze_structure_safe(str(sample_path), _fill_data())
        assert len(fake.store) == 1

        tpl.invalidate_structure_cache(str(sample_path), _fill_data())
        assert fake.store == {}
        # 失效后下一次调用重新分析（不再命中缓存）
        res = tpl.analyze_structure_safe(str(sample_path), _fill_data())
        assert res["structure_cached"] is False


# ============================================================
# ExcelRouter 读取优先快路径
# ============================================================

class TestRouterReadFastPath:
    @pytest.mark.asyncio
    async def test_read_check_result_skips_llm_router(self, monkeypatch):
        from src.tools.excel.excel_router import ExcelRouter

        router = ExcelRouter()

        async def _boom(*args, **kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("规则应命中，不应调用 LLM 路由")

        monkeypatch.setattr(ExcelRouter, "_get_gateway", lambda self: _boom)
        result = await router.route(
            "读取该文件内容检查修改结果", ["/tmp/quote_modified.xlsx"]
        )
        assert result["task"] == "read"

    @pytest.mark.asyncio
    async def test_write_intent_still_goes_to_llm_router(self, monkeypatch):
        from src.tools.excel.excel_router import ExcelRouter

        router = ExcelRouter()
        called = {"n": 0}

        class _Resp(dict):
            pass

        class _Gateway:
            def get_model_name(self):
                return "test-model"

            async def chat(self, **kwargs):
                called["n"] += 1
                return _Resp(content='{"task": "modify", "params": {"operations": []}, "reason": "写"}')

        monkeypatch.setattr(ExcelRouter, "_get_gateway", lambda self: _Gateway())
        result = await router.route(
            "读取模板后把 B4 单元格改为 2026年11月18日", ["/tmp/quote.xlsx"]
        )
        assert called["n"] == 1
        assert result["task"] == "modify"

    @pytest.mark.asyncio
    async def test_plain_read_intent_keeps_to_md_rule(self):
        from src.tools.excel.excel_router import ExcelRouter

        result = await ExcelRouter().route("读取该文件内容", ["/tmp/quote.xlsx"])
        assert result["task"] == "to_md"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ctx",
        [
            "读取该文件内容",
            "请帮我查看表格的完整结构",
            "看看这个附件的内容是否完整",
            "打开文件确认数据正确",
            "查看这份表格的明细信息",
        ],
    )
    async def test_pure_read_phrasings_hit_rule(self, ctx, monkeypatch):
        """纯读取表达的残差白名单：去掉读取动词/只读名词后无剩余，直接走规则。"""
        from src.tools.excel.excel_router import ExcelRouter

        class _NoLlm:
            def get_model_name(self):
                return "test-model"

            async def chat(self, **kwargs):  # pragma: no cover - 不应被调用
                raise AssertionError(f"纯读取指令不应调用 LLM: {ctx!r}")

        monkeypatch.setattr(ExcelRouter, "_get_gateway", lambda self: _NoLlm())
        result = await ExcelRouter().route(ctx, ["/tmp/quote.xlsx"])
        assert result["task"] in ("read", "to_md"), result

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ctx",
        [
            "读取并更新单价为新价格",
            "读取并替换所有旧价格",
            "读取表格后设置单价为 200",
            "读取后调整合计金额",
            "读取清单并修正小计",
            "读取导出数据后补全缺失行",
        ],
    )
    async def test_write_intent_not_downgraded_to_read(self, ctx, monkeypatch):
        """读取开头但含写意图词/强写动词的指令必须交 LLM 路由，不能判 read。"""
        from src.tools.excel.excel_router import ExcelRouter

        router = ExcelRouter()
        called = {"n": 0}

        class _Gateway:
            def get_model_name(self):
                return "test-model"

            async def chat(self, **kwargs):
                called["n"] += 1
                return dict(
                    content='{"task": "modify", "params": {"operations": []}, "reason": "写"}'
                )

        monkeypatch.setattr(ExcelRouter, "_get_gateway", lambda self: _Gateway())
        result = await router.route(ctx, ["/tmp/quote.xlsx"])
        assert called["n"] == 1, f"{ctx!r} 被错误降级为 {result}"
        assert result["task"] == "modify"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ctx",
        [
            "读取并修改后保存",
            "读取表格，修改后再导出",
            "读取并检查修改结果，把错误项改正",
            "读取表格，检查修改情况并改正错误",
            "读取文件后把表头加粗",
            "读取表格并粘贴到 B 列",
            "读取文件并追加内容到第 10 行",
            "读取表格后把单价列乘以 2",
            # 第三轮评审实测的未知动作词反例：黑名单穷不尽，必须靠残差白名单兜底
            "读取表格并复制A列到B列",
            "读取表格并填充空单元格",
            "读取文件后把重复项标红",
            "读取表格内容并给表头加边框",
            "读取表格，将A列拆成两列",
            "读取表格，将A列和B列对调",
            "读取表格，检查修改结果，把A列复制到C列",
            "读取表格，检查修改结果，按A列升序排列",
            "读取表格，检查修改结果，将表头那行冻结",
            "读取表格，检查修改结果，工作表改名为汇总",
        ],
    )
    async def test_nominalized_phrase_cannot_hide_write_action(self, ctx, monkeypatch):
        """剥离'修改后/修改结果'等短语后，动作词仍须拦截——不得降级为 read/to_md。"""
        from src.tools.excel.excel_router import ExcelRouter

        router = ExcelRouter()

        class _Gateway:
            def get_model_name(self):
                return "test-model"

            async def chat(self, **kwargs):
                return dict(
                    content='{"task": "modify", "params": {"operations": []}, "reason": "写"}'
                )

        monkeypatch.setattr(ExcelRouter, "_get_gateway", lambda self: _Gateway())
        result = await router.route(ctx, ["/tmp/quote.xlsx"])
        assert result["task"] not in ("read", "to_md"), f"{ctx!r} 被错误降级为 {result}"


# ============================================================
# _merge_results 透传
# ============================================================

def test_merge_results_passes_fill_template_hints():
    from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext

    tool = ExcelProcessTool()
    ctx = PipelineContext(file_paths=["/tmp/x.xlsx"])
    ctx.results.append({
        "operation": "fill_template",
        "file_path": "/tmp/x_filled.xlsx",
        "file_name": "",
        "file_size": 1024,
        "rows_rendered": 2,
        "unmapped_numeric_rows": [9, 10],
        "message": "输出文件第 9-10 行含未映射的数值数据…",
    })
    merged = tool._merge_results(ctx)
    assert merged["unmapped_numeric_rows"] == [9, 10]
    assert "9-10" in merged["message"]