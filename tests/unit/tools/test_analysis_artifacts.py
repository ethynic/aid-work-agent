"""
分析产物复用（artifact reuse）单元测试

覆盖：注册表 round trip / 同 var 覆盖 / 上限淘汰 / 会话与租户隔离 /
CSV 丢失条目过滤 / load_output 正常加载与安全拒绝 / user message 注入 /
注册失败容错不影响分析。
"""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from src.tools.data_analysis.analysis_agent import AnalysisAgent
from src.tools.data_analysis.analysis_artifacts import (
    INJECT_LIMIT,
    REGISTRY_LIMIT,
    artifact_csv_path,
    find_equivalent_artifacts,
    is_valid_var_name,
    load_artifacts,
    record_artifact,
)
from src.tools.data_analysis.data_analyzer import DataAnalyzer


# ============================================================
# Fixtures / Helpers
# ============================================================


@pytest.fixture(autouse=True)
def _isolated_tenants_root(tmp_path, monkeypatch):
    """把 storage._TENANTS_ROOT 重定向到 tmp_path，产物/注册表不污染仓库 storage/"""
    from src.core import storage as storage_mod
    monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", str(tmp_path / "tenants"))
    return tmp_path


TENANT = "tenant_t1"
SESSION = "session_s1"


def _make_agent(tenant_id=TENANT, session_id=SESSION):
    analyzer = DataAnalyzer(session_id=session_id, tenant_id=tenant_id)
    gateway = MagicMock()
    gateway.get_model_name = MagicMock(return_value="test-model")
    gateway.get_provider_name = MagicMock(return_value="test-provider")
    return AnalysisAgent(
        llm_gateway=gateway,
        analyzer=analyzer,
        analysis_id="analysis_test",
        tables_metadata=[],
        tenant_id=tenant_id,
        session_id=session_id,
    )


def _record(var, rows=10, session=SESSION, tenant=TENANT, description="描述", columns=None):
    # load_artifacts 会过滤 CSV 已丢失的条目（生产语义），注册前先落 CSV
    csv_path = artifact_csv_path(tenant, var)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    pd.DataFrame({"placeholder": [0]}).to_csv(csv_path, index=False)
    return record_artifact(
        tenant, session, var,
        description=description, method="merge", rows=rows,
        columns=columns or ["市场", "月份", "销售额"],
    )


# ============================================================
# Tests: 变量名校验
# ============================================================


class TestVarNameValidation:
    def test_valid_names(self):
        assert is_valid_var_name("mg_test")
        assert is_valid_var_name("c1")
        assert is_valid_var_name("合并宽表1")
        assert is_valid_var_name("a-b")

    def test_invalid_names_reject_path_components(self):
        assert not is_valid_var_name("")  # 空
        assert not is_valid_var_name("../etc/passwd")  # 穿越尝试
        assert not is_valid_var_name("a/b")
        assert not is_valid_var_name("a.csv")  # 含路径分隔点
        assert not is_valid_var_name("a" * 65)  # 超长
        # re 的 $ 锚允许尾随换行，必须整串匹配拒绝
        assert not is_valid_var_name("a\n")
        assert not is_valid_var_name("a\nb")


# ============================================================
# Tests: 注册表读写
# ============================================================


