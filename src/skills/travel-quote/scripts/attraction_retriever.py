# -*- coding: utf-8 -*-
"""
景点门票向量检索器

在 documents + chunks + chunks_vec 知识库中检索景点资源。
- chunk_index=0: 景点信息摘要（已向量化）
- 门票/项目价格表：metadata.raw_payload.ticket_table / project_table
  （Phase 2 起单一数据源；存量未迁移数据回退 chunk_index=1/2，
  Phase 5 清场后移除回退）
- source_type='attraction_resource'
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

import kb_metadata


class AttractionRetriever:
    """景点门票向量检索器"""

    SOURCE_TYPE = "attraction_resource"

    def __init__(self):
        self._embedding_client = None

    # ----------------------------------------------------------
    # 基础设施
    # ----------------------------------------------------------

    def _get_embedding_client(self):
        """延迟初始化 embedding 客户端"""
        if self._embedding_client is None:
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.config.settings import get_embedding_api_key

            embedding_api_key = get_embedding_api_key()
            self._embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
        return self._embedding_client

    def _get_conn(self):
        """获取数据库连接"""
        from src.db.database import get_db_connection
        return get_db_connection()

    def _embed(self, text: str) -> List[float]:
        """使用 TextEmbeddingV3Client 同步向量化（累加 usage 到 client）"""
        client = self._get_embedding_client()
        return client.embed_sync(text)

    # ----------------------------------------------------------
    # 搜索
    # ----------------------------------------------------------

    def _tenant_scope(self, tenant_id: Optional[str], subagent_id: Optional[str] = None,
                      shared_tenant_ids: Optional[List[str]] = None) -> Tuple[str, list]:
        """构建租户范围 SQL 与参数：本租户 + 已启用共享源租户。

        仅子智能体 + 租户模式（subagent_id 非空）生效，与通用知识库检索的
        shared_ranges 语义一致；tenant_id 为空时退回原逻辑 d.tenant_id = %s。
        shared_tenant_ids 为调用方已聚合好的共享租户 ID 列表（含本租户），
        用于独立 API（无子智能体上下文）场景，此时直接使用该列表。
        """
        if not tenant_id:
            return "d.tenant_id = %s", [tenant_id]
        if shared_tenant_ids:
            return "d.tenant_id = ANY(%s)", [shared_tenant_ids]
        if not subagent_id:
            return "d.tenant_id = %s", [tenant_id]
        tenant_ids = [tenant_id]
        from src.knowledge.retriever.tenant_range import load_shared_ranges
        for from_tenant_id, st in load_shared_ranges(tenant_id, subagent_id, self.SOURCE_TYPE):
            if from_tenant_id not in tenant_ids:
                tenant_ids.append(from_tenant_id)
        return "d.tenant_id = ANY(%s)", [tenant_ids]

    def search_by_name(self, tenant_id: str, name_query: str, top_k: int = 5, subagent_id: Optional[str] = None,
                       shared_tenant_ids: Optional[List[str]] = None) -> List[Dict]:
        """精确/模糊名称匹配（ILIKE）"""
        tenant_sql, tenant_params = self._tenant_scope(tenant_id, subagent_id, shared_tenant_ids)
        with self._get_conn() as conn:
            conn.execute(f"""
                SELECT c.doc_id, c.text, d.title, d.metadata, d.file_path, d.created_at
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE d.source_type = %s
                  AND {tenant_sql}
                  AND c.chunk_index = 0
                  AND c.text ILIKE %s
                ORDER BY d.id
                LIMIT %s
            """, (self.SOURCE_TYPE, *tenant_params, f'%{name_query}%', top_k))
            rows = conn.fetchall()

        return self._format_results(rows)

    def search_by_vector(self, tenant_id: str, query: str, top_k: int = 5, subagent_id: Optional[str] = None,
                         shared_tenant_ids: Optional[List[str]] = None) -> List[Dict]:
        """向量语义搜索"""
        client = self._get_embedding_client()
        client.reset_usage()
        embedding = self._embed(query)
        # 累加 embedding usage 到当前 SessionRecordService（对话内检索计费）
        if client.last_usage_tokens > 0:
            try:
                from src.services.session_record import SessionRecordManager
                record = SessionRecordManager.get_current_record()
                if record:
                    record.add_embedding_usage(client.last_usage_tokens, model=client.model)
            except Exception:
                logger.opt(exception=True).debug("Failed to record embedding usage")
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        tenant_sql, tenant_params = self._tenant_scope(tenant_id, subagent_id, shared_tenant_ids)
        with self._get_conn() as conn:
            conn.execute(f"""
                SELECT cv.chunk_id,
                       cv.embedding <=> %s::vector AS distance,
                       c.doc_id, c.text, d.title, d.metadata, d.file_path, d.created_at
                FROM chunks_vec cv
                JOIN chunks c ON cv.chunk_id = c.id
                JOIN documents d ON c.doc_id = d.id
                WHERE d.source_type = %s
                  AND {tenant_sql}
                  AND c.chunk_index = 0
                ORDER BY distance
                LIMIT %s
            """, (embedding_str, self.SOURCE_TYPE, *tenant_params, top_k))
            rows = conn.fetchall()

        results = self._format_results(rows)
        for i, row in enumerate(rows):
            results[i]["score"] = round(1.0 - float(row["distance"]), 4)
        logger.info(f"[AttractionRetriever] vector search '{query}' → {len(results)} results (tenant={tenant_id})")
        return results

    def search(self, tenant_id: str, query: str, top_k: int = 5, subagent_id: Optional[str] = None,
               shared_tenant_ids: Optional[List[str]] = None) -> List[Dict]:
        """组合搜索：名称匹配优先，无结果再走向量搜索"""
        name_results = self.search_by_name(tenant_id, query, top_k, subagent_id=subagent_id, shared_tenant_ids=shared_tenant_ids)
        if name_results:
            return name_results
        return self.search_by_vector(tenant_id, query, top_k, subagent_id=subagent_id, shared_tenant_ids=shared_tenant_ids)

    def _format_results(self, rows) -> List[Dict]:
        """将数据库行格式化为统一的搜索结果"""
        results = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            results.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "info": row["text"],
                "metadata": meta or {},
                "source_file": row["file_path"] or "",
                "created_at": str(row["created_at"]) if row.get("created_at") else "",
            })
        return results

    def list_all(self, tenant_id: str, limit: int = 200, offset: int = 0, subagent_id: Optional[str] = None,
                 shared_tenant_ids: Optional[List[str]] = None) -> Dict:
        """列出所有景点知识库文档（分页，含已启用共享范围）"""
        tenant_sql, tenant_params = self._tenant_scope(tenant_id, subagent_id, shared_tenant_ids)
        with self._get_conn() as conn:
            conn.execute(
                f"SELECT COUNT(*) AS cnt FROM documents d WHERE d.source_type = %s AND {tenant_sql}",
                (self.SOURCE_TYPE, *tenant_params),
            )
            total = conn.fetchone()["cnt"]

            conn.execute(f"""
                SELECT d.id AS doc_id, d.title, d.file_path, d.metadata, d.created_at,
                       c.text AS info
                FROM documents d
                LEFT JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
                WHERE d.source_type = %s AND {tenant_sql}
                ORDER BY d.id
                LIMIT %s OFFSET %s
            """, (self.SOURCE_TYPE, *tenant_params, limit, offset))
            rows = conn.fetchall()

        items = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            items.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "info": row["info"] or "",
                "metadata": meta or {},
                "source_file": row["file_path"] or "",
                "created_at": str(row["created_at"]) if row["created_at"] else "",
            })
        return {"total": total, "items": items}

    # ----------------------------------------------------------
    # 单文档读取
    # ----------------------------------------------------------

    def get_attraction_info(self, doc_id: int) -> Optional[str]:
        """取出景点信息摘要（chunk_index=0）"""
        with self._get_conn() as conn:
            conn.execute(
                "SELECT text FROM chunks WHERE doc_id = %s AND chunk_index = 0",
                (doc_id,)
            )
            row = conn.fetchone()
            return row["text"] if row else None

    def _get_table_text(self, doc_id: int, key: str, chunk_index: int) -> Optional[str]:
        """表文本读取公共路径：优先 metadata.raw_payload（单一数据源），
        未迁移存量回退对应 chunk 位次（过渡期兼容，Phase 5 清场后随清理任务移除）"""
        with self._get_conn() as conn:
            conn.execute("SELECT metadata FROM documents WHERE id = %s", (doc_id,))
            row = conn.fetchone()
            if row:
                text = kb_metadata.get_payload_table_text(row["metadata"], key)
                if text is not None:
                    return text
            conn.execute(
                "SELECT text FROM chunks WHERE doc_id = %s AND chunk_index = %s",
                (doc_id, chunk_index)
            )
            row = conn.fetchone()
            return row["text"] if row else None

    def get_ticket_table(self, doc_id: int) -> Optional[str]:
        """取出景点门票价格表（raw_payload.ticket_table，回退 chunk 1）"""
        return self._get_table_text(doc_id, "ticket_table", 1)

    def get_project_table(self, doc_id: int) -> Optional[str]:
        """取出景点项目/服务价格表（raw_payload.project_table，回退 chunk 2）"""
        return self._get_table_text(doc_id, "project_table", 2)

    # ----------------------------------------------------------
    # 导入
    # ----------------------------------------------------------

    async def import_attraction(
        self,
        tenant_id: str,
        attraction_name: str,
        region: str,
        info_text: str,
        ticket_table_text: str,
        project_table_text: str = "",
        metadata: Optional[Dict] = None,
        source_file: str = "",
        user_id: Optional[str] = None,
        cover_image_path: Optional[str] = None,
        gallery_image_paths: Optional[List[str]] = None,
    ) -> int:
        """
        导入一个景点到知识库。

        Args:
            tenant_id: 租户 ID
            attraction_name: 景点名称
            region: 区域
            info_text: 景点信息摘要（chunk 0，会向量化）
            ticket_table_text: 门票价格表（写入 raw_payload.ticket_table，不向量化）
            project_table_text: 项目/服务价格表（写入 raw_payload.project_table，不向量化）
            metadata: 额外元信息
            source_file: 来源文件名（仅记入 metadata）
            user_id: 上传用户 ID
            cover_image_path: 封面图本地路径；提供时注册到 ImageRegistry 并写入
                metadata.images.cover（顶层与 raw_payload 镜像同步；source="knowledge_base",
                usage="thumbnail"）
            gallery_image_paths: 图集本地路径列表；提供时循环注册，写入
                metadata.images.gallery（file_id 列表，同步镜像进 raw_payload）

        Returns:
            doc_id

        Notes:
            - 单张图片注册失败仅记 warning 不阻断（设计文档 §5.1.1 metadata.images 契约）
            - 表文本写入 metadata.raw_payload（统一知识库工具默认透出），不再插
              chunks 1/2；顶层业务键同步镜像（设计 §3/D2）
        """
        # reset 后向量化，配合下方独立落库计费（避免批量导入时计数器累积重复记账）
        client = self._get_embedding_client()
        client.reset_usage()
        embedding = self._embed(info_text)

        # 补计费：导入入库向量化消耗（API 入口无会话 record，独立落库归属租户）
        if client.last_usage_tokens > 0:
            try:
                from src.services.session_record import record_admin_embedding_usage
                record_admin_embedding_usage(
                    client,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    source_label="travel_quote_import_attraction",
                    source_type="background_embedding",
                )
            except Exception:
                logger.opt(exception=True).debug("Failed to record attraction import embedding usage")

        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        # metadata：表键入 raw_payload → 业务键镜像（region 等）。图片注册后经
        # sync_raw_payload 再同步 images 镜像，两处始终一致
        base_meta = dict(metadata or {})
        if region:
            base_meta.setdefault("region", region)  # 确定性补入顶层（设计 §3；parser 偶尔漏输出时兜底）
        kb_metadata.put_table_keys(base_meta, {
            "ticket_table": ticket_table_text,
            "project_table": project_table_text,
        })
        kb_metadata.sync_raw_payload(base_meta)
        initial_meta_json = json.dumps(base_meta, ensure_ascii=False)

        with self._get_conn() as conn:
            # 1. 创建 document（先用初始 metadata，后续可能 UPDATE 合入 images；
            #    仅 chunk 0 摘要，表数据在 metadata.raw_payload）
            conn.execute("""
                INSERT INTO documents (user_id, tenant_id, title, source_type, file_type, file_path,
                                       total_chunks, embedding_model, metadata, summary)
                VALUES (%s, %s, %s, %s, 'xlsx', %s, 1, 'text-embedding-v3', %s, %s)
                RETURNING id
            """, (user_id, tenant_id, f"景点：{attraction_name}", self.SOURCE_TYPE,
                  source_file, initial_meta_json, info_text))
            doc_id = conn.fetchone()["id"]

            # 2. 插入 chunk 0（景点信息摘要）
            conn.execute("""
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, 0, %s, %s, %s)
                RETURNING id
            """, (doc_id, info_text, len(info_text),
                  json.dumps({"type": "attraction_info", "region": region}, ensure_ascii=False)))
            chunk_id_0 = conn.fetchone()["id"]

            # 3. 向量化 chunk 0
            conn.execute("""
                INSERT INTO chunks_vec (chunk_id, embedding)
                VALUES (%s, %s::vector)
            """, (chunk_id_0, embedding_str))

            conn.commit()

        # 6. 注册图片到 ImageRegistry（拿到 doc_id 之后）。
        #    单张失败仅 warning 不阻断；正常情况下 metadata.images 不写或部分写。
        images_meta: Dict[str, Any] = {}
        if cover_image_path or gallery_image_paths:
            from pathlib import Path

            from src.core.image_asset import get_image_registry

            registry = get_image_registry()

            if cover_image_path:
                try:
                    cover_ref = await registry.register(
                        source_path=cover_image_path,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        display_name=Path(cover_image_path).name,
                        source="knowledge_base",
                        usage="thumbnail",
                        source_ref=source_file,
                        linked_doc_id=doc_id,
                    )
                    images_meta["cover"] = cover_ref.file_id
                except Exception as e:
                    logger.warning(
                        f"[AttractionRetriever] 注册封面图失败 attraction='{attraction_name}' "
                        f"cover_image_path={cover_image_path}: {e}"
                    )

            if gallery_image_paths:
                gallery_file_ids: List[str] = []
                for img_path in gallery_image_paths:
                    try:
                        g_ref = await registry.register(
                            source_path=img_path,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            display_name=Path(img_path).name,
                            source="knowledge_base",
                            usage="thumbnail",
                            source_ref=source_file,
                            linked_doc_id=doc_id,
                        )
                        gallery_file_ids.append(g_ref.file_id)
                    except Exception as e:
                        logger.warning(
                            f"[AttractionRetriever] 注册图集图片失败 attraction='{attraction_name}' "
                            f"path={img_path}: {e}"
                        )
                if gallery_file_ids:
                    images_meta["gallery"] = gallery_file_ids

        # 7. 如果有图片注册成功，合并到 metadata 并 UPDATE documents
        #    （顶层 images + raw_payload.images 镜像同步，D2 双写一致性）
        if images_meta:
            final_metadata = {**base_meta, "images": images_meta}
            kb_metadata.sync_raw_payload(final_metadata)
            final_meta_json = json.dumps(final_metadata, ensure_ascii=False)
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE documents SET metadata = %s WHERE id = %s",
                    (final_meta_json, doc_id),
                )
                conn.commit()

        logger.info(
            f"[AttractionRetriever] 导入景点 '{attraction_name}' 成功, doc_id={doc_id}, "
            f"images={'/'.join(images_meta.keys()) if images_meta else 'none'}"
        )
        return doc_id

    def import_attraction_sync(self, **kwargs) -> int:
        """同步兼容包装：覆盖旧的同步调用方。

        已在 event loop 内时（不该走到这，但兜底）使用线程池跑独立 loop；
        否则用 asyncio.run。
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        asyncio.run, self.import_attraction(**kwargs)
                    ).result()
        except RuntimeError:
            pass
        return asyncio.run(self.import_attraction(**kwargs))
