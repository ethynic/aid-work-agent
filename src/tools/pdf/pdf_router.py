"""
PDF 工具内部 LLM 路由器

根据 Agent 传来的 context 和 file_paths，调用 LLM 判断应执行哪些操作及参数。
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger


ROUTING_PROMPT_PREFIX = """你是 PDF 文档处理工具的内部路由器。根据用户的请求和上下文，决定应该执行哪些操作。

## 可用操作

1. read - 读取PDF的文本内容（文字型PDF，使用文本提取）
2. read_tables - 提取PDF中的表格数据
3. ocr - OCR识别PDF内容（扫描件、图片型PDF）
4. pdf_to_md - 将PDF转换为Markdown格式
5. md_to_pdf - 将Markdown文本转换为PDF文件
6. html_to_pdf - 将HTML内容转换为PDF文件；复杂 HTML/CSS 使用 Playwright print-to-pdf
7. docx_to_pdf - 将Word文档转换为PDF文件
8. merge - 合并多个PDF文件
9. split - 按页码范围拆分PDF
10. extract_pages - 提取PDF的指定页面为独立文件
11. inspect - 检查PDF结构、页数、尺寸、元数据、是否加密
12. render_pages - 将PDF页面渲染成PNG图片用于预览或质检
13. validate - 验证PDF是否可打开、页数正常、渲染是否基本正常
14. clean_metadata - 清理PDF元数据
15. add_watermark - 添加文字水印
16. protect - 添加PDF打开密码保护
17. compress - 压缩PDF文件大小
18. extract_images - 提取PDF内嵌图片
19. rotate - 旋转PDF页面

## 判断规则

- 有 PDF 文件且要求读取/查看/了解内容 → read
- 有 PDF 文件且要求提取表格 → read_tables
- 有 PDF 文件且明确要求 OCR 或识别扫描件 → ocr
- 有 PDF 文件且要求转为 Markdown → pdf_to_md
- context 中包含 Markdown 内容（# 标题、| 表格等）且要求生成 PDF → md_to_pdf
- context 中包含 HTML 内容且要求生成 PDF → html_to_pdf；如果强调复杂样式/高保真/网页效果，params.engine="playwright"
- 有 .docx 文件且要求转为 PDF → docx_to_pdf
- 有多个 PDF 文件且要求合并 → merge
- 有 PDF 文件且要求拆分/按页提取 → split 或 extract_pages
- 有 PDF 文件且要求检查结构/元数据/页数/是否正常 → inspect
- 有 PDF 文件且要求渲染页面/生成预览图 → render_pages
- 有 PDF 文件且要求验证质量/检查能否交付/检查排版是否正常 → validate
- 有 PDF 文件且要求清理/删除元数据/作者信息 → clean_metadata
- 有 PDF 文件且要求添加水印 → add_watermark
- 有 PDF 文件且要求加密/密码保护 → protect
- 有 PDF 文件且要求压缩/减小体积 → compress
- 有 PDF 文件且要求提取图片 → extract_images
- 有 PDF 文件且要求旋转页面 → rotate
- 不确定 PDF 是文字型还是扫描件 → 先 read，如果结果太少会自动提示用 ocr
- 操作可组合，如 "先读取内容再转 Markdown" → read,pdf_to_md

## 参数说明

- split 操作需要在 params 中提供 ranges，格式如 ["1-3", "5-7"]
- extract_pages 操作需要在 params 中提供 pages，格式如 [1, 3, 5]（页码，从1开始）
- merge 操作的文件来自 file_paths（多个文件路径）
- md_to_pdf / html_to_pdf 可在 params 中提供 title 和 output_name
- html_to_pdf 可在 params 中提供 engine，支持 auto/playwright/fpdf2；默认 auto 会优先 Playwright print-to-pdf，失败后回退 fpdf2
- 所有用户可见页码均从1开始
- read/read_tables/pdf_to_md/render_pages/validate 操作可在 params 中提供 pages，格式如 [1, 2, 3]（只处理指定页）
- add_watermark 操作需要 params.text
- protect 操作需要 params.password；密码属于敏感信息，不得在 reason 中复述
- rotate 操作需要 params.rotation，支持 90/180/270
- extract_images/rotate 可提供 pages，页码从1开始

## 输出格式

严格输出 JSON，不要输出其他内容：
{
  "task": "read",
  "params": {},
  "reason": "用户要求读取PDF内容"
}

如果是 split 操作：
{
  "task": "split",
  "params": {
    "ranges": ["1-3", "5-7"]
  },
  "reason": "用户要求按页码拆分PDF"
}

如果是 extract_pages 操作：
{
  "task": "extract_pages",
  "params": {
    "pages": [1, 3, 5]
  },
  "reason": "用户要求提取指定页面"
}

如果是 md_to_pdf 操作：
{
  "task": "md_to_pdf",
  "params": {
    "title": "从内容中提取的标题",
    "output_name": "从内容推断的文件名.pdf"
  },
  "reason": "用户要求将Markdown内容转为PDF"
}

## 用户上下文
"""

ROUTING_PROMPT_SUFFIX = """

## 附件文件
"""


class PdfRouter:
    """PDF 工具内部 LLM 路由器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    async def route(self, context: Optional[str], file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        prompt = self._build_prompt(context, file_paths)
        gateway = self._get_gateway()

        try:
            response = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
            )

            content = response.get("content", "")
            if not content:
                logger.warning("[PdfRouter] LLM 返回空内容")
                return {"task": "", "params": {}, "error": "LLM 返回空内容"}

            result = self._parse_response(content)
            if result.get("task"):
                logger.info(f"[PdfRouter] LLM 路由结果: task={result['task']}, reason={result.get('reason', '')}")
                return result

            logger.warning(f"[PdfRouter] LLM 路由无法确定操作: {content[:200]}")
            return {"task": "", "params": {}, "error": "LLM 无法确定操作类型"}

        except Exception as e:
            logger.error(f"[PdfRouter] LLM 调用失败: {e}")
            return {"task": "", "params": {}, "error": str(e)}

    def _build_prompt(self, context: Optional[str], file_paths: Optional[List[str]]) -> str:
        ctx = context or "(无上下文)"
        files = json.dumps(file_paths, ensure_ascii=False) if file_paths else "(无附件)"
        return ROUTING_PROMPT_PREFIX + ctx + ROUTING_PROMPT_SUFFIX + files

    def _parse_response(self, content: str) -> Dict[str, Any]:
        json_str = content.strip()

        if json_str.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

        try:
            data = json.loads(json_str)
            task = data.get("task", "")
            params = data.get("params", {})

            valid_tasks = {
                "read", "read_tables", "ocr", "pdf_to_md",
                "md_to_pdf", "html_to_pdf", "docx_to_pdf",
                "merge", "split", "extract_pages",
                "inspect", "render_pages", "validate",
                "clean_metadata", "add_watermark", "protect",
                "compress", "extract_images", "rotate",
            }

            tasks = [t.strip() for t in task.split(",") if t.strip()]
            invalid = [t for t in tasks if t not in valid_tasks]
            if invalid:
                logger.warning(f"[PdfRouter] LLM 返回无效操作: {invalid}")
                return {"task": "", "params": {}, "error": f"无效操作: {invalid}"}

            return {
                "task": task,
                "params": params,
                "reason": data.get("reason", ""),
            }
        except json.JSONDecodeError:
            logger.warning(f"[PdfRouter] JSON 解析失败: {content[:200]}")
            return {"task": "", "params": {}, "error": "JSON 解析失败"}
