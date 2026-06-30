from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.social_media.services import SocialMediaService, sanitize_error_info

router = APIRouter(prefix="/api/social-media", tags=["社媒运营"])
service = SocialMediaService()


class JsonResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    debug: Optional[str] = None


class AccountCreateRequest(BaseModel):
    platform: str = Field(..., description="平台标识")
    display_name: str
    external_account_id: Optional[str] = None
    auth_type: str = "credentials"
    credentials: dict[str, Any] = Field(default_factory=dict)


class PlanCreateRequest(BaseModel):
    name: str
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    goal: Optional[str] = None
    target_audience: Optional[str] = None
    owner_user_id: Optional[str] = None
    status: str = "draft"


class ContentMasterCreateRequest(BaseModel):
    item_id: Optional[str] = None
    title: str
    brief: Optional[str] = None
    facts: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    brand_constraints: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"


class PlanItemCreateRequest(BaseModel):
    topic: str
    objective: Optional[str] = None
    planned_at: Optional[str] = None
    timezone: str = "Asia/Shanghai"
    owner_user_id: Optional[str] = None
    status: str = "draft"


class AssetCreateRequest(BaseModel):
    storage_file_id: Optional[str] = None
    asset_type: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    source_type: Optional[str] = None
    source_uri: Optional[str] = None
    license_type: Optional[str] = None
    license_owner: Optional[str] = None
    license_expires_at: Optional[str] = None
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class VariantCreateRequest(BaseModel):
    account_id: str
    content_type: str = "article"
    content: dict[str, Any]
    prompt_version: str = "manual"


class ReviewRequest(BaseModel):
    comment: Optional[str] = None


class PublishJobCreateRequest(BaseModel):
    variant_id: str
    publish_mode: str = "immediate"
    scheduled_at: Optional[str] = None
    timezone: str = "Asia/Shanghai"


class ManualConfirmRequest(BaseModel):
    external_url: str
    external_content_id: Optional[str] = None
    published_at: Optional[str] = None


def _tenant_id(request: Request) -> Optional[str]:
    return getattr(request.state, "tenant_id", None)


def _user_id(request: Request) -> str:
    user = get_current_user(request)
    return (user or {}).get("user_id") or "anonymous"


def _ok(data: Any = None) -> JsonResponse:
    return JsonResponse(success=True, data=data)


def _fail(message: str, exc: Exception | None = None) -> JsonResponse:
    debug = sanitize_error_info(str(exc)) if exc else None
    return JsonResponse(success=False, error=message, debug=debug)


@router.get("/accounts", response_model=JsonResponse)
async def list_accounts(request: Request):
    try:
        return _ok({"items": service.list_accounts(_tenant_id(request))})
    except Exception as e:
        logger.error(f"社媒账号列表失败: {e}", exc_info=True)
        return _fail("查询账号失败", e)


@router.post("/accounts", response_model=JsonResponse)
async def create_account(request: Request, body: AccountCreateRequest):
    try:
        data = await service.create_account(_tenant_id(request), _user_id(request), body.model_dump())
        return _ok(data)
    except Exception as e:
        logger.error(f"社媒账号创建失败: {e}", exc_info=True)
        return _fail("创建账号失败", e)


@router.get("/accounts/{account_id}", response_model=JsonResponse)
async def get_account(request: Request, account_id: str):
    try:
        return _ok(service.get_account(_tenant_id(request), account_id))
    except Exception as e:
        return _fail("查询账号失败", e)


@router.post("/accounts/{account_id}/validate", response_model=JsonResponse)
async def validate_account(request: Request, account_id: str):
    try:
        data = await service.refresh_account_capabilities(_tenant_id(request), account_id)
        return _ok(data)
    except Exception as e:
        logger.error(f"社媒账号验证失败: {e}", exc_info=True)
        return _fail("验证账号失败", e)


@router.post("/accounts/{account_id}/refresh-capabilities", response_model=JsonResponse)
async def refresh_capabilities(request: Request, account_id: str):
    return await validate_account(request, account_id)


@router.delete("/accounts/{account_id}", response_model=JsonResponse)
async def delete_account(request: Request, account_id: str):
    try:
        service.delete_account(_tenant_id(request), account_id)
        return _ok({"account_id": account_id})
    except Exception as e:
        return _fail("解绑账号失败", e)


@router.get("/plans", response_model=JsonResponse)
async def list_plans(request: Request):
    try:
        return _ok({"items": service.list_plans(_tenant_id(request))})
    except Exception as e:
        return _fail("查询计划失败", e)


@router.post("/plans", response_model=JsonResponse)
async def create_plan(request: Request, body: PlanCreateRequest):
    try:
        return _ok(service.create_plan(_tenant_id(request), _user_id(request), body.model_dump()))
    except Exception as e:
        return _fail("创建计划失败", e)


