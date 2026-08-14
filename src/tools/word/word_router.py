"""
Word 工具内部 LLM 路由器

根据 Agent 传来的 context 和 file_paths，调用 LLM 判断应执行哪些操作及参数。
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger


ROUTING_PROMPT_PREFIX = """你是 Word 文档处理工具的内部路由器。根据用户的请求和上下文，决定应该执行哪些操作。

## 可用操作

1. read - 读取Word文档的文本内容和表格
2. analyze - 分析Word文档的结构（段落样式、字体、表格布局、页面设置）
3. word_to_md - 将Word文档转换为Markdown文本
4. md_to_word - 将Markdown文本转换为Word文档
5. modify - 修改Word文档内容（替换文本、增删段落/表格等）
6. format - 格式化Word文档（字体、段落格式、页面设置、页眉页脚）
7. fill_template - 填充Word模板中的变量占位符（{{变量名}} 或 [变量名]）
8. list_templates - 列出可用的文档模板
9. diff - 对比两个Word文档的差异

## 判断规则

- context 中包含 Markdown 格式内容（# 标题、| 表格、- 列表等）且要求生成/导出 Word → md_to_word
- context 要求读取/查看/了解 Word 文件内容 → read（如果有文件）
- context 要求分析 Word 文件结构 → analyze
- context 要求修改/编辑/替换 Word 文件 → modify
- context 要求格式化/排版 Word 文件 → format
- context 要求填充模板/替换变量 → fill_template
- context 要求对比/比较两个文件 → diff
- context 要求查看模板 → list_templates
- 有文件且只要求转为 Markdown → word_to_md
- 操作可组合，如 "先读取再修改" → read,modify

## 输出格式

严格输出 JSON，不要输出其他内容：
{
  "task": "md_to_word",
  "params": {
    "template": "default",
    "title": "从内容中提取的标题",
    "output_name": "从内容推断的文件名.docx"
  },
  "reason": "用户要求将行程安排生成Word，context中包含完整的Markdown表格内容"
}

如果是 modify 操作，params 中应包含 operations 列表：
{
  "task": "modify",
  "params": {
    "operations": [
      {"type": "replace_text", "target": "XX公司", "replacement": "YY科技有限公司"}
    ],
    "output_name": "修改后的文件名.docx"
  },
  "reason": "用户要求替换合同中的公司名称"
}

如果是 format 操作：
{
  "task": "format",
  "params": {
    "format_operations": [
      {"type": "set_font", "scope": "all", "font_name": "宋体", "font_size": 14}
    ]
  },
  "reason": "用户要求将文档字体设为宋体14号"
}

如果是 fill_template 操作：
{
  "task": "fill_template",
  "params": {
    "variables": {"甲方": "XX公司", "日期": "2026年5月"}
  },
  "reason": "用户要求填充模板变量"
}

如果是 diff 操作：
{
  "task": "diff",
  "params": {
    "output_format": "text"
  },
  "reason": "用户要求对比两个文档差异"
}

如果是 list_templates / read / analyze / word_to_md 操作，params 可以为空对象 {}。

## 用户上下文
"""

ROUTING_PROMPT_SUFFIX = """

## 附件文件
"""


class WordRouter:
    """Word 工具内部 LLM 路由器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        """延迟初始化 LLM Gateway，避免模块导入时的依赖问题"""
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    async def route(self, context: Optional[str], file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        根据 context 和 file_paths 决定操作类型和参数。

        Returns:
            {"task": "md_to_word", "params": {...}}
            失败时返回 {"task": "", "params": {}, "error": "..."}
        """
        prompt = self._build_prompt(context, file_paths)
        gateway = self._get_gateway()

        try:
            response = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
            )

            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                source="word_router",
            )

            content = response.get("content", "")
            if not content:
                logger.warning("[WordRouter] LLM 返回空内容")
                return {"task": "", "params": {}, "error": "LLM 返回空内容"}

            result = self._parse_response(content)
            if result.get("task"):
                logger.info(f"[WordRouter] LLM 路由结果: task={result['task']}, reason={result.get('reason', '')}")
                return result

            logger.warning(f"[WordRouter] LLM 路由无法确定操作: {content[:200]}")
            return {"task": "", "params": {}, "error": "LLM 无法确定操作类型"}

        except Exception as e:
            logger.error(f"[WordRouter] LLM 调用失败: {e}")
            return {"task": "", "params": {}, "error": str(e)}

    def _build_prompt(self, context: Optional[str], file_paths: Optional[List[str]]) -> str:
        ctx = context or "(无上下文)"
        files = json.dumps(file_paths, ensure_ascii=False) if file_paths else "(无附件)"
        return ROUTING_PROMPT_PREFIX + ctx + ROUTING_PROMPT_SUFFIX + files

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """解析 LLM 输出为结构化 JSON"""
        # 尝试提取 JSON（可能被 markdown 代码块包裹）
        json_str = content.strip()

        # 去掉 markdown 代码块
        if json_str.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

        try:
            data = json.loads(json_str)
            task = data.get("task", "")
            params = data.get("params", {})

            # 校验 task 是否为有效操作
            valid_tasks = {"read", "analyze", "word_to_md", "md_to_word",
                           "modify", "format", "fill_template", "list_templates", "diff"}

            # 支持逗号分隔的多操作
            tasks = [t.strip() for t in task.split(",") if t.strip()]
            invalid = [t for t in tasks if t not in valid_tasks]
            if invalid:
                logger.warning(f"[WordRouter] LLM 返回无效操作: {invalid}")
                return {"task": "", "params": {}, "error": f"无效操作: {invalid}"}

            return {
                "task": task,
                "params": params,
                "reason": data.get("reason", ""),
            }
        except json.JSONDecodeError:
            logger.warning(f"[WordRouter] JSON 解析失败: {content[:200]}")
            return {"task": "", "params": {}, "error": "JSON 解析失败"}
