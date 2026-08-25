"""
excel_extract（Excel ETL M3 抽取 + M4 校验修复回路 + 同人标注）单测

覆盖 gap-analysis §7 决议（D7~D12/D14）：
- 端到端黄金对照：5 夹具 render_llm_view → mask → run_extraction（mock LLM 按
  ## Sheet 行分发黄金记录）→ 与 golden_records.json 14 条字段级全等（D20）
- 分块（D7）：120 行 → 3 块且表头随块重复；24k 字符上限触发减半重切
- 容错（D8/D9）：```json 围栏/前后噪声解析；坏 JSON 重试 1 次；两次坏 → 任务级失败
- 校验（D10）：error/warning 逐规则
- 修复回路（D11）：1 轮修好 / 2 轮仍坏进 manual_review（附原始行）；warning 行不触碰
- 同人多条（D12）：跨 source 同身份证不合并、备注追加标记；脱敏身份证降级键分组
- schema（D14）：18 字段加载、模板指纹匹配/不匹配、extract_schema_from_template mock
- 计量：on_usage(usage, stage) 次数 = LLM 调用次数（含重试）
"""

import importlib.util
import json
from pathlib import Path

import openpyxl
import pytest

from src.tools.excel.excel_extract import (
    CHUNK_MAX_CHARS,
    _chunk_markdown_table,
    annotate_same_person,
    extract_records,
    extract_schema_from_template,
    id_card_checksum_ok,
    is_masked_id_card,
    is_valid_date,
    is_valid_ratio,
    is_valid_ym,
    load_default_schema,
    repair_records,
    run_extraction,
    schema_matches,
    template_fingerprint,
    validate_record,
    validate_records,
)
from src.tools.excel.excel_mask import MaskSession
from src.tools.excel.excel_reader import render_llm_view

pytestmark = [pytest.mark.tools]

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "excel_etl"
TEMPLATE_XLSX = FIXTURE_DIR / "生成标准模板格式.xlsx"

# 黄金对照的来源文件顺序（= golden_records.json 记录顺序）
GOLDEN_FILES = [
    "202608德勤派单增减人员.xlsx",
    "万宝盛华人员变动通知.xlsx",
    "活悦安徽分增减表.xlsx",
    "外管离职导出（上海中企）.xlsx",
    "上海信息数据（社保&公积金）.xlsx",
]

# 好身份证（build_fixtures 生成，校验位正确）与坏校验位
GOOD_ID = "340203199003074259"
GOOD_ID_X = "34020319921203451X"  # 末位 X 的合法身份证
BAD_CHECKSUM_ID = "340203199003074258"  # 校验位错（末位改 8）
MASKED_ID = "340203********1234"


@pytest.fixture(scope="module")
def schema():
    return load_default_schema()


def _full_record(**overrides):
    """18 字段全量合法记录（供校验/修复测试局部污染）"""
    record = {
        "增减类型": "增员",
        "姓名": "张伟",
        "身份证": GOOD_ID,
        "参保地": "芜湖市",
        "手机号码": "13805512366",
        "参保年月": "202608",
        "基本工资": 6500,
        "公积金基数": 6500,
        "公积金比例": "5%:5%",
        "劳动合同起始时间": "2026-09-01",
        "劳动合同终止时间": "2029-08-31",
        "岗位": "操作工",
        "学历": "大专",
        "备注": None,
        "离职方式": None,
        "用工信息结束时间": None,
        "社保最后缴纳月": None,
        "公积金最后缴纳月": None,
        "_source": "t.xlsx#Sheet1",
    }
    record.update(overrides)
    return record


def _mk_table(data_rows, header="| 序号 | 姓名 | 身份证 |"):
    """构造 render_llm_view 形态的 markdown 表"""
    return (
        "## Sheet: 测试\n\n" + header + "\n| --- | --- | --- |\n" + "\n".join(data_rows)
    )


# ============================================================
# 端到端黄金对照（D20）
# ============================================================


