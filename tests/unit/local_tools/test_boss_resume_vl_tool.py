"""boss_resume_detail / boss_resume_batch 云端 VL 评估链路单元测试（v2，全 mock 无真实 LLM/DB）

覆盖（开发计划 Phase 2/4，设计 §4.2/§6）：
- 预检覆写：两简历工具 _tool_credit_price() = 简历识别费单价（总开关关 → 0）
- detail 成功路径：切片→VL 评估→姓名门→回填 resume_summary/key_info/ocr_engine→入库→按份扣费
- 姓名门（决策⑨）：name_seen 与页面姓名不符 → RESUME_NAME_MISMATCH，不入库不扣费，全量日志含 name_seen
- VL 失败：RESUME_VL_FAILED，不入库不扣费
- ocr_text 信任撤销（设计 §6）：旧客户端带文本 → 丢弃 + warning，仍走 VL 评估入库，照常扣费
- 计费时机（v2）：姓名门通过即扣（入库失败不退）
- match_* 回写：score 有值回写（阈值取 evaluation.match_threshold，缺省 DEFAULT）；score=None 未评分
- batch 逐份串行：部分失败记 failures 继续、只对姓名门通过的份扣费、进度逐份推送；全部失败 RESUME_STORE_FAILED
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import BossToolBillingConfig, settings
from src.local_tools.proxy_tool import (
    BossResumeBatchTool,
    BossResumeDetailTool,
)
from src.services import resume_vl_service

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.proxy_tool.repository"
USAGE_DB = "src.local_tools.proxy_tool.ClientUsageLogDB"
TENANT_DB = "src.saas.db.tenant_db.TenantDB.get_by_id"
MATCH_SVC = "src.local_tools.proxy_tool.recruiting_match_service"
# 工具层经 `from src.services import resume_vl_service` 引用，patch 模块属性即可
VL_SLICE = "src.services.resume_vl_service.slice_stitched_image"
VL_EVALUATE = "src.services.resume_vl_service.evaluate_resume"


def _online_device():
    return {
        "id": "dev-1",
        "selected": True,
        "status": "active",
        "last_seen_at": datetime.now(),
        "capabilities_json": {"provider_id": "ai.aidwork.boss-recruiting"},
    }


def _kwargs(**extra):
    return {"_trusted_tenant_id": TENANT, "_trusted_user_id": USER,
            "_session_id": "sess-v1", **extra}


def _invocation(data=None, credit_cost=None, state="succeeded"):
    return {
        "id": "inv-1",
        "tenant_id": TENANT,
        "state": state,
        "effect": "applied" if state == "succeeded" else None,
        "result_json": {"message": "ok", **({"data": data} if data is not None else {})},
        "error_code": None,
        "error_message": None,
        "credit_cost": credit_cost,
    }


def _stateful_repo(rows_by_id):
    mocks = {
        "list_devices": MagicMock(return_value=[_online_device()]),
        "create_invocation": MagicMock(return_value="inv-1"),
        "list_events": MagicMock(return_value=[]),
        "get_invocation": MagicMock(side_effect=lambda inv_id, tenant: rows_by_id.get("inv-1")),
        "request_cancel": MagicMock(return_value=True),
        "set_invocation_credit_cost": MagicMock(return_value=None),
    }
    return patch.multiple(REPO, **mocks), mocks


def _record(payload_name="张三"):
    """create_resume_record_from_tool_result 的返回（紧凑摘要所需字段）"""
    return {
        "id": 101,
        "candidate_name": payload_name,
        "job_name": None,
        "job_id": None,
        "job_warning": None,
        "images": [{"name": "resume_full.png"}],
        "resume_summary": f"{payload_name}：全栈工程师，5 年经验",
    }


def _evaluation(name_seen="张三", score=82):
    """evaluate_resume 的合法返回（决策⑨：name_seen 为模型读出的简历姓名）"""
    return {
        "name_seen": name_seen,
        "resume_summary": f"{name_seen}：全栈工程师，5 年经验",
        "score": score,
        "match_summary": "技术栈匹配" if score is not None else None,
        "key_info": {"years_of_experience": 5, "education": "本科", "core_skills": ["Java"]},
        "model": "GLM-5.3-Flash",
        "usage": None,
        # _evaluate_resume_via_vl 会把阈值带出（无职位 None → match_* 不回写）
        # 这里给 60 便于断言 _update_match_fields 收到的 status 判定
    }


def _vl_payload(name="张三", with_text=False):
    """新客户端（无文本）/ 旧客户端（带 ocr_text）的单份 payload——v2 一律进 VL"""
    payload = {
        "candidate_name": name,
        "name_source": "param",
        "images": [{"name": "resume_full.png", "mime_type": "image/png", "base64": "AAAA"}],
    }
    if with_text:
        payload["ocr_text"] = f"{name} 男 26岁 本科"
        payload["ocr_chars"] = len(payload["ocr_text"])
    return payload


def _patch_env(rows_by_id, *, record_usage, store=None, slice_fn=None, evaluate=None,
               update_match=None, threshold=None):
    """统一 patch：设备闸门/invocation + 余额预检 + 台账 + 入库 + 职位上下文 + VL 服务 + match 回写"""
    repo_patch, _repo = _stateful_repo(rows_by_id)
    store_mock = store or MagicMock(side_effect=lambda *a, **k: _record((a[2] or {}).get("candidate_name", "张三")))
    update_match_mock = update_match or MagicMock(return_value=True)
    patchers = [
        repo_patch,
        patch(TENANT_DB, MagicMock(return_value=None)),  # 租户不存在 → 预检不阻断
        patch(USAGE_DB, record_tool_usage=record_usage),
        patch(STORE, store_mock),
        # 职位上下文：默认 None（无职位 → score 仍评估但阈值缺省）；match 回写打桩
        patch(f"{MATCH_SVC}._load_job_context", MagicMock(return_value=(
            {"job_name": "全栈工程师", "match_threshold": threshold} if threshold is not None else None))),
        patch(f"{MATCH_SVC}._update_match_fields", update_match_mock),
    ]
    if slice_fn is not None:
        patchers.append(patch(VL_SLICE, slice_fn))
    if evaluate is not None:
        patchers.append(patch(VL_EVALUATE, evaluate))
    return patchers, store_mock, update_match_mock


STORE = "src.local_tools.proxy_tool.recruiting_resume_service.create_resume_record_from_tool_result"


class TestPrecheckOverride:
    def test_resume_tools_price_from_recognition_price(self, monkeypatch):
        """两简历工具预检单价 = 简历识别费（非价目表按次费 0），NO_CREDIT 语义据此生效"""
        monkeypatch.setattr(settings, "boss_tool_billing", BossToolBillingConfig())
        assert BossResumeDetailTool()._tool_credit_price() == 1.0
        assert BossResumeBatchTool()._tool_credit_price() == 1.0
        # 价目表按次费已改 0（截图免费，write_result 不扣）——两处口径互不影响
        from src.local_tools.pricing import tool_credit_price
        assert tool_credit_price("boss_resume_detail") == 0.0
        assert tool_credit_price("boss_resume_batch") == 0.0

    def test_recognition_price_zero_when_switch_off(self, monkeypatch):
        """计费总开关关 → 识别费 0（预检放行 + 落账停扣，语义自洽）"""
        monkeypatch.setattr(settings, "boss_tool_billing", BossToolBillingConfig(enabled=False))
        assert BossResumeDetailTool()._tool_credit_price() == 0.0
        assert BossResumeBatchTool()._tool_credit_price() == 0.0


class TestBossResumeDetailVL:
    async def test_success_path_backfills_bills_and_updates_match(self, monkeypatch):
        """成功路径：VL 评估回填（resume_summary/key_info/ocr_engine=模型名）→ 扣费 → 入库 →
        match_* 回写（阈值带出，status 按阈值判定）；入库 payload 无 ocr_text"""
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation())
        rows = {"inv-1": _invocation(data=_vl_payload(), credit_cost=0.0)}
        patchers, store, update_match = _patch_env(
            rows, record_usage=record_usage,
            slice_fn=MagicMock(return_value=["band-1"]),
            evaluate=evaluate, threshold=60)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is True
        stored = store.call_args.args[2]
        assert stored["resume_summary"] == "张三：全栈工程师，5 年经验"
        assert stored["key_info"]["years_of_experience"] == 5
        assert stored["ocr_engine"] == "GLM-5.3-Flash"  # 决策④：回填实际模型名
        assert "ocr_text" not in stored and "ocr_accel" not in stored
        # 按份扣识别费（姓名门通过后、入库前）
        record_usage.assert_called_once()
        kwargs = record_usage.call_args.kwargs
        assert kwargs["tool_name"] == "boss_resume_recognition"
        assert kwargs["credit_cost"] == 1.0
        assert kwargs["tenant_id"] == TENANT
        assert kwargs["invocation_id"] == "inv-1"
        assert kwargs["session_id"] == "sess-v1"
        assert kwargs["user_id"] == USER
        # match_* 回写：score=82、阈值 60 → matched
        assert update_match.call_count == 1
        args = update_match.call_args.args
        assert args[1] == 101 and args[2] == 82 and args[4] == "matched"
        assert result["data"]["match_score"] == 82
        assert result["data"]["match_status"] == "matched"

    async def test_name_mismatch_no_store_no_bill_with_full_log(self, monkeypatch, caplog):
        """姓名门不过（决策⑨）→ RESUME_NAME_MISMATCH：不入库不扣费不打分；日志含 name_seen"""
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation(name_seen="李四"))
        rows = {"inv-1": _invocation(data=_vl_payload(name="张三"))}
        patchers, store, update_match = _patch_env(
            rows, record_usage=record_usage,
            slice_fn=MagicMock(return_value=["band-1"]),
            evaluate=evaluate, threshold=60)
        with patch("src.local_tools.proxy_tool.logger") as log_mock:
            with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
                result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "RESUME_NAME_MISMATCH"
        assert "张三" in result["message"] and "李四" in result["message"]
        store.assert_not_called()
        record_usage.assert_not_called()
        update_match.assert_not_called()
        # 全量日志：name_seen 必须在（排障区分识别误读 vs 真点错人）
        err_texts = " ".join(str(c) for c in log_mock.error.call_args_list)
        assert "name_seen" in err_texts and "李四" in err_texts and "张三" in err_texts

    async def test_vl_failure_no_store_no_bill(self, monkeypatch):
        """VL 失败（服务层已重试）→ RESUME_VL_FAILED：不入库不扣费"""
        record_usage = MagicMock()

        def _boom(*a, **k):
            raise resume_vl_service.ResumeVLError("简历 VL 评估失败（重试后仍失败）：api down")

        evaluate = AsyncMock(side_effect=_boom)
        rows = {"inv-1": _invocation(data=_vl_payload())}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "RESUME_VL_FAILED"
        assert "识别失败" in result["message"]
        store.assert_not_called()
        record_usage.assert_not_called()

    async def test_legacy_payload_ocr_text_dropped_and_still_vl(self, monkeypatch):
        """ocr_text 信任撤销（设计 §6）：旧客户端带文本 → 丢弃 + warning，仍走 VL 评估并扣费"""
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation())
        legacy = _vl_payload(with_text=True)
        # 别名旁路（CR 2026-09-17）：入库层按 ocr_text/ocr/text 别名组解析全文，
        # 换个别名（text/ocr）也必须被丢弃，否则可绕过姓名门把客户端文本写库
        legacy["text"] = "伪造全文"
        legacy["ocr"] = "伪造全文2"
        rows = {"inv-1": _invocation(data=legacy, credit_cost=0.0)}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate)
        with patch("src.local_tools.proxy_tool.logger") as log_mock:
            with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
                result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is True
        evaluate.assert_awaited_once()  # 旧客户端照样走 VL（文本不被信任）
        assert "ocr_text" in str(log_mock.warning.call_args_list)
        stored = store.call_args.args[2]
        # 文本及全部别名已丢弃，不入库
        for alias in ("ocr_text", "ocr", "text", "ocr_chars"):
            assert alias not in stored
        assert stored["resume_summary"].startswith("张三")
        record_usage.assert_called_once()  # 照常扣识别费

    async def test_missing_candidate_name_fails_before_vl(self, monkeypatch):
        """无姓名可核对 → 不进 VL（姓名门的比较基准缺失），RESUME_PAYLOAD_INVALID"""
        record_usage = MagicMock()
        evaluate = AsyncMock()
        payload = _vl_payload()
        payload.pop("candidate_name")
        rows = {"inv-1": _invocation(data=payload)}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "RESUME_PAYLOAD_INVALID"
        evaluate.assert_not_awaited()
        store.assert_not_called()

    async def test_store_failure_after_bill_no_refund(self, monkeypatch):
        """计费时机（v2）：姓名门通过即扣；入库失败不退（评分成功=服务交付）"""
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation())
        rows = {"inv-1": _invocation(data=_vl_payload())}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate,
                                        store=MagicMock(side_effect=RuntimeError("db down")))
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "RESUME_STORE_FAILED"
        record_usage.assert_called_once()  # 已扣，不退

    async def test_billing_failure_does_not_break_success(self, monkeypatch):
        """计费落账失败只告警：入库结果仍 success（台账可对账，不拖垮业务）"""
        record_usage = MagicMock(side_effect=RuntimeError("db down"))
        evaluate = AsyncMock(return_value=_evaluation())
        rows = {"inv-1": _invocation(data=_vl_payload())}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is True
        store.assert_called_once()
        record_usage.assert_called_once()

    async def test_zero_price_skips_billing(self, monkeypatch):
        """识别费配置 0 → 入库照常、不落账（停扣回退手段，设计 §7）"""
        monkeypatch.setattr(
            settings, "boss_tool_billing",
            BossToolBillingConfig(resume_recognition_price=0.0),
        )
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation())
        rows = {"inv-1": _invocation(data=_vl_payload())}
        patchers, _store, _ = _patch_env(rows, record_usage=record_usage,
                                         slice_fn=MagicMock(return_value=["band-1"]),
                                         evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is True
        record_usage.assert_not_called()

    async def test_no_job_score_none_marks_not_scored(self, monkeypatch):
        """无职位上下文 → score=null：match_* 不回写，摘要注明未评分"""
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation(score=None))
        rows = {"inv-1": _invocation(data=_vl_payload())}
        patchers, _store, update_match = _patch_env(rows, record_usage=record_usage,
                                                    slice_fn=MagicMock(return_value=["band-1"]),
                                                    evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeDetailTool().execute(**_kwargs())

        assert result["success"] is True
        update_match.assert_not_called()
        assert result["data"]["match_score"] is None
        assert result["data"]["match_note"] == "未评分"


class TestBossResumeBatchVL:
    async def test_partial_failure_continues_and_bills_only_passed(self, monkeypatch):
        """逐份串行（决策⑤）：份1 姓名门通过入库扣费，份2 姓名不符记 failures 继续，
        份3 缺姓名不进 VL 记 failures；进度逐份推送"""
        record_usage = MagicMock()
        # 份1 姓名=张三（命中）；份2 evaluate 返回他人姓名 → 姓名门不过
        evaluate = AsyncMock(side_effect=[
            _evaluation(name_seen="张三"),
            _evaluation(name_seen="王五"),
        ])
        payload_ok = _vl_payload(name="张三")
        payload_mismatch = _vl_payload(name="李四")
        payload_noname = _vl_payload(name="王五")
        payload_noname.pop("candidate_name")
        rows = {"inv-1": _invocation(data={
            "resumes": [payload_ok, payload_mismatch, payload_noname],
            "failures": [],
            "attempted": 3,
        })}
        queue = asyncio.Queue()
        patchers, store, update_match = _patch_env(rows, record_usage=record_usage,
                                                   slice_fn=MagicMock(return_value=["band-1"]),
                                                   evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeBatchTool().execute(**_kwargs(_progress_queue=queue))

        assert result["success"] is True
        summaries = result["data"]["resumes"]
        failures = result["data"]["failures"]
        assert [s["candidate_name"] for s in summaries] == ["张三"]
        assert len(failures) == 2
        assert failures[0]["name"] == "李四"
        assert "RESUME_NAME_MISMATCH" in failures[0]["error"] or "姓名核对不匹配" in failures[0]["error"]
        assert failures[1]["name"] is None  # 缺姓名 payload
        # 只对姓名门通过的 1 份扣费
        record_usage.assert_called_once()
        assert record_usage.call_args.kwargs["tool_name"] == "boss_resume_recognition"
        # 逐份进度推送（1 份成功）
        texts = []
        while not queue.empty():
            texts.append(queue.get_nowait()["text"])
        assert any("1/3" in t and "张三" in t for t in texts)
        # 失败份的 payload 未被误回填
        assert "resume_summary" not in payload_mismatch

    async def test_all_failed_returns_resume_store_failed(self, monkeypatch):
        """全部失败（VL 失败）→ 保留现有 RESUME_STORE_FAILED 语义（fail-loud）"""
        record_usage = MagicMock()
        evaluate = AsyncMock(
            side_effect=resume_vl_service.ResumeVLError("简历 VL 评估失败（重试后仍失败）：api down"))
        rows = {"inv-1": _invocation(data={
            "resumes": [_vl_payload(name="张三"), _vl_payload(name="李四")],
            "failures": [],
            "attempted": 2,
        })}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeBatchTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "RESUME_STORE_FAILED"
        store.assert_not_called()
        record_usage.assert_not_called()

    async def test_all_name_mismatch_keeps_store_failed(self, monkeypatch):
        """全部姓名不匹配 → 同样 RESUME_STORE_FAILED（不入库不扣费）"""
        record_usage = MagicMock()
        evaluate = AsyncMock(return_value=_evaluation(name_seen="王五"))
        rows = {"inv-1": _invocation(data={
            "resumes": [_vl_payload(name="张三")],
            "failures": [],
            "attempted": 1,
        })}
        patchers, store, _ = _patch_env(rows, record_usage=record_usage,
                                        slice_fn=MagicMock(return_value=["band-1"]),
                                        evaluate=evaluate)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7]:
            result = await BossResumeBatchTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "RESUME_STORE_FAILED"
        store.assert_not_called()
        record_usage.assert_not_called()