class TestRegistry:
    def test_record_and_load_roundtrip(self):
        assert _record("mg_merged", rows=18, description="6市场销售合并宽表")
        entries = load_artifacts(TENANT, SESSION)
        assert len(entries) == 1
        e = entries[0]
        assert e["var"] == "mg_merged"
        assert e["rows"] == 18
        assert e["columns"] == ["市场", "月份", "销售额"]
        assert e["description"] == "6市场销售合并宽表"

    def test_load_returns_recent_first(self):
        _record("v1")
        _record("v2")
        _record("v3")
        entries = load_artifacts(TENANT, SESSION)
        assert [e["var"] for e in entries] == ["v3", "v2", "v1"]

    def test_same_var_overwrites_not_duplicates(self):
        _record("v1", rows=5, description="旧")
        _record("v1", rows=18, description="新")
        entries = load_artifacts(TENANT, SESSION)
        assert len(entries) == 1
        assert entries[0]["rows"] == 18
        assert entries[0]["description"] == "新"

    def test_limit_caps_entries(self):
        """limit 参数截断注入条目数（INJECT_LIMIT 已提升为 50 与注册表上限对齐，
        截断机制本身用显式 limit 验证，避免测试随常量调整而失效）"""
        for i in range(15):
            _record(f"v{i}")
        entries = load_artifacts(TENANT, SESSION, limit=10)
        assert len(entries) == 10
        # 最近注册的在前
        assert entries[0]["var"] == "v14"

    def test_registry_limit_fifo_eviction(self):
        for i in range(REGISTRY_LIMIT + 5):
            _record(f"v{i}")
        entries = load_artifacts(TENANT, SESSION, limit=0)
        assert len(entries) == REGISTRY_LIMIT
        vars_kept = {e["var"] for e in entries}
        assert "v0" not in vars_kept  # 最早的被淘汰
        assert f"v{REGISTRY_LIMIT + 4}" in vars_kept

    def test_session_isolation(self):
        _record("v1", session="session_A")
        assert load_artifacts(TENANT, "session_B") == []
        assert len(load_artifacts(TENANT, "session_A")) == 1

    def test_tenant_isolation(self):
        _record("v1", tenant="tenant_t1")
        assert load_artifacts("tenant_t2", SESSION) == []

    def test_empty_tenant_or_session_noop(self):
        assert not record_artifact("", SESSION, "v1")
        assert not record_artifact(TENANT, "", "v1")
        assert load_artifacts("", SESSION) == []
        assert load_artifacts(TENANT, "") == []

    def test_session_id_traversal_rejected(self):
        """session_id 来自 LLM 可控参数：穿越构造必须被拒绝（跨租户读写防线）"""
        attacks = [
            "x/../../leaked",                      # 写出租户 temp 目录
            "x/../../../t2/temp/analysis_registry_real",  # 读他租户注册表
            "../..",
            "a.json",
            "a/b",
            "s\n1",
        ]
        for bad in attacks:
            assert not record_artifact(TENANT, bad, "v1"), f"写入未拒绝: {bad!r}"
            assert load_artifacts(TENANT, bad) == [], f"读取未拒绝: {bad!r}"
        # 越界目录不得被创建（record_artifact 内 makedirs 不应执行到越界路径）
        leaked = os.path.join(os.path.dirname(artifact_csv_path(TENANT, "x")), "..", "leaked.json")
        assert not os.path.exists(os.path.realpath(leaked))

    def test_missing_csv_entries_filtered_out(self):
        _record("v1")
        # 注册后 CSV 被清理：条目不注入
        os.remove(artifact_csv_path(TENANT, "v1"))
        assert load_artifacts(TENANT, SESSION) == []

    def test_corrupt_registry_returns_empty(self):
        path = artifact_csv_path(TENANT, "v0")  # 仅用于定位目录
        reg = os.path.join(os.path.dirname(path), f"analysis_registry_{SESSION}.json")
        os.makedirs(os.path.dirname(reg), exist_ok=True)
        with open(reg, "w") as f:
            f.write("{not json")
        assert load_artifacts(TENANT, SESSION) == []


# ============================================================
# Tests: _handle_load_output
# ============================================================


