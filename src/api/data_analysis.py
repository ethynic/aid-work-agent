"""
数据分析 - 数据源导入 API

提供数据连接器管理、Excel 上传解析、Schema 管理和关联关系推断功能。
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.db.database import get_db_connection
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
from src.knowledge.vector_db.vector_db import get_vector_db
from src.saas.context import get_current_tenant_id
from src.services.data_analysis.crypto import decrypt_password, encrypt_password
from src.services.data_analysis.db_connector import DatabaseConnector
from src.services.data_analysis.schema_extractor import SchemaExtractor
from src.services.data_analysis.schema_saver import generate_schema_text, save_schema_to_knowledge
from src.services.data_analysis.sheet_parser import SheetParser

router = APIRouter(prefix="/api/data-analysis", tags=["数据分析"])

# 全局实例
sheet_parser = SheetParser()
schema_extractor = SchemaExtractor()
db_connector = DatabaseConnector()


# ============== 敏感信息过滤 ==============

SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'passwd["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
    r'(mysql|postgresql)://\S+:\S+@',
]


def sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg
    for pattern in SENSITIVE_PATTERNS:
        error_msg = re.sub(
            pattern,
            lambda m: "[已过滤]",
            error_msg,
            flags=re.IGNORECASE,
        )
    return error_msg


def _error_response(error: str, debug: str = "", status_code: int = 500) -> JSONResponse:
    """构建标准错误响应"""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": error,
            "debug": sanitize_error_info(debug),
        },
    )


# ============== Pydantic Models ==============


class ConnectorCreate(BaseModel):
    name: str = Field(..., description="连接器名称")
    db_type: str = Field(..., description="数据库类型: mysql / postgresql")
    host: str = Field(..., description="主机地址")
    port: int = Field(..., description="端口")
    database_name: str = Field(..., description="数据库名")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class SchemaSave(BaseModel):
    table_name: str = Field(..., description="表名")
    description: str = Field("", description="表描述")
    source_info: str = Field("", description="来源信息（文件名或连接描述）")
    columns: List[Dict[str, Any]] = Field(..., description="列定义列表")
    connector_id: Optional[str] = None
    source: Optional[dict] = Field(None, description="数据源定位信息，如 {type:'excel', file_path:'...', sheet_name:'...'} 或 {type:'database', connector_id:'...', db_table_name:'...'}")


class RelationItem(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relation_type: str = "one_to_many"
    description: str = ""


class RelationBatch(BaseModel):
    relations: List[RelationItem]


# ============== Connector CRUD ==============


@router.post("/connectors")
async def create_connector(req: ConnectorCreate, request: Request):
    """创建数据连接器（先测试连接，再保存）"""
    tenant_id = get_current_tenant_id()

    # 构建连接配置并测试
    config = {
        "db_type": req.db_type,
        "host": req.host,
        "port": req.port,
        "database_name": req.database_name,
        "username": req.username,
        "password": req.password,
    }

    try:
        test_result = await asyncio.to_thread(db_connector.test_connection, config)
        if not test_result["success"]:
            return _error_response(
                f"连接测试失败: {test_result['error']}",
                debug=test_result["error"],
                status_code=400,
            )
    except Exception as e:
        return _error_response("连接测试失败", debug=str(e), status_code=400)

    # 加密密码后保存
    encrypted_pwd = encrypt_password(req.password)

    try:
        def _save():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO data_connectors
                        (tenant_id, name, db_type, host, port, database_name, username, password_encrypted)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (
                        tenant_id,
                        req.name,
                        req.db_type,
                        req.host,
                        req.port,
                        req.database_name,
                        req.username,
                        encrypted_pwd,
                    ),
                )
                row = cursor.fetchone()
                conn.commit()
                return dict(row)

        result = await asyncio.to_thread(_save)
        return {"success": True, "connector": {**result, "name": req.name, "db_type": req.db_type}}

    except Exception as e:
        logger.error(f"创建连接器失败: {e}", exc_info=True)
        return _error_response("创建连接器失败", debug=str(e))


@router.get("/connectors")
async def list_connectors(request: Request):
    """列出当前租户的所有连接器"""
    tenant_id = get_current_tenant_id()

    def _list():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, name, db_type, host, port, database_name, username, created_at
                FROM data_connectors
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id,),
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
                results.append(r)
            return results

    try:
        connectors = await asyncio.to_thread(_list)
        return {"success": True, "connectors": connectors}
    except Exception as e:
        logger.error(f"获取连接器列表失败: {e}", exc_info=True)
        return _error_response("获取连接器列表失败", debug=str(e))


@router.put("/connectors/{connector_id}")
async def update_connector(connector_id: str, req: ConnectorUpdate, request: Request):
    """更新连接器"""
    tenant_id = get_current_tenant_id()

    updates = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.host is not None:
        updates["host"] = req.host
    if req.port is not None:
        updates["port"] = req.port
    if req.database_name is not None:
        updates["database_name"] = req.database_name
    if req.username is not None:
        updates["username"] = req.username
    if req.password is not None:
        updates["password_encrypted"] = encrypt_password(req.password)

    if not updates:
        return _error_response("没有需要更新的字段", status_code=400)

    try:
        def _update():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
                values = list(updates.values()) + [connector_id, tenant_id]
                cursor.execute(
                    f"""
                    UPDATE data_connectors
                    SET {set_clause}, updated_at = NOW()
                    WHERE id = %s AND tenant_id = %s
                    """,
                    values,
                )
                conn.commit()
                return cursor.rowcount > 0

        updated = await asyncio.to_thread(_update)
        if not updated:
            return _error_response("连接器不存在或无权限", status_code=404)
        return {"success": True, "message": "更新成功"}

    except Exception as e:
        logger.error(f"更新连接器失败: {e}", exc_info=True)
        return _error_response("更新连接器失败", debug=str(e))


@router.delete("/connectors/{connector_id}")
async def delete_connector(connector_id: str, request: Request):
    """删除连接器及其关联的 schema"""
    tenant_id = get_current_tenant_id()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 先查找关联的 schema 文档
            cursor.execute(
                """
                SELECT id FROM documents
                WHERE tenant_id = %s
                  AND source_type = 'data-analysis-metadata'
                  AND metadata::text LIKE %s
                """,
                (tenant_id, f'%"connector_id": {connector_id}%'),
            )
            doc_ids = [row["id"] for row in cursor.fetchall()]

            # 删除关联文档的向量和 chunks
            if doc_ids:
                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.delete_by_doc(doc_ids[0] if len(doc_ids) == 1 else doc_ids[0])
                for doc_id in doc_ids:
                    await vector_db.delete_by_doc(doc_id)
                placeholders = ",".join(["%s"] * len(doc_ids))
                cursor.execute(
                    f"DELETE FROM chunks WHERE doc_id IN ({placeholders})",
                    doc_ids,
                )
                cursor.execute(
                    f"DELETE FROM documents WHERE id IN ({placeholders})",
                    doc_ids,
                )

            # 删除连接器
            cursor.execute(
                "DELETE FROM data_connectors WHERE id = %s AND tenant_id = %s",
                (connector_id, tenant_id),
            )
            conn.commit()

        return {"success": True, "message": "删除成功"}

    except Exception as e:
        logger.error(f"删除连接器失败: {e}", exc_info=True)
        return _error_response("删除连接器失败", debug=str(e))


@router.post("/connectors/test")
async def test_connection(req: ConnectorCreate, request: Request):
    """测试连接（不保存）"""
    config = {
        "db_type": req.db_type,
        "host": req.host,
        "port": req.port,
        "database_name": req.database_name,
        "username": req.username,
        "password": req.password,
    }

    try:
        result = await asyncio.to_thread(db_connector.test_connection, config)
        return {"success": result["success"], "version": result.get("version"), "error": result.get("error")}
    except Exception as e:
        return _error_response("连接测试失败", debug=str(e), status_code=400)


@router.post("/connectors/{connector_id}/test")
async def test_saved_connector(connector_id: str, request: Request):
    """测试已保存的连接器"""
    tenant_id = get_current_tenant_id()

    def _get_connector():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT db_type, host, port, database_name, username, password_encrypted
                FROM data_connectors
                WHERE id = %s AND tenant_id = %s
                """,
                (connector_id, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            r = dict(row)
            r["password"] = decrypt_password(r["password_encrypted"])
            del r["password_encrypted"]
            return r

    try:
        config = await asyncio.to_thread(_get_connector)
        if not config:
            return _error_response("连接器不存在或无权限", status_code=404)
        result = await asyncio.to_thread(db_connector.test_connection, config)
        return {"success": result["success"], "version": result.get("version"), "error": result.get("error")}
    except Exception as e:
        return _error_response("连接测试失败", debug=str(e), status_code=400)


@router.get("/connectors/{connector_id}/tables")
async def get_connector_tables(connector_id: str, request: Request):
    """获取连接器的远程表列表"""
    tenant_id = get_current_tenant_id()

    def _get_connector():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT db_type, host, port, database_name, username, password_encrypted
                FROM data_connectors
                WHERE id = %s AND tenant_id = %s
                """,
                (connector_id, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            r = dict(row)
            r["password"] = decrypt_password(r["password_encrypted"])
            del r["password_encrypted"]
            return r

    try:
        config = await asyncio.to_thread(_get_connector)
        if not config:
            return _error_response("连接器不存在或无权限", status_code=404)

        tables = await asyncio.to_thread(db_connector.list_tables, config)
        return {"success": True, "tables": tables}
    except Exception as e:
        logger.error(f"获取远程表列表失败: {e}", exc_info=True)
        return _error_response("获取远程表列表失败", debug=str(e))


@router.post("/connectors/{connector_id}/import")
async def import_connector_tables(connector_id: int, request: Request):
    """
    从连接器导入表结构：获取 DDL + 采样数据 -> LLM 推断 schema -> 返回供用户确认。

    请求体: {"table_names": ["table1", "table2"]}
    """
    tenant_id = get_current_tenant_id()
    body = await request.json()
    table_names = body.get("table_names", [])

    if not table_names:
        return _error_response("请选择要导入的表", status_code=400)

    def _get_connector():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT db_type, host, port, database_name, username, password_encrypted
                FROM data_connectors
                WHERE id = %s AND tenant_id = %s
                """,
                (connector_id, tenant_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            r = dict(row)
            r["password"] = decrypt_password(r["password_encrypted"])
            del r["password_encrypted"]
            return r

    try:
        config = await asyncio.to_thread(_get_connector)
        if not config:
            return _error_response("连接器不存在或无权限", status_code=404)

        schemas = []
        for table_name in table_names:
            # 获取采样数据
            sample_result = await asyncio.to_thread(
                db_connector.get_table_sample, config, table_name
            )

            # 转换为 schema_extractor 可接受的格式
            sheet_info = {
                "sheet_name": table_name,
                "rows": sample_result["rows"],
                "columns": [col["name"] for col in sample_result["columns"]],
                "sample_data": sample_result["sample_data"],
                "data_types": {
                    col["name"]: _map_db_type(col["type"])
                    for col in sample_result["columns"]
                },
            }

            # LLM 推断 schema
            schema = await schema_extractor.extract_schema(
                sheet_info=sheet_info,
                table_name_hint=table_name,
            )
            schema["source_type"] = "database"
            schema["connector_id"] = connector_id
            schema["source_info"] = f"{config['db_type']}://{config['host']}:{config['port']}/{config['database_name']}/{table_name}"
            schemas.append(schema)

        return {"success": True, "schemas": schemas}

    except Exception as e:
        logger.error(f"导入表结构失败: {e}", exc_info=True)
        return _error_response("导入表结构失败", debug=str(e))


def _map_db_type(db_type_str: str) -> str:
    """将数据库类型字符串映射为基本类型"""
    t = db_type_str.upper()
    if any(kw in t for kw in ("INT", "SERIAL", "BIGINT", "SMALLINT")):
        return "integer"
    if any(kw in t for kw in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL")):
        return "decimal"
    if any(kw in t for kw in ("DATE", "TIMESTAMP", "TIME")):
        return "date"
    if any(kw in t for kw in ("BOOL",)):
        return "boolean"
    if any(kw in t for kw in ("JSON", "JSONB")):
        return "json"
    return "text"


# ============== Excel Upload ==============


def _sse_event(event: Dict[str, Any]) -> str:
    """格式化为 SSE 事件帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
    request: Request = None,
):
    """
    上传 Excel/CSV 文件，解析结构并用 LLM 推断 schema，返回供用户确认。
    不保存到知识库。源文件持久化到 storage/uploads/{tenant_id}/data_sources/，
    供后续数据分析时加载数据使用。

    采用 SSE 流式响应：推送解析、推断进度，最终 complete 事件携带 schemas。
    """
    tenant_id = get_current_tenant_id()

    # 验证文件格式（在生成器外，便于直接返回错误）
    ext = Path(file.filename or "unknown").suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return _error_response(f"不支持的文件格式: {ext}，仅支持 .xlsx、.xls、.csv", status_code=400)

    filename = file.filename or "unknown"

    async def event_generator():
        total_start = time.monotonic()
        try:
            # 持久化源文件到 storage/uploads/{tenant_id}/data_sources/
            from src.config.settings import settings
            from pathlib import Path as _Path
            _project_root = _Path(__file__).resolve().parent.parent.parent
            persist_dir = _project_root / settings.storage.uploads_dir / (tenant_id or "_global") / "data_sources"
            persist_dir.mkdir(parents=True, exist_ok=True)
            file_id = uuid.uuid4().hex[:12]
            persist_path = persist_dir / f"{file_id}{ext}"

            with open(persist_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            logger.info(f"[upload_excel] tenant={tenant_id} file={filename} persisted to {persist_path}")
            yield _sse_event({"type": "connected", "filename": filename})

            # 阶段 1：解析文件
            yield _sse_event({"type": "progress", "stage": "parsing", "message": "正在解析文件结构..."})
            t0 = time.monotonic()
            sheets = await asyncio.to_thread(sheet_parser.parse_file, str(persist_path))
            logger.info(f"[upload_excel] parsing done in {time.monotonic() - t0:.2f}s, {len(sheets)} sheets: {[s['sheet_name'] for s in sheets]}")

            if not sheets:
                yield _sse_event({"type": "complete", "schemas": []})
                return

            # 阶段 2：LLM 推断每个 sheet 的 schema
            yield _sse_event({
                "type": "progress",
                "stage": "extracting",
                "message": f"正在分析 {len(sheets)} 张工作表..." if len(sheets) > 1 else "正在分析工作表结构...",
            })

            schemas = []
            total = len(sheets)
            for idx, sheet_info in enumerate(sheets):
                sheet_name = sheet_info.get("sheet_name", f"sheet_{idx + 1}")
                table_hint = Path(filename).stem
                if total > 1:
                    table_hint = f"{table_hint}_{sheet_name}"

                yield _sse_event({
                    "type": "sheet_progress",
                    "current": idx + 1,
                    "total": total,
                    "sheet_name": sheet_name,
                })

                t1 = time.monotonic()
                schema = await schema_extractor.extract_schema(
                    sheet_info=sheet_info,
                    table_name_hint=table_hint,
                )
                logger.info(f"[upload_excel] schema extracted ({idx + 1}/{total}) sheet='{sheet_name}' table='{schema.get('table_name')}' in {time.monotonic() - t1:.2f}s")

                schema["source_type"] = "file"
                schema["source_info"] = filename
                schema["source"] = {
                    "type": "excel",
                    "file_path": str(persist_path),
                    "sheet_name": sheet_name,
                }
                schemas.append(schema)

                yield _sse_event({
                    "type": "sheet_done",
                    "current": idx + 1,
                    "total": total,
                    "sheet_name": sheet_name,
                    "table_name": schema.get("table_name", ""),
                })

            logger.info(f"[upload_excel] complete: {len(schemas)} schemas, total {time.monotonic() - total_start:.2f}s")
            yield _sse_event({"type": "complete", "schemas": schemas})

        except ValueError as e:
            logger.warning(f"[upload_excel] value error: {e}")
            yield _sse_event({"type": "error", "message": str(e)})
        except Exception as e:
            logger.error(f"上传文件解析失败: {e}", exc_info=True)
            yield _sse_event({"type": "error", "message": "文件解析失败"})
        finally:
            try:
                await file.close()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============== Schema Management ==============


@router.post("/schemas")
async def save_schema(req: SchemaSave, request: Request):
    """
    保存确认的 schema 到知识库（documents + chunks + 向量）。
    这是用户确认后的最终步骤。
    """
    tenant_id = get_current_tenant_id()

    result = await save_schema_to_knowledge(
        tenant_id=tenant_id,
        table_name=req.table_name,
        description=req.description,
        columns=req.columns,
        source_info=req.source_info or "",
        connector_id=req.connector_id,
        source=req.source,
    )

    if not result["success"]:
        return _error_response("保存 schema 失败", debug=result.get("error", ""))
    return result


@router.get("/schemas")
async def list_schemas(request: Request):
    """列出知识库中所有数据分析 schema"""
    tenant_id = get_current_tenant_id()

    def _list():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, source_type, metadata, summary, created_at
                FROM documents
                WHERE tenant_id = %s AND source_type = 'data-analysis-metadata'
                ORDER BY created_at DESC
                """,
                (tenant_id,),
            )
            results = []
            for row in cursor.fetchall():
                r = dict(row)
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
                # 解析 metadata JSON
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except json.JSONDecodeError:
                        meta = {}
                r["metadata"] = meta
                results.append(r)
            return results

    try:
        schemas = await asyncio.to_thread(_list)
        return {"success": True, "schemas": schemas}
    except Exception as e:
        logger.error(f"获取 schema 列表失败: {e}", exc_info=True)
        return _error_response("获取 schema 列表失败", debug=str(e))


@router.put("/schemas/{doc_id}")
async def update_schema(doc_id: int, req: SchemaSave, request: Request):
    """更新 schema（重新生成嵌入）"""
    tenant_id = get_current_tenant_id()

    schema_text = generate_schema_text(req.table_name, req.description, req.columns)

    try:
        from src.config.settings import get_embedding_api_key
        embedding_api_key = get_embedding_api_key()
        embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
        embeddings = await embedding_client.embed_batch([schema_text])

        metadata = {
            "connector_id": req.connector_id,
            "source_info": req.source_info,
            "table_name": req.table_name,
            "columns": req.columns,
        }
        if req.source:
            metadata["source"] = req.source

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 验证归属
            cursor.execute(
                "SELECT id FROM documents WHERE id = %s AND tenant_id = %s AND source_type = 'data-analysis-metadata'",
                (doc_id, tenant_id),
            )
            if not cursor.fetchone():
                return _error_response("Schema 不存在或无权限", status_code=404)

            # 更新文档
            cursor.execute(
                """
                UPDATE documents
                SET title = %s, raw_text = %s, metadata = %s, summary = %s
                WHERE id = %s
                """,
                (
                    f"[数据表] {req.table_name}",
                    schema_text,
                    json.dumps(metadata, ensure_ascii=False),
                    req.description,
                    doc_id,
                ),
            )

            # 更新 chunk
            cursor.execute(
                """
                UPDATE chunks
                SET text = %s, tokens = %s
                WHERE doc_id = %s AND chunk_index = 0
                RETURNING id
                """,
                (schema_text, len(schema_text), doc_id),
            )
            chunk_row = cursor.fetchone()
            if chunk_row:
                # 重新生成向量
                chunk_id = chunk_row["id"]
                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.delete_by_doc(doc_id)
                await vector_db.insert([chunk_id], embeddings)

            conn.commit()

        return {"success": True, "message": "Schema 已更新"}

    except Exception as e:
        logger.error(f"更新 schema 失败: {e}", exc_info=True)
        return _error_response("更新 schema 失败", debug=str(e))


@router.delete("/schemas/{doc_id}")
async def delete_schema(doc_id: int, request: Request):
    """删除 schema 及其 chunks 和向量"""
    tenant_id = get_current_tenant_id()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 验证归属
            cursor.execute(
                "SELECT id FROM documents WHERE id = %s AND tenant_id = %s AND source_type = 'data-analysis-metadata'",
                (doc_id, tenant_id),
            )
            if not cursor.fetchone():
                return _error_response("Schema 不存在或无权限", status_code=404)

            # 删除向量
            vector_db = get_vector_db(dimension=1024, conn=conn)
            await vector_db.delete_by_doc(doc_id)

            # 删除 chunks
            cursor.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
            # 删除文档
            cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            conn.commit()

        return {"success": True, "message": "Schema 已删除"}
    except Exception as e:
        logger.error(f"删除 schema 失败: {e}", exc_info=True)
        return _error_response("删除 schema 失败", debug=str(e))


# ============== Relations ==============


@router.post("/relations/infer")
async def infer_relations(request: Request):
    """
    使用 LLM 推断 schema 之间的关联关系。

    请求体: {"schemas": [{table_name, columns: [{name, semantic_name, ...}]}]}
    """
    body = await request.json()
    schemas = body.get("schemas", [])

    if len(schemas) < 2:
        return _error_response("至少需要 2 个 schema 才能推断关联关系", status_code=400)

    try:
        relations = await schema_extractor.infer_relations(schemas)
        return {"success": True, "relations": relations}
    except Exception as e:
        logger.error(f"推断关联关系失败: {e}", exc_info=True)
        return _error_response("推断关联关系失败", debug=str(e))


@router.post("/relations/batch")
async def batch_save_relations(req: RelationBatch, request: Request):
    """
    批量保存关联关系。

    关联关系存储在 documents 表的 metadata JSONB 字段中，
    以 "data-analysis-relations" 为 key 存储在一张虚拟文档里。
    """
    tenant_id = get_current_tenant_id()

    try:
        relations_data = [r.dict() for r in req.relations]

        def _save():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # 查找是否已有 relations 文档
                cursor.execute(
                    """
                    SELECT id, metadata FROM documents
                    WHERE tenant_id = %s AND source_type = 'data-analysis-relations'
                    LIMIT 1
                    """,
                    (tenant_id,),
                )
                row = cursor.fetchone()

                if row:
                    # 合并已有关系
                    existing = {}
                    if row.get("metadata") and isinstance(row["metadata"], str):
                        try:
                            existing = json.loads(row["metadata"])
                        except json.JSONDecodeError:
                            pass
                    elif isinstance(row.get("metadata"), dict):
                        existing = row["metadata"]

                    existing_relations = existing.get("relations", [])
                    # 追加新关系（去重）
                    existing_keys = {
                        (r.get("from_table"), r.get("from_column"), r.get("to_table"), r.get("to_column"))
                        for r in existing_relations
                    }
                    for r in relations_data:
                        key = (r["from_table"], r["from_column"], r["to_table"], r["to_column"])
                        if key not in existing_keys:
                            existing_relations.append(r)

                    existing["relations"] = existing_relations
                    cursor.execute(
                        "UPDATE documents SET metadata = %s WHERE id = %s",
                        (json.dumps(existing, ensure_ascii=False), row["id"]),
                    )
                else:
                    # 创建 relations 文档
                    metadata = {"relations": relations_data}
                    cursor.execute(
                        """
                        INSERT INTO documents (tenant_id, title, source_type, total_chunks, metadata)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            tenant_id,
                            "[关联关系] 数据表关联",
                            "data-analysis-relations",
                            0,
                            json.dumps(metadata, ensure_ascii=False),
                        ),
                    )

                conn.commit()

        await asyncio.to_thread(_save)
        return {"success": True, "message": f"已保存 {len(req.relations)} 条关联关系"}

    except Exception as e:
        logger.error(f"批量保存关联关系失败: {e}", exc_info=True)
        return _error_response("保存关联关系失败", debug=str(e))


@router.get("/relations")
async def list_relations(request: Request):
    """列出所有关联关系"""
    tenant_id = get_current_tenant_id()

    def _list():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, metadata, created_at
                FROM documents
                WHERE tenant_id = %s AND source_type = 'data-analysis-relations'
                LIMIT 1
                """,
                (tenant_id,),
            )
            row = cursor.fetchone()
            if not row:
                return []

            meta = row.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            return meta.get("relations", [])

    try:
        relations = await asyncio.to_thread(_list)
        return {"success": True, "relations": relations}
    except Exception as e:
        logger.error(f"获取关联关系列表失败: {e}", exc_info=True)
        return _error_response("获取关联关系列表失败", debug=str(e))


@router.post("/relations")
async def add_relation(req: RelationItem, request: Request):
    """添加单条关联关系"""
    tenant_id = get_current_tenant_id()

    try:
        relation_data = req.dict()

        def _add():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, metadata FROM documents
                    WHERE tenant_id = %s AND source_type = 'data-analysis-relations'
                    LIMIT 1
                    """,
                    (tenant_id,),
                )
                row = cursor.fetchone()

                if row:
                    meta = row.get("metadata")
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except json.JSONDecodeError:
                            meta = {}
                    relations = meta.get("relations", [])
                    relations.append(relation_data)
                    meta["relations"] = relations
                    cursor.execute(
                        "UPDATE documents SET metadata = %s WHERE id = %s",
                        (json.dumps(meta, ensure_ascii=False), row["id"]),
                    )
                else:
                    metadata = {"relations": [relation_data]}
                    cursor.execute(
                        """
                        INSERT INTO documents (tenant_id, title, source_type, total_chunks, metadata)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            tenant_id,
                            "[关联关系] 数据表关联",
                            "data-analysis-relations",
                            0,
                            json.dumps(metadata, ensure_ascii=False),
                        ),
                    )

                conn.commit()

        await asyncio.to_thread(_add)
        return {"success": True, "message": "关联关系已添加"}

    except Exception as e:
        logger.error(f"添加关联关系失败: {e}", exc_info=True)
        return _error_response("添加关联关系失败", debug=str(e))


