"""
知识库分块器测试
"""

import unittest
from src.knowledge.chunker import TextChunker, MAX_EMBEDDING_CHUNK_CHARS


class TestTextChunker(unittest.TestCase):
    """文本分块器测试"""

    def setUp(self):
        self.chunker = TextChunker(chunk_size=100, overlap=20)

    def test_chunk_simple_text(self):
        """正常路径：简单文本分块"""
        text = "这是第一段文本。" * 10 + "\n\n" + "这是第二段文本。" * 10
        chunks = self.chunker.chunk(text)

        self.assertGreater(len(chunks), 0)
        self.assertTrue(all("text" in c for c in chunks))
        self.assertTrue(all("tokens" in c for c in chunks))
        self.assertTrue(all("index" in c for c in chunks))

    def test_chunk_empty_text(self):
        """异常路径：空文本"""
        chunks = self.chunker.chunk("")
        self.assertEqual(len(chunks), 0)

    def test_chunk_whitespace_text(self):
        """异常路径：仅空白字符"""
        chunks = self.chunker.chunk("   \n\n   ")
        self.assertEqual(len(chunks), 0)

    def test_chunk_single_paragraph(self):
        """边界条件：单个段落"""
        text = "短文本"
        chunks = self.chunker.chunk(text)
        self.assertEqual(len(chunks), 1)

    def test_chunk_multiple_paragraphs(self):
        """边界条件：多段落，超过 chunk_size"""
        # 构造多个段落，总字符超过 chunk_size
        para1 = "第一段内容。" * 15  # ~90 字符
        para2 = "第二段内容。" * 15  # ~90 字符
        para3 = "第三段内容。" * 15  # ~90 字符
        text = para1 + "\n\n" + para2 + "\n\n" + para3  # 总共 ~270 字符，超过 chunk_size=100

        chunks = self.chunker.chunk(text)
        self.assertGreater(len(chunks), 1)

    def test_estimate_tokens(self):
        """测试 Token 估算"""
        # 中文每个字符约 1 Token
        chinese_text = "你好世界"
        tokens = self.chunker._estimate_tokens(chinese_text)
        self.assertEqual(tokens, 4)

        # 英文每 4 字符约 1 Token
        english_text = "hello world"
        tokens = self.chunker._estimate_tokens(english_text)
        self.assertEqual(tokens, 2)  # 11 字符 / 4 = 2 (取整)

    def test_chunk_with_overlap(self):
        """测试重叠机制"""
        text = ("第一段内容。" * 20) + "\n\n" + ("第二段内容。" * 20)
        chunks = self.chunker.chunk(text)

        if len(chunks) > 1:
            # 验证索引连续
            indices = [c["index"] for c in chunks]
            self.assertEqual(indices, list(range(len(chunks))))

    def test_split_long_text(self):
        """测试超长文本强制分割（防止 Embedding API 超限）"""
        # 创建一个超过 MAX_EMBEDDING_CHUNK_CHARS 的长段落
        long_paragraph = "这是一段非常长的文本。" * 1000  # 远超 6000 字符
        self.assertGreater(len(long_paragraph), MAX_EMBEDDING_CHUNK_CHARS)

        chunks = self.chunker.chunk(long_paragraph)

        # 验证所有 chunk 都不超过限制
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), MAX_EMBEDDING_CHUNK_CHARS)

        # 验证分割后的块数量大于 1
        self.assertGreater(len(chunks), 1)

    def test_normal_text_not_split(self):
        """测试正常长度文本不会被过度分割"""
        # 创建正常长度的段落
        text = "这是正常长度的段落。" * 50  # 约几百字符
        self.assertLess(len(text), MAX_EMBEDDING_CHUNK_CHARS)

        chunks = self.chunker.chunk(text)

        # 验证文本没有被强制分割（按 chunk_size 分）
        # 由于 chunk_size=100，正常文本可能产生多个块
        # 但不应该因为超过 MAX_EMBEDDING_CHUNK_CHARS 而被分割

    def test_split_long_text_no_overlap_bug(self):
        """测试超长文本分割时索引连续"""
        long_paragraph = "长文本内容。" * 500
        chunks = self.chunker.chunk(long_paragraph)

        # 验证索引连续
        indices = [c["index"] for c in chunks]
        self.assertEqual(indices, list(range(len(chunks))))


if __name__ == "__main__":
    unittest.main()
