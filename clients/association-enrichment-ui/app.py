"""协会资料调查工作台：仅供本机使用的 FastAPI 入口。"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.association_batch_enrichment import AssociationBatchEnricher
from src.services.association_enrichment_providers import ProjectAssociationProviders
from src.services.association_enrichment_ui import (
    AssociationUiRunManager,
    extract_upload_evidence,
    parse_association_evidence_with_llm,
    temporary_upload_path,
)
from src.services.llm_usage_meter import (
    TokenUsage,
    install_usage_recorder,
    reset_usage_recorder,
    reset_usage_context,
    set_usage_context,
)

APP_DIR = Path(__file__).resolve().parent
DATA_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", APP_DIR))
    / "AidWorkAgent"
    / "association-enrichment-ui"
    / "runs"
)
_plans: dict[str, dict] = {}
PLAN_TTL_SECONDS = 30 * 60
_tasks: set[asyncio.Task] = set()


def _enricher_factory(progress, audit):
    providers = ProjectAssociationProviders(
        repository_root=ROOT,
        audit_callback=audit,
    )
    return AssociationBatchEnricher(
        official_profile_collector=providers.collect_official_profile,
        fallback_profile_provider=providers.fallback_profile,
        wechat_mobile_provider=providers.wechat_mobile,
        headless=False,
        progress_reporter=progress,
    )


manager = AssociationUiRunManager(DATA_ROOT, _enricher_factory)
app = FastAPI(title="协会资料调查工作台", docs_url=None, redoc_url=None)


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="LOOPBACK_ONLY")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.port != request.url.port
        ):
            raise HTTPException(status_code=403, detail="SAME_ORIGIN_ONLY")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.post("/api/parse")
async def parse_input(
    request: Request,
    text: str = Form(default=""),
    upload: UploadFile | None = File(default=None),
):
    _require_loopback(request)
    evidence: list[str] = [text] if text.strip() else []
    if upload is not None and upload.filename:
        content = await upload.read(10 * 1024 * 1024 + 1)
        temporary = None
        try:
            temporary = temporary_upload_path(upload.filename, content)
            evidence.extend(extract_upload_evidence(temporary))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    try:
        parse_usage = TokenUsage()
        usage_token = install_usage_recorder(
            lambda _association, _stage, usage: parse_usage.add(usage)
        )
        context_tokens = set_usage_context(association="", stage="清单解析")
        try:
            names = await parse_association_evidence_with_llm(evidence)
        finally:
            reset_usage_context(context_tokens)
            reset_usage_recorder(usage_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    plan_id = uuid.uuid4().hex
    _plans[plan_id] = {
        "associations": names,
        "usage": parse_usage.as_dict(),
        "created_at": monotonic(),
    }
    return {
        "plan_id": plan_id,
        "associations": names,
        "token_usage": parse_usage.as_dict(),
    }


class StartRequest(BaseModel):
    plan_id: str
    associations: list[str]


@app.post("/api/runs")
async def create_run(payload: StartRequest, request: Request):
    _require_loopback(request)
    plan = _plans.get(payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=400, detail="PLAN_NOT_FOUND")
    if (
        isinstance(plan, dict)
        and isinstance(plan.get("created_at"), (int, float))
        and monotonic() - plan["created_at"] > PLAN_TTL_SECONDS
    ):
        _plans.pop(payload.plan_id, None)
        raise HTTPException(status_code=400, detail="PLAN_EXPIRED")
    allowed = plan["associations"] if isinstance(plan, dict) else plan
    if not payload.associations or any(name not in allowed for name in payload.associations):
        raise HTTPException(status_code=400, detail="PLAN_ASSOCIATION_NOT_ALLOWED")
    if manager.busy:
        raise HTTPException(status_code=409, detail="RPA_BUSY")
    run = manager.create(
        payload.associations,
        initial_usage=TokenUsage(**plan.get("usage", {})) if isinstance(plan, dict) else None,
    )
    _plans.pop(payload.plan_id, None)
    task = manager.launch(run.run_id)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run.public_dict()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    _require_loopback(request)
    try:
        return manager.get(run_id).public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/details/{detail_ref}")
async def get_detail(run_id: str, detail_ref: str, request: Request):
    _require_loopback(request)
    try:
        return {"detail": manager.store(run_id).read_detail(detail_ref)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/download")
async def download(run_id: str, request: Request):
    _require_loopback(request)
    try:
        path = manager.output_path(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        filename="association-results.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def main() -> None:
    import argparse
    import webbrowser

    import uvicorn

    parser = argparse.ArgumentParser(description="启动协会资料调查工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_open:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
