"""
知识库 API 路由
"""

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from loguru import logger

from src.api import auth
from src.knowledge.service import knowledge_service
from src.saas.context import get_current_tenant_id
from src.config.settings import settings
from src.db.database import get_db_connection

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {
    ".docx", ".xlsx", ".pptx", ".pdf",  # Office 和 PDF
    ".txt", ".md", ".json",             # 纯文本 / Markdown / JSON
    ".yaml", ".yml",                    # YAML 配置
    ".log",                             # 日志文件
    ".csv",                             # CSV 数据
    ".xml",                             # XML 数据
    ".ini", ".properties", ".conf", ".config",  # 各类配置文件
}


class UploadResponse(BaseModel):
    document_id: int
    title: str
    total_chunks: int
    status: str
    message: str


class BatchUploadResponse(BaseModel):
    success: bool
    results: list[UploadResponse]
    errors: list[dict[str, str]]


class DocumentResponse(BaseModel):
    id: int
    title: Optional[str] = None
    source_type: Optional[str] = None
    sub_category: Optional[str] = None
    file_type: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    total_chunks: int = 0
    created_at: Optional[str] = None
    summary: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 10
    source_type: Optional[str] = None  # 顶级分类代号，选中分类时限定搜索范围
    sub_category: Optional[str] = None  # 直接选中分类代号，后端展开为含其所有子级


class SearchResultItem(BaseModel):
    doc_id: int
    chunk_id: int
    text: str
    title: str
    file_type: str
    file_path: Optional[str] = None
    score: float


class SearchResponse(BaseModel):
    success: bool
    results: List[SearchResultItem]
    count: int
    error: Optional[str] = None
    debug: Optional[str] = None


class CategoryResponse(BaseModel):
    id: int
    source_type: str
    display_name: Optional[str] = None
    parent_id: Optional[int] = None
    document_count: int = 0
    created_at: Optional[str] = None


class MoveDocumentsRequest(BaseModel):
    doc_ids: List[int]
    source_type: str  # 目标顶级分类代号（必须传）
    sub_category: Optional[str] = None  # 目标直接所属子分类代号，顶级分类下为 None


class CreateCategoryRequest(BaseModel):
    source_type: Optional[str] = None  # 分类英文代号，不传时后端自动生成
    display_name: Optional[str] = None
    parent_id: Optional[int] = None


class UpdateCategoryRequest(BaseModel):
    display_name: str


def _is_global_admin_view(http_request: Optional[Request]) -> bool:
    """平台管理员全局视图判定：认证后 role=platform_admin 且当前无租户上下文
    （未通过 X-Tenant-Id 代管指定租户，platform_admin 自身 tenant_id 为空）。

    True 时知识库 list/count/search/delete/chunks 恢复无租户过滤的全局口径；
    未认证、普通用户、admin 代管指定租户、非 SaaS 模式（中间件未挂载，
    state 恒无 user_role）一律 False，保持 NULL 收窄防泄漏行为。
    """
    if http_request is None:
        return False
    return (
        getattr(http_request.state, "user_role", None) == "platform_admin"
        and get_current_tenant_id() is None
    )


@router.get("/categories")
async def list_categories(http_request: Request = None):
    """获取知识库分类列表"""
    tenant_id = get_current_tenant_id()
    categories = knowledge_service.list_categories(tenant_id)
    return {"items": [CategoryResponse(**c) for c in categories]}


@router.post("/categories")
async def create_category(request: CreateCategoryRequest, http_request: Request = None):
    """创建知识库分类"""
    tenant_id = get_current_tenant_id()
    result = knowledge_service.create_category(
        tenant_id=tenant_id,
        source_type=request.source_type,
        display_name=request.display_name,
        parent_id=request.parent_id
    )
    if not result.get("success"):
        status_code = result.get("status", 400)
        return JSONResponse(status_code=status_code, content={"success": False, "error": result.get("error", "创建失败")})
    return result


