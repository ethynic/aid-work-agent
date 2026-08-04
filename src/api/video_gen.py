"""视频生成 API 路由（/api/video-gen/*，独立模块）。

照抄 src/api/social_media.py 的 JsonResponse + _ok/_fail/_tenant_id/_user_id 模式。
端点清单（mvp-design.md §9）：
- GET    /scenes                       场景列表
- POST   /sessions                     创建抽卡会话
- GET    /sessions                     会话历史
- GET    /sessions/{session_id}        会话详情（含 cards 状态）
- PATCH  /cards/{card_id}/kept         标记留用
- POST   /cards/{card_id}/regenerate   重新生成
- GET    /cards/{card_id}/download-url 获取成片下载 URL
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.config.settings import settings
from src.video_gen.service import VideoGenService

router = APIRouter(prefix="/api/video-gen", tags=["视频生成"])
_service = VideoGenService()


class JsonResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    debug: Optional[str] = None


def _sanitize_error(error_msg: str) -> str:
    """错误信息脱敏（复用 social_media 的模式）。"""
    if not error_msg:
        return error_msg
    for pattern in [r'password["\s:=]+\S+', r'appsecret["\s:=]+\S+', r'secret["\s:=]+\S+', r'token["\s:=]+\S+', r'api[_-]?key["\s:=]+\S+']:
        error_msg = re.sub(pattern, lambda m: m.group(0).split("=")[0] + "=***", error_msg, flags=re.IGNORECASE)
    return error_msg


def _tenant_id(request: Request) -> Optional[str]:
    return getattr(request.state, "tenant_id", None)


def _user_id(request: Request) -> str:
    user = get_current_user(request)
    return (user or {}).get("user_id") or "anonymous"


def _ok(data: Any = None) -> JsonResponse:
    return JsonResponse(success=True, data=data)


def _fail(message: str, exc: Exception | None = None) -> JsonResponse:
    debug = _sanitize_error(str(exc)) if exc else None
    return JsonResponse(success=False, error=message, debug=debug)


def _download_url(file_id: str) -> str:
    """构造文件下载 URL（复用现有 /api/files/{file_id}/download 路由）。"""
    base = settings.app.public_base_url or ""
    return f"{base}/api/files/{file_id}/download" if base else f"/api/files/{file_id}/download"


@router.get("/scenes", response_model=JsonResponse)
async def list_scenes(request: Request):
    """场景列表。"""
    try:
        return _ok({"items": _service.list_scenes()})
    except Exception as e:
        logger.error(f"视频生成-场景列表失败: {e}", exc_info=True)
        return _fail("查询场景失败", e)


@router.post("/sessions", response_model=JsonResponse)
async def create_session(request: Request):
    """创建抽卡会话：{scene_id, product_image_fid, copywriting, model_image_fid?, card_count?, expanded_prompt?}"""
    try:
        body = await request.json()
        result = await _service.create_session(
            tenant_id=_tenant_id(request),
            user_id=_user_id(request),
            scene_id=body["scene_id"],
            product_image_fid=body["product_image_fid"],
            copywriting=body["copywriting"],
            card_count=body.get("card_count", 3),
            expanded_prompt=body.get("expanded_prompt"),
            model_image_fid=body.get("model_image_fid"),
        )
        return _ok(result)
    except ValueError as e:
        return _fail(str(e), e)
    except KeyError as e:
        return _fail(f"缺少必填参数: {e}")
    except Exception as e:
        logger.error(f"视频生成-创建会话失败: {e}", exc_info=True)
        return _fail("创建会话失败", e)


@router.get("/sessions", response_model=JsonResponse)
async def list_sessions(request: Request):
    """会话历史列表。"""
    try:
        limit = int(request.query_params.get("limit", 20))
        return _ok({"items": _service.list_sessions(_tenant_id(request), limit=limit)})
    except Exception as e:
        logger.error(f"视频生成-会话列表失败: {e}", exc_info=True)
        return _fail("查询会话列表失败", e)


@router.get("/sessions/{session_id}", response_model=JsonResponse)
async def get_session(request: Request, session_id: str):
    """会话详情（含 cards 状态）。"""
    try:
        result = _service.get_session(_tenant_id(request), session_id)
        if result is None:
            return _fail("会话不存在或无权限")
        return _ok(result)
    except Exception as e:
        logger.error(f"视频生成-会话详情失败: {e}", exc_info=True)
        return _fail("查询会话详情失败", e)


@router.patch("/cards/{card_id}/kept", response_model=JsonResponse)
async def set_card_kept(request: Request, card_id: str):
    """标记 card 留用/取消：{kept: bool}"""
    try:
        body = await request.json()
        result = _service.set_card_kept(_tenant_id(request), card_id, bool(body.get("kept", True)))
        return _ok(result)
    except ValueError as e:
        return _fail(str(e), e)
    except Exception as e:
        logger.error(f"视频生成-留用失败: {e}", exc_info=True)
        return _fail("操作失败", e)


@router.post("/cards/{card_id}/regenerate", response_model=JsonResponse)
async def regenerate_card(request: Request, card_id: str):
    """重新生成式编辑：{prompt_override?, seed_override?}"""
    try:
        body = await request.json()
        result = await _service.regenerate_card(
            _tenant_id(request),
            card_id,
            prompt_override=body.get("prompt_override"),
            seed_override=body.get("seed_override"),
        )
        return _ok(result)
    except ValueError as e:
        return _fail(str(e), e)
    except Exception as e:
        logger.error(f"视频生成-重新生成失败: {e}", exc_info=True)
        return _fail("重新生成失败", e)


@router.get("/cards/{card_id}/download-url", response_model=JsonResponse)
async def get_download_url(request: Request, card_id: str):
    """获取成片下载 URL：{download_url, file_id}"""
    try:
        # 通过 service 查 card 的 output_fid（复用 get_session 里的查询走 session 太绕，
        # 这里直接查 card）。为保持 service 接口稳定，加一个轻量查询方法。
        file_id = _service.get_card_output_fid(_tenant_id(request), card_id)
        if not file_id:
            return _fail("成片尚未就绪或不可用")
        return _ok({"download_url": _download_url(file_id), "file_id": file_id})
    except Exception as e:
        logger.error(f"视频生成-下载URL失败: {e}", exc_info=True)
        return _fail("获取下载地址失败", e)
