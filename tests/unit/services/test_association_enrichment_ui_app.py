import importlib.util
from time import monotonic
from pathlib import Path

from fastapi.testclient import TestClient


APP_PATH = (
    Path(__file__).parents[3]
    / "clients"
    / "association-enrichment-ui"
    / "app.py"
)
SPEC = importlib.util.spec_from_file_location("association_enrichment_ui_app", APP_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_endpoint_accepts_csv_and_removes_temporary_file(monkeypatch):
    captured = {}

    async def fake_parse(evidence):
        captured["evidence"] = evidence
        return ["中国缝制机械协会"]

    monkeypatch.setattr(MODULE, "parse_association_evidence_with_llm", fake_parse)
    with TestClient(MODULE.app) as client:
        response = client.post(
            "/api/parse",
            data={"text": ""},
            files={
                "upload": (
                    "input.csv",
                    "协会名称\n中国缝制机械协会\n".encode("utf-8"),
                    "text/csv",
                )
            },
        )
    assert response.status_code == 200
    assert response.json()["associations"] == ["中国缝制机械协会"]
    assert captured["evidence"] == ["中国缝制机械协会"]


def test_start_rejects_name_not_in_llm_confirmed_plan():
    plan_id = "a" * 32
    MODULE._plans[plan_id] = ["中国缝制机械协会"]
    with TestClient(MODULE.app) as client:
        response = client.post(
            "/api/runs",
            json={
                "plan_id": plan_id,
                "associations": ["未经过模型解析的协会"],
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "PLAN_ASSOCIATION_NOT_ALLOWED"


def test_api_rejects_cross_origin_loopback_requests():
    with TestClient(MODULE.app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/parse",
            headers={"Origin": "https://attacker.example"},
            data={"text": "测试协会"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "SAME_ORIGIN_ONLY"


def test_frontend_exposes_accessible_progress_and_evidence_regions():
    html = (APP_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert 'aria-label="总体进度"' in html
    assert 'role="progressbar"' in html
    assert 'aria-valuenow="0"' in html
    assert 'createElement(e.detail_ref?"button":"div")' in html
    assert "row.innerHTML" not in html
    assert "summary.textContent" in html
    assert 'aria-live="polite"' in html
    assert "证据事件流" in html
    assert "模型解析清单" in html


def test_run_api_redacts_summary_and_only_detail_endpoint_decrypts(
    monkeypatch, tmp_path
):
    manager = MODULE.AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: None,
    )
    run = manager.create(["测试协会"])
    event = manager.store(run.run_id).append(
        association="测试协会",
        stage="微信",
        kind="wechat_artifact",
        summary="找到 13912345678",
        detail={"text": "联系人 13912345678"},
    )
    run.events.append(event)
    manager._persist(run)
    monkeypatch.setattr(MODULE, "manager", manager)

    with TestClient(MODULE.app) as client:
        ordinary = client.get(f"/api/runs/{run.run_id}")
        detail = client.get(
            f"/api/runs/{run.run_id}/details/{event.detail_ref}"
        )
        invalid = client.get(
            f"/api/runs/{run.run_id}/details/not-a-valid-ref"
        )

    assert ordinary.status_code == 200
    assert "13912345678" not in ordinary.text
    assert "139****5678" in ordinary.text
    assert detail.status_code == 200
    assert detail.json()["detail"]["text"] == "联系人 13912345678"
    assert invalid.status_code == 404


def test_frontend_renders_batch_and_per_association_token_usage():
    html = (APP_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="tokenBatch"' in html
    assert 'id="tokenRows"' in html
    assert "average_cached_input_tokens" in html
    assert "batch_overhead" in html


def test_expired_plan_is_rejected_and_removed():
    plan_id = "b" * 32
    MODULE._plans[plan_id] = {
        "associations": ["测试协会"],
        "usage": MODULE.TokenUsage().as_dict(),
        "created_at": monotonic() - MODULE.PLAN_TTL_SECONDS - 1,
    }
    with TestClient(MODULE.app) as client:
        response = client.post(
            "/api/runs",
            json={"plan_id": plan_id, "associations": ["测试协会"]},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "PLAN_EXPIRED"
    assert plan_id not in MODULE._plans


def test_plan_is_single_use_and_parse_usage_stays_batch_scoped(
    monkeypatch, tmp_path
):
    import asyncio

    real_manager = MODULE.AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: None,
    )

    class ManagerAdapter:
        busy = False

        def create(self, *args, **kwargs):
            return real_manager.create(*args, **kwargs)

        def launch(self, _run_id):
            return asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(MODULE, "manager", ManagerAdapter())
    plan_id = "c" * 32
    MODULE._plans[plan_id] = {
        "associations": ["协会一", "协会二"],
        "usage": MODULE.TokenUsage(
            input_tokens=10,
            cached_input_tokens=4,
            output_tokens=2,
            total_tokens=12,
            call_count=1,
        ).as_dict(),
        "created_at": monotonic(),
    }
    payload = {"plan_id": plan_id, "associations": ["协会二"]}
    with TestClient(MODULE.app) as client:
        first = client.post("/api/runs", json=payload)
        replay = client.post("/api/runs", json=payload)
    assert first.status_code == 200
    usage = first.json()["token_usage"]
    assert usage["associations"]["协会二"]["total_tokens"] == 0
    assert usage["batch_overhead"]["total_tokens"] == 12
    assert usage["batch"]["total_tokens"] == 12
    assert replay.status_code == 400
    assert replay.json()["detail"] == "PLAN_NOT_FOUND"
