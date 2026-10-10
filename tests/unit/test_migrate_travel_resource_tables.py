"""
旅游资源表迁 metadata 迁移脚本单测（Phase 1）

覆盖：raw_payload 合成（表键+镜像键）、幂等/冲突/force 决策、32KB 告警、
verify 差异输出（含 drop 后复查）、清场决策（total_chunks 修正）、主循环接线。
纯逻辑直测；DB 流程经 monkeypatch 桩化（不连库）。

生命周期：随迁移脚本同生共死——真机迁移 + 清场 + verify 复查通过后，
本文件与脚本一并删除（见开发计划 Phase 5 收尾清理）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# scripts/ 无 __init__.py；按文件路径显式加载，不依赖 sys.path 状态
# （与 test_tenant_migrate_kb_target_storage.py 同模式）
_spec = importlib.util.spec_from_file_location(
    "_migrate_travel_resource_tables",
    Path(__file__).resolve().parents[2] / "scripts" / "migrate_travel_resource_tables_to_metadata.py",
)
migr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migr)


# ---------------------------------------------------------------------------
# raw_payload 合成
# ---------------------------------------------------------------------------

class TestBuildRawPayload:
    def test_hotel_table_plus_mirrored_business_keys(self):
        metadata = {
            "region": "贵阳", "sub_region": "花溪", "diamond_level": "4钻",
            "address": "某路1号", "source_sheet": "贵阳汇总",
        }
        payload = migr.build_raw_payload("hotel_resource", metadata, {1: "房型A | 580 | ..."})

        assert payload["price_table"] == "房型A | 580 | ..."
        # 顶层业务键全量镜像（含 address——设计目标结构未列但属 parser 业务键）
        assert payload["region"] == "贵阳"
        assert payload["sub_region"] == "花溪"
        assert payload["diamond_level"] == "4钻"
        assert payload["address"] == "某路1号"
        assert payload["source_sheet"] == "贵阳汇总"

    def test_attraction_ticket_project_and_images_mirror(self):
        metadata = {
            "region": "黔南", "category": "natural", "category_cn": "自然风光",
            "images": {"cover": "file_abc", "gallery": ["file_d"]},
        }
        payload = migr.build_raw_payload(
            "attraction_resource", metadata, {1: "门票表", 2: "项目表"})

        assert payload["ticket_table"] == "门票表"
        assert payload["project_table"] == "项目表"
        assert payload["images"] == {"cover": "file_abc", "gallery": ["file_d"]}
        assert payload["category_cn"] == "自然风光"

    def test_system_keys_and_raw_payload_not_mirrored(self):
        metadata = {"region": "贵阳", "run_id": "r1", "pipeline_version": "v2",
                    "ingested_at": "2026-10-10", "raw_payload": {"旧": "值"}}
        payload = migr.build_raw_payload("hotel_resource", metadata, {1: "P"})

        assert "run_id" not in payload
        assert "pipeline_version" not in payload
        assert "ingested_at" not in payload
        assert "旧" not in payload  # 已有 raw_payload 不参与镜像，避免嵌套自引用

    def test_missing_chunk_row_omits_table_key(self):
        payload = migr.build_raw_payload(
            "attraction_resource", {"region": "贵阳"}, {1: "门票表"})
        assert "ticket_table" in payload
        assert "project_table" not in payload

    def test_top_level_key_does_not_clobber_table_text(self):
        # chunk 文本是表数据唯一权威来源，即使顶层 metadata 恰有同名键
        payload = migr.build_raw_payload(
            "hotel_resource", {"price_table": "陈旧值"}, {1: "chunk 权威文本"})
        assert payload["price_table"] == "chunk 权威文本"


# ---------------------------------------------------------------------------
# 迁移决策（幂等 / 冲突 / force / 超限）
# ---------------------------------------------------------------------------

class TestPlanDocument:
    def test_fresh_doc_migrates(self):
        metadata = {"region": "贵阳"}
        plan = migr.plan_document("hotel_resource", metadata, {1: "P"})

        assert plan["action"] == "migrate"
        assert plan["conflicts"] == []
        assert plan["new_metadata"]["raw_payload"]["price_table"] == "P"
        assert plan["new_metadata"]["region"] == "贵阳"  # 顶层键原样保留

    def test_rerun_after_migrate_is_ok_zero_change(self):
        # 幂等核心：迁移后的 metadata 再跑一次 → ok，不产生写动作
        first = migr.plan_document("hotel_resource", {"region": "贵阳"}, {1: "P"})
        second = migr.plan_document("hotel_resource", first["new_metadata"], {1: "P"})

        assert second["action"] == "ok"

    def test_existing_key_differs_skips_without_force(self):
        metadata = {"region": "贵阳", "raw_payload": {"price_table": "旧表", "region": "贵阳"}}
        plan = migr.plan_document("hotel_resource", metadata, {1: "新表"})

        assert plan["action"] == "skip_conflict"
        assert plan["conflicts"] == ["price_table"]

    def test_force_overwrites_conflicting_key(self):
        metadata = {"region": "贵阳", "raw_payload": {"price_table": "旧表", "region": "贵阳"}}
        plan = migr.plan_document("hotel_resource", metadata, {1: "新表"}, force=True)

        assert plan["action"] == "migrate"
        assert plan["new_metadata"]["raw_payload"]["price_table"] == "新表"

    def test_merge_preserves_unknown_existing_payload_keys(self):
        # raw_payload 已有的未知业务键（非表键/镜像键）合并不删
        metadata = {"region": "贵阳", "raw_payload": {"custom_field": "保留我"}}
        plan = migr.plan_document("hotel_resource", metadata, {1: "P"})

        assert plan["action"] == "migrate"
        assert plan["new_metadata"]["raw_payload"]["custom_field"] == "保留我"

    def test_corrupt_raw_payload_conflicts_unless_force(self):
        plan = migr.plan_document(
            "hotel_resource", {"raw_payload": "不是对象"}, {1: "P"})
        assert plan["action"] == "skip_conflict"

        plan = migr.plan_document(
            "hotel_resource", {"raw_payload": "不是对象"}, {1: "P"}, force=True)
        assert plan["action"] == "migrate"

    def test_oversized_payload_flags_but_still_migrates(self):
        big_text = "x" * (migr.RAW_PAYLOAD_LIMIT_BYTES + 1)
        plan = migr.plan_document("hotel_resource", {}, {1: big_text})

        assert plan["action"] == "migrate"
        assert plan["oversized"] is True  # 告警不截断，照常写入由人工处理

    def test_doc_without_chunks_migrates_mirror_keys_only(self):
        # uuid 导入存量：无 chunk 行，仅镜像顶层业务键（价格表无源可迁）
        plan = migr.plan_document("attraction_resource", {"region": "贵阳"}, {})

        assert plan["action"] == "migrate"
        payload = plan["new_metadata"]["raw_payload"]
        assert "ticket_table" not in payload
        assert payload["region"] == "贵阳"


# ---------------------------------------------------------------------------
# verify 差异输出
# ---------------------------------------------------------------------------

class TestVerifyDocument:
    def test_all_match_ok(self):
        metadata = {"region": "贵阳", "raw_payload": {"price_table": "P", "region": "贵阳"}}
        status, diffs = migr.verify_document("hotel_resource", metadata, {1: "P"})

        assert status == "ok"
        assert diffs == []

    def test_value_mismatch_and_missing_key_reported(self):
        metadata = {"raw_payload": {"ticket_table": "旧文本"}}  # 缺 project_table
        status, diffs = migr.verify_document(
            "attraction_resource", metadata, {1: "新文本", 2: "项目表"})

        assert status == "mismatch"
        assert any("ticket_table" in d and "不一致" in d for d in diffs)
        assert any("project_table" in d and "缺" in d for d in diffs)

    def test_post_drop_verification_ok(self):
        # --drop-chunks 后表 chunk 行已删，raw_payload 表键齐全 → 结构性通过
        metadata = {"raw_payload": {"ticket_table": "T", "project_table": "P"}}
        status, diffs = migr.verify_document("attraction_resource", metadata, {0: "摘要"})

        assert status == "ok"

    def test_no_chunks_no_payload_is_no_source(self):
        # uuid 存量未迁移：无 chunk 行且 raw_payload 无表键 → 无可比对（非失败）
        status, _ = migr.verify_document("hotel_resource", {}, {})
        assert status == "no_source"

    def test_mirror_drift_reported(self):
        # 双写漂移：raw_payload.region 与顶层不一致 → 暴露差异
        metadata = {"region": "新值", "raw_payload": {"price_table": "P", "region": "旧值"}}
        status, diffs = migr.verify_document("hotel_resource", metadata, {1: "P"})

        assert status == "mismatch"
        assert any("镜像键 region" in d for d in diffs)

    def test_mirror_drift_after_drop_still_fails_exit(self):
        # --drop-chunks 后（无表 chunk 行）：镜像漂移必须仍判 mismatch——
        # 此时 raw_payload 是唯一数据源，顶层更新未同步 raw 即数据丢失
        metadata = {"region": "新值", "raw_payload": {"price_table": "P", "region": "旧值"}}
        status, diffs = migr.verify_document("hotel_resource", metadata, {0: "摘要"})

        assert status == "mismatch"
        assert any("镜像键 region" in d for d in diffs)

    def test_unmirrored_top_level_key_reported(self):
        # 顶层新增业务键未镜像进 raw_payload（D2 上线前 PUT 只改顶层的窗口）→ 暴露
        metadata = {"region": "A", "new_key": "B", "raw_payload": {"price_table": "P", "region": "A"}}
        status, diffs = migr.verify_document("hotel_resource", metadata, {1: "P"})

        assert status == "mismatch"
        assert any("new_key" in d and "未镜像" in d for d in diffs)


# ---------------------------------------------------------------------------
# 清场决策
# ---------------------------------------------------------------------------

class TestPlanDrop:
    def test_complete_payload_drops_with_total_1(self):
        metadata = {"raw_payload": {"ticket_table": "T", "project_table": "P"}}
        should, reason, total = migr.plan_drop(
            "attraction_resource", metadata, {0: "摘要", 1: "T", 2: "P"})

        assert should is True
        assert total == 1

    def test_missing_table_key_refuses(self):
        metadata = {"raw_payload": {"ticket_table": "T"}}  # 缺 project_table
        should, reason, total = migr.plan_drop(
            "attraction_resource", metadata, {0: "摘要", 1: "T", 2: "P"})

        assert should is False
        assert "拒绝清场" in reason
        assert total is None

    def test_no_chunk_rows_is_noop(self):
        should, reason, _ = migr.plan_drop(
            "hotel_resource", {"raw_payload": {"price_table": "P"}}, {0: "摘要"})

        assert should is False
        assert "无需清理" in reason

    def test_doc_without_chunk0_corrects_total_to_zero(self):
        # uuid 存量无任何 chunk：raw_payload 已有表键（Excel 全量导出导入补齐）时
        # total_chunks 如实修正为 0
        should, _, total = migr.plan_drop(
            "hotel_resource", {"raw_payload": {"price_table": "P"}}, {1: "P"})

        assert should is True
        assert total == 0

    def test_stale_payload_value_refuses_drop(self):
        # 键在而值落后（观察期内旧 PUT 只改了 chunk 未改 metadata）：
        # chunk 是唯一较新数据源，删除不可逆 → 拒绝并提示重跑迁移
        metadata = {"raw_payload": {"price_table": "旧表"}}
        should, reason, total = migr.plan_drop(
            "hotel_resource", metadata, {0: "摘要", 1: "新表"})

        assert should is False
        assert "落后于 chunk" in reason
        assert total is None


# ---------------------------------------------------------------------------
# 主循环接线（桩化 DB，验证写库调用与计数）
# ---------------------------------------------------------------------------

class TestParseMetadataDict:
    def test_dict_passthrough_and_str_json_parsed(self):
        # 真实路径：驱动返回 TEXT 字符串；dict 分支为防御兜底
        assert migr._parse_metadata_dict({"region": "贵阳"}) == {"region": "贵阳"}
        assert migr._parse_metadata_dict(json.dumps({"region": "贵阳"})) == {"region": "贵阳"}

    def test_broken_or_non_object_values_return_empty(self):
        assert migr._parse_metadata_dict('{"broken') == {}
        assert migr._parse_metadata_dict("   ") == {}
        assert migr._parse_metadata_dict(None) == {}
        assert migr._parse_metadata_dict('[1, 2]') == {}  # JSON 数组非对象
        assert migr._parse_metadata_dict(42) == {}


class TestChunk2OnlyLayout:
    def test_attraction_with_only_project_chunk_migrates_partial(self):
        # 异常历史布局：仅 chunk 2 存在 → 只迁 project_table，ticket_table 无源不造键
        plan = migr.plan_document("attraction_resource", {"region": "贵阳"}, {2: "项目表"})

        assert plan["action"] == "migrate"
        payload = plan["new_metadata"]["raw_payload"]
        assert payload.get("project_table") == "项目表"
        assert "ticket_table" not in payload


class TestRunMigrateFlow:
    def _patch_db(self, monkeypatch, docs, chunks, write_results=None):
        monkeypatch.setattr(migr, "scan_documents", lambda conn, tid=None: docs)
        monkeypatch.setattr(migr, "load_chunk_texts",
                            lambda conn, doc_id, st: chunks[doc_id])
        writes = []
        results = write_results if write_results is not None else [True] * len(docs)
        idx = [0]

        def _write(conn, doc_id, meta, expected_raw):
            writes.append((doc_id, meta, expected_raw))
            ok = results[idx[0]] if idx[0] < len(results) else True
            idx[0] += 1
            return ok

        monkeypatch.setattr(migr, "write_metadata", _write)
        return writes

    def test_dry_run_reports_but_never_writes(self, monkeypatch):
        docs = [
            {"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
             "metadata": json.dumps({"region": "贵阳"})},
        ]
        writes = self._patch_db(monkeypatch, docs, {1: {0: "摘要", 1: "P"}})

        stats = migr.run_migrate(None, None, execute=False, force=False)

        assert stats == {"scanned": 1, "migrated": 1, "ok": 0,
                         "conflict": 0, "oversized": 0, "concurrent": 0}
        assert writes == []  # dry-run 零写库

    def test_execute_writes_only_docs_needing_change(self, monkeypatch):
        docs = [
            # doc 1：待迁移；doc 2：已迁移（幂等）；doc 3：冲突（跳过）
            {"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
             "metadata": json.dumps({"region": "贵阳"})},
            {"id": 2, "source_type": "hotel_resource", "title": "酒店：B",
             "metadata": json.dumps({"region": "安顺", "raw_payload": {"price_table": "P", "region": "安顺"}})},
            {"id": 3, "source_type": "hotel_resource", "title": "酒店：C",
             "metadata": json.dumps({"raw_payload": {"price_table": "另一份表"}})},
        ]
        chunks = {1: {0: "摘要", 1: "P"}, 2: {0: "摘要", 1: "P"}, 3: {0: "摘要", 1: "P"}}
        writes = self._patch_db(monkeypatch, docs, chunks)

        stats = migr.run_migrate(None, None, execute=True, force=False)

        assert stats["migrated"] == 1
        assert stats["ok"] == 1
        assert stats["conflict"] == 1
        assert [w[0] for w in writes] == [1]
        assert writes[0][1]["raw_payload"]["price_table"] == "P"
        # 乐观锁：scan 读到的 metadata 原文透传给 write_metadata
        assert writes[0][2] == docs[0]["metadata"]

    def test_concurrent_skip_counted_and_survives(self, monkeypatch):
        # 乐观锁 0 行命中（执行期间 metadata 被外部更新）→ 并发跳过，不炸不丢计数
        docs = [{"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
                 "metadata": json.dumps({"region": "贵阳"})}]
        writes = self._patch_db(monkeypatch, docs, {1: {0: "摘要", 1: "P"}},
                                write_results=[False])

        stats = migr.run_migrate(None, None, execute=True, force=False)

        assert stats["concurrent"] == 1
        assert stats["migrated"] == 0
        assert len(writes) == 1  # 尝试过写但被乐观锁拒绝

    def test_migrated_doc_survives_rerun_zero_change(self, monkeypatch):
        # 端到端幂等：第一轮 execute 后，把写回的 metadata 作为第二轮输入 → 零写
        docs = [{"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
                 "metadata": json.dumps({"region": "贵阳"})}]
        chunks = {1: {0: "摘要", 1: "P"}}
        writes = self._patch_db(monkeypatch, docs, chunks)

        migr.run_migrate(None, None, execute=True, force=False)
        migrated_meta = writes[0][1]

        docs_round2 = [dict(docs[0], metadata=json.dumps(migrated_meta, ensure_ascii=False))]
        writes2 = self._patch_db(monkeypatch, docs_round2, chunks)
        stats2 = migr.run_migrate(None, None, execute=True, force=False)

        assert stats2["ok"] == 1
        assert stats2["migrated"] == 0
        assert writes2 == []


class TestRunDropChunksFlow:
    def _patch_db(self, monkeypatch, docs, chunks):
        monkeypatch.setattr(migr, "scan_documents", lambda conn, tid=None: docs)
        monkeypatch.setattr(migr, "load_chunk_texts",
                            lambda conn, doc_id, st: chunks[doc_id])
        drops = []
        monkeypatch.setattr(migr, "drop_chunks_for_doc",
                            lambda conn, doc_id, st, total: drops.append((doc_id, st, total)))
        return drops

    def test_execute_drops_only_complete_docs(self, monkeypatch):
        docs = [
            # doc 1：表键齐全且值一致 → 删；doc 2：值落后 → 拒绝
            {"id": 1, "source_type": "attraction_resource", "title": "景点：A",
             "metadata": json.dumps({"raw_payload": {"ticket_table": "T", "project_table": "P"}})},
            {"id": 2, "source_type": "attraction_resource", "title": "景点：B",
             "metadata": json.dumps({"raw_payload": {"ticket_table": "旧", "project_table": "P"}})},
        ]
        chunks = {1: {0: "摘要", 1: "T", 2: "P"}, 2: {0: "摘要", 1: "新", 2: "P"}}
        drops = self._patch_db(monkeypatch, docs, chunks)

        stats = migr.run_drop_chunks(None, None, execute=True)

        assert stats == {"scanned": 2, "dropped": 1, "skipped": 1, "no_chunks": 0}
        assert drops == [(1, "attraction_resource", 1)]  # doc 2 值落后被拒，未删

    def test_dry_run_never_drops(self, monkeypatch):
        docs = [{"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
                 "metadata": json.dumps({"raw_payload": {"price_table": "P"}})}]
        drops = self._patch_db(monkeypatch, docs, {1: {0: "摘要", 1: "P"}})

        stats = migr.run_drop_chunks(None, None, execute=False)

        assert stats["dropped"] == 1  # 计入「将删除」
        assert drops == []  # 但零删除


class TestMainVerifyExitCode:
    def test_verify_mismatch_returns_nonzero(self, monkeypatch):
        # 设计要求：--verify 差异非零退出码（迁移前全量不一致场景）
        docs = [{"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
                 "metadata": json.dumps({"region": "贵阳"})}]
        monkeypatch.setattr(migr, "scan_documents", lambda conn, tid=None: docs)
        monkeypatch.setattr(migr, "load_chunk_texts",
                            lambda conn, doc_id, st: {0: "摘要", 1: "P"})
        monkeypatch.setattr(migr, "write_report_file", lambda env: None)

        class _Conn:
            def rollback(self): pass
            def close(self): pass

        monkeypatch.setattr(migr, "_connect", lambda: _Conn())

        assert migr.main(["--verify"]) == 1

    def test_verify_all_ok_returns_zero(self, monkeypatch):
        docs = [{"id": 1, "source_type": "hotel_resource", "title": "酒店：A",
                 "metadata": json.dumps({"region": "贵阳", "raw_payload": {"price_table": "P", "region": "贵阳"}})}]
        monkeypatch.setattr(migr, "scan_documents", lambda conn, tid=None: docs)
        monkeypatch.setattr(migr, "load_chunk_texts",
                            lambda conn, doc_id, st: {0: "摘要", 1: "P"})
        monkeypatch.setattr(migr, "write_report_file", lambda env: None)

        class _Conn:
            def rollback(self): pass
            def close(self): pass

        monkeypatch.setattr(migr, "_connect", lambda: _Conn())

        assert migr.main(["--verify"]) == 0


class TestWriteMetadataSqlDirection:
    def test_optimistic_lock_uses_null_safe_equality(self):
        # 回归锚点：乐观锁曾把 IS NOT DISTINCT FROM（NULL 安全相等）误写成
        # IS DISTINCT FROM（已变才写），方向恰好放行要防的丢失更新（P0）。
        # 真实 SQL 行为由 tests/integration/test_migrate_travel_resource_tables_db.py
        # 功能覆盖；此断言保证单测层也能拦住方向翻转。
        import inspect
        src = inspect.getsource(migr.write_metadata)
        assert "IS NOT DISTINCT FROM" in src
        assert "IS DISTINCT FROM %s" not in src.replace("IS NOT DISTINCT FROM", "")