class TestLoadOutput:
    def _write_csv(self, var, df):
        path = artifact_csv_path(TENANT, var)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False)
        return path

    def test_load_output_success(self):
        df = pd.DataFrame({"市场": ["德国", "美国"], "销售额": [1.0, 2.0]})
        _record("mg_merged", rows=2)
        self._write_csv("mg_merged", df)  # 覆盖占位 CSV 为真实数据

        agent = _make_agent()
        result = agent._handle_load_output("mg_merged")
        assert result["success"] is True
        assert result["rows"] == 2
        assert result["columns"] == ["市场", "销售额"]
        # 已装入分析环境：可直接作为 source 引用
        resolved = agent.analyzer._resolve_source("mg_merged")
        assert resolved is not None and len(resolved) == 2

    def test_load_output_rejects_path_traversal(self):
        agent = _make_agent()
        result = agent._handle_load_output("../etc/passwd")
        assert result["success"] is False

    def test_load_output_rejects_invalid_var(self):
        agent = _make_agent()
        for bad in ("", "a.csv", "a/b"):
            assert agent._handle_load_output(bad)["success"] is False

    def test_load_output_missing_lists_available(self):
        self._write_csv("v_exists", pd.DataFrame({"a": [1]}))
        _record("v_exists")
        agent = _make_agent()
        result = agent._handle_load_output("v_missing")
        assert result["success"] is False
        assert "v_exists" in result["error"]

    def test_load_output_without_tenant_context(self):
        agent = _make_agent(tenant_id=None)
        result = agent._handle_load_output("v1")
        assert result["success"] is False
        assert "租户" in result["error"]

    @pytest.mark.asyncio
    async def test_load_output_dispatched_via_execute_tool(self):
        """load_output 经 _execute_tool 白名单分发可达（run() 全链路入口）"""
        df = pd.DataFrame({"市场": ["德国"], "销售额": [1.0]})
        _record("mg_dispatch")
        path = artifact_csv_path(TENANT, "mg_dispatch")
        df.to_csv(path, index=False)
        agent = _make_agent()
        result = await agent._execute_tool("load_output", {"output_var": "mg_dispatch"}, 1)
        assert result["success"] is True
        assert "mg_dispatch" in agent.analyzer._variables


# ============================================================
# Tests: 注册钩子与 user message 注入
# ============================================================


class TestAgentIntegration:
    def test_data_method_registers_artifact(self):
        agent = _make_agent()
        df = pd.DataFrame({"市场": ["德国"], "销售额": [1.0]})
        result = agent._handle_data_method(
            "merge", "mg_test", df, {"left": "t1", "right": "t2"}
        )
        assert result["success"] is True
        # 注册表有记录 + agent 跟踪了新产物
        assert load_artifacts(TENANT, SESSION)[0]["var"] == "mg_test"
        assert agent._new_artifacts[0]["output_var"] == "mg_test"

    def test_register_failure_does_not_break_analysis(self):
        agent = _make_agent()
        df = pd.DataFrame({"市场": ["德国"], "销售额": [1.0]})
        with patch(
            "src.tools.data_analysis.analysis_artifacts.record_artifact",
            side_effect=OSError("disk full"),
        ):
            result = agent._handle_data_method("merge", "mg_x", df, {})
        assert result["success"] is True  # 分析不受注册失败影响
        assert agent._new_artifacts == []

    def test_build_user_message_includes_artifacts_section(self):
        _record("mg_merged", rows=18, description="6市场销售合并宽表")
        agent = _make_agent()
        msg = agent._build_user_message("分析各市场增长")
        assert "本会话已有分析产物" in msg
        assert "mg_merged" in msg
        assert "load_output" in msg

    def test_build_user_message_without_artifacts(self):
        agent = _make_agent()
        msg = agent._build_user_message("分析各市场增长")
        assert "本会话已有分析产物" not in msg

    def test_build_result_carries_reusable_outputs(self):
        agent = _make_agent()
        agent._new_artifacts = [{"output_var": "mg", "description": "d", "rows": 3}]
        result = agent._build_result(success=True, summary="done", iterations=1, start_time=1.0)
        assert result["reusable_outputs"] == [{"output_var": "mg", "description": "d", "rows": 3}]

    def test_build_result_omits_reusable_outputs_when_empty(self):
        agent = _make_agent()
        result = agent._build_result(success=True, summary="done", iterations=1, start_time=1.0)
        assert "reusable_outputs" not in result

    def test_session_id_falls_back_to_analyzer(self):
        """未显式传 session_id 时回退 analyzer.session_id（smart 工具链既有入参）"""
        from unittest.mock import MagicMock as MM
        analyzer = DataAnalyzer(session_id=SESSION, tenant_id=TENANT)
        agent = AnalysisAgent(
            llm_gateway=MM(), analyzer=analyzer, analysis_id="t",
            tenant_id=TENANT,
        )
        assert agent.session_id == SESSION

    def test_registry_entry_with_invalid_var_not_injected(self):
        """注册表文件被手工塞入非法 var 条目：读取侧过滤，不进 prompt"""
        csv_path = artifact_csv_path(TENANT, "ok_var")
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        pd.DataFrame({"a": [1]}).to_csv(csv_path, index=False)
        reg = os.path.join(os.path.dirname(csv_path), f"analysis_registry_{SESSION}.json")
        with open(reg, "w", encoding="utf-8") as f:
            json.dump([
                {"var": "../evil", "rows": 1, "columns": [], "description": ""},
                {"var": "ok_var", "rows": 1, "columns": ["a"], "description": "ok"},
            ], f, ensure_ascii=False)
        entries = load_artifacts(TENANT, SESSION)
        assert [e["var"] for e in entries] == ["ok_var"]