@router.get("/plans/{plan_id}/items", response_model=JsonResponse)
async def list_plan_items(request: Request, plan_id: str):
    try:
        return _ok({"items": service.list_plan_items(_tenant_id(request), plan_id)})
    except Exception as e:
        return _fail("查询日历条目失败", e)


@router.post("/plans/{plan_id}/items", response_model=JsonResponse)
async def create_plan_item(request: Request, plan_id: str, body: PlanItemCreateRequest):
    try:
        return _ok(service.create_plan_item(_tenant_id(request), plan_id, body.model_dump()))
    except Exception as e:
        return _fail("创建日历条目失败", e)


@router.get("/assets", response_model=JsonResponse)
async def list_assets(request: Request):
    try:
        return _ok({"items": service.list_assets(_tenant_id(request))})
    except Exception as e:
        return _fail("查询素材失败", e)


@router.post("/assets", response_model=JsonResponse)
async def create_asset(request: Request, body: AssetCreateRequest):
    try:
        return _ok(service.create_asset(_tenant_id(request), _user_id(request), body.model_dump()))
    except Exception as e:
        return _fail("登记素材失败", e)


@router.post("/content-masters", response_model=JsonResponse)
async def create_content_master(request: Request, body: ContentMasterCreateRequest):
    try:
        return _ok(service.create_content_master(_tenant_id(request), _user_id(request), body.model_dump()))
    except Exception as e:
        return _fail("创建内容母版失败", e)


@router.post("/content-masters/{master_id}/variants", response_model=JsonResponse)
async def create_variant(request: Request, master_id: str, body: VariantCreateRequest):
    try:
        data = await service.create_variant(_tenant_id(request), _user_id(request), master_id, body.model_dump())
        return _ok(data)
    except Exception as e:
        logger.error(f"社媒平台版本创建失败: {e}", exc_info=True)
        return _fail("创建平台版本失败", e)


@router.get("/variants/{variant_id}", response_model=JsonResponse)
async def get_variant(request: Request, variant_id: str):
    try:
        return _ok(service.get_variant(_tenant_id(request), variant_id))
    except Exception as e:
        return _fail("查询平台版本失败", e)


@router.post("/variants/{variant_id}/submit-review", response_model=JsonResponse)
async def submit_review(request: Request, variant_id: str):
    try:
        service.submit_review(_tenant_id(request), variant_id)
        return _ok({"variant_id": variant_id, "status": "pending_review"})
    except Exception as e:
        return _fail("提交审核失败", e)


@router.post("/variants/{variant_id}/approve", response_model=JsonResponse)
async def approve_variant(request: Request, variant_id: str, body: ReviewRequest):
    try:
        return _ok(service.review_variant(_tenant_id(request), _user_id(request), variant_id, "approved", body.comment))
    except Exception as e:
        return _fail("审核通过失败", e)


@router.post("/variants/{variant_id}/reject", response_model=JsonResponse)
async def reject_variant(request: Request, variant_id: str, body: ReviewRequest):
    try:
        return _ok(service.review_variant(_tenant_id(request), _user_id(request), variant_id, "rejected", body.comment))
    except Exception as e:
        return _fail("驳回失败", e)


@router.post("/publish-jobs", response_model=JsonResponse)
async def create_publish_job(
    request: Request,
    body: PublishJobCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    try:
        return _ok(service.create_publish_job(_tenant_id(request), _user_id(request), body.model_dump(), idempotency_key))
    except Exception as e:
        return _fail("创建发布任务失败", e)


@router.get("/publish-jobs", response_model=JsonResponse)
async def list_publish_jobs(request: Request):
    try:
        return _ok({"items": service.list_publish_jobs(_tenant_id(request))})
    except Exception as e:
        return _fail("查询发布任务失败", e)


@router.get("/publish-jobs/{job_id}", response_model=JsonResponse)
async def get_publish_job(request: Request, job_id: str):
    try:
        return _ok(service.get_publish_job(_tenant_id(request), job_id))
    except Exception as e:
        return _fail("查询发布任务失败", e)


@router.post("/publish-jobs/{job_id}/cancel", response_model=JsonResponse)
async def cancel_publish_job(request: Request, job_id: str):
    try:
        return _ok(service.cancel_publish_job(_tenant_id(request), job_id))
    except Exception as e:
        return _fail("取消发布任务失败", e)


@router.post("/publish-jobs/{job_id}/manual-confirm", response_model=JsonResponse)
async def manual_confirm(request: Request, job_id: str, body: ManualConfirmRequest):
    try:
        return _ok(service.manual_confirm(_tenant_id(request), _user_id(request), job_id, body.model_dump()))
    except Exception as e:
        return _fail("人工确认失败", e)


@router.get("/analytics/overview", response_model=JsonResponse)
async def analytics_overview(request: Request):
    try:
        return _ok(service.analytics_overview(_tenant_id(request)))
    except Exception as e:
        return _fail("查询运营数据失败", e)
