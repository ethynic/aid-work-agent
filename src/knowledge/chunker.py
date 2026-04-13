"""
文本分块器 - 将长文本切分成小块
"""

import re
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
        将文本切分成块（按句子边界分割，保留语义完整性）

        优化说明：
        - 原方案：按固定字符数分割，会在句子中间硬切，破坏语义完整性
        - 新方案：按句子边界（。！？；\n）分割，每块累积直到接近 chunk_size

        Returns:
            List[{"text": str, "tokens": int, "index": int}]
        """
        if not text or not text.strip():
            return []

        # TODO(方案2): 可选实现 LLM 智能分片
        # 调用 LLM 分析文本语义结构，在自然断点分割：
        # prompt = f"将以下文本分成语义完整的块，每块约 {self.chunk_size} 字符。
        # 返回 JSON 数组格式..."
        # 优点：真正理解语义，最佳分割点
        # 缺点：成本高（每次分片都需调用 LLM），速度慢，响应格式不稳定

        # TODO(方案3): 可选实现混合方案
        # 1. 基础分块（按段落+句子）
        # 2. 检测可疑边界（两块存在共现实体/关键词）
        # 3. 对可疑边界调用 LLM 确认是否合并
        # 优点：平衡质量和成本，只对可疑位置调用 LLM
        # 缺点：实现复杂度较高

        # ======== 方案1：按句子边界分割（当前实现） ========

        # 1. 清理空白字符，规范化段落
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        # 2. 按段落分割
        paragraphs = text.split("\n\n")

        # 3. 按句子边界（。！？；以及段落结束）分割，合并成块
        chunks = []
        current_chunk = ""
        current_chars = 0
        chunk_index = 0

        # 句子结束标记
        sentence_ends = set('。！？；')

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 按句子分割段落
            sentences = self._split_into_sentences(para)

            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue

                sent_chars = len(sent)

                # 如果单个句子就超过 chunk_size，需要特殊处理
                if sent_chars > self.chunk_size:
                    # 先保存当前累积的块
                    if current_chunk:
                        chunks.append({
                            "text": current_chunk.strip(),
                            "tokens": self._estimate_tokens(current_chunk),
                            "index": chunk_index
                        })
                        chunk_index += 1
                        current_chunk = ""
                        current_chars = 0

                    # 超长句子按字符分割（保留部分语义）
                    sub_chunks = self._split_long_text(sent)
                    for sub in sub_chunks:
                        chunks.append({
                            "text": sub.strip(),
                            "tokens": self._estimate_tokens(sub),
                            "index": chunk_index
                        })
                        chunk_index += 1
                    continue

                # 检查加入当前句子后是否超限
                if current_chars + sent_chars > self.chunk_size:
                    if current_chunk:
                        chunks.append({
                            "text": current_chunk.strip(),
                            "tokens": self._estimate_tokens(current_chunk),
                            "index": chunk_index
                        })
                        chunk_index += 1

                    # 保留重叠部分（上一个句子的尾部）
                    overlap_text = ""
                    if current_chunk and self.overlap > 0:
                        # 取最后一个完整句子作为 overlap
                        overlap_text = self._get_tail_sentences(current_chunk, 1)
                    current_chunk = overlap_text + sent if overlap_text else sent
                    current_chars = len(current_chunk)
                else:
                    current_chunk += " " + sent if current_chunk else sent
                    current_chars += sent_chars

        # 最后一块
        if current_chunk:
            chunks.append({
                "text": current_chunk.strip(),
                "tokens": self._estimate_tokens(current_chunk),
                "index": chunk_index
            })

        # 4. 对超长块进行二次分割（防止 Embedding API 超限）
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

    def _split_into_sentences(self, text: str) -> List[str]:
        """
        将文本按句子边界分割成句子列表

        句子边界：。！？；以及英文的 .!?

        Returns:
            句子列表
        """
        # 先处理中文句子边界
        result = []
        buffer = ""

        i = 0
        while i < len(text):
            char = text[i]

            # 检测句子结束标记
            if char in '。！？；':
                buffer += char
                result.append(buffer)
                buffer = ""
            elif char == '.' or char == '!' or char == '?':
                buffer += char
                # 检查是否是英文句子结束（后面跟空格或结束）
                if i + 1 >= len(text) or text[i + 1] in ' \n\t':
                    result.append(buffer)
                    buffer = ""
            elif char == '\n':
                # 换行符作为段落分隔，保留当前累积内容
                if buffer:
                    result.append(buffer)
                    buffer = ""
            else:
                buffer += char

            i += 1

        # 处理剩余内容
        if buffer:
            result.append(buffer)

        # 过滤空句子
        return [s.strip() for s in result if s.strip()]

    def _get_tail_sentences(self, text: str, n: int = 1) -> str:
        """
        获取文本末尾的 n 个完整句子

        用于 overlap 部分，保留语义连贯性

        Args:
            text: 文本
            n: 返回的句子数量

        Returns:
            末尾的 n 个句子
        """
        sentences = self._split_into_sentences(text)
        if len(sentences) <= n:
            return text
        return "".join(sentences[-n:])