# ============================================================
# Tests: 改进3 — lineage 记录与等价产物判定（2026-10）
# ============================================================


def _record_with_upstream(var, upstreams, rows=10, session=SESSION, tenant=TENANT, description="加工产物"):
    """注册带 lineage 上游的产物（先落 CSV，满足读取侧的文件存在过滤）"""
    csv_path = artifact_csv_path(tenant, var)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    pd.DataFrame({"placeholder": [0]}).to_csv(csv_path, index=False)
    return record_artifact(
        tenant, session, var,
        description=description, method="merge", rows=rows,
        columns=["市场", "销售额"], upstreams=upstreams,
    )


class TestLineageRecording:
    def test_upstreams_roundtrip(self):
        """业务意图：产物必须记住自己由哪些原始表/上游产物加工而来，否则拦截无从判定"""
        assert _record_with_upstream("wide_a", ["9078", "9079"])
        entries = {e["var"]: e for e in load_artifacts(TENANT, SESSION)}
        assert entries["wide_a"]["upstreams"] == ["9078", "9079"]

    def test_upstreams_overwrite_on_same_var(self):
        """同 var 覆盖时 lineage 随之更新（旧上游不能残留误导拦截判定）"""
        _record_with_upstream("wide_a", ["9078"])
        _record_with_upstream("wide_a", ["9099"])
        entries = {e["var"]: e for e in load_artifacts(TENANT, SESSION)}
        assert entries["wide_a"]["upstreams"] == ["9099"]

    def test_upstreams_truncated_to_limit(self):
        from src.tools.data_analysis.analysis_artifacts import UPSTREAM_LIMIT
        _record_with_upstream("wide_a", [f"t{i}" for i in range(UPSTREAM_LIMIT + 5)])
        entries = {e["var"]: e for e in load_artifacts(TENANT, SESSION)}
        assert len(entries["wide_a"]["upstreams"]) == UPSTREAM_LIMIT

    def test_extract_source_refs_per_method(self):
        """参数提取与 DataAnalyzer._resolve_source 的三种引用方式一一对应"""
        assert AnalysisAgent._extract_source_refs(
            "merge", {"left_ref": "t1", "right_ref": "v2"}
        ) == ["t1", "v2"]
        assert AnalysisAgent._extract_source_refs(
            "append", {"sources": ["t1", "v2", "t3"]}
        ) == ["t1", "v2", "t3"]
        assert AnalysisAgent._extract_source_refs("query", {"source": "t1"}) == ["t1"]
        assert AnalysisAgent._extract_source_refs("query", {}) == []

    def test_data_method_registers_upstreams(self):
        """_handle_data_method 注册产物时必须带上本步的 source 引用"""
        csv_path = artifact_csv_path(TENANT, "wide_x")
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        pd.DataFrame({"placeholder": [0]}).to_csv(csv_path, index=False)
        agent = _make_agent()
        agent._register_artifact(
            "wide_x", "aggregate", "按市场汇总", 5, ["市场", "销售额"],
            upstreams=["9078"],
        )
        entries = {e["var"]: e for e in load_artifacts(TENANT, SESSION)}
        assert entries["wide_x"]["upstreams"] == ["9078"]