class TestGoldenEndToEnd:
    def test_run_extraction_matches_golden_records(self, schema):
        golden = json.loads((FIXTURE_DIR / "golden_records.json").read_text(encoding="utf-8"))
        golden_by_source = {}
        for rec in golden:
            golden_by_source.setdefault(rec["_source"], []).append(rec)

        # render_llm_view（M1）→ mask（Q3）→ run_extraction，与 Phase 3 管线同序
        mask_session = MaskSession()
        rendered_sheets = []
        for file_name in GOLDEN_FILES:
            rendered = render_llm_view(str(FIXTURE_DIR / file_name))
            assert rendered["success"] is True
            for sheet in rendered["sheets"]:
                rendered_sheets.append({
                    "name": sheet["name"],
                    "text": mask_session.mask(sheet["text"]),
                    "source_label": f"{file_name}#{sheet['name']}",
                })
        assert len(rendered_sheets) == 8

        def llm(prompt):
            # 按 prompt 中的 source_label 分发对应黄金记录（prompt 钉死了 _source 标签）
            for source_label, records in golden_by_source.items():
                if source_label in prompt:
                    content = json.dumps({"records": records}, ensure_ascii=False)
                    return content, {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
            raise AssertionError(f"mock LLM 无法识别 prompt 的来源: {prompt[:200]}")

        usages = []
        result = run_extraction(
            rendered_sheets, schema,
            llm_callable=llm,
            on_usage=lambda usage, stage: usages.append((usage, stage)),
        )

        assert result["success"] is True
        # 字段级全等（含 _source、日期/年月/比例格式；记录顺序 = 来源顺序）
        assert result["records"] == golden
        assert result["manual_review"] == []
        assert result["duplicate_groups"] == []
        assert result["stats"] == {
            "extracted": 14,
            "repaired": 0,
            "manual_review_count": 0,
            "dropped_items": 0,
            "llm_calls": 8,  # 8 个 sheet 各 1 块 1 次
        }
        # 计量契约：每次 LLM 调用都上报，stage 全为 extract，usage 原样透传
        assert len(usages) == 8
        assert all(stage == "extract" for _, stage in usages)
        assert all(u == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
                   for u, _ in usages)


# ============================================================
# 分块（D7）
# ============================================================


class TestChunking:
    def test_120_rows_split_into_3_chunks_with_header(self):
        rows = [f"| {i} | 名{i} | {GOOD_ID} |" for i in range(1, 121)]
        chunks = _chunk_markdown_table(_mk_table(rows))
        assert len(chunks) == 3
        for chunk in chunks:
            lines = chunk.splitlines()
            assert lines[0] == "## Sheet: 测试"
            assert lines[2] == "| 序号 | 姓名 | 身份证 |"       # 表头行随块重复
            assert lines[3] == "| --- | --- | --- |"            # 分隔行随块重复
        # 数据行切分 50/50/20
        data_counts = [len([l for l in c.splitlines() if l.startswith("|")]) - 2 for c in chunks]
        assert data_counts == [50, 50, 20]

    def test_24k_char_limit_halves_rows(self):
        # 60 行 x ~900 字符：50 行/块约 45k 超 24k → 减半到 25 行/块（~22.7k）通过
        rows = [f"| {i} | {'x' * 900} | {GOOD_ID} |" for i in range(1, 61)]
        chunks = _chunk_markdown_table(_mk_table(rows))
        assert len(chunks) == 3
        assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks)
        data_counts = [len([l for l in c.splitlines() if l.startswith("|")]) - 2 for c in chunks]
        assert data_counts == [25, 25, 10]

    def test_header_only_sheet_yields_no_chunks(self):
        assert _chunk_markdown_table("## Sheet: T\n\n| a |\n| --- |") == []
        assert _chunk_markdown_table("## Sheet: T\n\n(空工作表)") == []


# ============================================================
# 抽取容错（D8/D9）
# ============================================================