@router.put("/categories/{category_id}")
async def update_category(category_id: int, request: UpdateCategoryRequest, http_request: Request = None):
    """更新分类名称"""
    tenant_id = get_current_tenant_id()
    result = knowledge_service.update_category(category_id, tenant_id, request.display_name)
    if not result.get("success"):
        return JSONResponse(status_code=404, content={"success": False, "error": result.get("error", "更新失败")})
    return {"success": True}


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, http_request: Request = None):
    """删除分类（不删除文档）"""
    tenant_id = get_current_tenant_id()
    result = knowledge_service.delete_category(category_id, tenant_id)
    if not result.get("success"):
        return JSONResponse(status_code=404, content={"success": False, "error": result.get("error", "删除失败")})
    return {"success": True}


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    source_type: Optional[str] = Form(None),
    sub_category: Optional[str] = Form(None),
    http_request: Request = None
):
    """
    上传知识库文档（单文件）

    - 支持格式：docx, xlsx, pptx, pdf, txt, md, json, yaml, yml, log, csv, xml, ini, properties, conf, config
    - 自动解析、分块、向量化
    - 返回文档 ID
    - source_type: 顶级分类代号；sub_category: 直接所属子分类代号（可选）
    """
    # 验证文件格式
    ext = Path(file.filename or "unknown").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持的格式：{', '.join(ALLOWED_EXTENSIONS)}"
        )

    # 验证文件大小
    max_size = settings.storage.max_knowledge_file_size
    if file.size and file.size > max_size:
        max_size_mb = max_size / 1024 / 1024
        return JSONResponse(
            status_code=200,
            content={"success": False, "error": f"文件过大，最大支持 {max_size_mb:.0f}MB"}
        )

    # 获取用户 ID
    user_id = None
    current_user = auth.get_current_user(http_request) if http_request else None
    if current_user:
        user_id = current_user.get("user_id")

    # 获取租户 ID，决定上传目录
    tenant_id = get_current_tenant_id()

    # 保存文件（使用统一存储结构）
    file_id = f"kb_{uuid.uuid4().hex[:12]}"
    upload_dir = knowledge_service._get_upload_path(tenant_id)
    file_path = upload_dir / f"{file_id}{ext}"

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"后端日志：文件保存成功: {file_path}")

        # 处理文档
        result = await knowledge_service.upload_document(
            file_path=str(file_path),
            file_filename=file.filename or "unknown",
            user_id=user_id,
            tenant_id=tenant_id,
            source_type=source_type,
            sub_category=sub_category
        )

        if not result.get("success"):
            # 清理已保存的文件
            if file_path.exists():
                file_path.unlink()
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": result.get("error", "处理失败"),
                    "debug": result.get("error", "")
                }
            )

        return UploadResponse(
            document_id=result["document_id"],
            title=result["title"],
            total_chunks=result["total_chunks"],
            status="success",
            message=result["message"]
        )

    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：文档上传失败: {e}")
        # 清理已保存的文件
        if file_path.exists():
            file_path.unlink()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "文档上传失败",
                "debug": str(e)
            }
        )


@router.post("/upload/batch", response_model=BatchUploadResponse)
async def upload_documents_batch(
    files: list[UploadFile] = File(...),
    source_type: Optional[str] = Form(None),
    sub_category: Optional[str] = Form(None),
    http_request: Request = None
):
    """
    批量上传知识库文档（多文件）

    - 支持格式：docx, xlsx, pptx, pdf, txt, md, json, yaml, yml, log, csv, xml, ini, properties, conf, config
    - 自动解析、分块、向量化
    - 返回每个文件的处理结果和错误信息
    - source_type: 顶级分类代号；sub_category: 直接所属子分类代号（可选）
    """
    max_size = settings.storage.max_knowledge_file_size
    max_size_mb = max_size / 1024 / 1024

    # 获取用户 ID
    user_id = None
    current_user = auth.get_current_user(http_request) if http_request else None
    if current_user:
        user_id = current_user.get("user_id")

    # 获取租户 ID
    tenant_id = get_current_tenant_id()
    upload_dir = knowledge_service._get_upload_path(tenant_id)

    results: list[UploadResponse] = []
    errors: list[dict[str, str]] = []

    for file in files:
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower()

        # 验证文件格式
        if ext not in ALLOWED_EXTENSIONS:
            errors.append({
                "filename": filename,
                "error": f"不支持的文件格式: {ext}"
            })
            continue

        # 验证文件大小
        if file.size and file.size > max_size:
            errors.append({
                "filename": filename,
                "error": f"文件过大，最大支持 {max_size_mb:.0f}MB"
            })
            continue

        # 保存并处理文件
        file_id = f"kb_{uuid.uuid4().hex[:12]}"
        file_path = upload_dir / f"{file_id}{ext}"

        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            logger.info(f"后端日志：文件保存成功: {file_path}")

            # 处理文档
            result = await knowledge_service.upload_document(
                file_path=str(file_path),
                file_filename=filename,
                user_id=user_id,
                tenant_id=tenant_id,
                source_type=source_type,
                sub_category=sub_category
            )

            if not result.get("success"):
                # 清理已保存的文件
                if file_path.exists():
                    file_path.unlink()
                errors.append({
                    "filename": filename,
                    "error": result.get("error", "处理失败")
                })
            else:
                results.append(UploadResponse(
                    document_id=result["document_id"],
                    title=result["title"],
                    total_chunks=result["total_chunks"],
                    status="success",
                    message=result["message"]
                ))

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：文档上传失败: {filename}: {e}")
            # 清理已保存的文件
            if file_path.exists():
                file_path.unlink()
            errors.append({
                "filename": filename,
                "error": str(e)
            })

    return BatchUploadResponse(
        success=len(errors) == 0,
        results=results,
        errors=errors
    )


