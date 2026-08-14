"""
Excel 工具内部 LLM 路由器

根据 Agent 传来的 context 和 file_paths，调用 LLM 判断应执行哪些操作及参数。
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


ROUTING_PROMPT_PREFIX = """你是 Excel 电子表格处理工具的内部路由器。根据用户的请求和上下文，决定应该执行哪些操作并提取参数。

## 可用操作

1. **read** — 读取 Excel 文件的数据内容
   - 触发：用户想看Excel里有什么数据、查看某个Sheet
   - 参数：{sheet_name: "可选", range: "可选,如A1:D10", include_formulas: false}

2. **to_md** — 将 Excel 转为 Markdown 表格
   - 触发：用户要求查看Excel内容、将Excel转为文本
   - 参数：{sheet_name: "可选", max_rows: 100}

3. **export** — 从数据创建新的 Excel 文件
   - 触发：用户要求导出数据为Excel、把表格数据存为Excel、创建报表
   - 参数：{data_type: "markdown|csv|json|table", file_name: "输出文件名", sheet_name: "可选", auto_format: true}

4. **modify** — 修改已有 Excel 文件内容
   - 触发：用户要求修改单元格、插入行列、删除行列、合并单元格
   - 参数：{operations: [{type, ...具体参数}], output_name: "可选"}

5. **format** — 设置 Excel 格式样式
   - 触发：用户要求设置字体、边框、颜色、列宽、数字格式
   - 参数：{format_operations: [{type, ...具体参数}], output_name: "可选"}

6. **fill_template** — 使用模板填充数据（两种模式）
   - 触发：用户要求基于模板/样例生成Excel、按某个样例版式填写数据
   - 模板来源：a) template_name=系统模板名 b) template_file=用户上传的模板路径 c) file_paths 中的样例附件
   - **模式一（智能填充，推荐）**：调用方直接传 `data`（结构化 {meta,rows,group_subtotals,totals}）+ 样例附件。
     工具会 AI 分析样例结构并按版式填入，自动处理行数多/少/相等、保留样例样式。**注意：data 由调用方在工具入参直接传递，路由器无需从 context 提取，仅需返回 task=fill_template + template_file=附件路径。**
   - **模式二（占位符替换，旧）**：样例含 `{{变量名}}` 时用 variables={key:value} 替换
   - 参数：{template_file: "样例路径", variables: "模式二用", output_name: "可选"}

7. **list_templates** — 列出可用模板
   - 触发：用户问有哪些模板可用
   - 参数：{}

8. **merge** — 合并多个文件
   - 触发：用户要求合并多个Excel/CSV文件
   - 参数：{output_name: "可选", merge_mode: "rows|sheets"}

9. **convert** — 格式转换
   - 触发：用户要求CSV转Excel、Excel转CSV、JSON转Excel
   - 参数：{source_format: "csv|json|excel", target_format: "csv|json|excel", output_name: "可选"}

## 判断规则

按优先级从高到低匹配：
- 有附件且上下文提到"模板"、"按这个格式"、"照着这个填"等 → fill_template（template_file = 附件路径）
- 要求基于系统模板生成（无附件模板，提到模板名或要求选择）→ fill_template（template_name）或先 list_templates
- 有附件且要求转为Markdown理解 → to_md
- 要求修改已有Excel → modify
- 要求设置样式格式 → format
- 要求创建新Excel / 导出数据 → export（但 context 中必须包含实际数据，如果只有用户意图没有数据，则 params 中标注 needs_data: true）
- 要求格式转换 → convert
- 要求合并文件 → merge
- 要求查看数据内容 → read
- 仅有附件无明确指令 → to_md（默认将内容转为可理解格式）

## export 操作的数据检查

export 操作必须检查 context 中是否包含实际表格数据（Markdown表格有 | 分隔符，JSON有 [] 或 {}）：
- 如果 context 包含实际数据 → 正常返回 export，params 中包含 data_type
- 如果 context 只有用户意图描述（如"帮我生成Excel"、"导出数据"等），没有实际数据 → 仍然返回 export，但在 params 中加 "needs_data": true
- 路由器不要返回 to_md 等其他操作来替代 export，即使数据不完整也优先返回 export

## 输出格式

严格输出 JSON，不要输出其他内容：
{
  "task": "操作名（可逗号分隔多个，如 read,export）",
  "params": { ... 操作对应参数 ... },
  "reason": "判断依据"
}

## fill_template 参数示例

场景一：用户选择系统模板
用户："用月度报告模板生成4月份报告"
→ {"task": "fill_template", "params": {"template_name": "monthly_report", "variables": {"month": "4月"}}, "reason": "用户指定系统模板名"}

场景二：用户上传临时模板
用户上传 attachment.xlsx 后说："按这个模板填一下，公司名是XX科技"
→ {"task": "fill_template", "params": {"template_file": "/path/to/attachment.xlsx", "variables": {"公司名": "XX科技"}}, "reason": "用户上传模板并要求填充"}

场景三：用户不确定有什么模板
用户："有哪些报告模板？"
→ {"task": "list_templates", "params": {}, "reason": "用户要求查看模板列表"}

## 用户上下文
"""

ROUTING_PROMPT_SUFFIX = """

