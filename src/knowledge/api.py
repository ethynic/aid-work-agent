"""
知识库 API 路由
"""

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
    file_type: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    total_chunks: int = 0
    created_at: Optional[str] = None
    summary: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 10


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
    document_count: int = 0
    created_at: Optional[str] = None


class CreateCategoryRequest(BaseModel):
    source_type: str
    display_name: Optional[str] = None


class UpdateCategoryRequest(BaseModel):
    display_name: str


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
        display_name=request.display_name
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
    http_request: Request = None
):
    """
    上传知识库文档（单文件）

    - 支持格式：docx, xlsx, pptx, pdf, txt, md, json, yaml, yml, log, csv, xml, ini, properties, conf, config
    - 自动解析、分块、向量化
    - 返回文档 ID
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
            source_type=source_type
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
    http_request: Request = None
):
    """
    批量上传知识库文档（多文件）

    - 支持格式：docx, xlsx, pptx, pdf, txt, md, json, yaml, yml, log, csv, xml, ini, properties, conf, config
    - 自动解析、分块、向量化
    - 返回每个文件的处理结果和错误信息
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
                source_type=source_type
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
    http_request: Request = None
):
    """
    获取知识库文档列表

    - 支持分页查询
    - 支持按 source_type 过滤
    - 返回 {items, total} 格式
    - 仅做租户隔离，同一租户内所有用户共享可见
    """
    tenant_id = get_current_tenant_id()

    documents = knowledge_service.list_documents(
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
        source_type=source_type
    )
    total = knowledge_service.count_documents(tenant_id=tenant_id, source_type=source_type)

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
        top_k=request.top_k or 10
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


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int):
    """
    删除知识库文档

    - 级联删除所有 chunks
    - 级联删除所有向量
    - 删除文件
    """
    result = await knowledge_service.delete_document(doc_id)

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
async def get_document_chunks(doc_id: int):
    """
    获取文档的所有分块（用于调试）
    """
    chunks = knowledge_service.get_document_chunks(doc_id)

    if not chunks:
        raise HTTPException(status_code=404, detail="文档不存在或没有分块")

    return JSONResponse(content={
        "success": True,
        "doc_id": doc_id,
        "chunks": chunks,
        "count": len(chunks)
    })


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: int):
    """
    下载/预览原始文档文件

    - 新窗口打开或下载原文
    """
    # 查询文档的 file_path
    with knowledge_service._get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, title FROM documents WHERE id = %s", (doc_id,))
        row = cursor.fetchone()

    # row 是 dict: {"file_path": ..., "title": ...}，对应 SELECT file_path, title
    if not row or not row.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在或文件已丢失")

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