@router.get("/documents")
async def list_documents(
    limit: int = 100,
    offset: int = 0,
    source_type: Optional[str] = None,
    sub_category: Optional[str] = None,
    http_request: Request = None
):
    """
    获取知识库文档列表

    - 支持分页查询
    - 支持按 source_type 过滤（顶级分类）
    - 支持按 sub_category 过滤（子分类）
    - 返回 {items, total} 格式
    - 仅做租户隔离，同一租户内所有用户共享可见
    """
    tenant_id = get_current_tenant_id()

    documents = knowledge_service.list_documents(
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
        source_type=source_type,
        sub_category=sub_category,
        global_view=_is_global_admin_view(http_request)
    )
    total = knowledge_service.count_documents(
        tenant_id=tenant_id, source_type=source_type, sub_category=sub_category,
        global_view=_is_global_admin_view(http_request)
    )

    return {
        "items": [DocumentResponse(**doc) for doc in documents],
        "total": total
    }


@router.post("/search_documents", response_model=SearchResponse)
async def search_documents(
    request: SearchRequest,
    http_request: Request = None
):
    """
    根据内容搜索文档（混合检索：向量 + FTS5 + RRF）

    - 支持关键词、语义搜索
    - 返回相关文档片段
    """
    user_id = None
    current_user = auth.get_current_user(http_request) if http_request else None
    if current_user:
        user_id = current_user.get("user_id")

    tenant_id = get_current_tenant_id()

    result = await knowledge_service.search_documents(
        query=request.query,
        user_id=user_id,
        tenant_id=tenant_id,
        top_k=request.top_k or 10,
        source_type=request.source_type,
        sub_category=request.sub_category,
        global_view=_is_global_admin_view(http_request),
    )

    if not result.get("success"):
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "result": result,
                "count": 0
            }
        )

    return SearchResponse(
        success=True,
        results=[SearchResultItem(**r) for r in result.get("results", [])],
        count=result.get("count", 0)
    )


@router.post("/documents/move")
async def move_documents(request: MoveDocumentsRequest, http_request: Request = None):
    """批量移动文档到目标分类（更新 source_type + sub_category，chunks/向量无需改动）"""
    tenant_id = get_current_tenant_id()
    result = knowledge_service.move_documents(
        tenant_id=tenant_id,
        doc_ids=request.doc_ids,
        source_type=request.source_type,
        sub_category=request.sub_category
    )
    if not result.get("success"):
        return JSONResponse(
            status_code=result.get("status", 400),
            content={
                "success": False,
                "error": result.get("error", "移动失败"),
                "debug": result.get("debug", result.get("error", ""))
            }
        )
    return JSONResponse(content={"success": True, "moved": result["moved"], "skipped": result["skipped"]})


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, http_request: Request = None):
    """
    删除知识库文档

    - 级联删除所有 chunks
    - 级联删除所有向量
    - 删除文件
    - 对象级租户校验：跨租户文档统一按「文档不存在」响应，不泄漏存在性
    """
    # 租户上下文由 TenantMiddleware 注入；service 层 SQL 本体再校验一次（防 TOCTOU）
    result = await knowledge_service.delete_document(
        doc_id, tenant_id=get_current_tenant_id(),
        global_view=_is_global_admin_view(http_request),
    )

    if not result.get("success"):
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": result.get("error", "删除失败"),
                "debug": result.get("error", "")
            }
        )

    return JSONResponse(content={"success": True, "message": result.get("message", "已删除")})


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: int, http_request: Request = None):
    """
    获取文档的所有分块（用于调试）

    - 对象级租户校验：跨租户文档统一按 404「文档不存在或没有分块」响应，不泄漏存在性
    """
    # 租户上下文由 TenantMiddleware 注入；service 层经 JOIN documents 校验归属（防 TOCTOU）
    chunks = knowledge_service.get_document_chunks(
        doc_id, tenant_id=get_current_tenant_id(),
        global_view=_is_global_admin_view(http_request),
    )

    if not chunks:
        raise HTTPException(status_code=404, detail="文档不存在或没有分块")

    return JSONResponse(content={
        "success": True,
        "doc_id": doc_id,
        "chunks": chunks,
        "count": len(chunks)
    })


