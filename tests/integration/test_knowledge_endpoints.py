"""
Knowledge API 端到端测试
测试数据库交互层：文档管理、文本块管理
注：文档上传和向量化涉及文件处理和 LLM 调用，仅测试数据库交互层
"""

import pytest
import uuid
import os
import json
from pathlib import Path


class TestKnowledgeDocuments:
    """知识库文档测试"""

    @pytest.fixture
    def test_user_for_knowledge(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"kb_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "kb_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield user_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_document(self, test_user_for_knowledge):
        """测试创建文档记录"""
        from src.db.database import get_db_connection

        user_id = test_user_for_knowledge

        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO documents (
                    user_id, title, source_type, file_type, file_path,
                    file_size, total_chunk, embedding_model, raw_text, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id,
                "Test Document",
                "file",
                "txt",
                "/uploads/test.txt",
                1024,
                5,
                "text-embedding-v3",
                "Sample text content",
                json.dumps({"author": "test"})
            ))

            doc_id = cursor.fetchone()["id"]

            conn.commit()

        # 验证创建成功
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            doc = cursor.fetchone()

        assert doc is not None
        assert doc["title"] == "Test Document"

    def test_get_document_by_id(self, test_user_for_knowledge):
        """测试根据 ID 获取文档"""
        from src.db.database import get_db_connection

        user_id = test_user_for_knowledge

        # 先创建文档
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO documents (
                    user_id, title, source_type, file_type, file_path,
                    file_size, total_chunk, embedding_model
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id,
                "Get Test Doc",
                "file",
                "pdf",
                "/uploads/test.pdf",
                2048,
                10,
                "text-embedding-v3"
            ))
            doc_id = cursor.fetchone()["id"]
            conn.commit()

        # 根据 ID 获取
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            doc = cursor.fetchone()

        assert doc is not None
        assert doc["id"] == doc_id

    def test_list_documents_by_user(self, test_user_for_knowledge):
        """测试列出用户的所有文档"""
        from src.db.database import get_db_connection

        user_id = test_user_for_knowledge

        # 创建多个文档
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for i in range(3):
                cursor.execute("""
                    INSERT INTO documents (
                        user_id, title, source_type, file_type, file_path,
                        file_size, total_chunk, embedding_model
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id,
                    f"Document {i}",
                    "file",
                    "txt",
                    f"/uploads/doc{i}.txt",
                    1000 + i,
                    5,
                    "text-embedding-v3"
                ))
            conn.commit()

        # 列出文档
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM documents WHERE user_id = %s", (user_id,))
            docs = cursor.fetchall()

        assert len(docs) >= 3

    def test_delete_document(self, test_user_for_knowledge):
        """测试删除文档"""
        from src.db.database import get_db_connection

        user_id = test_user_for_knowledge

        # 创建文档
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO documents (
                    user_id, title, source_type, file_type, file_path,
                    file_size, total_chunk, embedding_model
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id,
                "To Delete",
                "file",
                "txt",
                "/uploads/delete.txt",
                500,
                2,
                "text-embedding-v3"
            ))
            doc_id = cursor.fetchone()["id"]
            conn.commit()

        # 删除文档
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            conn.commit()

        # 验证删除成功
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            doc = cursor.fetchone()

        assert doc is None


class TestKnowledgeChunks:
    """知识库文本块测试"""

    @pytest.fixture
    def test_doc_id(self, test_user_for_knowledge):
        """创建测试文档并返回 doc_id"""
        from src.db.database import get_db_connection

        user_id = test_user_for_knowledge

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO documents (
                    user_id, title, source_type, file_type, file_path,
                    file_size, total_chunk, embedding_model
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id,
                "Chunk Test Doc",
                "file",
                "txt",
                "/uploads/chunk_test.txt",
                3000,
                5,
                "text-embedding-v3"
            ))
            doc_id = cursor.fetchone()["id"]
            conn.commit()

        yield doc_id

    def test_create_chunk(self, test_doc_id):
        """测试创建文本块"""
        from src.db.database import get_db_connection

        doc_id = test_doc_id

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chunk (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (
                doc_id,
                0,
                "This is the first chunk of text.",
                100,
                json.dumps({"char_count": 35})
            ))

            chunk_id = cursor.fetchone()["id"]
            conn.commit()

        assert chunk_id is not None

    def test_get_chunks_by_doc(self, test_doc_id):
        """测试获取文档的所有文本块"""
        from src.db.database import get_db_connection

        doc_id = test_doc_id

        # 创建多个文本块
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for i in range(3):
                cursor.execute("""
                    INSERT INTO chunk (doc_id, chunk_index, text, tokens, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    doc_id,
                    i,
                    f"Chunk content {i}",
                    100 + i * 10,
                    json.dumps({"char_count": 15 + i * 2})
                ))
            conn.commit()

        # 获取所有文本块
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM chunk WHERE doc_id = %s ORDER BY chunk_index
            """, (doc_id,))
            chunks = cursor.fetchall()

        assert len(chunk) >= 3

    def test_delete_chunks_by_doc(self, test_doc_id):
        """测试删除文档的所有文本块"""
        from src.db.database import get_db_connection

        doc_id = test_doc_id

        # 创建文本块
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for i in range(2):
                cursor.execute("""
                    INSERT INTO chunk (doc_id, chunk_index, text, tokens)
                    VALUES (%s, %s, %s, %s)
                """, (doc_id, i, f"To delete {i}", 100))
            conn.commit()

        # 删除文本块
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chunk WHERE doc_id = %s", (doc_id,))
            conn.commit()

        # 验证删除成功
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chunk WHERE doc_id = %s", (doc_id,))
            chunks = cursor.fetchall()

        assert len(chunk) == 0