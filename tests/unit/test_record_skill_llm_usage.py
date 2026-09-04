"""
record_skill_llm_usage（skill 子进程 LLM 计量，Excel ETL Phase 2 接线）单测

验证（monkeypatch ChatRecordDB.create 捕获参数，不落真库）：
- 显式参数版：source_type="skill_llm"、session_id/user_message(stage 拼接)/
  token 字段/credit_cost 正确落库
- 环境变量回退：AID_TENANT_ID / AID_SESSION_ID / AID_USER_ID 缺省参数时生效
- usage 空/None 直接 return（不落库）
- ChatRecordDB.create 抛异常不向上抛（计量失败不影响技能主流程）
"""

from unittest.mock import patch

import pytest

from src.services.session_record import record_skill_llm_usage

pytestmark = [pytest.mark.db]


@pytest.fixture
def captured_create(monkeypatch):
    """mock ChatRecordDB.create，记录每次落库参数"""
    calls = []

    def _fake_create(**kwargs):
        calls.append(kwargs)
        return {"record_id": "r_test"}

    monkeypatch.setattr(
        "src.db.models.ChatRecordDB.create", staticmethod(_fake_create)
    )
    return calls


@pytest.fixture(autouse=True)
def _patch_billing(monkeypatch):
    """计费计算打桩，返回固定 (credit, breakdown)"""
    monkeypatch.setattr(
        "src.services.session_record.calculate_credit_cost_with_breakdown",
        lambda **kwargs: (0.05, {"non_cached_input_tokens": 10, "unit_prices": {}, "credits": {}}),
    )


@pytest.fixture(autouse=True)
def _clean_aid_env(monkeypatch):
    for name in ("AID_TENANT_ID", "AID_SESSION_ID", "AID_USER_ID"):
        monkeypatch.delenv(name, raising=False)


USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class TestRecordSkillLlmUsage:
    def test_explicit_params_persisted(self, captured_create):
        record_skill_llm_usage(
            USAGE,
            tenant_id="tenant-1",
            session_id="sess-1",
            user_id="user-1",
            stage="extract",
            model="deepseek-v4-flash",
        )
        assert len(captured_create) == 1
        kwargs = captured_create[0]
        assert kwargs["source_type"] == "skill_llm"
        assert kwargs["session_id"] == "sess-1"
        assert kwargs["tenant_id"] == "tenant-1"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["user_message"] == "[stage=extract] Excel ETL LLM 调用"
        assert kwargs["model"] == "deepseek-v4-flash"
        assert kwargs["prompt_tokens"] == 100
        assert kwargs["completion_tokens"] == 50
        assert kwargs["total_token_count"] == 150
        assert kwargs["credit_cost"] == 0.05
        assert kwargs["usage_breakdown"]["chat"]["total_tokens"] == 150

    def test_stage_none_user_message(self, captured_create):
        record_skill_llm_usage(USAGE, tenant_id="t", user_id="u")
        assert captured_create[0]["user_message"] == "Skill 子进程 LLM 调用"

    def test_env_fallback(self, captured_create, monkeypatch):
        monkeypatch.setenv("AID_TENANT_ID", "env-tenant")
        monkeypatch.setenv("AID_SESSION_ID", "env-sess")
        monkeypatch.setenv("AID_USER_ID", "env-user")
        record_skill_llm_usage(USAGE, model="qwen-plus")
        kwargs = captured_create[0]
        assert kwargs["tenant_id"] == "env-tenant"
        assert kwargs["session_id"] == "env-sess"
        assert kwargs["user_id"] == "env-user"

    def test_session_id_default_composed_from_source_and_user(self, captured_create):
        record_skill_llm_usage(USAGE, tenant_id="t", user_id="u1", stage="repair")
        kwargs = captured_create[0]
        assert kwargs["session_id"] == "skill_llm_skill_llm_u1"
        assert kwargs["user_message"] == "[stage=repair] Excel ETL LLM 调用"

    def test_no_env_no_user_falls_back_unknown(self, captured_create):
        record_skill_llm_usage(USAGE)
        kwargs = captured_create[0]
        assert kwargs["user_id"] == "unknown"
        assert kwargs["session_id"] == "skill_llm_skill_llm_unknown"

    def test_empty_usage_returns_early(self, captured_create):
        record_skill_llm_usage(None)
        record_skill_llm_usage({})
        assert captured_create == []

    def test_create_exception_swallowed(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr("src.db.models.ChatRecordDB.create", staticmethod(_boom))
        # 不抛异常
        record_skill_llm_usage(USAGE, tenant_id="t", session_id="s", user_id="u")

    def test_model_fallback_from_usage_then_default(self, captured_create):
        """model 回退链：显式参数 > usage["model"]（_default_llm return_usage 附带）> deepseek-v4-flash。

        主 LLM provider 默认 zhipu/qwen——不回退 usage 附带模型会把 glm/qwen 调用
        按 deepseek-v4-flash 单价错算积分。
        """
        # usage 附带 model，未显式传 → 用 usage 的
        record_skill_llm_usage({**USAGE, "model": "qwen-plus"})
        assert captured_create[0]["model"] == "qwen-plus"
        # 显式参数优先于 usage 附带
        record_skill_llm_usage({**USAGE, "model": "qwen-plus"}, model="GLM-5.3-Flash")
        assert captured_create[1]["model"] == "GLM-5.3-Flash"
        # 都没有 → deepseek-v4-flash 兜底
        record_skill_llm_usage(USAGE)
        assert captured_create[2]["model"] == "deepseek-v4-flash"
