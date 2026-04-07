"""
知识库分块器测试
"""

import pytest

pytestmark = pytest.mark.skills

from src.knowledge.chunker import TextChunker, MAX_EMBEDDING_CHUNK_CHARS


class TestTextChunker:
    """文本分块器测试"""

    @pytest.fixture(autouse=True)
    def setup_chunker(self):
        self.chunker = TextChunker(chunk_size=100, overlap=20)

    def test_chunk_simple_text(self):
        text = "这是第一段文本。" * 10 + "\n\n" + "这是第二段文本。" * 10
        chunks = self.chunker.chunk(text)
        assert len(chunks) > 0
        assert all("text" in c for c in chunks)
        assert all("tokens" in c for c in chunks)
        assert all("index" in c for c in chunks)

    def test_chunk_empty_text(self):
        chunks = self.chunker.chunk("")
        assert len(chunks) == 0

    def test_chunk_whitespace_text(self):
        chunks = self.chunker.chunk("   \n\n   ")
        assert len(chunks) == 0

    def test_chunk_single_paragraph(self):
        chunks = self.chunker.chunk("短文本")
        assert len(chunks) == 1

    def test_chunk_multiple_paragraphs(self):
        para1 = "第一段内容。" * 15
        para2 = "第二段内容。" * 15
        para3 = "第三段内容。" * 15
        text = para1 + "\n\n" + para2 + "\n\n" + para3
        chunks = self.chunker.chunk(text)
        assert len(chunks) > 1

    def test_estimate_tokens_chinese(self):
        tokens = self.chunker._estimate_tokens("你好世界")
        assert tokens == 4

    def test_estimate_tokens_english(self):
        tokens = self.chunker._estimate_tokens("hello world")
        assert tokens == 2

    def test_chunk_with_overlap(self):
        text = ("第一段内容。" * 20) + "\n\n" + ("第二段内容。" * 20)
        chunks = self.chunker.chunk(text)
        if len(chunks) > 1:
            indices = [c["index"] for c in chunks]
            assert indices == list(range(len(chunks)))

    def test_split_long_text(self):
        long_paragraph = "这是一段非常长的文本。" * 1000
        assert len(long_paragraph) > MAX_EMBEDDING_CHUNK_CHARS
        chunks = self.chunker.chunk(long_paragraph)
        for chunk in chunks:
            assert len(chunk["text"]) <= MAX_EMBEDDING_CHUNK_CHARS
        assert len(chunks) > 1

    def test_split_long_text_no_overlap_bug(self):
        long_paragraph = "长文本内容。" * 500
        chunks = self.chunker.chunk(long_paragraph)
        indices = [c["index"] for c in chunks]
        assert indices == list(range(len(chunks)))