class TestExtractTolerance:
    def _one_row_table(self):
        return _mk_table([f"| 1 | 张伟 | {GOOD_ID} |"])

    def test_fenced_and_noisy_output_parsed(self, schema):
        def llm(prompt):
            content = (
                "以下是抽取结果：\n```json\n"
                + json.dumps({"records": [_full_record()]}, ensure_ascii=False)
                + "\n```\n以上完毕。"
            )
            return content, {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

        result = extract_records(self._one_row_table(), schema,
                                 source_label="a.xlsx#S1", llm_callable=llm)
        assert result["success"] is True
        assert len(result["records"]) == 1
        assert result["records"][0]["_source"] == "a.xlsx#S1"

    def test_bad_json_retries_once_then_succeeds(self, schema):
        prompts = []

        def llm(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return "这不是 JSON {{{", {}
            return json.dumps({"records": [_full_record()]}), {}

        result = extract_records(self._one_row_table(), schema,
                                 source_label="a.xlsx#S1", llm_callable=llm)
        assert result["success"] is True
        assert len(result["records"]) == 1
        assert len(prompts) == 2
        # 重试 prompt 附上失败原因
        assert "失败原因" in prompts[1]
        assert "JSON" in prompts[1]

    def test_two_bad_jsons_task_level_failure(self, schema):
        calls = []

        def llm(prompt):
            calls.append(prompt)
            return "坏输出 {{{", {}

        result = extract_records(self._one_row_table(), schema,
                                 source_label="a.xlsx#S1", llm_callable=llm)
        assert result["success"] is False
        assert "抽取失败" in result["error"]
        assert len(calls) == 2  # 首调 + 重试 1 次

    def test_usage_reported_per_call_including_retry(self, schema):
        usages = []

        def llm(prompt):
            n = len(usages) + 1
            if n == 1:
                return "bad", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            return json.dumps({"records": [_full_record()]}), {
                "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
            }

        result = extract_records(self._one_row_table(), schema, source_label="a.xlsx#S1",
                                 llm_callable=llm,
                                 on_usage=lambda usage, stage: usages.append((usage, stage)))
        assert result["success"] is True
        assert usages == [
            ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "extract"),
            ({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}, "extract"),
        ]


# ============================================================
# 校验（D10）
# ============================================================


class TestValidation:
    def test_valid_record_has_no_errors(self, schema):
        result = validate_record(_full_record(), schema)
        assert result["errors"] == []
        # 最后缴纳月 null 是 warning 不阻断（参保年月有值不报）
        assert result["warnings"] == [
            "社保最后缴纳月为 null", "公积金最后缴纳月为 null",
        ]

    def test_id_card_rules(self, schema):
        # 好身份证（末位 X）通过
        assert id_card_checksum_ok(GOOD_ID_X)
        # 校验位错 → error
        result = validate_record(_full_record(身份证=BAD_CHECKSUM_ID), schema)
        assert any("校验位错误" in e for e in result["errors"])
        # 非 18 位 → error
        result = validate_record(_full_record(身份证="3402031990030742"), schema)
        assert any("非 18 位" in e for e in result["errors"])
        # 脱敏形式：放行 + warning
        assert is_masked_id_card(MASKED_ID)
        assert not is_masked_id_card(GOOD_ID_X)  # 单个 X 是合法校验位，不算脱敏
        result = validate_record(_full_record(身份证=MASKED_ID), schema)
        assert not any("身份证" in e for e in result["errors"])
        assert any("脱敏" in w for w in result["warnings"])

    def test_placeholder_id_treated_as_masked(self, schema):
        """Q3 生产路径：mask 后 LLM 照抄 [ID_n]/[TEL_n] 占位符，校验必须放行 + warning。

        若按"非 18 位"报 error，脱敏管线全量记录会误进修复回路（修不好）再全量
        落人工清单——生产管线整体失效（CR P0 回归锚点）。
        """
        assert is_masked_id_card("[ID_3]")
        assert is_masked_id_card("[TEL_12]")
        assert not is_masked_id_card("[ID_3]x")   # 非纯占位符不误放行
        assert not is_masked_id_card("[IDX_3]")
        for value in ("[ID_3]", "[TEL_12]"):
            result = validate_record(_full_record(身份证=value), schema)
            assert result["errors"] == [], f"{value} 不应报 error: {result['errors']}"
            assert any("脱敏" in w for w in result["warnings"])

    def test_date_ym_enum_required_rules(self, schema):
        # 坏日期
        result = validate_record(_full_record(劳动合同起始时间="2026/9/1"), schema)
        assert any("非 YYYY-MM-DD" in e for e in result["errors"])
        assert not is_valid_date("2026-13-01")
        # 坏年月（格式 / 月 13）
        result = validate_record(_full_record(参保年月="2026/08"), schema)
        assert any("非 YYYYMM" in e for e in result["errors"])
        assert not is_valid_ym("202613")
        assert is_valid_ym("202608")
        # 增减类型非枚举
        result = validate_record(_full_record(增减类型="新增"), schema)
        assert any("非枚举值" in e for e in result["errors"])
        # 必填为空
        for field in ("姓名", "身份证", "增减类型"):
            result = validate_record(_full_record(**{field: None}), schema)
            assert any(e == f"{field}为空" for e in result["errors"])

    def test_warning_rules(self, schema):
        # 社保≠公积金最后缴纳月
        result = validate_record(_full_record(社保最后缴纳月="202607", 公积金最后缴纳月="202608"), schema)
        assert any("≠" in w for w in result["warnings"])
        assert result["errors"] == []
        # 参保年月 null → warning
        result = validate_record(_full_record(参保年月=None), schema)
        assert any("参保年月为 null" in w for w in result["warnings"])
        # 比例格式异常 → warning 不阻断
        assert not is_valid_ratio("5")
        result = validate_record(_full_record(公积金比例="5"), schema)
        assert result["errors"] == []
        assert any("比例格式异常" in w for w in result["warnings"])

    def test_validate_records_aligned_length(self, schema):
        results = validate_records([_full_record(), _full_record(姓名=None)], schema)
        assert len(results) == 2
        assert results[0]["errors"] == []
        assert any("姓名为空" in e for e in results[1]["errors"])


# ============================================================
# 修复回路（D11）
# ============================================================


class TestRepairLoop:
    ROW = f"| 1 | 张伟 | {BAD_CHECKSUM_ID} |"

    def _rendered(self):
        return _mk_table([self.ROW])

    def test_one_round_fix(self, schema):
        record = _full_record(身份证=BAD_CHECKSUM_ID)
        validation = validate_record(record, schema)
        assert validation["errors"]
        usages = []

        def llm(prompt):
            # 修复回喂必须含原行文本 + 已抽记录 + 错误清单
            assert self.ROW in prompt
            assert "校验位" in prompt
            assert BAD_CHECKSUM_ID in prompt
            return json.dumps(_full_record(), ensure_ascii=False), {}

        result = repair_records(
            [(record, validation)], self._rendered(), schema,
            source_label="a.xlsx#S1", llm_callable=llm,
            on_usage=lambda usage, stage: usages.append((usage, stage)),
        )
        assert result["success"] is True
        assert result["manual_review"] == []
        assert len(result["records"]) == 1
        assert result["records"][0]["身份证"] == GOOD_ID
        assert result["repaired"] == 1
        assert usages and all(stage == "repair" for _, stage in usages)

    def test_two_rounds_still_bad_goes_to_manual_review(self, schema):
        record = _full_record(身份证=BAD_CHECKSUM_ID)
        validation = validate_record(record, schema)
        calls = []

        def llm(prompt):
            calls.append(prompt)
            return json.dumps(_full_record(身份证=BAD_CHECKSUM_ID)), {}  # 始终修不好

        result = repair_records(
            [(record, validation)], self._rendered(), schema,
            source_label="a.xlsx#S1", llm_callable=llm,
        )
        assert len(calls) == 2  # 最多 2 轮
        assert result["records"] == []
        assert result["repaired"] == 0
        assert len(result["manual_review"]) == 1
        item = result["manual_review"][0]
        assert item["source_row"] == self.ROW          # 附原始行文本
        assert any("校验位" in e for e in item["errors"])
        assert item["_source"] == "a.xlsx#S1"

    def test_warning_only_records_untouched_no_llm_call(self, schema):
        record = _full_record(社保最后缴纳月="202607", 公积金最后缴纳月="202608")  # 仅 warning
        validation = validate_record(record, schema)
        assert not validation["errors"] and validation["warnings"]

        def llm(prompt):
            raise AssertionError("warning 行不应触发修复 LLM 调用")

        result = repair_records(
            [(record, validation)], self._rendered(), schema,
            source_label="a.xlsx#S1", llm_callable=llm,
        )
        assert result["records"] == [record]
        assert result["manual_review"] == []
        assert result["repaired"] == 0


# ============================================================
# 同人多条（D12）
# ============================================================


class TestSamePerson:
    def test_same_full_id_across_sources_no_merge_with_marker(self):
        r1 = _full_record(_source="A报表.xlsx#Sheet1", 备注=None)
        r2 = _full_record(增减类型="减员", _source="B报表.xlsx#Sheet1", 备注="7月离职")
        result = annotate_same_person([r1, r2])
        # 不合并不去重
        assert len(result["records"]) == 2
        # 备注末尾追加标记（跨来源互指）
        assert result["records"][0]["备注"] == "【同人多条：另见 B报表.xlsx】"
        assert result["records"][1]["备注"] == "7月离职【同人多条：另见 A报表.xlsx】"
        # duplicate_groups 正确
        assert len(result["duplicate_groups"]) == 1
        group = result["duplicate_groups"][0]
        assert group["key"] == GOOD_ID
        assert group["members"] == [
            {"姓名": "张伟", "_source": "A报表.xlsx#Sheet1"},
            {"姓名": "张伟", "_source": "B报表.xlsx#Sheet1"},
        ]

    def test_masked_id_falls_back_to_name_region_type_key(self):
        r1 = _full_record(身份证=MASKED_ID, 姓名="周霞", _source="A.xlsx#S1")
        r2 = _full_record(身份证="340203********5678", 姓名="周霞", _source="B.xlsx#S1")
        result = annotate_same_person([r1, r2])
        assert len(result["records"]) == 2
        assert result["duplicate_groups"][0]["key"] == "周霞|芜湖市|增员"

    def test_single_records_untouched(self):
        r1 = _full_record(_source="A.xlsx#S1", 备注="原备注")
        r2 = _full_record(姓名="李四", 身份证=GOOD_ID_X, _source="A.xlsx#S1", 备注=None)
        result = annotate_same_person([r1, r2])
        assert result["duplicate_groups"] == []
        assert result["records"][0]["备注"] == "原备注"
        assert result["records"][1]["备注"] is None


# ============================================================
# schema（D14）
# ============================================================


class TestSchema:
    def test_load_default_schema(self, schema):
        assert len(schema["fields"]) == 18
        names = [f["name"] for f in schema["fields"]]
        assert names[0] == "增减类型" and "公积金最后缴纳月" in names
        required = {f["name"] for f in schema["fields"] if f.get("required")}
        assert required == {"姓名", "身份证", "增减类型"}
        enum_field = schema["fields"][0]
        assert enum_field["enum_values"] == ["增员", "减员"]
        assert schema["template_fingerprint"]

    def _build_template(self):
        spec = importlib.util.spec_from_file_location(
            "build_fixtures", FIXTURE_DIR / "build_fixtures.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build_standard_template()
        return TEMPLATE_XLSX

    def test_template_fingerprint_match_and_mismatch(self, schema, tmp_path):
        template = self._build_template()
        # 匹配
        assert template_fingerprint(template) == schema["template_fingerprint"]
        assert schema_matches(template, schema) is True
        # 改一列表头后不匹配
        wb = openpyxl.load_workbook(template)
        wb.worksheets[0].cell(row=1, column=2, value="员工姓名2")
        modified = tmp_path / "modified.xlsx"
        wb.save(modified)
        wb.close()
        assert schema_matches(modified, schema) is False

    def test_extract_schema_from_template_with_mock_llm(self, tmp_path, schema):
        template = self._build_template()
        fields = [dict(f, template_header=[f["template_header"][0]]) for f in schema["fields"]]

        def llm(prompt):
            assert "表头" in prompt
            return json.dumps({"fields": fields}, ensure_ascii=False), {"prompt_tokens": 9}

        usages = []
        result = extract_schema_from_template(
            template, llm_callable=llm,
            on_usage=lambda usage, stage: usages.append((usage, stage)),
        )
        assert len(result["fields"]) == 18
        assert result["template_fingerprint"] == schema["template_fingerprint"]
        assert usages == [({"prompt_tokens": 9}, "schema")]


# ============================================================
# run_extraction 修复路径（D11 编排级）
# ============================================================


class TestRunExtractionRepairPath:
    def test_error_record_repaired_and_manual_review_flow(self, schema):
        rendered_text = _mk_table([f"| 1 | 张伟 | {BAD_CHECKSUM_ID} |"])
        extract_calls = []
        repair_calls = []

        def llm(prompt):
            if "数据修复器" in prompt:
                repair_calls.append(prompt)
                return json.dumps(_full_record(), ensure_ascii=False), {}
            extract_calls.append(prompt)
            return json.dumps({"records": [_full_record(身份证=BAD_CHECKSUM_ID)]},
                              ensure_ascii=False), {}

        result = run_extraction(
            [{"name": "测试", "text": rendered_text, "source_label": "a.xlsx#测试"}],
            schema, llm_callable=llm,
        )
        assert result["success"] is True
        assert result["stats"]["extracted"] == 1
        assert result["stats"]["repaired"] == 1
        assert result["stats"]["manual_review_count"] == 0
        assert result["stats"]["llm_calls"] == 2  # extract 1 + repair 1
        assert result["records"][0]["身份证"] == GOOD_ID
        assert len(extract_calls) == 1 and len(repair_calls) == 1

    def test_masked_placeholder_pipeline_no_repair_no_manual_review(self, schema):
        """Q3 脱敏域全链路：mask 后 LLM 照抄 [ID_n]，管线不进修复回路、不落人工清单。

        黄金 e2e 的 mock 直接返回真值身份证，未覆盖"LLM 照抄占位符"的真实路径；
        本用例钉死该路径（validate 占位符放行 + warning 不阻断 + unmask 属 Phase 3）。
        """
        masked_text = _mk_table([f"| 1 | 张伟 | [ID_1] | 138****366 |"])
        masked_record = _full_record(身份证="[ID_1]", 手机号码="[TEL_1]")

        def llm(prompt):
            assert "[ID_1]" in prompt  # LLM 全程只见脱敏文本（Q3）
            return json.dumps({"records": [masked_record]}, ensure_ascii=False), {"prompt_tokens": 7}

        result = run_extraction(
            [{"name": "测试", "text": masked_text, "source_label": "a.xlsx#测试"}],
            schema, llm_callable=llm,
        )
        assert result["success"] is True
        assert result["stats"] == {
            "extracted": 1, "repaired": 0, "manual_review_count": 0,
            "dropped_items": 0, "llm_calls": 1,
        }
        assert result["records"][0]["身份证"] == "[ID_1]"
        assert result["manual_review"] == []

    def test_extract_failure_short_circuits(self, schema):
        def llm(prompt):
            return "bad {{{", {}

        result = run_extraction(
            [{"name": "测试", "text": _mk_table([f"| 1 | 张伟 | {GOOD_ID} |"]),
              "source_label": "a.xlsx#测试"}],
            schema, llm_callable=llm,
        )
        assert result["success"] is False
        assert result["stats"]["llm_calls"] == 2  # 首调 + 重试


# ============================================================
# Phase 3 消化：Phase 2 CodeReview 遗留 P2 回归用例
# ============================================================


class TestPhase2CrP2Regressions:
    """CR P2 五项修复的回归钉子"""

    def test_find_source_row_exact_name_match_preferred(self, schema):
        """姓名降级定位：全等命中行优先于子串命中（"王强"不得误定位"王小强"行）"""
        from src.tools.excel.excel_extract import _find_source_row
        text = _mk_table([
            "| 1 | 王小强 | 340203199003074259 |",
            "| 2 | 王强 | 340222198806215179 |",
        ])
        record = {"姓名": "王强", "身份证": None}
        # 姓名"王强"子串同时命中两行，精确匹配必须定位到第 2 行
        assert "王小强" not in _find_source_row(text, record)
        assert "340222198806215179" in _find_source_row(text, record)

    def test_repair_parse_failure_keeps_original_errors(self, schema):
        """第 2 轮解析失败：manual_review 的 errors 必须保留原始校验错误（合并而非替换）"""
        bad = _full_record(身份证=BAD_CHECKSUM_ID)
        rendered = _mk_table([f"| 1 | 张伟 | {BAD_CHECKSUM_ID} |"])

        def llm(_prompt):
            return "坏 JSON {{{", {}  # 两轮解析全失败

        result = repair_records(
            [(bad, validate_record(bad, schema))], rendered, schema,
            source_label="a.xlsx#测试", llm_callable=llm,
        )
        assert result["success"] is True
        assert result["records"] == []
        assert len(result["manual_review"]) == 1
        errors = result["manual_review"][0]["errors"]
        # 原始校验错误保留 + 两轮解析失败原因合并
        assert any("校验位错误" in e for e in errors)
        assert sum("解析失败" in e for e in errors) == 2

    def test_run_extraction_returns_record_warnings(self, schema):
        """record_warnings 与 records 等长对齐回传（供 Phase 3 报告明细）"""
        # 脱敏身份证 + 社保≠公积金：两条 warning 不阻断，须逐条回传
        rec = _full_record(
            身份证=MASKED_ID,
            社保最后缴纳月="202607", 公积金最后缴纳月="202608",
        )

        def llm(_prompt):
            return json.dumps({"records": [rec]}, ensure_ascii=False), {}

        result = run_extraction(
            [{"name": "测试", "text": _mk_table([f"| 1 | 张伟 | {MASKED_ID} |"]),
              "source_label": "a.xlsx#测试"}],
            schema, llm_callable=llm,
        )
        assert result["success"] is True
        assert len(result["record_warnings"]) == len(result["records"]) == 1
        warnings = result["record_warnings"][0]
        assert any("脱敏" in w for w in warnings)
        assert any("社保最后缴纳月" in w and "≠" in w for w in warnings)

    def test_dropped_items_counted_in_stats(self, schema):
        """非 dict 记录项丢弃计数进 stats.dropped_items，不静默消失"""
        def llm(_prompt):
            payload = {"records": [_full_record(), "垃圾字符串", None]}
            return json.dumps(payload, ensure_ascii=False), {}

        result = run_extraction(
            [{"name": "测试", "text": _mk_table([f"| 1 | 张伟 | {GOOD_ID} |"]),
              "source_label": "a.xlsx#测试"}],
            schema, llm_callable=llm,
        )
        assert result["success"] is True
        assert result["stats"]["dropped_items"] == 2
        assert len(result["records"]) == 1

    def test_extract_schema_validates_field_shape(self, tmp_path, schema):
        """schema 重抽输出 fields 元素形状校验：坏元素按下标报 ValueError"""
        template = self._build_template()

        def llm(_prompt):
            # 第 2 个元素缺 type——下游 _schema_field_lines 会 KeyError
            return json.dumps({"fields": [
                {"name": "姓名", "type": "string"},
                {"name": "身份证"},
            ]}, ensure_ascii=False), {}

        with pytest.raises(ValueError, match=r"fields\[1\]\.type"):
            extract_schema_from_template(template, llm_callable=llm)

    def _build_template(self):
        spec = importlib.util.spec_from_file_location(
            "build_fixtures", FIXTURE_DIR / "build_fixtures.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build_standard_template()
        return TEMPLATE_XLSX
