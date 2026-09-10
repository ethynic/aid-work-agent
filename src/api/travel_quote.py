"""旅游报价定价数据管理 API

提供车辆、景点、酒店、餐标、导游、费用、淡旺季等定价数据的 CRUD 接口。
"""

import io
import json
import os
import re
import shutil
import tempfile
import uuid
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from src.api.auth import get_current_user

from src.db.database import get_db_connection
from src.services.export_service import ExportTableConfig
from src.services.export_service import export_table_to_excel as _svc_export_table_to_excel


def sanitize_error_info(error_msg: str) -> str:
    if not error_msg:
        return error_msg
    for pattern in [r'password["\s:=]+\S+', r'token["\s:=]+\S+', r'api[_-]?key["\s:=]+\S+']:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg


router = APIRouter(prefix="/api/v1/travel-quote", tags=["旅游报价管理"])


def _get_tenant_id(request: Request) -> str:
    tenant_id = getattr(request.state, 'tenant_id', None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")
    return tenant_id


def _resolve_travel_shared_tenant_ids(tenant_id: str, source_type: str) -> List[str]:
    """聚合该租户所有子智能体已启用共享的来源租户 ID（∩ 租户级授权），含本租户。

    独立 API（/search/hotels、/kb/attractions 等）无子智能体上下文，无法直接走
    load_shared_ranges(tenant_id, subagent_id)，故枚举该租户全部子智能体的共享
    配置后聚合去重，得到检索租户范围。授权撤销后交集为空，共享项自动失效。
    """
    from src.knowledge.retriever.tenant_range import load_shared_ranges

    tenant_ids: List[str] = [tenant_id]
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT subagent_name FROM subagent_knowledge_sources WHERE tenant_id = %s",
                (tenant_id,),
            )
            subagent_names = [r["subagent_name"] for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"[TravelQuote] 加载共享子智能体列表失败: {e}")
        subagent_names = []

    for subagent_name in subagent_names:
        for from_tenant_id, _st in load_shared_ranges(tenant_id, subagent_name, source_type):
            if from_tenant_id not in tenant_ids:
                tenant_ids.append(from_tenant_id)
    return tenant_ids


def _save_upload_to_storage(content: bytes, filename: str, tenant_id: str) -> str:
    """保存上传文件到 storage 目录，返回相对路径（storage/...）"""
    from pathlib import Path
    from src.core.storage import ensure_tenant_storage_dir
    import uuid

    upload_dir = Path(ensure_tenant_storage_dir(tenant_id, "knowledge"))

    ext = Path(filename).suffix.lower()
    file_id = f"kb_{uuid.uuid4().hex[:12]}"
    file_path = upload_dir / f"{file_id}{ext}"

    with open(file_path, "wb") as f:
        f.write(content)

    # 返回相对路径
    return str(file_path).replace("\\", "/")


class ApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


# ============================================================
# 通用 CRUD 辅助
# ============================================================

def _crud_list(table: str, tenant_id: str, filters: Dict[str, Any] = None,
               order_by: str = "id") -> List[Dict]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        where = "tenant_id = %s AND is_active = true"
        params = [tenant_id]
        if filters:
            for k, v in filters.items():
                if v is not None:
                    where += f" AND {k} = %s"
                    params.append(v)
        cursor.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order_by}", tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def _crud_get(table: str, record_id: int, tenant_id: str) -> Optional[Dict]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE id = %s AND tenant_id = %s", (record_id, tenant_id))
        row = cursor.fetchone()
        return dict(row) if row else None


_UUID_PREFIX_MAP = {
    "bs_travel_quote_vehicles": "tqv",
    "bs_travel_quote_meals": "tqm",
    "bs_travel_quote_guides": "tqg",
    "bs_travel_quote_fees": "tqf",
    "bs_travel_quote_seasons": "tqs",
}


def _crud_create(table: str, tenant_id: str, data: Dict) -> Dict:
    data['tenant_id'] = tenant_id
    if table in _UUID_PREFIX_MAP:
        data['uuid'] = f"{_UUID_PREFIX_MAP[table]}_{uuid.uuid4().hex[:12]}"
    cols = [k for k in data.keys() if k != 'id']
    vals = [data[k] for k in cols]
    placeholders = ', '.join(['%s'] * len(cols))
    col_names = ', '.join(cols)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) RETURNING *",
            tuple(vals)
        )
        conn.commit()
        return dict(cursor.fetchone())


def _crud_update(table: str, record_id: int, tenant_id: str, data: Dict) -> Optional[Dict]:
    cols = [k for k in data.keys() if k not in ('id', 'tenant_id', 'created_at')]
    if not cols:
        return _crud_get(table, record_id, tenant_id)
    set_clause = ', '.join(f"{k} = %s" for k in cols)
    vals = [data[k] for k in cols]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = %s AND tenant_id = %s RETURNING *",
            tuple(vals) + (record_id, tenant_id)
        )
        conn.commit()
        row = cursor.fetchone()
        return dict(row) if row else None