class TestFindEquivalentArtifacts:
    def test_direct_hit(self):
        """业务意图：load_table 拦截的直接依据——产物直接引用该原始表"""
        _record_with_upstream("wide_a", ["9078"])
        found = find_equivalent_artifacts(TENANT, SESSION, "9078")
        assert [e["var"] for e in found] == ["wide_a"]

    def test_transitive_hit_via_upstream_artifact(self):
        """B 由 A 加工、A 来自表 X：B 也是 X 的等价产物（沿 lineage 传递）"""
        _record_with_upstream("step1", ["9078"])
        _record_with_upstream("step2", ["step1"])
        found = {e["var"] for e in find_equivalent_artifacts(TENANT, SESSION, "9078")}
        assert found == {"step1", "step2"}

    def test_no_hit_for_unrelated_table(self):
        _record_with_upstream("wide_a", ["9078"])
        assert find_equivalent_artifacts(TENANT, SESSION, "9999") == []

    def test_cycle_safe(self):
        """注册表出现环形 lineage（异常数据）时不死循环、不误报"""
        _record_with_upstream("var_a", ["var_b"])
        _record_with_upstream("var_b", ["var_a"])
        assert find_equivalent_artifacts(TENANT, SESSION, "9999") == []

    def test_empty_registry_or_missing_table_id(self):
        assert find_equivalent_artifacts(TENANT, SESSION, "9078") == []
        _record_with_upstream("wide_a", ["9078"])
        assert find_equivalent_artifacts(TENANT, SESSION, "") == []


class TestLoadTableIntercept:
    @pytest.mark.asyncio
    async def test_first_load_intercepted_guides_load_output(self):
        """业务意图：该表已有等价产物时，首次 load_table 不执行加载，改为引导复用——
        阻断 392 积分事故的「重新加载-清洗-合并」链路"""
        agent = _make_agent()
        _record_with_upstream("merged_wide", ["9078"])
        result = await agent._handle_load_table("9078")
        assert result["success"] is False
        assert result["intercepted"] is True
        assert "load_output" in result["error"]
        assert result["equivalent_outputs"][0]["output_var"] == "merged_wide"
        assert "9078" not in agent._loaded_table_ids  # 未真正加载
        assert "9078" in agent._lineage_intercepted

    @pytest.mark.asyncio
    async def test_second_call_passes_through(self):
        """业务意图：软拦截不死拦——模型第二次坚持视为确需原始明细，放行加载"""
        agent = _make_agent()
        _record_with_upstream("merged_wide", ["9078"])
        await agent._handle_load_table("9078")  # 第一次：拦截

        mock_meta = {
            "table_id": "9078", "table_name": "订单表", "description": "",
            "columns": [{"name": "a", "data_type": "text", "description": ""}],
            "source": {"type": "excel", "file_path": "x.xlsx"},
        }
        agent.analyzer._tables = {"9078": pd.DataFrame({"a": [1]})}
        with patch.object(agent, "_fetch_single_table_metadata", return_value=mock_meta), \
             patch.object(agent.analyzer, "load_table", new=AsyncMock(return_value="9078")):
            result = await agent._handle_load_table("9078")  # 第二次：放行
        assert result["success"] is True
        assert "9078" in agent._loaded_table_ids

    @pytest.mark.asyncio
    async def test_table_without_equivalents_loads_normally(self):
        """无产物的新表不受拦截影响（拦截只作用于「确有等价产物」的表）"""
        agent = _make_agent()
        _record_with_upstream("merged_wide", ["9078"])
        mock_meta = {
            "table_id": "9999", "table_name": "新表", "description": "",
            "columns": [{"name": "a", "data_type": "text", "description": ""}],
            "source": {"type": "excel", "file_path": "x.xlsx"},
        }
        agent.analyzer._tables = {"9999": pd.DataFrame({"a": [1]})}
        with patch.object(agent, "_fetch_single_table_metadata", return_value=mock_meta), \
             patch.object(agent.analyzer, "load_table", new=AsyncMock(return_value="9999")):
            result = await agent._handle_load_table("9999")
        assert result["success"] is True
        assert "intercepted" not in result


class TestExportModeUserMessage:
    def test_export_mode_message_guides_reuse(self):
        """业务意图：导出模式下无预加载表，开局消息必须明示「有意不预加载、复用优先」，
        防止内层模型回落到「search -> load 原始表重加工」的旧路径"""
        agent = _make_agent()
        agent.export_only_mode = True
        msg = agent._build_user_message("把已有汇总表导出为 Excel")
        assert "有意未预加载" in msg
        assert "本会话已有分析产物" in msg

    def test_normal_mode_without_tables_keeps_search_guidance(self):
        """非导出模式且无预加载：维持既有「先 search 再 load」引导，行为不回归"""
        agent = _make_agent()
        msg = agent._build_user_message("分析各市场增长")
        assert "有意未预加载" not in msg
        assert "search_data_tables" in msg