## 附件文件
"""


class ExcelRouter:
    """Excel 工具内部 LLM 路由器"""

    VALID_TASKS = {
        "read", "to_md", "export", "modify", "format",
        "fill_template", "list_templates", "merge", "convert",
    }

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    async def route(self, context: Optional[str], file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        根据 context 和 file_paths 决定操作类型和参数。

        Returns:
            {"task": "export", "params": {...}} 或 {"task": "", "params": {}, "error": "..."}
        """
        rule_result = self._rule_based_route(context, file_paths)
        if rule_result.get("task"):
            logger.info(
                f"[ExcelRouter] 规则路由结果: task={rule_result['task']}, "
                f"reason={rule_result.get('reason', '')}"
            )
            return rule_result

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
                source="excel_router",
            )

            content = response.get("content", "")
            if not content:
                logger.warning("[ExcelRouter] LLM 返回空内容")
                if rule_result.get("task"):
                    return rule_result
                return {"task": "", "params": {}, "error": "LLM 返回空内容"}

            result = self._parse_response(content)
            if result.get("task"):
                logger.info(f"[ExcelRouter] 路由结果: task={result['task']}, reason={result.get('reason', '')}")
                return result

            logger.warning(f"[ExcelRouter] 无法确定操作: {content[:200]}")
            if rule_result.get("task"):
                return rule_result
            return {"task": "", "params": {}, "error": "LLM 无法确定操作类型"}

        except Exception as e:
            logger.error(f"[ExcelRouter] LLM 调用失败: {e}")
            if rule_result.get("task"):
                return rule_result
            return {"task": "", "params": {}, "error": str(e)}

    def _build_prompt(self, context: Optional[str], file_paths: Optional[List[str]]) -> str:
        ctx = context or "(无上下文)"
        files = json.dumps(file_paths, ensure_ascii=False) if file_paths else "(无附件)"
        return ROUTING_PROMPT_PREFIX + ctx + ROUTING_PROMPT_SUFFIX + files

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """解析 LLM 输出为结构化 JSON"""
        json_str = content.strip()

        if json_str.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

        try:
            data = json.loads(json_str)
            task = data.get("task", "")
            params = data.get("params", {})

            tasks = [t.strip() for t in task.split(",") if t.strip()]
            invalid = [t for t in tasks if t not in self.VALID_TASKS]
            if invalid:
                logger.warning(f"[ExcelRouter] 无效操作: {invalid}")
                return {"task": "", "params": {}, "error": f"无效操作: {invalid}"}

            return {
                "task": task,
                "params": params,
                "reason": data.get("reason", ""),
            }
        except json.JSONDecodeError:
            logger.warning(f"[ExcelRouter] JSON 解析失败: {content[:200]}")
            return {"task": "", "params": {}, "error": "JSON 解析失败"}

    def _rule_based_route(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Handle deterministic spreadsheet tasks without depending on LLM routing."""
        ctx = context or ""
        paths = file_paths or []
        lower_ctx = ctx.lower()

        first_path = Path(paths[0]) if paths else None
        first_ext = first_path.suffix.lower() if first_path else ""

        wants_excel = any(
            token in lower_ctx
            for token in ("excel", "xlsx", "电子表格", "转为excel", "转成excel", "导出", "创建")
        )

        if paths and first_ext == ".csv" and wants_excel:
            return {
                "task": "convert",
                "params": {
                    "source_format": "csv",
                    "target_format": "excel",
                    "output_name": first_path.with_suffix(".xlsx").name,
                },
                "reason": "规则识别：CSV附件转Excel",
            }

        if paths and first_ext == ".json" and wants_excel:
            return {
                "task": "convert",
                "params": {
                    "source_format": "json",
                    "target_format": "excel",
                    "output_name": first_path.with_suffix(".xlsx").name,
                },
                "reason": "规则识别：JSON附件转Excel",
            }

        data_type = self._detect_context_data_type(ctx)
        if data_type and wants_excel:
            return {
                "task": "export",
                "params": {
                    "data_type": data_type,
                    "file_name": self._extract_output_name(ctx),
                    "sheet_name": "Sheet1",
                    "auto_format": True,
                },
                "reason": f"规则识别：context包含{data_type}表格数据并要求导出Excel",
            }

        if paths and any(token in lower_ctx for token in ("markdown", "文本", "查看", "读取", "内容")):
            return {
                "task": "to_md",
                "params": {},
                "reason": "规则识别：附件转为可读文本",
            }

        if len(paths) >= 2 and any(token in lower_ctx for token in ("合并", "merge")):
            return {
                "task": "merge",
                "params": {"merge_mode": "sheets"},
                "reason": "规则识别：合并多个表格文件",
            }

        return {"task": "", "params": {}, "error": "规则路由未命中"}

    def _detect_context_data_type(self, context: str) -> str:
        stripped = (context or "").strip()
        if not stripped:
            return ""
        if self._has_markdown_table(stripped):
            return "markdown"
        if stripped.startswith("[") or stripped.startswith("{"):
            return "json"
        if "," in stripped and "\n" in stripped:
            return "csv"
        return ""

    def _has_markdown_table(self, text: str) -> bool:
        table_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("|") and line.strip().endswith("|")
        ]
        if len(table_lines) < 2:
            return False
        return any(re.match(r"^\|[\s\-:|]+\|$", line) for line in table_lines)

    def _extract_output_name(self, context: str) -> Optional[str]:
        match = re.search(r"([\w\u4e00-\u9fff（）()《》+_\- ]+\.xlsx)", context or "")
        if match:
            return match.group(1).strip()
        return None
