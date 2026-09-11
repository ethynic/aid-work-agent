"""
数据分析 - Schema 提取器

使用 LLM 从表格/数据库表信息中提取语义化的表结构描述。
"""

import json
import re
from typing import Any, Dict, List

from loguru import logger

from src.llm.gateway import LLMGateway


# Schema 提取的系统提示词
_SCHEMA_SYSTEM_PROMPT = """你是一个数据库表结构分析专家。你的任务是分析表格数据，生成语义化的表结构描述。

你必须严格按照指定的 JSON 格式输出，不要输出任何其他内容。"""

_SCHEMA_USER_PROMPT_TEMPLATE = """请分析以下表格数据，生成表结构描述。

表名提示: {table_name_hint}

列名: {columns}
推断类型: {data_types}

采样数据（前几行）:
{sample_data_str}

请严格按照以下 JSON 格式输出（不要包含 markdown 代码块标记）:
{{
    "table_name": "推荐的英文表名（蛇形命名法）",
    "description": "表的中文描述（一句话说明这表的用途）",
    "columns": [
        {{
            "name": "原始列名",
            "semantic_name": "语义化的英文列名（蛇形命名法）",
            "data_type": "推断的数据类型: text/integer/decimal/date/boolean",
            "description": "列的中文描述（说明这列存什么数据）",
            "enum_values": [],
            "sample_values": ["样例值1", "样例值2"]
        }}
    ]
}}

注意事项:
1. table_name 要能体现数据含义，使用蛇形命名法（如 customer_orders）
2. 每列的 semantic_name 要简洁、有意义（如 customer_phone 而不是 c_ph）
3. description 要准确描述列的数据含义，不要猜测不确定的内容
4. enum_values 仅在列值明显是枚举时填写（如性别、状态类字段）
5. sample_values 从采样数据中取 2-3 个代表性值
6. 如果某列看起来是主键或唯一标识，在 description 中说明"""

# 关系推断的提示词
_RELATION_SYSTEM_PROMPT = """你是一个数据库关系分析专家。你的任务是分析多个表的结构，推断它们之间可能存在的关联关系。

你必须严格按照指定的 JSON 格式输出，不要输出任何其他内容。"""

_RELATION_USER_PROMPT_TEMPLATE = """请分析以下表结构，推断它们之间的关联关系。

表结构:
{schemas_str}

请严格按照以下 JSON 数组格式输出（不要包含 markdown 代码块标记）:
[
    {{
        "from_table": "源表名",
        "from_column": "源表列名",
        "to_table": "目标表名",
        "to_column": "目标表列名",
        "relation_type": "关系类型: one_to_one / one_to_many / many_to_one / many_to_many",
        "description": "关系描述"
    }}
]

推断依据:
1. 列名相同或相似（如 user_id / user_id）
2. 列名是另一张表名+_id 的模式
3. 语义上存在引用关系
4. 如果没有明显关系，返回空数组 []

注意: 只输出有较高可信度的关系，不要猜测不确定的关系。"""


