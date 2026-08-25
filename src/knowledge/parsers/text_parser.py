"""
纯文本文档解析器 - 支持 txt, md, json, yaml, log, csv, xml, ini, properties, conf, config 等格式
"""

import json
from typing import List
from pathlib import Path
from loguru import logger
from . import BaseParser, ParseResult


class TextParser(BaseParser):
    """纯文本文档解析器"""

    def supported_extensions(self) -> List[str]:
        return [
            ".txt",    # 纯文本
            ".md",     # Markdown
            ".json",   # JSON
            ".yaml",   # YAML
            ".yml",    # YAML (短扩展名)
            ".log",    # 日志文件
            ".csv",    # CSV 逗号分隔值（作为文本读取）
            ".xml",    # XML
            ".ini",    # INI 配置
            ".properties",  # Java properties
            ".conf",   # 配置文件
            ".config", # 配置文件
        ]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"后端日志：开始解析纯文本文档: {file_path}")
        try:
            path = Path(file_path)
            ext = path.suffix.lower()

            # 尝试多种编码读取
            encodings = ["utf-8", "gbk", "gb2312", "latin-1"]
            content = ""
            used_encoding = ""

            for encoding in encodings:
                try:
                    with open(file_path, "r", encoding=encoding) as f:
                        content = f.read()
                    used_encoding = encoding
                    break
                except UnicodeDecodeError:
                    continue

            if not content and used_encoding == "":
                # 所有编码都失败，作为二进制错误处理
                logger.warning(f"后端日志：无法解码文件: {file_path}")
                return ParseResult(text="", metadata={"error": "无法解码文件"})

            # 根据文件类型做特殊处理
            metadata = {
                "file_type": ext,
                "encoding": used_encoding,
                "char_count": len(content),
                "line_count": len(content.splitlines()),
            }

            # JSON 格式化，提升可读性
            if ext == ".json":
                try:
                    parsed = json.loads(content)
                    content = json.dumps(parsed, ensure_ascii=False, indent=2)
                    metadata["json_valid"] = True
                except json.JSONDecodeError:
                    metadata["json_valid"] = False
                    logger.warning(f"后端日志：JSON 格式无效: {file_path}")

            if not content:
                logger.warning(f"后端日志：纯文本文档内容为空: {file_path}")

            logger.info(f"后端日志：纯文本文档解析完成: {file_path}, 编码={used_encoding}, 字符数={len(content)}")
            return ParseResult(text=content, metadata=metadata)

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：纯文本文档解析失败: {file_path}, error: {e}")
            raise