def _crud_delete(table: str, record_id: int, tenant_id: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {table} WHERE id = %s AND tenant_id = %s", (record_id, tenant_id))
        conn.commit()
        return cursor.rowcount > 0


# ============================================================
# 车辆管理
# ============================================================

@router.get("/vehicles")
async def list_vehicles(request: Request, region_name: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"region_name": region_name} if region_name else None
    return {"success": True, "data": _crud_list("bs_travel_quote_vehicles", tid, filters)}


@router.post("/vehicles")
async def create_vehicle(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_vehicles", tid, body)}


@router.put("/vehicles/{record_id}")
async def update_vehicle(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_vehicles", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/vehicles/{record_id}")
async def delete_vehicle(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_vehicles", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 景点知识库：删除和更新
# ============================================================

def _delete_kb_doc(doc_id: int, source_type: str, tenant_id: str) -> bool:
    """删除知识库文档（documents + chunks）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM documents WHERE id = %s AND source_type = %s AND tenant_id = %s",
                       (doc_id, source_type, tenant_id))
        if not cursor.fetchone():
            return False
        # 先删向量（依赖 chunks id）
        cursor.execute("SELECT id FROM chunks WHERE doc_id = %s", (doc_id,))
        chunk_ids = [r["id"] for r in cursor.fetchall()]
        if chunk_ids:
            cursor.execute("DELETE FROM chunks_vec WHERE chunk_id = ANY(%s)", (chunk_ids,))
        cursor.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
        cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.commit()
        return True


def _update_chunk_embedding(doc_id: int, chunk_index: int, text: str, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> None:
    """更新 chunk 文本并重新计算向量嵌入"""
    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    from attraction_retriever import AttractionRetriever
    retriever = AttractionRetriever()
    client = retriever._get_embedding_client()
    client.reset_usage()
    embedding = retriever._embed(text)

    # 补计费：chunk 重新向量化消耗（管理后台独立落库）
    if client.last_usage_tokens > 0:
        try:
            from src.services.session_record import record_admin_embedding_usage
            record_admin_embedding_usage(
                client,
                tenant_id=tenant_id,
                user_id=user_id,
                source_label="travel_quote_update_chunk",
            )
        except Exception:
            logger.opt(exception=True).debug("Failed to record chunk embedding usage")

    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM chunks WHERE doc_id = %s AND chunk_index = %s",
                       (doc_id, chunk_index))
        chunk_row = cursor.fetchone()
        if chunk_row:
            chunk_id = chunk_row["id"]
            cursor.execute("UPDATE chunks_vec SET embedding = %s::vector WHERE chunk_id = %s",
                           (embedding_str, chunk_id))
            conn.commit()


@router.delete("/kb/attractions/{doc_id}")
async def delete_attraction_kb(doc_id: int, request: Request):
    """删除景点知识库文档"""
    tenant_id = _get_tenant_id(request)
    if not _delete_kb_doc(doc_id, "attraction_resource", tenant_id):
        raise HTTPException(status_code=404, detail="景点文档不存在")
    return {"success": True}


@router.delete("/kb/attractions")
async def batch_delete_attractions_kb(request: Request, body: Dict[str, Any]):
    """批量删除景点知识库文档"""
    tenant_id = _get_tenant_id(request)
    doc_ids = body.get("doc_ids", [])
    if not doc_ids:
        return {"success": True, "deleted": 0}
    deleted = 0
    for doc_id in doc_ids:
        if _delete_kb_doc(doc_id, "attraction_resource", tenant_id):
            deleted += 1
    return {"success": True, "deleted": deleted}


@router.put("/kb/attractions/{doc_id}")
async def update_attraction_kb(doc_id: int, request: Request, body: Dict[str, Any]):
    """更新景点知识库文档"""
    tenant_id = _get_tenant_id(request)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, metadata FROM documents WHERE id = %s AND source_type = %s AND tenant_id = %s",
                       (doc_id, "attraction_resource", tenant_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="景点文档不存在")

        updates = []
        params = []
        if "title" in body:
            updates.append("title = %s")
            params.append(body["title"])
        if "metadata" in body:
            existing_meta = row["metadata"] or {}
            if isinstance(existing_meta, str):
                try:
                    existing_meta = json.loads(existing_meta)
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}
            merged = {**existing_meta, **body["metadata"]}
            updates.append("metadata = %s")
            params.append(json.dumps(merged, ensure_ascii=False))
        if updates:
            params.append(doc_id)
            cursor.execute(f"UPDATE documents SET {', '.join(updates)} WHERE id = %s", params)

        if "info" in body:
            cursor.execute("UPDATE chunks SET text = %s WHERE doc_id = %s AND chunk_index = 0",
                           (body["info"], doc_id))
        if "ticket_table" in body:
            cursor.execute("UPDATE chunks SET text = %s WHERE doc_id = %s AND chunk_index = 1",
                           (body["ticket_table"], doc_id))
        if "project_table" in body:
            cursor.execute("UPDATE chunks SET text = %s WHERE doc_id = %s AND chunk_index = 2",
                           (body["project_table"], doc_id))

        conn.commit()

    # 更新 info 后同步更新向量嵌入
    if "info" in body:
        try:
            _update_chunk_embedding(doc_id, 0, body["info"], tenant_id=tenant_id, user_id=getattr(request.state, "user_id", None))
        except Exception as e:
            logger.warning(f"更新景点向量嵌入失败 doc_id={doc_id}: {e}")

    return {"success": True}


# ============================================================
# 景点图片管理（单景点补图/换图/删图）
# ============================================================

_ALLOWED_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@router.patch("/kb/attractions/{doc_id}/images")
async def patch_attraction_images(
    request: Request,
    doc_id: int,
    action: str = File(...),                    # replace_cover | add_gallery | remove_cover | remove_gallery_file_id
    file: Optional[UploadFile] = File(None),    # replace_cover / add_gallery 时必传
    file_id: Optional[str] = File(None),        # remove_gallery_file_id 时必传（要删的 file_id）
):
    """管理景点知识库文档的图片资产（封面 / 图集）。

    action 取值：
      - ``replace_cover``：上传/替换封面图（file 必传）。旧封面 file_id 会被丢弃（不主动清理磁盘/Redis，由 24h TTL 兜底）
      - ``add_gallery``：追加图集（file 必传，可多次调用逐张追加）
      - ``remove_cover``：移除封面（无需 file/file_id）
      - ``remove_gallery_file_id``：从图集中删除指定 file_id（file_id 必传）

    Returns:
        ``{success, data: {cover, gallery}}`` —— 返回更新后的 cover / gallery file_id 列表
    """
    tenant_id = _get_tenant_id(request)
    user_id = None
    current_user = get_current_user(request)
    if current_user:
        user_id = current_user.get("user_id")

    # 1. 校验景点存在 + 取现有 metadata
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, metadata FROM documents WHERE id = %s AND source_type = %s AND tenant_id = %s",
            (doc_id, "attraction_resource", tenant_id),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="景点文档不存在")

    existing_meta = row["metadata"] or {}
    if isinstance(existing_meta, str):
        try:
            existing_meta = json.loads(existing_meta)
        except (json.JSONDecodeError, TypeError):
            existing_meta = {}

    images_meta = existing_meta.get("images") if isinstance(existing_meta, dict) else None
    if not isinstance(images_meta, dict):
        images_meta = {}
    cover_now = images_meta.get("cover")
    gallery_now = list(images_meta.get("gallery") or [])

    from src.core.image_asset import get_image_registry
    registry = get_image_registry()

    # 2. 按动作处理
    if action == "replace_cover":
        if not file:
            raise HTTPException(status_code=400, detail="replace_cover 需要上传 file")
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in _ALLOWED_IMG_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的图片格式（仅支持 {sorted(_ALLOWED_IMG_EXTS)}）",
            )
        # 落地到临时文件 → ImageRegistry.register（会 move 到租户目录）
        suffix = ext
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        try:
            ref = await registry.register(
                source_path=tmp_path,
                tenant_id=tenant_id,
                user_id=user_id,
                display_name=os.path.basename(file.filename or f"cover{suffix}"),
                source="knowledge_base",
                usage="thumbnail",
                source_ref=f"attraction_doc:{doc_id}",
                linked_doc_id=doc_id,
                move=True,
            )
            cover_now = ref.file_id
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    elif action == "add_gallery":
        if not file:
            raise HTTPException(status_code=400, detail="add_gallery 需要上传 file")
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in _ALLOWED_IMG_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的图片格式（仅支持 {sorted(_ALLOWED_IMG_EXTS)}）",
            )
        suffix = ext
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        try:
            ref = await registry.register(
                source_path=tmp_path,
                tenant_id=tenant_id,
                user_id=user_id,
                display_name=os.path.basename(file.filename or f"gallery{suffix}"),
                source="knowledge_base",
                usage="inline",
                source_ref=f"attraction_doc:{doc_id}",
                linked_doc_id=doc_id,
                move=True,
            )
            if ref.file_id not in gallery_now:
                gallery_now.append(ref.file_id)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    elif action == "remove_cover":
        cover_now = None

    elif action == "remove_gallery_file_id":
        if not file_id:
            raise HTTPException(status_code=400, detail="remove_gallery_file_id 需要 file_id 参数")
        gallery_now = [fid for fid in gallery_now if fid != file_id]

    else:
        raise HTTPException(
            status_code=400,
            detail="action 必须是 replace_cover / add_gallery / remove_cover / remove_gallery_file_id",
        )

    # 3. 写回 documents.metadata（合并 images 字段，保留其他 metadata 不变）
    new_images_meta = {}
    if cover_now:
        new_images_meta["cover"] = cover_now
    if gallery_now:
        new_images_meta["gallery"] = gallery_now
    merged_meta = {**existing_meta, "images": new_images_meta} if new_images_meta else {k: v for k, v in existing_meta.items() if k != "images"}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE documents SET metadata = %s WHERE id = %s",
            (json.dumps(merged_meta, ensure_ascii=False), doc_id),
        )
        conn.commit()

    logger.info(
        f"[AttractionImages] doc_id={doc_id} action={action} "
        f"cover={'set' if cover_now else 'none'} gallery_count={len(gallery_now)}"
    )

    return {
        "success": True,
        "data": {
            "cover": cover_now,
            "gallery": gallery_now,
        },
    }


# ============================================================
# 酒店知识库：删除和更新
# ============================================================

@router.delete("/kb/hotels/{doc_id}")
async def delete_hotel_kb(doc_id: int, request: Request):
    """删除酒店知识库文档"""
    tenant_id = _get_tenant_id(request)
    if not _delete_kb_doc(doc_id, "hotel_resource", tenant_id):
        raise HTTPException(status_code=404, detail="酒店文档不存在")
    return {"success": True}


@router.delete("/kb/hotels")
async def batch_delete_hotels_kb(request: Request, body: Dict[str, Any]):
    """批量删除酒店知识库文档"""
    tenant_id = _get_tenant_id(request)
    doc_ids = body.get("doc_ids", [])
    if not doc_ids:
        return {"success": True, "deleted": 0}
    deleted = 0
    for doc_id in doc_ids:
        if _delete_kb_doc(doc_id, "hotel_resource", tenant_id):
            deleted += 1
    return {"success": True, "deleted": deleted}


@router.put("/kb/hotels/{doc_id}")
async def update_hotel_kb(doc_id: int, request: Request, body: Dict[str, Any]):
    """更新酒店知识库文档"""
    tenant_id = _get_tenant_id(request)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, metadata FROM documents WHERE id = %s AND source_type = %s AND tenant_id = %s",
                       (doc_id, "hotel_resource", tenant_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="酒店文档不存在")

        updates = []
        params = []
        if "title" in body:
            updates.append("title = %s")
            params.append(body["title"])
        if "metadata" in body:
            existing_meta = row["metadata"] or {}
            if isinstance(existing_meta, str):
                try:
                    existing_meta = json.loads(existing_meta)
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}
            merged = {**existing_meta, **body["metadata"]}
            updates.append("metadata = %s")
            params.append(json.dumps(merged, ensure_ascii=False))
        if updates:
            params.append(doc_id)
            cursor.execute(f"UPDATE documents SET {', '.join(updates)} WHERE id = %s", params)

        if "info" in body:
            cursor.execute("UPDATE chunks SET text = %s WHERE doc_id = %s AND chunk_index = 0",
                           (body["info"], doc_id))
        if "price_table" in body:
            cursor.execute("UPDATE chunks SET text = %s WHERE doc_id = %s AND chunk_index = 1",
                           (body["price_table"], doc_id))

        conn.commit()

    # 更新 info 后同步更新向量嵌入
    if "info" in body:
        try:
            _update_chunk_embedding(doc_id, 0, body["info"], tenant_id=tenant_id, user_id=getattr(request.state, "user_id", None))
        except Exception as e:
            logger.warning(f"更新酒店向量嵌入失败 doc_id={doc_id}: {e}")

    return {"success": True}


# ============================================================
# 餐标管理
# ============================================================

@router.get("/meals")
async def list_meals(request: Request, region_name: str = Query(None), meal_tier: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {}
    if region_name:
        filters["region_name"] = region_name
    if meal_tier:
        filters["meal_tier"] = meal_tier
    return {"success": True, "data": _crud_list("bs_travel_quote_meals", tid, filters or None)}


@router.post("/meals")
async def create_meal(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_meals", tid, body)}


@router.put("/meals/{record_id}")
async def update_meal(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_meals", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/meals/{record_id}")
async def delete_meal(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_meals", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 导游管理
# ============================================================

@router.get("/guides")
async def list_guides(request: Request, region_name: str = Query(None), guide_type: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {}
    if region_name:
        filters["region_name"] = region_name
    if guide_type:
        filters["guide_type"] = guide_type
    return {"success": True, "data": _crud_list("bs_travel_quote_guides", tid, filters or None)}


@router.post("/guides")
async def create_guide(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_guides", tid, body)}


@router.put("/guides/{record_id}")
async def update_guide(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_guides", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/guides/{record_id}")
async def delete_guide(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_guides", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 其他费用管理
# ============================================================

@router.get("/fees")
async def list_fees(request: Request, fee_category: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"fee_category": fee_category} if fee_category else None
    return {"success": True, "data": _crud_list("bs_travel_quote_fees", tid, filters)}


@router.post("/fees")
async def create_fee(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_fees", tid, body)}


@router.put("/fees/{record_id}")
async def update_fee(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_fees", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/fees/{record_id}")
async def delete_fee(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_fees", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 淡旺季管理
# ============================================================

@router.get("/seasons")
async def list_seasons(request: Request):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_list("bs_travel_quote_seasons", tid, order_by="start_date")}


@router.post("/seasons")
async def create_season(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_seasons", tid, body)}


@router.put("/seasons/{record_id}")
async def update_season(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_seasons", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/seasons/{record_id}")
async def delete_season(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_seasons", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# Excel 批量导入
# ============================================================

SHEET_TABLE_MAP = {
    "车辆": ("bs_travel_quote_vehicles", [
        "region_name", "vehicle_type", "vehicle_type_label", "seats_max",
        "per_km_rate", "driver_meal_allowance",
        "driver_accommodation", "pricing_mode", "remark"
    ]),
    "景点": ("bs_travel_quote_attractions", [
        "region_name", "name", "category", "address", "open_time",
        "visit_duration_hours", "internal_transport_name", "internal_transport_price", "remark"
    ]),
    "门票": ("bs_travel_quote_tickets", [
        "attraction_id", "ticket_type", "ticket_type_label", "retail_price",
        "agency_price", "group_price", "group_min_people", "season_type", "remark"
    ]),
    "酒店": ("bs_travel_quote_hotels", [
        "region_name", "name", "star_rating", "star_rating_label",
        "address", "contact_phone", "remark"
    ]),
    "房型": ("bs_travel_quote_rooms", [
        "hotel_id", "room_type", "room_type_label", "max_occupancy", "bed_count",
        "retail_price", "agency_price", "includes_breakfast", "breakfast_count",
        "extra_bed_rate", "season_type", "remark"
    ]),
    "餐标": ("bs_travel_quote_meals", [
        "region_name", "meal_tier", "meal_tier_label", "meal_type", "meal_type_label",
        "price_per_person", "pax_per_table", "dishes_standard", "season_type", "remark"
    ]),
    "导游": ("bs_travel_quote_guides", [
        "region_name", "guide_type", "guide_type_label", "guide_level", "guide_level_label",
        "billing_method", "daily_rate", "trip_rate", "language_premium",
        "peak_season_multiplier", "season_type", "remark"
    ]),
    "费用": ("bs_travel_quote_fees", [
        "fee_name", "fee_category", "billing_method", "unit_price",
        "is_mandatory", "sort_order", "remark"
    ]),
    "淡旺季": ("bs_travel_quote_seasons", [
        "season_type", "season_type_label", "start_date", "end_date",
        "price_multiplier", "remark"
    ]),
}


def _coerce_value(col_name: str, value: Any) -> Any:
    """将 Excel 读取的值转换为数据库兼容类型"""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None

    numeric_cols = {
        "seats_min", "seats_max", "daily_rate", "overtime_rate", "overkm_rate",
        "driver_meal_allowance", "driver_accommodation", "attraction_id", "hotel_id",
        "retail_price", "agency_price", "group_price", "group_min_people",
        "max_occupancy", "bed_count", "breakfast_count", "extra_bed_rate",
        "price_per_person", "pax_per_table", "daily_rate", "trip_rate",
        "language_premium", "peak_season_multiplier", "unit_price", "sort_order",
        "price_multiplier", "visit_duration_hours",
    }
    bool_cols = {"includes_breakfast", "is_mandatory"}

    if col_name in numeric_cols:
        try:
            val = float(value)
            return int(val) if val == int(val) else val
        except (ValueError, TypeError):
            return None

    if col_name in bool_cols:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "1", "是", "yes"):
            return True
        return False

    return str(value).strip()


@router.get("/import/template")
async def download_import_template():
    """下载 Excel 导入模板（包含 10 个 Sheet 的列头）"""
    import openpyxl

    wb = openpyxl.Workbook()
    first = True
    for sheet_name, (_, columns) in SHEET_TABLE_MAP.items():
        if first:
            ws = wb.active
            ws.title = sheet_name
            first = False
        else:
            ws = wb.create_sheet(title=sheet_name)
        for col_idx, col_name in enumerate(columns, 1):
            ws.cell(row=1, column=col_idx, value=col_name)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=travel_quote_template.xlsx"}
    )


# ============================================================
# Excel 导出
# ============================================================

VEHICLES_EXPORT_COLS = [
    "uuid", "region_name", "vehicle_type", "vehicle_type_label", "seats_max",
    "per_km_rate", "driver_meal_allowance", "driver_accommodation",
    "pricing_mode", "remark",
]

MEALS_EXPORT_COLS = [
    "uuid", "region_name", "meal_tier", "meal_tier_label", "meal_type",
    "meal_type_label", "price_per_person", "pax_per_table",
    "dishes_standard", "season_type", "remark",
]

GUIDES_EXPORT_COLS = [
    "uuid", "region_name", "guide_type", "guide_type_label", "guide_level",
    "guide_level_label", "billing_method", "daily_rate", "trip_rate",
    "language_premium", "peak_season_multiplier", "season_type", "remark",
]

FEES_EXPORT_COLS = [
    "uuid", "fee_name", "fee_category", "billing_method", "unit_price",
    "is_mandatory", "sort_order", "remark",
]

SEASONS_EXPORT_COLS = [
    "uuid", "season_type", "season_type_label", "start_date", "end_date",
    "price_multiplier", "remark",
]

KB_DOC_EXPORT_COLS = [
    "uuid", "title", "source_type", "file_type", "file_path", "file_size",
    "total_chunks", "embedding_model", "summary", "metadata",
]
KB_DOC_IMPORT_COLS = [c for c in KB_DOC_EXPORT_COLS if c != "source_type"]

_EXPORT_TABLE_CONFIG: Dict[str, ExportTableConfig] = {
    "vehicles": ExportTableConfig(
        table="bs_travel_quote_vehicles",
        columns=VEHICLES_EXPORT_COLS,
        filename="车辆价格数据",
        where_clause="AND is_active = true",
    ),
    "meals": ExportTableConfig(
        table="bs_travel_quote_meals",
        columns=MEALS_EXPORT_COLS,
        filename="餐标价格数据",
        where_clause="AND is_active = true",
    ),
    "guides": ExportTableConfig(
        table="bs_travel_quote_guides",
        columns=GUIDES_EXPORT_COLS,
        filename="导游费用数据",
        where_clause="AND is_active = true",
    ),
    "fees": ExportTableConfig(
        table="bs_travel_quote_fees",
        columns=FEES_EXPORT_COLS,
        filename="其他费用数据",
        where_clause="AND is_active = true",
    ),
    "seasons": ExportTableConfig(
        table="bs_travel_quote_seasons",
        columns=SEASONS_EXPORT_COLS,
        filename="淡旺季数据",
        where_clause="AND is_active = true",
    ),
    "attractions": ExportTableConfig(
        table="documents",
        columns=KB_DOC_EXPORT_COLS,
        filename="景点知识库数据",
        where_clause="AND source_type = %s",
        where_params=("attraction_resource",),
    ),
    "hotels": ExportTableConfig(
        table="documents",
        columns=KB_DOC_EXPORT_COLS,
        filename="酒店知识库数据",
        where_clause="AND source_type = %s",
        where_params=("hotel_resource",),
    ),
}


# ============================================================
# 业务表 UUID 导入映射（Phase 6）
# ============================================================

from src.services.import_service import (
    ImportTableConfig,
    import_table_by_uuid as _svc_import_table_by_uuid,
)

_IMPORT_TABLE_CONFIG: Dict[str, ImportTableConfig] = {
    "vehicles": ImportTableConfig(
        table="bs_travel_quote_vehicles",
        columns=VEHICLES_EXPORT_COLS,
        numeric_cols={"seats_max", "per_km_rate", "driver_meal_allowance", "driver_accommodation"},
        bool_cols=set(),
        uuid_prefix="tqv",
    ),
    "meals": ImportTableConfig(
        table="bs_travel_quote_meals",
        columns=MEALS_EXPORT_COLS,
        numeric_cols={"price_per_person", "pax_per_table"},
        bool_cols=set(),
        uuid_prefix="tqm",
    ),
    "guides": ImportTableConfig(
        table="bs_travel_quote_guides",
        columns=GUIDES_EXPORT_COLS,
        numeric_cols={"daily_rate", "trip_rate", "language_premium", "peak_season_multiplier"},
        bool_cols=set(),
        uuid_prefix="tqg",
    ),
    "fees": ImportTableConfig(
        table="bs_travel_quote_fees",
        columns=FEES_EXPORT_COLS,
        numeric_cols={"unit_price", "sort_order"},
        bool_cols={"is_mandatory"},
        uuid_prefix="tqf",
    ),
    "seasons": ImportTableConfig(
        table="bs_travel_quote_seasons",
        columns=SEASONS_EXPORT_COLS,
        numeric_cols={"price_multiplier"},
        bool_cols=set(),
        uuid_prefix="tqs",
    ),
    "attractions": ImportTableConfig(
        table="documents",
        columns=KB_DOC_IMPORT_COLS,
        numeric_cols={"file_size", "total_chunks"},
        bool_cols=set(),
        json_cols={"metadata"},
        uuid_prefix="doc",
        fixed_values={"source_type": "attraction_resource"},
    ),
    "hotels": ImportTableConfig(
        table="documents",
        columns=KB_DOC_IMPORT_COLS,
        numeric_cols={"file_size", "total_chunks"},
        bool_cols=set(),
        json_cols={"metadata"},
        uuid_prefix="doc",
        fixed_values={"source_type": "hotel_resource"},
    ),
}


def _import_table_by_uuid(
    cfg: ImportTableConfig,
    tenant_id: str,
    file_content: bytes,
    operator: Optional[str] = None,
) -> dict:
    result = _svc_import_table_by_uuid(
        cfg=cfg,
        tenant_id=tenant_id,
        file_content=file_content,
        operator=operator,
        sanitize=sanitize_error_info,
    )
    return result.to_dict()


def _export_table(key: str, request: Request):
    tid = _get_tenant_id(request)
    return _svc_export_table_to_excel(_EXPORT_TABLE_CONFIG[key], tid)


@router.get("/vehicles/export")
async def export_vehicles(request: Request):
    return _export_table("vehicles", request)


@router.get("/meals/export")
async def export_meals(request: Request):
    return _export_table("meals", request)


@router.get("/guides/export")
async def export_guides(request: Request):
    return _export_table("guides", request)


@router.get("/fees/export")
async def export_fees(request: Request):
    return _export_table("fees", request)


@router.get("/seasons/export")
async def export_seasons(request: Request):
    return _export_table("seasons", request)


@router.get("/kb/attractions/export")
async def export_attractions(request: Request):
    return _export_table("attractions", request)


@router.get("/kb/hotels/export")
async def export_hotels(request: Request):
    return _export_table("hotels", request)


class DownloadTicketRequest(BaseModel):
    """下载票据签发请求"""
    path: str = Field(..., description="目标下载路径，如 /v1/travel-quote/vehicles/export")


_EXPORT_TICKET_PATH_TEMPLATE = re.compile(
    r"^/api/v1/travel-quote/((?:vehicles|meals|guides|fees|seasons|kb/attractions|kb/hotels)/export|import/template)$"
)


@router.post("/export_ticket")
async def create_export_ticket(body: DownloadTicketRequest, request: Request):
    """签发导出/模板下载票据（5 分钟有效），供前端直链原生下载

    浏览器导航下载无法携带 Authorization header，前端先经认证换取票据，
    再访问 download_path?ticket=xxx。path 必须在导出路径白名单内。
    """
    from src.core.download_ticket import TICKET_TTL_SECONDS, issue_download_ticket

    resource_path = body.path if body.path.startswith("/api") else "/api" + body.path.lstrip("/")
    if not _EXPORT_TICKET_PATH_TEMPLATE.match(resource_path):
        raise HTTPException(status_code=400, detail="不支持的下载路径")

    tenant_id = _get_tenant_id(request)
    user_id = getattr(request.state, "user_id", None)
    role = getattr(request.state, "user_role", None)

    ticket = issue_download_ticket(resource_path, tenant_id, user_id, role)
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}


# ============================================================
# 业务表 UUID 导入端点（Phase 6）
# ============================================================

@router.post("/vehicles/import")
async def import_vehicles(request: Request, file: UploadFile = File(...)):
    """上传车辆价格 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["vehicles"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/meals/import")
async def import_meals(request: Request, file: UploadFile = File(...)):
    """上传餐标价格 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["meals"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/guides/import")
async def import_guides(request: Request, file: UploadFile = File(...)):
    """上传导游费用 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["guides"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/fees/import")
async def import_fees(request: Request, file: UploadFile = File(...)):
    """上传其他费用 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["fees"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/seasons/import")
async def import_seasons(request: Request, file: UploadFile = File(...)):
    """上传淡旺季配置 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["seasons"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/kb/attractions/import")
async def import_attractions(request: Request, file: UploadFile = File(...)):
    """上传景点知识库 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["attractions"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/kb/hotels/import")
async def import_hotels(request: Request, file: UploadFile = File(...)):
    """上传酒店知识库 Excel，通过 UUID 匹配导入（存在则更新，不存在则新增）"""
    tid = _get_tenant_id(request)
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = await file.read()
    cfg = _IMPORT_TABLE_CONFIG["hotels"]
    result = _import_table_by_uuid(
        cfg, tid, content,
        operator=getattr(request.state, "user_id", None),
    )
    return {"success": True, "data": result}


@router.post("/import/excel")
async def import_excel(request: Request, file: UploadFile = File(...)):
    """上传 Excel 文件批量导入定价数据"""
    import openpyxl

    tid = _get_tenant_id(request)

    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")

    content = await file.read()
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)

    results = []
    total_imported = 0
    total_skipped = 0

    for sheet_name in wb.sheetnames:
        if sheet_name not in SHEET_TABLE_MAP:
            continue

        table_name, expected_cols = SHEET_TABLE_MAP[sheet_name]
        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)

        # 读取列头
        try:
            header = next(rows_iter)
        except StopIteration:
            continue

        header_list = [str(h).strip() if h else "" for h in header]
        col_indices = {}
        for col_name in expected_cols:
            if col_name in header_list:
                col_indices[col_name] = header_list.index(col_name)

        if not col_indices:
            continue

        imported = 0
        skipped = 0
        errors = []

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for row_num, row in enumerate(rows_iter, start=2):
                if not row or all(v is None or (isinstance(v, str) and v.strip() == "") for v in row):
                    continue

                data = {"tenant_id": tid}
                for col_name, col_idx in col_indices.items():
                    if col_idx < len(row):
                        data[col_name] = _coerce_value(col_name, row[col_idx])

                # 至少需要一个非空业务字段
                business_fields = {k: v for k, v in data.items() if k != "tenant_id" and v is not None}
                if not business_fields:
                    skipped += 1
                    continue

                cols = list(data.keys())
                vals = list(data.values())
                placeholders = ", ".join(["%s"] * len(cols))
                col_names = ", ".join(cols)

                try:
                    cursor.execute(
                        f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
                        tuple(vals)
                    )
                    imported += 1
                except Exception as e:
                    skipped += 1
                    error_msg = sanitize_error_info(str(e))
                    errors.append(f"第{row_num}行: {error_msg}")
                    logger.warning(f"Import error in {sheet_name} row {row_num}: {error_msg}")

            conn.commit()

        total_imported += imported
        total_skipped += skipped
        results.append({
            "sheet": sheet_name,
            "table": table_name,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10]  # 最多返回 10 条错误
        })

    wb.close()
    logger.info(f"[TravelQuoteImport] tenant={tid} imported={total_imported} skipped={total_skipped}")

    return {
        "success": True,
        "data": {
            "total_imported": total_imported,
            "total_skipped": total_skipped,
            "results": results
        }
    }


# ============================================================
# 车辆 Excel 智能导入（LLM 解析）
# ============================================================

@router.post("/import/vehicle-excel")
async def import_vehicle_excel(request: Request, file: UploadFile = File(...)):
    """上传车辆价格 Excel，LLM 智能解析后导入到 bs_travel_quote_vehicles 表"""
    tenant_id = _get_tenant_id(request)

    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        import sys
        from pathlib import Path
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
        if str(skill_dir) not in sys.path:
            sys.path.insert(0, str(skill_dir))

        from vehicle_excel_parser import VehicleExcelParser

        parser = VehicleExcelParser()
        all_parsed = await parser.parse_excel_sheets(tmp_path, tenant_id=tenant_id, user_id=getattr(request.state, "user_id", None))

        if not all_parsed:
            return {
                "success": True,
                "data": {"imported": 0, "skipped": 0, "errors": ["未解析到有效车辆数据"], "details": []},
            }

        imported = 0
        skipped = 0
        errors = []

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for i, record in enumerate(all_parsed):
                record["tenant_id"] = tenant_id

                cols = list(record.keys())
                vals = list(record.values())
                placeholders = ", ".join(["%s"] * len(cols))
                col_names = ", ".join(cols)

                try:
                    cursor.execute(
                        f"INSERT INTO bs_travel_quote_vehicles ({col_names}) VALUES ({placeholders})",
                        tuple(vals),
                    )
                    conn.commit()
                    imported += 1
                except Exception as e:
                    conn.rollback()
                    skipped += 1
                    error_msg = sanitize_error_info(str(e))
                    label = record.get("vehicle_type_label") or record.get("vehicle_type", f"#{i+1}")
                    errors.append(f"{label}: {error_msg}")
                    logger.warning(f"[VehicleExcelImport] 导入失败 {label}: {error_msg}")

        logger.info(f"[VehicleExcelImport] tenant={tenant_id} imported={imported} skipped={skipped}")

        return {
            "success": True,
            "data": {
                "imported": imported,
                "skipped": skipped,
                "errors": errors[:20],
                "details": [{"total": len(all_parsed), "imported": imported, "skipped": skipped}],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"[VehicleExcelImport] 导入失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ============================================================
# 酒店 Excel → 知识库导入
# ============================================================

@router.post("/import/hotel-excel-kb")
async def import_hotel_excel_to_kb(request: Request, file: UploadFile = File(...)):
    """上传酒店报价 Excel，逐 Sheet 解析后导入到向量知识库"""
    tenant_id = _get_tenant_id(request)

    user_id = None
    current_user = get_current_user(request)
    if current_user:
        user_id = current_user.get("user_id")

    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")

    # 1. 保存到持久目录（供 documents.file_path 引用）
    content = await file.read()
    file_rel_path = _save_upload_to_storage(content, file.filename or "unknown.xlsx", tenant_id)

    # 同时写临时文件供 openpyxl 读取
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 2. 逐 Sheet 解析并立即写入知识库
        import sys
        from pathlib import Path
        skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
        if str(skill_dir) not in sys.path:
            sys.path.insert(0, str(skill_dir))

        from hotel_excel_parser import HotelExcelParser
        from hotel_retriever import HotelRetriever

        parser = HotelExcelParser()
        retriever = HotelRetriever()
        source_filename = file.filename or "unknown.xlsx"

        imported = 0
        skipped = 0
        errors = []
        sheet_stats: Dict[str, Dict] = {}

        # 先扫描有效 Sheet
        import openpyxl
        wb = openpyxl.load_workbook(tmp_path, data_only=True, read_only=True)
        valid_sheets = []
        for sn in wb.sheetnames:
            if sn.startswith("WpsReserved"):
                continue
            ws = wb[sn]
            non_empty = sum(1 for row in ws.iter_rows(values_only=True) if any(v is not None for v in row))
            if non_empty >= 2:
                valid_sheets.append(sn)
        wb.close()

        total_sheets = len(valid_sheets)
        logger.info(f"[HotelExcelImport] 共 {total_sheets} 个有效 Sheet，开始逐个解析并导入")

        for idx, sheet_name in enumerate(valid_sheets, 1):
            logger.info(f"[HotelExcelImport] 处理 Sheet {idx}/{total_sheets}: '{sheet_name}'")

            try:
                # 解析单个 Sheet
                parsed = await parser.parse_sheet_by_name(tmp_path, sheet_name, tenant_id=tenant_id, user_id=user_id)
            except Exception as e:
                error_msg = sanitize_error_info(str(e))
                errors.append(f"Sheet '{sheet_name}' 解析失败: {error_msg}")
                logger.warning(f"[HotelExcelImport] Sheet '{sheet_name}' 解析失败: {error_msg}")
                if sheet_name not in sheet_stats:
                    sheet_stats[sheet_name] = {"total": 0, "imported": 0, "skipped": 0}
                continue

            if not parsed:
                continue

            # 逐条写入知识库
            for hotel in parsed:
                name = hotel.get("hotel_name", "").strip()
                if not name:
                    skipped += 1
                    continue

                region = hotel.get("region", "")
                info_text = hotel.get("info_text", "")
                price_table_text = hotel.get("price_table_text", "")
                metadata = hotel.get("metadata", {}) or {}

                if not info_text and not price_table_text:
                    skipped += 1
                    errors.append(f"{name}: 无有效数据")
                    continue

                # 查重
                try:
                    existing = retriever.search_by_name(tenant_id, name, top_k=1)
                    if existing and any(name in r.get("title", "") for r in existing):
                        skipped += 1
                        continue
                except Exception:
                    pass

                try:
                    retriever.import_hotel(
                        tenant_id=tenant_id,
                        hotel_name=name,
                        region=region,
                        info_text=info_text,
                        price_table_text=price_table_text,
                        metadata=metadata,
                        source_file=file_rel_path,
                        user_id=user_id,
                    )
                    imported += 1

                    stat_key = sheet_name
                    if stat_key not in sheet_stats:
                        sheet_stats[stat_key] = {"total": 0, "imported": 0, "skipped": 0}
                    sheet_stats[stat_key]["total"] += 1
                    sheet_stats[stat_key]["imported"] += 1

                except Exception as e:
                    skipped += 1
                    error_msg = sanitize_error_info(str(e))
                    errors.append(f"{name}: {error_msg}")
                    logger.warning(f"[HotelExcelImport] 导入失败 {name}: {error_msg}")

                    stat_key = sheet_name
                    if stat_key not in sheet_stats:
                        sheet_stats[stat_key] = {"total": 0, "imported": 0, "skipped": 0}
                    sheet_stats[stat_key]["total"] += 1
                    sheet_stats[stat_key]["skipped"] += 1

        details = [{"sheet": k, **v} for k, v in sheet_stats.items()]

        logger.info(
            f"[HotelExcelImport] tenant={tenant_id} "
            f"imported={imported} skipped={skipped}"
        )

        return {
            "success": True,
            "data": {
                "total_hotels": imported + skipped,
                "imported": imported,
                "skipped": skipped,
                "errors": errors[:20],
                "details": details,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"[HotelExcelImport] 导入失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ============================================================
# 景点 Excel → 知识库导入
# ============================================================

# 允许的图片扩展名（zip 包导入图片时校验）
ALLOWED_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _resolve_image_path(images_dir: Optional[str], filename: Optional[str]) -> Optional[str]:
    """校验并解析图片文件路径，返回绝对路径或 None（不合法/不存在时跳过）

    安全力：filename 来自 LLM 解析结果，可能含路径遍历（../），
    用 os.path.basename 取纯文件名后再拼路径，确保不会逃出 images_dir。
    """
    if not images_dir or not filename:
        return None
    # 防 LLM 输出路径遍历：只取 basename（"../xxx.jpg" → "xxx.jpg"；"a/b.jpg" → "b.jpg"）
    safe_filename = os.path.basename(filename)
    if not safe_filename:
        return None
    ext = os.path.splitext(safe_filename)[1].lower()
    if ext not in ALLOWED_IMG_EXTS:
        logger.warning(
            f"[AttractionExcelImport] 不支持的图片格式 {safe_filename}"
            f"（仅支持 {sorted(ALLOWED_IMG_EXTS)}）"
        )
        return None
    full_path = os.path.join(images_dir, safe_filename)
    if not os.path.isfile(full_path):
        logger.warning(f"[AttractionExcelImport] 图片文件不存在: {safe_filename}")
        return None
    return full_path


async def _process_parsed_attractions(
    parser,
    retriever,
    xlsx_path: str,
    tenant_id: str,
    user_id: Optional[str],
    file_rel_path: str,
    images_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    逐 Sheet 解析 Excel 并写入知识库（xlsx 模式和 zip 模式共用）。

    Args:
        parser: AttractionExcelParser 实例
        retriever: AttractionRetriever 实例
        xlsx_path: Excel 文件路径（绝对路径）
        tenant_id: 租户 ID
        user_id: 用户 ID（可能为 None）
        file_rel_path: 持久化文件相对路径（写入 documents.file_path）
        images_dir: 图片目录绝对路径（zip 模式传入，xlsx 模式传 None）
    """
    imported = 0
    skipped = 0
    errors: List[str] = []
    sheet_stats: Dict[str, Dict] = {}

    # 先扫描有效 Sheet
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    valid_sheets = []
    for sn in wb.sheetnames:
        if sn.startswith("WpsReserved"):
            continue
        ws = wb[sn]
        non_empty = sum(1 for row in ws.iter_rows(values_only=True) if any(v is not None for v in row))
        if non_empty >= 2:
            valid_sheets.append(sn)
    wb.close()

    total_sheets = len(valid_sheets)
    logger.info(f"[AttractionExcelImport] 共 {total_sheets} 个有效 Sheet，开始逐个解析并导入")

    for idx, sheet_name in enumerate(valid_sheets, 1):
        logger.info(f"[AttractionExcelImport] 处理 Sheet {idx}/{total_sheets}: '{sheet_name}'")

        try:
            parsed = await parser.parse_sheet_by_name(xlsx_path, sheet_name, tenant_id=tenant_id, user_id=user_id)
        except Exception as e:
            error_msg = sanitize_error_info(str(e))
            errors.append(f"Sheet '{sheet_name}' 解析失败: {error_msg}")
            logger.warning(f"[AttractionExcelImport] Sheet '{sheet_name}' 解析失败: {error_msg}")
            if sheet_name not in sheet_stats:
                sheet_stats[sheet_name] = {"total": 0, "imported": 0, "skipped": 0}
            continue

        if not parsed:
            continue

        for attraction in parsed:
            name = attraction.get("attraction_name", "").strip()
            if not name:
                skipped += 1
                continue

            region = attraction.get("region", "")
            info_text = attraction.get("info_text", "")
            ticket_table_text = attraction.get("ticket_table_text", "")
            project_table_text = attraction.get("project_table_text", "")
            metadata = attraction.get("metadata", {}) or {}

            if not info_text and not ticket_table_text and not project_table_text:
                skipped += 1
                errors.append(f"{name}: 无有效数据")
                continue

            # 查重
            try:
                existing = retriever.search_by_name(tenant_id, name, top_k=1)
                if existing and any(name in r.get("title", "") for r in existing):
                    skipped += 1
                    continue
            except Exception:
                pass

            # 解析图片路径（zip 模式且 images_dir 存在时）
            cover_path = _resolve_image_path(images_dir, attraction.get("cover_image_filename"))
            gallery_paths: List[str] = []
            for gf in attraction.get("gallery_image_filenames") or []:
                gp = _resolve_image_path(images_dir, gf)
                if gp:
                    gallery_paths.append(gp)

            try:
                await retriever.import_attraction(
                    tenant_id=tenant_id,
                    attraction_name=name,
                    region=region,
                    info_text=info_text,
                    ticket_table_text=ticket_table_text,
                    project_table_text=project_table_text,
                    metadata=metadata,
                    source_file=file_rel_path,
                    user_id=user_id,
                    cover_image_path=cover_path,
                    gallery_image_paths=gallery_paths or None,
                )
                imported += 1

                stat_key = sheet_name
                if stat_key not in sheet_stats:
                    sheet_stats[stat_key] = {"total": 0, "imported": 0, "skipped": 0}
                sheet_stats[stat_key]["total"] += 1
                sheet_stats[stat_key]["imported"] += 1

            except Exception as e:
                skipped += 1
                error_msg = sanitize_error_info(str(e))
                errors.append(f"{name}: {error_msg}")
                logger.warning(f"[AttractionExcelImport] 导入失败 {name}: {error_msg}")

                stat_key = sheet_name
                if stat_key not in sheet_stats:
                    sheet_stats[stat_key] = {"total": 0, "imported": 0, "skipped": 0}
                sheet_stats[stat_key]["total"] += 1
                sheet_stats[stat_key]["skipped"] += 1

    details = [{"sheet": k, **v} for k, v in sheet_stats.items()]

    logger.info(
        f"[AttractionExcelImport] tenant={tenant_id} "
        f"imported={imported} skipped={skipped}"
    )

    return {
        "success": True,
        "data": {
            "total_attractions": imported + skipped,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:20],
            "details": details,
        },
    }


@router.post("/import/attraction-excel-kb")
async def import_attraction_excel_to_kb(request: Request, file: UploadFile = File(...)):
    """上传景点报价 Excel（.xlsx）或包含图片的 zip 包，解析后导入到向量知识库

    zip 包结构约定：
        attraction_data.zip
        ├── attractions.xlsx     # 必须在根目录
        └── images/              # 可选，景点图片目录
            ├── 黄果树瀑布.jpg
            └── ...

    xlsx 模式（仅 .xlsx 文件）保持向后兼容，不导入图片。
    """
    import io
    import zipfile
    import shutil

    tenant_id = _get_tenant_id(request)

    user_id = None
    current_user = get_current_user(request)
    if current_user:
        user_id = current_user.get("user_id")

    filename = (file.filename or "unknown").lower()
    is_zip = filename.endswith(".zip")
    is_xlsx = filename.endswith(".xlsx")
    if not (is_zip or is_xlsx):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 或 .zip 文件")

    content = await file.read()

    # 共享：准备 skill scripts 路径与 parser/retriever 实例
    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    from attraction_excel_parser import AttractionExcelParser
    from attraction_retriever import AttractionRetriever

    parser = AttractionExcelParser()
    retriever = AttractionRetriever()

    # ==================== xlsx 模式（向后兼容） ====================
    if is_xlsx:
        file_rel_path = _save_upload_to_storage(content, file.filename or "unknown.xlsx", tenant_id)

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            return await _process_parsed_attractions(
                parser=parser,
                retriever=retriever,
                xlsx_path=tmp_path,
                tenant_id=tenant_id,
                user_id=user_id,
                file_rel_path=file_rel_path,
                images_dir=None,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.opt(exception=True).error(f"[AttractionExcelImport] 导入失败: {e}")
            return {"success": False, "error": sanitize_error_info(str(e))}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ==================== zip 模式 ====================
    tmpdir = tempfile.mkdtemp(prefix="attraction_zip_")
    try:
        # 解压（含 zip slip 防护：校验每个 member 解压后的绝对路径仍在 tmpdir 内）
        try:
            tmpdir_abs = os.path.abspath(tmpdir)
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for member in zf.namelist():
                    target = os.path.abspath(os.path.join(tmpdir, member))
                    if not target.startswith(tmpdir_abs + os.sep) and target != tmpdir_abs:
                        return {"success": False, "error": f"zip 包含非法路径（疑似 zip slip）: {member}"}
                zf.extractall(tmpdir)
        except zipfile.BadZipFile as e:
            return {"success": False, "error": f"zip 文件损坏或格式错误: {sanitize_error_info(str(e))}"}

        # 找根目录 xlsx（只看 tmpdir 直属文件，不递归子目录）
        root_files = [f for f in os.listdir(tmpdir)
                      if os.path.isfile(os.path.join(tmpdir, f)) and f.lower().endswith(".xlsx")]
        if not root_files:
            return {"success": False, "error": "zip 包根目录未找到 .xlsx 文件"}
        xlsx_filename = root_files[0]
        xlsx_path = os.path.join(tmpdir, xlsx_filename)

        # 校验 images/ 目录
        images_dir = os.path.join(tmpdir, "images")
        has_images_dir = os.path.isdir(images_dir)
        if not has_images_dir:
            logger.warning("[AttractionExcelImport] zip 包内无 images/ 目录，仅导入 Excel（不导图）")
            images_dir = None  # type: ignore

        # 把 xlsx 持久化到 storage（供 documents.file_path 引用）
        with open(xlsx_path, "rb") as f:
            xlsx_bytes = f.read()
        file_rel_path = _save_upload_to_storage(xlsx_bytes, xlsx_filename, tenant_id)

        try:
            return await _process_parsed_attractions(
                parser=parser,
                retriever=retriever,
                xlsx_path=xlsx_path,
                tenant_id=tenant_id,
                user_id=user_id,
                file_rel_path=file_rel_path,
                images_dir=images_dir,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.opt(exception=True).error(f"[AttractionExcelImport] 导入失败: {e}")
            return {"success": False, "error": sanitize_error_info(str(e))}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 知识库模式：酒店/景点搜索
# ============================================================

@router.get("/search/hotels")
async def search_hotels(request: Request, q: str = Query(..., min_length=1), top_k: int = Query(5)):
    """向量搜索酒店"""
    tenant_id = _get_tenant_id(request)

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from hotel_retriever import HotelRetriever
        retriever = HotelRetriever()
        shared_tenant_ids = _resolve_travel_shared_tenant_ids(tenant_id, HotelRetriever.SOURCE_TYPE)
        results = retriever.search(tenant_id, q, top_k, shared_tenant_ids=shared_tenant_ids)
        return {"success": True, "data": results}
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteSearch] 酒店搜索失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


@router.get("/kb/hotels")
async def list_hotels_kb(request: Request, limit: int = Query(200), offset: int = Query(0)):
    """列出所有酒店知识库文档"""
    tenant_id = _get_tenant_id(request)

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from hotel_retriever import HotelRetriever
        retriever = HotelRetriever()
        shared_tenant_ids = _resolve_travel_shared_tenant_ids(tenant_id, HotelRetriever.SOURCE_TYPE)
        result = retriever.list_all(tenant_id, limit, offset, shared_tenant_ids=shared_tenant_ids)
        return {"success": True, "data": result}
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteKB] 列出酒店失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


@router.get("/kb/attractions")
async def list_attractions_kb(request: Request, limit: int = Query(200), offset: int = Query(0)):
    """列出所有景点知识库文档"""
    tenant_id = _get_tenant_id(request)

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from attraction_retriever import AttractionRetriever
        retriever = AttractionRetriever()
        shared_tenant_ids = _resolve_travel_shared_tenant_ids(tenant_id, AttractionRetriever.SOURCE_TYPE)
        result = retriever.list_all(tenant_id, limit, offset, shared_tenant_ids=shared_tenant_ids)
        return {"success": True, "data": result}
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteKB] 列出景点失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


@router.get("/search/attractions")
async def search_attractions(request: Request, q: str = Query(..., min_length=1), top_k: int = Query(5)):
    """向量搜索景点"""
    tenant_id = _get_tenant_id(request)

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from attraction_retriever import AttractionRetriever
        retriever = AttractionRetriever()
        shared_tenant_ids = _resolve_travel_shared_tenant_ids(tenant_id, AttractionRetriever.SOURCE_TYPE)
        results = retriever.search(tenant_id, q, top_k, shared_tenant_ids=shared_tenant_ids)
        return {"success": True, "data": results}
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteSearch] 景点搜索失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


# ============================================================
# 知识库模式：酒店/景点文档详情
# ============================================================

@router.get("/kb/hotels/{doc_id}")
async def get_hotel_kb(doc_id: int, request: Request):
    """获取知识库中的酒店详情（信息摘要 + 价格表）"""
    _get_tenant_id(request)  # 验证租户身份

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from hotel_retriever import HotelRetriever
        retriever = HotelRetriever()
        info = retriever.get_hotel_info(doc_id)
        price_table = retriever.get_price_table(doc_id)

        if not info:
            raise HTTPException(status_code=404, detail="酒店文档不存在")

        # 获取文档元信息（标题、来源文件等）
        from src.db.database import get_db_connection
        doc_meta = {}
        with get_db_connection() as conn:
            conn.execute(
                "SELECT title, file_path, metadata FROM documents WHERE id = %s",
                (doc_id,)
            )
            row = conn.fetchone()
            if row:
                doc_meta = {
                    "title": row["title"],
                    "source_file": row["file_path"] or "",
                    "metadata": row["metadata"] if isinstance(row["metadata"], dict) else {},
                }

        return {
            "success": True,
            "data": {
                "doc_id": doc_id,
                "info": info,
                "price_table": price_table,
                **doc_meta,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteKB] 获取酒店详情失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


@router.get("/kb/attractions/{doc_id}")
async def get_attraction_kb(doc_id: int, request: Request):
    """获取知识库中的景点详情（信息摘要 + 门票价格表）"""
    _get_tenant_id(request)  # 验证租户身份

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from attraction_retriever import AttractionRetriever
        retriever = AttractionRetriever()
        info = retriever.get_attraction_info(doc_id)
        ticket_table = retriever.get_ticket_table(doc_id)
        project_table = retriever.get_project_table(doc_id)

        if not info:
            raise HTTPException(status_code=404, detail="景点文档不存在")

        # 获取文档元信息（标题、来源文件等）
        from src.db.database import get_db_connection
        doc_meta = {}
        with get_db_connection() as conn:
            conn.execute(
                "SELECT title, file_path, metadata FROM documents WHERE id = %s",
                (doc_id,)
            )
            row = conn.fetchone()
            if row:
                doc_meta = {
                    "title": row["title"],
                    "source_file": row["file_path"] or "",
                    "metadata": row["metadata"] if isinstance(row["metadata"], dict) else {},
                }

        return {
            "success": True,
            "data": {
                "doc_id": doc_id,
                "info": info,
                "ticket_table": ticket_table,
                "project_table": project_table,
                **doc_meta,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteKB] 获取景点详情失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


# ============================================================
# 知识库模式：导入酒店/景点到向量知识库
# ============================================================

class ImportHotelKBRequest(BaseModel):
    hotel_name: str = Field(..., description="酒店名称")
    region: str = Field("", description="区域")
    info_text: str = Field(..., description="酒店信息摘要（用于向量化）")
    price_table_text: str = Field(..., description="价格明细表（不向量化）")
    metadata: Optional[Dict[str, Any]] = Field(None, description="额外元信息")


class ImportAttractionKBRequest(BaseModel):
    attraction_name: str = Field(..., description="景点名称")
    region: str = Field("", description="区域")
    info_text: str = Field(..., description="景点信息摘要（用于向量化）")
    ticket_table_text: str = Field(..., description="门票价格表（不向量化）")
    metadata: Optional[Dict[str, Any]] = Field(None, description="额外元信息")


@router.post("/import/hotels-kb")
async def import_hotels_kb(request: Request, body: ImportHotelKBRequest):
    """导入酒店到向量知识库"""
    tenant_id = _get_tenant_id(request)

    user_id = None
    current_user = get_current_user(request)
    if current_user:
        user_id = current_user.get("user_id")

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from hotel_retriever import HotelRetriever
        retriever = HotelRetriever()
        doc_id = retriever.import_hotel(
            tenant_id=tenant_id,
            hotel_name=body.hotel_name,
            region=body.region,
            info_text=body.info_text,
            price_table_text=body.price_table_text,
            metadata=body.metadata,
            user_id=user_id,
        )
        return {"success": True, "data": {"doc_id": doc_id}}
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteKB] 导入酒店失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}


@router.post("/import/attractions-kb")
async def import_attractions_kb(request: Request, body: ImportAttractionKBRequest):
    """导入景点到向量知识库"""
    tenant_id = _get_tenant_id(request)

    user_id = None
    current_user = get_current_user(request)
    if current_user:
        user_id = current_user.get("user_id")

    import sys
    from pathlib import Path
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "travel-quote" / "scripts"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    try:
        from attraction_retriever import AttractionRetriever
        retriever = AttractionRetriever()
        doc_id = await retriever.import_attraction(
            tenant_id=tenant_id,
            attraction_name=body.attraction_name,
            region=body.region,
            info_text=body.info_text,
            ticket_table_text=body.ticket_table_text,
            metadata=body.metadata,
        )
        return {"success": True, "data": {"doc_id": doc_id}}
    except Exception as e:
        logger.opt(exception=True).error(f"[TravelQuoteKB] 导入景点失败: {e}")
        return {"success": False, "error": sanitize_error_info(str(e))}
