"""视频创作智能体知识中心 API

设计依据：docs/plans/plan-video-agent-phase1.md §4

三套 CRUD API：
- 素材库 (asset_library)：列表 / 详情 / 删除 / 手动上传
- 视频库 (work_outcomes where outcome_type='file' AND subagent_id='video-agent')：列表 / 详情 / 删除
- 提示词库 (prompt_library)：列表 / 详情 / 升级模版 / 删除

所有 API 必须遵循租户隔离规范（[backend_dev.md SaaS 租户隔离规范]）：
- 通过 get_current_tenant_id() 取租户
- 所有查询带 tenant_id 过滤
- promote 端点仅租户管理员可调
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.db.database import get_db_connection
from src.saas.context import get_current_tenant_id
from src.saas.permissions.checker import is_tenant_admin
from src.api.auth import get_current_user


router = APIRouter(prefix="/api/video-agent", tags=["视频创作智能体-知识中心"])


# ============== 工具函数 ==============

def _sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg
    patterns = [
        r'password["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'secret["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', sanitized, flags=re.IGNORECASE)
    return sanitized


def _error_response(error: str, debug: str, status_code: int = 500) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": error, "debug": _sanitize_error_info(debug)},
    )


def _require_tenant() -> Optional[str]:
    """获取当前租户 ID（SaaS 模式下必填）"""
    tenant_id = get_current_tenant_id()
    return tenant_id


def _parse_json_field(val: Any) -> Any:
    """把 JSONB 字段从 str 解析为 dict（PostgreSQL JSONB 一般已是 dict，但兜底）"""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return val


# ============== 1. 素材库 API ==============

class ManualUploadAssetRequest(BaseModel):
    file_id: str = Field(..., description="已上传文件的 file_id（前端先调 /api/upload 拿到）")
    display_name: str = Field(..., description="显示名")
    mime_type: str = Field(..., description="MIME 类型，如 image/jpeg")
    size_bytes: int = Field(..., description="文件大小（字节）")
    scene: Optional[str] = Field(None, description="业务场景标签（product / model / bgm 等）")
    width: Optional[int] = None
    height: Optional[int] = None


@router.get("/assets")
async def list_assets(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scene: Optional[str] = Query(None, description="按场景筛选"),
    source: Optional[str] = Query(None, description="按来源筛选（video_chat / user_upload / other_agent_manual）"),
):
    """素材库列表（分页 + 按 scene/source 筛选）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["tenant_id = %s"]
            params: list = [tenant_id]
            if scene:
                conditions.append("scene = %s")
                params.append(scene)
            if source:
                conditions.append("source = %s")
                params.append(source)
            where = " AND ".join(conditions)

            cursor.execute(f"SELECT COUNT(*) AS total FROM asset_library WHERE {where}", params)
            total = cursor.fetchone()["total"]

            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT * FROM asset_library WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            items = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "data": {"items": items, "total": total, "page": page, "page_size": page_size}}
    except Exception as e:
        logger.opt(exception=True).error(f"素材库列表查询失败: {e}")
        return _error_response("素材库列表查询失败", str(e))


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: int, request: Request):
    """素材详情"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM asset_library WHERE id = %s AND tenant_id = %s",
                (asset_id, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                return _error_response("素材不存在", f"asset_id={asset_id} not found", 404)
            return {"success": True, "data": dict(row)}
    except Exception as e:
        logger.opt(exception=True).error(f"素材详情查询失败: {e}")
        return _error_response("素材详情查询失败", str(e))


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: int, request: Request):
    """删除素材（仅删除库记录，不删除底层文件）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM asset_library WHERE id = %s AND tenant_id = %s",
                (asset_id, tenant_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return _error_response("素材不存在", f"asset_id={asset_id} not found", 404)
        return {"success": True}
    except Exception as e:
        logger.opt(exception=True).error(f"素材删除失败: {e}")
        return _error_response("素材删除失败", str(e))


@router.post("/assets/manual")
async def manual_upload_asset(req: ManualUploadAssetRequest, request: Request):
    """手动上传素材到素材库（其他智能体附件收藏）

    前端先调 /api/upload 上传文件拿到 file_id，再调本接口登记到 asset_library。
    source 固定为 'user_upload'。
    """
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        user = get_current_user(request)
        user_id = user.get("user_id") if user else None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO asset_library
                    (tenant_id, user_id, file_id, display_name, mime_type, size_bytes,
                     source, scene, width, height)
                VALUES (%s, %s, %s, %s, %s, %s, 'user_upload', %s, %s, %s)
                RETURNING id
                """,
                (
                    tenant_id, user_id, req.file_id, req.display_name, req.mime_type,
                    req.size_bytes, req.scene, req.width, req.height,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
            asset_id = row["id"] if row else None

        return {"success": True, "data": {"id": asset_id}}
    except Exception as e:
        logger.opt(exception=True).error(f"手动上传素材失败: {e}")
        return _error_response("手动上传素材失败", str(e))


# ============== 2. 视频库 API ==============

@router.get("/videos")
async def list_videos(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """视频库列表（work_outcomes where outcome_type='file' AND subagent_id='video-agent'）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["tenant_id = %s", "outcome_type = 'file'", "subagent_id = 'video-agent'"]
            params: list = [tenant_id]
            where = " AND ".join(conditions)

            cursor.execute(f"SELECT COUNT(*) AS total FROM work_outcomes WHERE {where}", params)
            total = cursor.fetchone()["total"]

            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT * FROM work_outcomes WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            items = []
            for row in cursor.fetchall():
                item = dict(row)
                item["metadata"] = _parse_json_field(item.get("metadata"))
                items.append(item)

        return {"success": True, "data": {"items": items, "total": total, "page": page, "page_size": page_size}}
    except Exception as e:
        logger.opt(exception=True).error(f"视频库列表查询失败: {e}")
        return _error_response("视频库列表查询失败", str(e))


@router.get("/videos/{video_id}")
async def get_video(video_id: int, request: Request):
    """视频详情（含提示词溯源：通过 metadata.prompt_library_id 关联 prompt_library）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM work_outcomes
                WHERE id = %s AND tenant_id = %s
                  AND outcome_type = 'file' AND subagent_id = 'video-agent'
                """,
                (video_id, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                return _error_response("视频不存在", f"video_id={video_id} not found", 404)
            video = dict(row)
            video["metadata"] = _parse_json_field(video.get("metadata"))

            # 提示词溯源
            prompt_library_id = (video.get("metadata") or {}).get("prompt_library_id")
            prompt_detail: Optional[Dict[str, Any]] = None
            if prompt_library_id:
                cursor.execute(
                    "SELECT * FROM prompt_library WHERE id = %s AND tenant_id = %s",
                    (prompt_library_id, tenant_id),
                )
                prompt_row = cursor.fetchone()
                if prompt_row:
                    prompt_detail = dict(prompt_row)
                    prompt_detail["model_params"] = _parse_json_field(prompt_detail.get("model_params"))
                    prompt_detail["metadata"] = _parse_json_field(prompt_detail.get("metadata"))
            video["source_prompt"] = prompt_detail
            return {"success": True, "data": video}
    except Exception as e:
        logger.opt(exception=True).error(f"视频详情查询失败: {e}")
        return _error_response("视频详情查询失败", str(e))


@router.delete("/videos/{video_id}")
async def delete_video(video_id: int, request: Request):
    """删除视频（仅删除 work_outcomes 记录，不删除底层文件）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM work_outcomes
                WHERE id = %s AND tenant_id = %s
                  AND outcome_type = 'file' AND subagent_id = 'video-agent'
                """,
                (video_id, tenant_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return _error_response("视频不存在", f"video_id={video_id} not found", 404)
        return {"success": True}
    except Exception as e:
        logger.opt(exception=True).error(f"视频删除失败: {e}")
        return _error_response("视频删除失败", str(e))


# ============== 3. 提示词库 API ==============

@router.get("/prompts")
async def list_prompts(
    request: Request,
    category: Optional[str] = Query(None, description="按类别筛选：kept / blacklist / template"),
    scene_tag: Optional[str] = Query(None, description="按场景标签筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """提示词库列表（按 category 筛选）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["tenant_id = %s"]
            params: list = [tenant_id]
            if category:
                conditions.append("category = %s")
                params.append(category)
            if scene_tag:
                conditions.append("scene_tag = %s")
                params.append(scene_tag)
            where = " AND ".join(conditions)

            cursor.execute(f"SELECT COUNT(*) AS total FROM prompt_library WHERE {where}", params)
            total = cursor.fetchone()["total"]

            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT * FROM prompt_library WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            items = []
            for row in cursor.fetchall():
                item = dict(row)
                item["model_params"] = _parse_json_field(item.get("model_params"))
                item["metadata"] = _parse_json_field(item.get("metadata"))
                items.append(item)

        return {"success": True, "data": {"items": items, "total": total, "page": page, "page_size": page_size}}
    except Exception as e:
        logger.opt(exception=True).error(f"提示词库列表查询失败: {e}")
        return _error_response("提示词库列表查询失败", str(e))


@router.get("/prompts/{prompt_id}")
async def get_prompt(prompt_id: int, request: Request):
    """提示词详情"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prompt_library WHERE id = %s AND tenant_id = %s",
                (prompt_id, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                return _error_response("提示词不存在", f"prompt_id={prompt_id} not found", 404)
            item = dict(row)
            item["model_params"] = _parse_json_field(item.get("model_params"))
            item["metadata"] = _parse_json_field(item.get("metadata"))
            return {"success": True, "data": item}
    except Exception as e:
        logger.opt(exception=True).error(f"提示词详情查询失败: {e}")
        return _error_response("提示词详情查询失败", str(e))


@router.post("/prompts/{prompt_id}/promote")
async def promote_prompt(prompt_id: int, request: Request):
    """升级为模版（仅租户管理员可调）

    实现要点：复制一条新记录（category=template, promoted_from_kept_id, promoted_by_user_id, promoted_at），
    原 kept 记录保留（保持溯源链）。
    """
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        user = get_current_user(request)
        if not user:
            return _error_response("未登录", "user is None", 401)
        if not is_tenant_admin(user):
            return _error_response("无权限", "仅租户管理员可升级模版", 403)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 1. 查原记录
            cursor.execute(
                "SELECT * FROM prompt_library WHERE id = %s AND tenant_id = %s",
                (prompt_id, tenant_id),
            )
            src = cursor.fetchone()
            if not src:
                return _error_response("提示词不存在", f"prompt_id={prompt_id} not found", 404)
            src = dict(src)
            if src["category"] != "kept":
                return _error_response("仅留用记录可升级", f"current category={src['category']}", 400)

            # 2. 复制一条新记录（category=template）
            cursor.execute(
                """
                INSERT INTO prompt_library
                    (tenant_id, user_id, category, business_prompt, craft_prompt, model_params,
                     industry_tag, scene_tag, source_video_file_id, source_chat_session_id,
                     promoted_from_kept_id, promoted_by_user_id, promoted_at, metadata)
                VALUES (%s, %s, 'template', %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                RETURNING id
                """,
                (
                    tenant_id, user.get("user_id"),
                    src["business_prompt"], src["craft_prompt"],
                    psycopg2.extras.Json(src["model_params"]) if src["model_params"] is not None else None,
                    src["industry_tag"], src["scene_tag"],
                    src["source_video_file_id"], src["source_chat_session_id"],
                    src["id"], user.get("user_id"),
                    psycopg2.extras.Json(src["metadata"]) if src["metadata"] is not None else None,
                ),
            )
            new_row = cursor.fetchone()
            conn.commit()
            new_id = new_row["id"] if new_row else None

        logger.info(f"提示词升级模版: src_id={prompt_id}, new_id={new_id}, operator={user.get('user_id')}")
        return {"success": True, "data": {"id": new_id, "promoted_from_kept_id": prompt_id}}
    except Exception as e:
        logger.opt(exception=True).error(f"提示词升级模版失败: {e}")
        return _error_response("提示词升级模版失败", str(e))


@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: int, request: Request):
    """删除提示词"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM prompt_library WHERE id = %s AND tenant_id = %s",
                (prompt_id, tenant_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return _error_response("提示词不存在", f"prompt_id={prompt_id} not found", 404)
        return {"success": True}
    except Exception as e:
        logger.opt(exception=True).error(f"提示词删除失败: {e}")
        return _error_response("提示词删除失败", str(e))
