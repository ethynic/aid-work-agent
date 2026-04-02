"""
知识库 API 路由
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger

from src.api import auth
from src.knowledge.service import knowledge_service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".pdf"}


class UploadResponse(BaseModel):
    document_id: int
    title: str
    total_chunks: int
    status: str
    message: str


class DocumentResponse(BaseModel):
    id: int
    title: str
    source_type: str
    file_type: str
    file_size: Optional[int]
    total_chunks: int
    created_at: str


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    http_request=None
):
    """
    上传知识库文档

    - 支持格式：docx, xlsx, pptx, pdf
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

    # 获取用户 ID
    user_id = None
    current_user = auth.get_current_user(http_request) if http_request else None
    if current_user:
        user_id = current_user.get("user_id")

    # 保存文件
    file_id = f"kb_{uuid.uuid4().hex[:12]}"
    upload_dir = Path("./uploads/knowledge")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{file_id}{ext}"

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"后端日志：文件保存成功: {file_path}")

        # 处理文档
        result = await knowledge_service.upload_document(
            file_path=str(file_path),
            file_filename=file.filename or "unknown",
            user_id=user_id
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
        logger.error(f"后端日志：文档上传失败: {e}", exc_info=True)
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


@router.get("/documents", response_model=List[DocumentResponse])
async def list_documents(
    limit: int = 100,
    offset: int = 0,
    http_request=None
):
    """
    获取知识库文档列表

    - 支持分页查询
    """
    user_id = None
    current_user = auth.get_current_user(http_request) if http_request else None
    if current_user:
        user_id = current_user.get("user_id")

    documents = knowledge_service.list_documents(
        user_id=user_id,
        limit=limit,
        offset=offset
    )

    return [DocumentResponse(**doc) for doc in documents]


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
