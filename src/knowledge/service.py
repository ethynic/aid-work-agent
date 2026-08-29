"""
知识库服务 - 文档上传、解析、向量化的业务逻辑
"""

import os
import json
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

from src.knowledge.parsers.parser_factory import parser_factory
from src.knowledge.chunker import TextChunker
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client, sanitize_error_info
from src.knowledge.vector_db.vector_db import get_vector_db
from src.config.settings import settings
from src.db.database import get_db_connection
from src.db.models import ChatRecordDB
from src.llm.gateway import LLMGateway
from src.services.billing import (
    calculate_credit_cost_with_breakdown,
    calculate_embedding_credit_cost_with_breakdown,
)

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """知识库服务"""

    def __init__(self):
        self.chunk_size = getattr(settings, 'knowledge_chunk_size', 512)
        self.chunk_overlap = getattr(settings, 'knowledge_chunk_overlap', 64)
        self.chunker = TextChunker(
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap
        )
        # LLM 网关（懒加载）
        self._llm_gateway = None
        # 最近一次文档摘要 LLM 调用的 usage（供 upload_document 接入计费）
        self._last_summary_usage: Optional[Dict[str, Any]] = None

    @property
    def llm_gateway(self):
        """获取 LLM 网关实例（懒加载）"""
        if self._llm_gateway is None:
            self._llm_gateway = LLMGateway()
        return self._llm_gateway

    async def generate_summary(self, text: str, title: str, max_length: int = 300) -> str:
        """
        调用 LLM 生成文档摘要

        Args:
            text: 文档原文
            title: 文档标题
            max_length: 摘要最大长度（字符）

        Returns:
            文档摘要字符串
        """
        if not text or not text.strip():
            return ""

        # 限制输入文本长度，避免超出 LLM 上下文限制
        # 取前 5000 字符（大约 1250 tokens）用于生成摘要
        truncated_text = text[:5000]
        if len(text) > 5000:
            truncated_text += "..."

        prompt = f"""请为以下文档生成一个简洁的中文摘要，不超过 {max_length} 个字符。

文档标题: {title}

文档内容:
{truncated_text}

要求:
1. 准确概括文档的核心内容
2. 语言简洁通顺
3. 不要包含"摘要"、"本文"等字样
4. 直接输出摘要内容，不需要其他说明
"""

        try:
            response = await self.llm_gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )
            self._last_summary_usage = response.get("usage") if isinstance(response, dict) else None
            summary = response.get("content", "") or ""
            return summary.strip()
        except Exception as e:
            logger.warning(f"生成文档摘要失败: {e}")
            self._last_summary_usage = None
            return ""

    def _record_knowledge_embedding_billing(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        doc_id: int,
        file_filename: str,
        embedding_tokens: int,
        summary_usage: Optional[Dict[str, Any]],
    ) -> None:
        """知识库文档处理独立计费（source_type=knowledge_embedding）

        包含两部分：
        - embedding_tokens: 文档向量化消耗（按 token 计费，calculate_embedding_credit_cost）
        - summary_usage: 摘要 LLM 调用消耗（按 token 计费，calculate_credit_cost）

        合并为一条 chat_records，usage_breakdown 记录分项明细。
        """
        # Embedding 积分
        emb_bd: Dict[str, Any] = {}
        try:
            embedding_credit, emb_bd = calculate_embedding_credit_cost_with_breakdown(
                embedding_tokens=embedding_tokens,
            )
        except Exception as e:
            logger.error(f"embedding 计费计算失败，降级为 0: {e}")
            embedding_credit = 0.0
            emb_bd = {}

        # 摘要 LLM 积分
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        cached_input_tokens = 0
        cache_creation_input_tokens = 0
        llm_model = None
        llm_credit = 0.0
        if summary_usage:
            prompt_tokens = int(summary_usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(summary_usage.get("completion_tokens", 0) or 0)
            total_tokens = int(summary_usage.get("total_tokens", 0) or 0)
            cached_input_tokens = int(summary_usage.get("cached_tokens", 0) or 0)
            cache_creation_input_tokens = int(summary_usage.get("cache_creation_tokens", 0) or 0)
            # 知识库摘要使用主 gateway 默认模型
            try:
                llm_model = getattr(settings.llm, "model_code", None) or "qwen3.7-flash"
            except Exception:
                llm_model = "qwen3.7-flash"
            chat_bd: Dict[str, Any] = {}
            try:
                llm_credit, chat_bd = calculate_credit_cost_with_breakdown(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    model=llm_model,
                    cached_input_tokens=cached_input_tokens,
                    cache_creation_input_tokens=cache_creation_input_tokens,
                )
            except Exception as e:
                logger.error(f"摘要 LLM 计费计算失败，降级为 0: {e}")
                llm_credit = 0.0
                chat_bd = {}

        total_credit = round(embedding_credit + llm_credit, 2)
        if total_credit <= 0 and embedding_tokens == 0 and total_tokens == 0:
            return  # 无任何用量，不写空记录

        usage_breakdown: Dict[str, Any] = {
            "embedding": {
                "tokens": embedding_tokens,
                "model": "text-embedding-v3",
                "credit": round(embedding_credit, 2),
            },
        }
        if emb_bd:
            usage_breakdown["embedding"].update({
                "unit_price_per_m": emb_bd.get("unit_price_per_m"),
                "usage_factor": emb_bd.get("usage_factor"),
            })
        if summary_usage:
            usage_breakdown["summary_llm"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_input_tokens": cached_input_tokens,
                "total_tokens": total_tokens,
                "model": llm_model,
                "credit": round(llm_credit, 2),
            }
            if chat_bd:
                usage_breakdown["summary_llm"].update({
                    "non_cached_input_tokens": chat_bd.get("non_cached_input_tokens", 0),
                    "unit_prices": chat_bd.get("unit_prices", {}),
                    "usage_factor": chat_bd.get("usage_factor"),
                    "credits": chat_bd.get("credits", {}),
                })

        ChatRecordDB.create(
            session_id=f"knowledge_embedding_{doc_id}",
            tenant_id=tenant_id,
            user_id=user_id,
            user_message=f"知识库文档向量化+摘要: {file_filename}",
            assistant_message=None,
            total_token_count=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=cached_input_tokens,
            model=llm_model,
            provider="qwen",
            source_type="knowledge_embedding",
            credit_cost=total_credit,
            embedding_tokens=embedding_tokens,
            usage_breakdown=usage_breakdown,
            status="completed",
        )
        logger.info(
            f"知识库文档计费: doc_id={doc_id}, tenant={tenant_id}, "
            f"embedding_tokens={embedding_tokens}, llm_tokens={total_tokens}, "
            f"credit={total_credit} (embedding={embedding_credit}, llm={llm_credit})"
        )

    def _get_upload_path(self, tenant_id: Optional[str] = None) -> Path:
        """获取知识库文档上传路径
        有租户: storage/tenants/{tenant_id}/knowledge/
        无租户: storage/tenants/_anonymous/knowledge/
        """
        from src.core.storage import ensure_tenant_storage_dir
        tid = tenant_id or "_anonymous"
        return Path(ensure_tenant_storage_dir(tid, "knowledge"))

    def _get_db_connection(self):
        """获取数据库连接（使用统一的数据库连接管理）"""
        return get_db_connection()

    @staticmethod
    def _collect_subtree_source_types(rows: List[Dict[str, Any]], category_source_type: str) -> List[str]:
        """给定分类行（含 id/source_type/parent_id），返回 category_source_type 自身 + 所有后代 source_type。
        内存建树后 DFS 收集，供 list_categories / list_documents / count_documents 共用，保证三处语义一致。"""
        children = {}  # parent_id -> [child_id]
        source_type_by_id = {}
        target_id = None
        for r in rows:
            source_type_by_id[r["id"]] = r["source_type"]
            children.setdefault(r["parent_id"], []).append(r["id"])
            if r["source_type"] == category_source_type:
                target_id = r["id"]
        # source_type 租户内唯一，命中不到说明该分类不存在（可能被删除），退化为仅自身
        if target_id is None:
            return [category_source_type]
        result = [category_source_type]
        stack = list(children.get(target_id, []))
        while stack:
            cid = stack.pop()
            result.append(source_type_by_id[cid])
            stack.extend(children.get(cid, []))
        return result

    def _get_category_subtree_source_types(self, tenant_id: str, category_source_type: str) -> List[str]:
        """返回 category_source_type 自身 + 其所有后代分类的 source_type 列表。
        供 list_categories / list_documents / count_documents 共用，保证三处语义一致。
        实现：一次 SELECT 该租户全部分类 (source_type, parent_id)，内存建树后 DFS 收集后代。"""
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, source_type, parent_id FROM knowledge_categories WHERE tenant_id = %s",
                (tenant_id,)
            )
            rows = cursor.fetchall()
        return self._collect_subtree_source_types(rows, category_source_type)

    def _validate_sub_category(self, tenant_id: str, source_type: Optional[str], sub_category: Optional[str]) -> None:
        """校验 sub_category 归属：对应分类必须存在、同租户、非顶级分类，
        且其祖先根分类的 source_type 等于入参 source_type（source_type 恒为顶级分类代号）。
        无效时抛 ValueError，供 upload_document / move_documents 复用。"""
        if not sub_category:
            return
        with self._get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT source_type, parent_id FROM knowledge_categories WHERE source_type = %s AND tenant_id = %s",
                (sub_category, tenant_id)
            )
            cat = cur.fetchone()
            if not cat:
                raise ValueError("子分类不存在")
            if cat["parent_id"] is None:
                # source_type 租户内全局唯一，命中顶级分类说明 sub_category 传了顶级分类自身
                raise ValueError("所选分类是顶级分类，不能作为子分类")
            root_source_type = cat["source_type"]
            parent_id = cat["parent_id"]
            depth = 0
            while parent_id is not None and depth < 100:
                cur.execute(
                    "SELECT source_type, parent_id FROM knowledge_categories WHERE id = %s AND tenant_id = %s",
                    (parent_id, tenant_id)
                )
                parent = cur.fetchone()
                if not parent:
                    raise ValueError("子分类层级异常")
                root_source_type = parent["source_type"]
                parent_id = parent["parent_id"]
                depth += 1
        if not source_type:
            raise ValueError("选择子分类时必须同时选择顶级分类")
        if source_type != root_source_type:
            raise ValueError("子分类不属于所选顶级分类")

    # ========== 分类管理 ==========

    def list_categories(self, tenant_id: str) -> List[Dict[str, Any]]:
        """获取分类列表，含文档数统计。返回扁平结构（含 parent_id），树形由前端组装。
        文档数统计：每级分类 = 其下所有子级（含自身）的文档总数，与列表过滤语义一致。"""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT kc.id, kc.source_type, kc.display_name, kc.parent_id, kc.created_at
                    FROM knowledge_categories kc
                    WHERE kc.tenant_id = %s
                    ORDER BY kc.created_at ASC
                """, (tenant_id,))
                rows = cursor.fetchall()
                # 文档按 (source_type, sub_category) 分组计数，供内存统计，替代逐分类子查询
                cursor.execute("""
                    SELECT source_type, sub_category, COUNT(*) AS cnt
                    FROM documents
                    WHERE tenant_id = %s
                    GROUP BY source_type, sub_category
                """, (tenant_id,))
                doc_rows = cursor.fetchall()

            source_type_count = {}   # source_type -> 文档总数（顶级分类口径）
            sub_category_count = {}  # sub_category -> 直接归属文档数
            for dr in doc_rows:
                source_type_count[dr["source_type"]] = source_type_count.get(dr["source_type"], 0) + dr["cnt"]
                sc = dr["sub_category"]
                if sc:
                    sub_category_count[sc] = sub_category_count.get(sc, 0) + dr["cnt"]

            result = []
            for row in rows:
                d = dict(row)
                if d["parent_id"] is None:
                    # 顶级分类：该 source_type 全部文档（子级文档 source_type 恒为顶级，天然包含）
                    d["document_count"] = source_type_count.get(d["source_type"], 0)
                else:
                    # 子分类：自身 + 所有后代分类的 sub_category 文档数之和
                    subtree = self._collect_subtree_source_types(rows, d["source_type"])
                    d["document_count"] = sum(sub_category_count.get(s, 0) for s in subtree)
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
                result.append(d)
            return result
        except Exception as e:
            logger.error(f"获取分类列表失败: {e}", exc_info=True)
            return []

    def create_category(self, tenant_id: str, source_type: Optional[str] = None, display_name: Optional[str] = None, parent_id: Optional[int] = None) -> Dict[str, Any]:
        """创建分类，parent_id 非空时创建为子分类；source_type 为空时自动生成唯一代号"""
        import re
        if source_type is not None:
            if not re.match(r'^[a-z][a-z0-9_-]*$', source_type):
                return {"success": False, "error": "source_type 格式错误，仅允许小写字母开头，后续为小写字母、数字、下划线或连字符"}
        else:
            source_type = f"k_{uuid.uuid4().hex[:12]}"
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                if parent_id is not None:
                    cursor.execute(
                        "SELECT id FROM knowledge_categories WHERE id = %s AND tenant_id = %s",
                        (parent_id, tenant_id)
                    )
                    if not cursor.fetchone():
                        return {"success": False, "error": "父分类不存在", "status": 404}
                category_uuid = f"kc_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO knowledge_categories (tenant_id, source_type, display_name, parent_id, uuid)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, created_at
                """, (tenant_id, source_type, display_name or source_type, parent_id, category_uuid))
                row = cursor.fetchone()
                conn.commit()
                d = dict(row)
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
                return {
                    "success": True,
                    "id": d["id"],
                    "source_type": source_type,
                    "display_name": display_name or source_type,
                    "parent_id": parent_id,
                    "created_at": d.get("created_at", "")
                }
        except Exception as e:
            err = str(e)
            if "unique" in err.lower() or "duplicate" in err.lower():
                return {"success": False, "error": "该代号已存在", "status": 409}
            logger.error(f"创建分类失败: {e}", exc_info=True)
            return {"success": False, "error": "创建分类失败"}

    def update_category(self, category_id: int, tenant_id: str, display_name: str) -> Dict[str, Any]:
        """更新分类名称"""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE knowledge_categories SET display_name = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND tenant_id = %s
                """, (display_name, category_id, tenant_id))
                if cursor.rowcount == 0:
                    return {"success": False, "error": "分类不存在"}
                conn.commit()
                return {"success": True}
        except Exception as e:
            logger.error(f"更新分类失败: {e}", exc_info=True)
            return {"success": False, "error": "更新分类失败"}

    def delete_category(self, category_id: int, tenant_id: str) -> Dict[str, Any]:
        """删除分类（仅删记录，不删文档；含子分类的分类禁止删除）"""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM knowledge_categories WHERE parent_id = %s AND tenant_id = %s LIMIT 1",
                    (category_id, tenant_id)
                )
                if cursor.fetchone():
                    return {"success": False, "error": "该分类下存在子分类，请先删除子分类", "status": 400}
                cursor.execute("""
                    DELETE FROM knowledge_categories WHERE id = %s AND tenant_id = %s
                """, (category_id, tenant_id))
                if cursor.rowcount == 0:
                    return {"success": False, "error": "分类不存在"}
                conn.commit()
                return {"success": True}
        except Exception as e:
            logger.error(f"删除分类失败: {e}", exc_info=True)
            return {"success": False, "error": "删除分类失败"}

    async def upload_document(
        self,
        file_path: str,
        file_filename: str,
        user_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
        source_type: Optional[str] = None,
        sub_category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        上传并处理文档

        Args:
            file_path: 文件保存路径
            file_filename: 原始文件名
            user_id: 用户 ID
            tenant_id: 租户 ID
            source_type: 文档来源类型（恒为顶级分类代号），默认 "file"
            sub_category: 文档直接所属分类的 source_type 代号（挂子分类时传），顶级分类下为 None

        Returns:
            处理结果
        """
        try:
            # 0. 校验子分类归属（分类存在、同租户、非顶级分类、祖先根分类 source_type 等于入参 source_type）
            self._validate_sub_category(tenant_id, source_type, sub_category)

            # 1. 解析文档
            parser = parser_factory.get_parser(file_path)
            if not parser:
                raise ValueError(f"不支持的文件格式: {file_filename}")

            parse_result = await parser.parse(file_path)

            # 2. 分块（将文件名加入文本内容，便于搜索时匹配文件名）
            text_with_filename = f"文档标题：{file_filename}\n\n{parse_result.text}"
            chunks = self.chunker.chunk(text_with_filename)
            if not chunks:
                raise ValueError("文档内容为空或无法提取文本")

            # 3. 向量化（批量）
            from src.config.settings import get_embedding_api_key
            embedding_api_key = get_embedding_api_key()
            # TODO: 后续支持 key 池轮询或并发控制，避免单 key 限流
            embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
            chunk_texts = [c["text"] for c in chunks]
            embedding_client.reset_usage()
            embeddings = await embedding_client.embed_batch(chunk_texts)
            # 读取 embedding usage（供计费）
            embedding_usage_tokens = embedding_client.last_usage_tokens

            # 4. 生成文档摘要
            summary = await self.generate_summary(
                text=parse_result.text or "",
                title=file_filename
            )
            # 读取摘要 LLM usage（供计费）
            summary_usage = self._last_summary_usage

            # 5. 保存到数据库
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                # 获取文件扩展名
                ext = Path(file_filename).suffix.lower().lstrip('.')

                # 插入文档记录（PostgreSQL 使用 RETURNING 获取 ID）
                doc_uuid = f"doc_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO documents (
                        user_id, tenant_id, title, source_type, sub_category, file_type, file_path,
                        file_size, total_chunks, embedding_model,
                        raw_text, metadata, summary, uuid
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    user_id,
                    tenant_id,
                    file_filename,
                    source_type or "file",
                    sub_category,
                    ext,
                    file_path,
                    os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                    len(chunks),
                    "text-embedding-v3",
                    parse_result.text if parse_result.text else None,
                    json.dumps(parse_result.metadata) if parse_result.metadata else None,
                    summary or None,
                    doc_uuid
                ))
                # row 是 dict: {"id": ...}，对应 RETURNING id
                doc_id = cursor.fetchone()["id"]

                # 插入 chunks
                chunk_ids = []
                for chunk in chunks:
                    chunk_uuid = f"chunk_{uuid.uuid4().hex[:12]}"
                    cursor.execute("""
                        INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata, uuid)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        doc_id,
                        chunk["index"],
                        chunk["text"],
                        chunk["tokens"],
                        json.dumps({"char_count": len(chunk["text"])}),
                        chunk_uuid
                    ))
                    # row 是 dict: {"id": ...}，对应 RETURNING id
                    chunk_ids.append(cursor.fetchone()["id"])

                # 插入向量（复用同一个数据库连接，避免锁冲突）
                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.insert(chunk_ids, embeddings)

                # FTS5 触发器会自动处理，无需手动插入

                conn.commit()

            logger.info(f"后端日志：文档上传成功，doc_id={doc_id}, 文件={file_filename}, chunks={len(chunks)}")

            # 知识库文档处理独立计费（source_type=knowledge_embedding）
            # 包含 embedding tokens + 摘要 LLM tokens，失败只记日志不影响文档上传
            try:
                self._record_knowledge_embedding_billing(
                    tenant_id=tenant_id,
                    user_id=str(user_id) if user_id is not None else None,
                    doc_id=doc_id,
                    file_filename=file_filename,
                    embedding_tokens=embedding_usage_tokens,
                    summary_usage=summary_usage,
                )
            except Exception as billing_err:
                logger.error(f"知识库文档计费失败: {billing_err}", exc_info=True)

            return {
                "success": True,
                "document_id": doc_id,
                "title": file_filename,
                "total_chunks": len(chunks),
                "message": "文档处理成功"
            }

        except Exception as e:
            error_str = str(e)
            # 避免重复过滤
            if "=***" not in error_str:
                error_str = sanitize_error_info(error_str)
            logger.error(f"后端日志：文档上传失败: {error_str}", exc_info=True)
            return {
                "success": False,
                "error": "文档上传失败，请稍后重试",
                "debug": error_str,
                "document_id": None
            }

    async def delete_document(self, doc_id: int, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """删除文档

        对象级租户校验（安全加固设计 §2.4）：SQL 本体携带租户条件，不依赖路由参数
        或上游查询结果。对当前上下文不可见（跨租户/无主）的文档一律按「文档不存在」
        处理，不泄漏存在性：
        - 有租户上下文：仅能删除本租户文档
        - 无租户上下文（demo/无租户模式）：仅能删除 demo/无主文档，与检索侧
          （hybrid_retriever/vector_db）及下载侧口径一致，绝不触碰真实租户数据
        """
        # 租户作用域条件（常量拼接，无用户输入插值；无租户上下文时收窄到 demo/无主文档）
        if tenant_id:
            scope_sql, scope_params = "tenant_id = %s", [tenant_id]
        else:
            scope_sql, scope_params = "(tenant_id = 'demo' OR tenant_id IS NULL)", []
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                # 获取文件路径（带租户条件，跨租户文档视为不存在）
                cursor.execute(
                    f"SELECT file_path FROM documents WHERE id = %s AND {scope_sql}",
                    [doc_id] + scope_params)
                row = cursor.fetchone()
                if not row:
                    return {"success": False, "error": "文档不存在"}

                # row 是 dict: {"file_path": ...}，对应 SELECT file_path
                file_path = row["file_path"]

                # 删除向量（复用同一个数据库连接，避免锁冲突；文档归属已在上方按租户校验）
                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.delete_by_doc(doc_id)

                # 删除 chunks（FTS 触发器会自动删除；chunks 表无 tenant_id 列，
                # 经 documents 子查询携带租户条件）
                cursor.execute(
                    f"""
                    DELETE FROM chunks
                    WHERE doc_id = %s
                      AND doc_id IN (SELECT id FROM documents WHERE id = %s AND {scope_sql})
                    """,
                    [doc_id, doc_id] + scope_params)

                # 删除文档记录（带租户条件）
                cursor.execute(
                    f"DELETE FROM documents WHERE id = %s AND {scope_sql}",
                    [doc_id] + scope_params)

                conn.commit()

            # 删除文件
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                # 清理空的租户目录
                try:
                    parent_dir = os.path.dirname(file_path)
                    if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                        logger.info(f"已清理空目录: {parent_dir}")
                except OSError:
                    pass  # 目录非空或无权限，忽略

            logger.info(f"后端日志：文档删除成功，doc_id={doc_id}")
            return {"success": True, "message": "文档已删除"}

        except Exception as e:
            logger.error(f"后端日志：文档删除失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def move_documents(self, tenant_id: str, doc_ids: List[int],
                       source_type: str, sub_category: Optional[str]) -> Dict[str, Any]:
        """批量移动文档到目标分类（仅 UPDATE documents.source_type + sub_category，chunks/向量无需改动）。
        返回 {success, moved, skipped}；skipped = 传入数与实际更新数之差（含他租户文档、已在目标分类的文档）。"""
        if not doc_ids:
            return {"success": False, "error": "未选择文档", "status": 400}
        if not source_type:
            return {"success": False, "error": "目标顶级分类不能为空", "status": 400}
        try:
            self._validate_sub_category(tenant_id, source_type, sub_category)
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                if sub_category is None:
                    # 防御：documents.source_type 恒为顶级分类代号，sub_category 为空时目标必须是本租户顶级分类
                    cursor.execute(
                        "SELECT 1 FROM knowledge_categories WHERE tenant_id = %s AND source_type = %s AND parent_id IS NULL",
                        (tenant_id, source_type)
                    )
                    if not cursor.fetchone():
                        return {"success": False, "error": "目标顶级分类不存在", "status": 400}
                cursor.execute(
                    "UPDATE documents SET source_type = %s, sub_category = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ANY(%s) AND tenant_id = %s "
                    "AND NOT (source_type = %s AND COALESCE(sub_category, '') = COALESCE(%s, ''))",
                    (source_type, sub_category, doc_ids, tenant_id, source_type, sub_category)
                )
                moved = cursor.rowcount
                conn.commit()
            skipped = len(doc_ids) - moved
            logger.info(f"后端日志：文档移动成功，doc_ids={doc_ids}, 目标={source_type}/{sub_category}, moved={moved}, skipped={skipped}")
            return {"success": True, "moved": moved, "skipped": skipped}
        except ValueError as e:
            return {"success": False, "error": str(e), "status": 400}
        except Exception as e:
            logger.error(f"后端日志：文档移动失败: {e}", exc_info=True)
            return {"success": False, "error": "移动文档失败", "debug": sanitize_error_info(str(e))}

    def count_documents(self, user_id: Optional[int] = None, tenant_id: Optional[str] = None, source_type: Optional[str] = None, sub_category: Optional[str] = None) -> int:
        """获取文档总数

        租户作用域（安全加固设计 §2.4）：有租户上下文只统计本租户；无租户上下文
        （demo/无租户模式）收窄到 demo/无主文档，与 list_documents/检索侧口径一致，
        绝不统计真实租户文档。
        """
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                conditions = []
                params = []

                if tenant_id:
                    conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                else:
                    # 常量拼接，无用户输入插值；无租户上下文时收窄到 demo/无主文档
                    conditions.append("(tenant_id = 'demo' OR tenant_id IS NULL)")
                if user_id:
                    conditions.append("user_id = %s")
                    params.append(user_id)
                if source_type is not None:
                    conditions.append("source_type = %s")
                    params.append(source_type)
                if sub_category is not None:
                    if tenant_id is not None:
                        # 展开为自身 + 所有后代分类，保证与 list_documents 语义一致
                        subtree = self._get_category_subtree_source_types(tenant_id, sub_category)
                        conditions.append("sub_category = ANY(%s)")
                        params.append(subtree)
                    else:
                        conditions.append("sub_category = %s")
                        params.append(sub_category)

                where_clause = " AND ".join(conditions)
                if where_clause:
                    cursor.execute(f"SELECT COUNT(*) AS count FROM documents WHERE {where_clause}", params)
                else:
                    cursor.execute("SELECT COUNT(*) AS count FROM documents")

                # row 是 dict: {"count": ...}，对应 SELECT COUNT(*) AS count
                count = cursor.fetchone()["count"]
                return count
        except Exception as e:
            logger.error(f"后端日志：获取文档总数失败: {e}", exc_info=True)
            return 0

    def list_documents(
        self,
        user_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        source_type: Optional[str] = None,
        sub_category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """获取文档列表

        租户作用域（安全加固设计 §2.4）：有租户上下文只返回本租户文档；无租户上下文
        （demo/无租户模式）收窄到 demo/无主文档，与 count_documents/检索侧口径一致，
        绝不返回真实租户文档。
        """
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                placeholder = "%s"

                conditions = []
                params = []

                if tenant_id:
                    conditions.append(f"tenant_id = {placeholder}")
                    params.append(tenant_id)
                else:
                    # 常量拼接，无用户输入插值；无租户上下文时收窄到 demo/无主文档
                    conditions.append("(tenant_id = 'demo' OR tenant_id IS NULL)")
                if user_id:
                    conditions.append(f"user_id = {placeholder}")
                    params.append(user_id)
                if source_type is not None:
                    conditions.append(f"source_type = {placeholder}")
                    params.append(source_type)
                if sub_category is not None:
                    if tenant_id is not None:
                        # 展开为自身 + 所有后代分类，与 count_documents / list_categories 语义一致
                        subtree = self._get_category_subtree_source_types(tenant_id, sub_category)
                        conditions.append(f"sub_category = ANY({placeholder})")
                        params.append(subtree)
                    else:
                        conditions.append(f"sub_category = {placeholder}")
                        params.append(sub_category)

                where_clause = ""
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)

                cursor.execute(f"""
                    SELECT id, title, source_type, sub_category, file_type, file_path, file_size,
                           total_chunks, created_at, summary
                    FROM documents
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT {limit} OFFSET {offset}
                """, params)

                rows = cursor.fetchall()

                # 转换 datetime 为字符串
                result = []
                for row in rows:
                    doc = dict(row)
                    if doc.get("created_at"):
                        doc["created_at"] = doc["created_at"].isoformat()
                    result.append(doc)
                return result

        except Exception as e:
            logger.error(f"后端日志：获取文档列表失败: {e}", exc_info=True)
            return []

    def get_document_chunks(self, doc_id: int, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取文档的所有分块（含向量数据）

        对象级租户校验（安全加固设计 §2.4）：chunks 表无 tenant_id 列，经 JOIN
        documents 携带租户条件。跨租户文档返回空，对外表现为「文档不存在或没有
        分块」，不泄漏存在性；无租户上下文（demo/无租户模式）仅可见 demo/无主
        文档，与检索侧口径一致。
        """
        # 租户作用域条件（常量拼接，无用户输入插值；无租户上下文时收窄到 demo/无主文档）
        if tenant_id:
            scope_sql, scope_params = "d.tenant_id = %s", [tenant_id]
        else:
            scope_sql, scope_params = "(d.tenant_id = 'demo' OR d.tenant_id IS NULL)", []
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(f"""
                    SELECT c.id, c.chunk_index, c.text, c.tokens, c.metadata,
                           cv.embedding IS NOT NULL AS has_vector,
                           CASE WHEN cv.embedding IS NOT NULL
                                THEN cv.embedding::text ELSE NULL END AS vector_text
                    FROM chunks c
                    JOIN documents d ON d.id = c.doc_id AND {scope_sql}
                    LEFT JOIN chunks_vec cv ON c.id = cv.chunk_id
                    WHERE c.doc_id = %s
                    ORDER BY c.chunk_index
                """, scope_params + [doc_id])

                rows = cursor.fetchall()

                return [
                    {
                        "chunk_id": row["id"],
                        "index": row["chunk_index"],
                        "text": row["text"],
                        "tokens": row["tokens"],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                        "has_vector": bool(row["has_vector"]),
                        "vector_text": row["vector_text"]
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error(f"后端日志：获取文档分块失败: {e}", exc_info=True)
            return []

    async def search_documents(
        self,
        query: str,
        user_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
        top_k: int = 10,
        source_type: Optional[str] = None,
        sub_category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        根据内容搜索文档（混合检索：向量 + FTS5 + RRF）

        Args:
            query: 搜索关键词
            user_id: 用户 ID（权限控制，暂未实现）
            tenant_id: 租户 ID
            top_k: 返回结果数量
            source_type: 顶级分类代号，提供时只搜索该分类（含其下所有子级）
            sub_category: 直接选中分类代号，展开为自身 + 所有后代分类再过滤（不传时全分类搜索）

        Returns:
            搜索结果
        """
        try:
            # 初始化 HybridRetriever
            from src.knowledge.retriever.hybrid_retriever import HybridRetriever
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.knowledge.vector_db.vector_db import get_vector_db

            from src.config.settings import get_embedding_api_key
            try:
                embedding_api_key = get_embedding_api_key()
            except ValueError as e:
                return {
                    "success": False,
                    "error": "Embedding API key 未配置，请设置环境变量 EMBEDDING_API_KEY",
                    "results": [],
                    "count": 0
                }

            with self._get_db_connection() as conn:
                embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
                vector_db = get_vector_db(dimension=1024, conn=conn)

                retriever = HybridRetriever(
                    vector_db=vector_db,
                    embedding_client=embedding_client,
                    conn=conn
                )

                # 选中子分类时展开为自身 + 所有后代，与分类树过滤语义一致
                sub_categories = None
                if sub_category:
                    if tenant_id is not None:
                        sub_categories = self._get_category_subtree_source_types(tenant_id, sub_category)
                    else:
                        sub_categories = [sub_category]

                # 执行混合检索
                results = await retriever.retrieve(
                    query=query, top_k=top_k, user_id=user_id, tenant_id=tenant_id,
                    source_type=source_type, sub_categories=sub_categories,
                )

                # 提取文档标题
                if results:
                    doc_ids = {r["doc_id"] for r in results}
                    placeholders = ','.join(['%s'] * len(doc_ids))

                    cursor = conn.cursor()
                    # 按 tenant_id 过滤，确保租户隔离
                    if tenant_id is not None:
                        cursor.execute(f"""
                            SELECT id, title, file_type, file_path FROM documents
                            WHERE id IN ({placeholders}) AND tenant_id = %s
                        """, list(doc_ids) + [tenant_id])
                    else:
                        # 无租户上下文同样收窄到 demo/无主文档（检索侧 hybrid_retriever/
                        # vector_db 已收窄，此处兜底防上游口径漂移，§2.4）
                        cursor.execute(f"""
                            SELECT id, title, file_type, file_path FROM documents
                            WHERE id IN ({placeholders})
                              AND (tenant_id = 'demo' OR tenant_id IS NULL)
                        """, list(doc_ids))

                    # 转换为 dict 以便访问列名
                    rows = [dict(row) for row in cursor.fetchall()]
                    doc_info = {row["id"]: {"title": row["title"], "file_type": row["file_type"], "file_path": row["file_path"]} for row in rows}

                    # 格式化结果
                    formatted_results = [
                        {
                            "doc_id": r["doc_id"],
                            "chunk_id": r["chunk_id"],
                            "text": r["text"],
                            "title": doc_info.get(r["doc_id"], {}).get("title", "未知文档"),
                            "file_type": doc_info.get(r["doc_id"], {}).get("file_type", ""),
                            "file_path": doc_info.get(r["doc_id"], {}).get("file_path", ""),
                            "score": round(r["score"], 4)
                        }
                        for r in results
                    ]
                else:
                    formatted_results = []

                logger.info(f"后端日志：文档搜索完成，查询={query}, 结果数={len(formatted_results)}")
                return {
                    "success": True,
                    "results": formatted_results,
                    "count": len(formatted_results)
                }

        except Exception as e:
            error_str = str(e)
            if "=***" not in error_str:
                error_str = sanitize_error_info(error_str)
            logger.error(f"后端日志：文档搜索失败: {error_str}", exc_info=True)
            return {
                "success": False,
                "error": "搜索失败，请稍后重试",
                "debug": error_str,
                "results": [],
                "count": 0
            }


# 全局单例
knowledge_service = KnowledgeBaseService()