def _has_shared_access(to_tenant_id: str, from_tenant_id: str, source_type: str) -> bool:
    """校验 B 租户（to_tenant_id）是否对 A 租户（from_tenant_id）该分类拥有已启用的共享下载权限。

    与检索侧 `_load_shared_ranges` 判定一致（租户级授权 ∩ 数字员工级启用），避免
    下载权限与检索权限不一致：能搜到却不能下载，或反之。
    同时满足才放行：
    1. tenant_knowledge_shares 中存在 (from_tenant_id -> to_tenant_id)
    2. subagent_knowledge_sources.sources 中某 JSONB 项含
       owner_tenant_id=from_tenant_id 且 source_type=source_type
    """
    if not to_tenant_id or not from_tenant_id or not source_type:
        return False
    try:
        conn_cm = get_db_connection()
        conn = conn_cm.__enter__()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM tenant_knowledge_shares
                    WHERE from_tenant_id = %s AND to_tenant_id = %s
                ) AND EXISTS (
                    SELECT 1 FROM subagent_knowledge_sources
                    WHERE tenant_id = %s
                      AND sources @> %s::jsonb
                ) AS granted
            """, (
                from_tenant_id, to_tenant_id,
                to_tenant_id,
                json.dumps([{"owner_tenant_id": from_tenant_id, "source_type": source_type}],
                           ensure_ascii=False),
            ))
            row = cursor.fetchone()
            return bool(row["granted"]) if row else False
        finally:
            conn_cm.__exit__(None, None, None)
    except Exception as e:
        logger.warning(f"后端日志：校验共享下载权限失败: {e}")
        return False


def _can_download_document(doc_row: dict, current_tenant_id: Optional[str]) -> bool:
    """下载权限判定：
    - 当前租户匹配文档 tenant_id -> 允许
    - 无租户上下文 -> 仅允许无租户文档
    - 否则校验共享访问（租户级授权 ∩ 数字员工级启用）
    """
    doc_tenant_id = doc_row.get("tenant_id")
    source_type = doc_row.get("source_type") or ""
    if current_tenant_id:
        if doc_tenant_id == current_tenant_id:
            return True
        if doc_tenant_id and source_type:
            return _has_shared_access(current_tenant_id, doc_tenant_id, source_type)
        return False
    return doc_tenant_id is None


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: int):
    """
    下载/预览原始文档文件

    - 新窗口打开或下载原文
    - 做租户隔离与共享范围校验：仅本租户文档或已启用共享分类可下载
    """
    # 查询文档的 file_path + 租户归属（用于权限校验）
    with knowledge_service._get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_path, title, tenant_id, source_type FROM documents WHERE id = %s",
            (doc_id,)
        )
        row = cursor.fetchone()

    # row 是 dict: {"file_path": ..., "title": ..., "tenant_id": ..., "source_type": ...}
    if not row or not row.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在或文件已丢失")

    if not _can_download_document(row, get_current_tenant_id()):
        raise HTTPException(status_code=403, detail="无权访问该文档")

    file_path = row["file_path"]
    title = row.get("title") or f"document_{doc_id}"

    if not os.path.exists(file_path):
        logger.warning(f"后端日志：下载文档失败，文件不存在: {file_path}")
        raise HTTPException(status_code=404, detail=f"文件不存在，可能已被删除 (路径: {file_path})")

    logger.info(f"后端日志：下载文档成功，文件路径: {file_path}")
    # 从实际文件路径提取扩展名，拼到下载文件名上
    ext = Path(file_path).suffix  # 如 ".xlsx"
    download_name = title if title.endswith(ext) else title + ext

    return FileResponse(
        path=file_path,
        filename=download_name,
        media_type='application/octet-stream'
    )
