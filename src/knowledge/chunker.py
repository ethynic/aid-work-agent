"""
文本分块器 - 将长文本切分成小块
"""

from typing import List, Dict, Any
from loguru import logger

# Embedding API 的最大输入限制（通义千问 text-embedding-v3 限制为 8192 tokens，约 6000-8000 字符）
MAX_EMBEDDING_CHUNK_CHARS = 6000


class TextChunker:
    """文本分块器"""

    def __init__(
        self,
        chunk_size: int = 512,      # 每块字符数
        overlap: int = 64            # 重叠字符数
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def _estimate_tokens(self, text: str) -> int:
        """估算 Token 数量（中文约 1.5 字/Token，英文约 4 字/Token）"""
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return chinese_chars + other_chars // 4

    def _split_long_text(self, text: str) -> List[str]:
        """
        将超长文本强制分割成多个小块

        用于处理单个段落超过 Embedding API 限制的情况
        """
        if len(text) <= MAX_EMBEDDING_CHUNK_CHARS:
            return [text]

        # 按固定长度分割，保留重叠
        chunks = []
        start = 0
        while start < len(text):
            end = start + MAX_EMBEDDING_CHUNK_CHARS
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - self.overlap  # 重叠部分

        logger.warning(
            f"后端日志：文本超过 {MAX_EMBEDDING_CHUNK_CHARS} 字符，"
            f"强制分割为 {len(chunks)} 块"
        )
        return chunks

    def chunk(self, text: str) -> List[Dict[str, Any]]:
        """
        将文本切分成块（按字符数分割，估算 Token）

        Returns:
            List[{"text": str, "tokens": int, "index": int}]
        """
        if not text or not text.strip():
            return []

        # 1. 按段落分割
        paragraphs = text.split("\n\n")

        # 2. 合并段落直到达到 chunk_size
        chunks = []
        current_chunk = ""
        current_chars = 0
        chunk_index = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_chars = len(para)

            if current_chars + para_chars > self.chunk_size:
                if current_chunk:
                    chunks.append({
                        "text": current_chunk.strip(),
                        "tokens": self._estimate_tokens(current_chunk),
                        "index": chunk_index
                    })
                    chunk_index += 1

                # 保留重叠部分
                overlap_text = current_chunk[-self.overlap:] if current_chunk else ""
                current_chunk = overlap_text + "\n\n" + para if overlap_text else para
                current_chars = len(current_chunk)
            else:
                current_chunk += "\n\n" + para if current_chunk else para
                current_chars += para_chars

        # 最后一块
        if current_chunk:
            chunks.append({
                "text": current_chunk.strip(),
                "tokens": self._estimate_tokens(current_chunk),
                "index": chunk_index
            })

        # 3. 对超长块进行二次分割（防止 Embedding API 超限）
        final_chunks = []
        final_index = 0
        for chunk in chunks:
            text_len = len(chunk["text"])
            if text_len > MAX_EMBEDDING_CHUNK_CHARS:
                # 超长块需要强制分割
                sub_texts = self._split_long_text(chunk["text"])
                for sub_text in sub_texts:
                    final_chunks.append({
                        "text": sub_text.strip(),
                        "tokens": self._estimate_tokens(sub_text),
                        "index": final_index
                    })
                    final_index += 1
            else:
                chunk["index"] = final_index
                final_chunks.append(chunk)
                final_index += 1

        logger.info(f"后端日志：文本分块完成，块数={len(final_chunks)}, 总字符={len(text)}")
        return final_chunks