@router.delete("/relations")
async def delete_relation(request: Request):
    """
    删除一条关联关系。

    请求体: {"from_table": "...", "from_column": "...", "to_table": "...", "to_column": "..."}
    """
    tenant_id = get_current_tenant_id()
    body = await request.json()

    try:
        def _delete():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, metadata FROM documents
                    WHERE tenant_id = %s AND source_type = 'data-analysis-relations'
                    LIMIT 1
                    """,
                    (tenant_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return

                meta = row.get("metadata")
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except json.JSONDecodeError:
                        meta = {}

                relations = meta.get("relations", [])
                # 过滤掉匹配的关系
                filtered = [
                    r for r in relations
                    if not (
                        r.get("from_table") == body.get("from_table")
                        and r.get("from_column") == body.get("from_column")
                        and r.get("to_table") == body.get("to_table")
                        and r.get("to_column") == body.get("to_column")
                    )
                ]

                meta["relations"] = filtered
                cursor.execute(
                    "UPDATE documents SET metadata = %s WHERE id = %s",
                    (json.dumps(meta, ensure_ascii=False), row["id"]),
                )
                conn.commit()

        await asyncio.to_thread(_delete)
        return {"success": True, "message": "关联关系已删除"}

    except Exception as e:
        logger.error(f"删除关联关系失败: {e}", exc_info=True)
        return _error_response("删除关联关系失败", debug=str(e))


# ============== Helper Functions ==============
