"""简历评估接口单测（POST /api/local-tools/runtime/resume/evaluate，v2，无真实 LLM/DB）

设备鉴权依赖打桩（_require_device 覆写），识别/切片/计费全部 mock：
- 成功路径：切片入参、扣费参数、响应 name_seen/resume_summary/score/billing 齐全
- 入参契约：candidate_name 必填（422）；image 与 images 二选一（都缺 422）；
  非法模型 422（白名单）；VL 失败 502 不扣费；姓名门不过 422 不扣费；价格 0 不落账
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.local_tools import api as local_tools_api
from src.services import resume_vl_service

pytestmark = pytest.mark.unit

FAKE_DEVICE = {"tenant_id": "tenant-1", "id": 7, "user_id": "user-1"}


@pytest.fixture()
def client(monkeypatch):
    """构建只挂 local_tools 路由的 app：设备鉴权覆写为固定设备，计费取价默认 1.0"""
    monkeypatch.setattr(local_tools_api, "resume_recognition_price", lambda: 1.0)
    app = FastAPI()
    app.include_router(local_tools_api.router)
    app.dependency_overrides[local_tools_api._require_device] = lambda: dict(FAKE_DEVICE)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def billing_spy(monkeypatch):
    """记录 record_tool_usage 调用参数；可设 raises 模拟落账失败"""
    calls = []
    calls_raises = {"raise": False}

    def _record(**kwargs):
        calls.append(kwargs)
        if calls_raises["raise"]:
            raise RuntimeError("db down")
        return {"credit_cost": kwargs["credit_cost"], "balance_after": 99.0}

    monkeypatch.setattr(
        local_tools_api.ClientUsageLogDB, "record_tool_usage", staticmethod(_record)
    )
    return SimpleNamespace(calls=calls, raises=calls_raises)


def _patch_eval(monkeypatch, *, bands=None, evaluation=None, error=None):
    """mock 切片与评估：slice 返回 bands（记录入参），evaluate 返回 evaluation 或抛 error"""
    sliced = []

    def _slice(b64):
        sliced.append(b64)
        return bands or ["BAND-1", "BAND-2"]

    if evaluation is None:
        evaluation = {
            "name_seen": "王宣广", "resume_summary": "全栈工程师 5 年",
            "score": 82, "match_summary": "匹配", "key_info": {"education": "本科"},
            "model": "GLM-5.3-Flash", "usage": None,
        }

    async def _evaluate(band_images, cand_name, job_ctx=None, model_param=None):
        if error is not None:
            raise error
        return evaluation

    monkeypatch.setattr(resume_vl_service, "slice_stitched_image", _slice)
    monkeypatch.setattr(resume_vl_service, "evaluate_resume", _evaluate)
    monkeypatch.setattr(resume_vl_service, "resume_name_matches", lambda name, seen: True)
    return sliced


class TestResumeEvaluateSuccess:
    def test_success_with_stitched_image(self, client, billing_spy, monkeypatch):
        """image 拼接长图 → 服务端切片 → VL 评估 → 姓名门 → 扣费，响应字段齐全"""
        sliced = _patch_eval(monkeypatch, bands=["B0", "B1"])
        resp = client.post(
            "/api/local-tools/runtime/resume/evaluate",
            json={"image": "STITCHED", "candidate_name": "王宣广"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["name_seen"] == "王宣广"
        assert body["resume_summary"] == "全栈工程师 5 年"
        assert body["score"] == 82
        assert body["key_info"] == {"education": "本科"}
        assert body["bands"] == 2
        assert body["model"] == "GLM-5.3-Flash"
        assert sliced == ["STITCHED"]  # 切片收到的是拼接长图本体
        assert body["billing"] == {"credit_cost": 1.0, "balance_after": 99.0}
        assert len(billing_spy.calls) == 1
        call = billing_spy.calls[0]
        assert call["tenant_id"] == "tenant-1"
        assert call["tool_name"] == "boss_resume_recognition"
        assert call["credit_cost"] == 1.0
        assert call["user_id"] == "user-1"
        assert call["device_id"] == "7"  # 无 invocation 场景，device_id 对账

    def test_job_context_loaded_when_given(self, client, billing_spy, monkeypatch):
        """带 job_name → 加载职位上下文传给评估（给了才评分）"""
        seen = {}
        sliced = _patch_eval(monkeypatch, bands=["B0"])

        def _fake_load(tenant_id, job_id, job_name):
            seen["args"] = (tenant_id, job_id, job_name)
            return {"job_name": job_name, "match_threshold": 60}

        monkeypatch.setattr(local_tools_api.recruiting_match_service, "_load_job_context", _fake_load)
        resp = client.post(
            "/api/local-tools/runtime/resume/evaluate",
            json={"images": ["P1"], "candidate_name": "王宣广", "job_name": "全栈工程师"},
        )
        assert resp.status_code == 200
        assert seen["args"] == ("tenant-1", None, "全栈工程师")

    def test_zero_price_skips_billing(self, client, billing_spy, monkeypatch):
        """识别费 0（总开关关/配置 0）→ 不落账，评估照常返回"""
        monkeypatch.setattr(local_tools_api, "resume_recognition_price", lambda: 0.0)
        _patch_eval(monkeypatch)
        resp = client.post("/api/local-tools/runtime/resume/evaluate",
                           json={"images": ["P1"], "candidate_name": "王宣广"})
        assert resp.status_code == 200
        assert resp.json()["billing"]["credit_cost"] == 0.0
        assert billing_spy.calls == []

    def test_billing_failure_does_not_break_response(self, client, billing_spy, monkeypatch):
        """计费落账异常 → 只告警不影响评估结果（台账可对账）"""
        billing_spy.raises["raise"] = True
        _patch_eval(monkeypatch)
        resp = client.post("/api/local-tools/runtime/resume/evaluate",
                           json={"images": ["P1"], "candidate_name": "王宣广"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestResumeEvaluateFailures:
    def test_missing_candidate_name_422(self, client, billing_spy, monkeypatch):
        """candidate_name 必填（决策⑨姓名门的比较基准）"""
        _patch_eval(monkeypatch)
        resp = client.post("/api/local-tools/runtime/resume/evaluate", json={"images": ["P1"]})
        assert resp.status_code == 422
        assert "candidate_name" in resp.json()["detail"]["error"]

    def test_missing_image_and_images_422(self, client, billing_spy, monkeypatch):
        _patch_eval(monkeypatch)
        resp = client.post("/api/local-tools/runtime/resume/evaluate",
                           json={"candidate_name": "王宣广"})
        assert resp.status_code == 422
        assert "缺少识别入参" in resp.json()["detail"]["error"]

    def test_invalid_model_422(self, client, billing_spy, monkeypatch):
        """白名单外模型 → 422（识别费按份固定积分，不放行任意模型）"""
        _patch_eval(monkeypatch)
        resp = client.post(
            "/api/local-tools/runtime/resume/evaluate",
            json={"images": ["P1"], "candidate_name": "王宣广", "model": "openai/gpt-9"},
        )
        assert resp.status_code == 422
        assert "不在允许列表" in resp.json()["detail"]["error"]

    def test_vl_failure_502_and_no_billing(self, client, billing_spy, monkeypatch):
        from src.services.resume_vl_service import ResumeVLError

        _patch_eval(monkeypatch, error=ResumeVLError("重试后仍失败：api down"))
        resp = client.post("/api/local-tools/runtime/resume/evaluate",
                           json={"images": ["P1"], "candidate_name": "王宣广"})
        assert resp.status_code == 502
        assert "不扣费" in resp.json()["detail"]["error"]
        assert billing_spy.calls == []

    def test_name_mismatch_422_and_no_billing(self, client, billing_spy, monkeypatch):
        """姓名门不过（决策⑨）→ 422 + 全量日志字段（name_seen），不扣费"""
        _patch_eval(monkeypatch, evaluation={
            "name_seen": "李四", "resume_summary": "别人的总结", "score": 90,
            "match_summary": None, "key_info": None, "model": "GLM-5.3-Flash", "usage": None,
        })
        monkeypatch.setattr(resume_vl_service, "resume_name_matches", lambda name, seen: False)
        resp = client.post("/api/local-tools/runtime/resume/evaluate",
                           json={"images": ["P1"], "candidate_name": "王宣广"})
        assert resp.status_code == 422
        assert "姓名核对不匹配" in resp.json()["detail"]["error"]
        assert billing_spy.calls == []