class SchemaExtractor:
    """Schema 提取器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self) -> LLMGateway:
        """延迟初始化 LLM Gateway"""
        if self._gateway is None:
            self._gateway = LLMGateway()
        return self._gateway

    async def extract_schema(
        self,
        sheet_info: Dict[str, Any],
        table_name_hint: str = "",
    ) -> Dict[str, Any]:
        """
        从 sheet/表信息中提取语义化 schema。

        Args:
            sheet_info: 包含 sheet_name, columns, sample_data, data_types 的字典
            table_name_hint: 表名提示

        Returns:
            {
                "table_name": str,
                "description": str,
                "columns": [
                    {
                        "name": str,
                        "semantic_name": str,
                        "data_type": str,
                        "description": str,
                        "enum_values": list,
                        "sample_values": list,
                    }
                ]
            }
        """
        columns = sheet_info.get("columns", [])
        data_types = sheet_info.get("data_types", {})
        sample_data = sheet_info.get("sample_data", [])

        # 格式化采样数据
        sample_data_str = self._format_sample_data(sample_data, columns)

        user_prompt = _SCHEMA_USER_PROMPT_TEMPLATE.format(
            table_name_hint=table_name_hint or sheet_info.get("sheet_name", ""),
            columns=", ".join(str(c) for c in columns),
            data_types=json.dumps(data_types, ensure_ascii=False),
            sample_data_str=sample_data_str,
        )

        try:
            gateway = self._get_gateway()
            response = await gateway.chat_lite(
                messages=[
                    {"role": "system", "content": _SCHEMA_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )

            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                source="schema_extract",
                model=gateway.get_model_name(),
            )

            content = response.get("content", "")
            schema = self._parse_json_response(content)

            if schema:
                # 确保必要字段存在
                schema.setdefault("table_name", table_name_hint or sheet_info.get("sheet_name", "unknown"))
                schema.setdefault("description", "")
                schema.setdefault("columns", [])
                return schema

        except Exception as e:
            logger.opt(exception=True).error(f"LLM schema 提取失败: {e}")

        # LLM 失败时返回基础 schema（best-effort）
        return self._build_fallback_schema(sheet_info, table_name_hint)

    async def infer_relations(self, schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        推断多个表之间的关联关系。

        Args:
            schemas: 多个表的 schema 列表

        Returns:
            关联关系列表
        """
        if len(schemas) < 2:
            return []

        # 构建 schema 描述文本
        schemas_str = self._format_schemas_for_relation(schemas)

        user_prompt = _RELATION_USER_PROMPT_TEMPLATE.format(
            schemas_str=schemas_str,
        )

        try:
            gateway = self._get_gateway()
            response = await gateway.chat_lite(
                messages=[
                    {"role": "system", "content": _RELATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )

            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                source="schema_infer_relations",
                model=gateway.get_model_name(),
            )

            content = response.get("content", "")
            relations = self._parse_json_response(content)

            if isinstance(relations, list):
                return relations
            if isinstance(relations, dict) and "relations" in relations:
                return relations["relations"]

        except Exception as e:
            logger.opt(exception=True).error(f"LLM 关系推断失败: {e}")

        return []

    def _parse_json_response(self, content: str) -> Any:
        """
        从 LLM 响应中解析 JSON。

        处理可能的格式问题：markdown 代码块、多余文本等。
        """
        if not content:
            return None

        # 去除 markdown 代码块标记
        content = re.sub(r"```(?:json)?\s*", "", content)
        content = re.sub(r"```\s*", "", content)
        content = content.strip()

        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 对象
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # 尝试提取 JSON 数组
        match = re.search(r"\[[\s\S]*\]", content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning(f"无法解析 LLM 返回的 JSON: {content[:200]}")
        return None

    def _format_sample_data(
        self, sample_data: List[List], columns: List[str]
    ) -> str:
        """格式化采样数据为文本"""
        if not sample_data:
            return "（无数据）"

        lines = []
        for i, row in enumerate(sample_data[:10]):
            row_parts = []
            for j, val in enumerate(row):
                col_name = columns[j] if j < len(columns) else f"col_{j}"
                row_parts.append(f"{col_name}={val}")
            lines.append(f"行{i + 1}: {', '.join(row_parts)}")

        return "\n".join(lines)

    def _format_schemas_for_relation(self, schemas: List[Dict]) -> str:
        """格式化多个 schema 用于关系推断"""
        parts = []
        for schema in schemas:
            table_name = schema.get("table_name", "unknown")
            description = schema.get("description", "")
            columns = schema.get("columns", [])

            col_lines = []
            for col in columns:
                col_line = f"    - {col.get('name', '')}"
                semantic = col.get("semantic_name", "")
                if semantic:
                    col_line += f" ({semantic})"
                col_line += f": {col.get('data_type', 'text')}"
                col_desc = col.get("description", "")
                if col_desc:
                    col_line += f" — {col_desc}"
                col_lines.append(col_line)

            part = f"表: {table_name}\n描述: {description}\n列:\n" + "\n".join(col_lines)
            parts.append(part)

        return "\n\n".join(parts)

    def _build_fallback_schema(
        self, sheet_info: Dict, table_name_hint: str
    ) -> Dict[str, Any]:
        """LLM 失败时构建基础 schema"""
        columns = sheet_info.get("columns", [])
        data_types = sheet_info.get("data_types", {})
        sample_data = sheet_info.get("sample_data", [])

        col_defs = []
        for col_name in columns:
            sample_values = []
            for row in sample_data[:3]:
                idx = columns.index(col_name)
                if idx < len(row) and row[idx] is not None:
                    sample_values.append(str(row[idx]))

            col_defs.append({
                "name": col_name,
                "semantic_name": col_name,
                "data_type": data_types.get(col_name, "text"),
                "description": "",
                "enum_values": [],
                "sample_values": sample_values[:3],
            })

        return {
            "table_name": table_name_hint or sheet_info.get("sheet_name", "unknown"),
            "description": "",
            "columns": col_defs,
        }
