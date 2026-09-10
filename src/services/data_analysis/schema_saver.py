"""
数据分析 - Schema 保存共享服务

从 API 层提取的 schema 保存逻辑，供 API 端点和 Agent 工具共用。
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.core.text_sanitizer import sanitize_text, sanitize_value
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
from src.knowledge.vector_db.vector_db import get_vector_db


def generate_schema_text(
    table_name: str,
    description: str,
    columns: List[Dict[str, Any]],
) -> str:
    """
    从 schema 生成描述文本，用于嵌入。

    格式：
    表名: xxx
    描述: xxx
    列:
      - 列名(语义名): 类型, 描述, 枚举值
    """
    lines = [f"表名: {table_name}"]
    if description:
        lines.append(f"描述: {description}")
    lines.append("列:")

    for col in columns:
        name = col.get("name", "")
        semantic_name = col.get("semantic_name", "")
        data_type = col.get("data_type", "text")
        col_desc = col.get("description", "")
        enum_values = col.get("enum_values", [])

        col_line = f"  - {name}"
        if semantic_name and semantic_name != name:
            col_line += f"({semantic_name})"
        col_line += f": {data_type}"
        if col_desc:
            col_line += f", {col_desc}"
        if enum_values:
            col_line += f", 枚举值: {', '.join(str(v) for v in enum_values[:10])}"
        lines.append(col_line)

    return "\n".join(lines)


async def save_schema_to_knowledge(
    tenant_id: Optional[str],
    table_name: str,
    description: str,
    columns: List[Dict[str, Any]],
    source_info: str = "",
    connector_id: Optional[int] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """
    保存 schema 到知识库（documents + chunks + 向量），带去重。

    Returns:
        {"success": True, "doc_id": int, "message": str} 或
        {"success": False, "error": str}
    """
    # 上传文件/HTTP 请求体可能携带孤立代理字符（如 PDF 复制的 \ud83c），
    # 写库前统一清洗（schema_text 与 metadata 均由这些字段派生）
    table_name = sanitize_text(table_name)
    description = sanitize_text(description)
    source_info = sanitize_text(source_info)
    columns = sanitize_value(columns)
    schema_text = generate_schema_text(table_name, description, columns)

    try:
        from src.config.settings import get_embedding_api_key
        embedding_api_key = get_embedding_api_key()
        embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
        embedding_client.reset_usage()
        embeddings = await embedding_client.embed_batch([schema_text])

        # 补计费：schema 向量化消耗（对话内累加到当前记录，管理后台独立落库）
        emb_tokens = int(getattr(embedding_client, "last_usage_tokens", 0) or 0)
        if emb_tokens > 0:
            try:
                from src.services.session_record import (
                    SessionRecordManager, record_admin_embedding_usage,
                )
                record = SessionRecordManager.get_current_record()
                if record:
                    record.add_embedding_usage(emb_tokens, model=embedding_client.model)
                else:
                    record_admin_embedding_usage(
                        embedding_client,
                        tenant_id=tenant_id,
                        source_label="save_schema_to_knowledge",
                    )
            except Exception:
                logger.opt(exception=True).debug("Failed to record schema embedding usage")

        metadata = {
            "connector_id": connector_id,
            "source_info": source_info,
            "table_name": table_name,
            "columns": columns,
        }
        if source:
            metadata["source"] = source

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 去重：检查是否已存在同名同源 schema
            # 注意：documents.metadata 字段类型为 TEXT（存 JSON 字符串），
            # 需要显式转换为 jsonb 才能使用 ->> 操作符
            cursor.execute(
                """
                SELECT id FROM documents
                WHERE tenant_id IS NOT DISTINCT FROM %s
                    AND source_type = 'data-analysis-metadata'
                    AND metadata::jsonb->>'table_name' = %s
                    AND metadata::jsonb->>'source_info' = %s
                """,
                (tenant_id, table_name, source_info),
            )
            existing = cursor.fetchone()
            if existing:
                return {
                    "success": True,
                    "doc_id": existing["id"],
                    "message": f"Schema '{table_name}' 已存在，跳过重复注册",
                }

            # 插入文档
            cursor.execute(
                """
                INSERT INTO documents
                    (tenant_id, title, source_type, file_type, total_chunks,
                     embedding_model, raw_text, metadata, summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    tenant_id,
                    f"[数据表] {table_name}",
                    "data-analysis-metadata",
                    "json",
                    1,
                    "text-embedding-v3",
                    schema_text,
                    json.dumps(metadata, ensure_ascii=False),
                    description,
                ),
            )
            doc_id = cursor.fetchone()["id"]

            # 插入 chunk
            cursor.execute(
                """
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (doc_id, 0, schema_text, len(schema_text), json.dumps({"char_count": len(schema_text)})),
            )
            chunk_id = cursor.fetchone()["id"]

            # 插入向量
            vector_db = get_vector_db(dimension=1024, conn=conn)
            await vector_db.insert([chunk_id], embeddings)

            conn.commit()

        return {"success": True, "doc_id": doc_id, "message": "Schema 已保存到知识库"}

    except Exception as e:
        logger.opt(exception=True).error(f"保存 schema 到知识库失败: {e}")
        return {"success": False, "error": str(e)}
