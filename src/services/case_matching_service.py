"""
通用案例匹配服务，基于向量相似度 + 结构化属性匹配。

复用场景：
- 投诉智能体的历史案例检索
- 技术支持智能体的历史工单检索
- 售前方案推荐的历史方案检索

MVP 阶段使用 LLM 直接匹配方案。
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from loguru import logger


@dataclass
class CaseMatch:
    """案例匹配结果"""
    complaint_id: str
    similarity: float
    category: str
    description: str
    resolution: str
    customer_satisfied: bool = False
    resolution_time_hours: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class CaseMatchingService:
    """
    通用案例匹配服务。
    MVP 阶段：LLM 直接匹配（将历史案例摘要发给 LLM 判断相似度）。
    """

    async def find_similar(
        self,
        query: str,
        domain: str = "complaint",
        filters: Dict = None,
        top_k: int = 5,
    ) -> List[CaseMatch]:
        """
        查找相似案例。

        Args:
            query: 查询描述
            domain: 领域标识
            filters: 结构化过滤条件
            top_k: 返回最多 K 条结果
        """
        try:
            cases = self._fetch_cases_from_db(domain, filters, limit=20)
            if not cases:
                return []

            return await self._llm_match(query, cases, top_k)
        except Exception as e:
            logger.opt(exception=True).error(f"案例匹配失败: {e}")
            return []

    async def index_case(
        self,
        case_id: str,
        domain: str,
        content: str,
        metadata: Dict = None,
    ) -> bool:
        """将案例索引（当前已通过数据库表自动存储）"""
        return True

    def _fetch_cases_from_db(self, domain: str, filters: Dict = None, limit: int = 20) -> List[Dict]:
        """从数据库获取候选案例"""
        try:
            from src.db.database import get_db_connection, get_postgres_pool, init_postgres_pool
            if get_postgres_pool() is None:
                init_postgres_pool()

            with get_db_connection() as conn:
                cursor = conn.cursor()

                sql = """
                    SELECT complaint_id, category, sub_category, problem_summary,
                           solution, resolution_time_hours, customer_satisfied, tags
                    FROM bs_complaint_handling_case_solutions
                    WHERE effective = true
                """
                params = []

                if filters:
                    if filters.get("category"):
                        sql += " AND category = %s"
                        params.append(filters["category"])
                    if filters.get("tenant_id"):
                        sql += " AND tenant_id = %s"
                        params.append(filters["tenant_id"])

                sql += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                cursor.execute(sql, params)
                rows = cursor.fetchall()

                cases = []
                for row in rows:
                    cases.append({
                        "complaint_id": row[0],
                        "category": row[1],
                        "sub_category": row[2],
                        "problem_summary": row[3],
                        "solution": row[4],
                        "resolution_time_hours": float(row[5]) if row[5] else 0.0,
                        "customer_satisfied": row[6],
                        "tags": row[7],
                    })
                return cases
        except Exception as e:
            logger.opt(exception=True).error(f"获取候选案例失败: {e}")
            return []

    async def _llm_match(self, query: str, cases: List[Dict], top_k: int) -> List[CaseMatch]:
        """使用 LLM 进行案例相似度匹配"""
        try:
            from src.llm.gateway import llm_gateway

            case_descriptions = []
            for i, case in enumerate(cases):
                case_descriptions.append(
                    f"[案例{i+1}] ID:{case['complaint_id']} 分类:{case['category']}\n"
                    f"问题: {case['problem_summary']}\n"
                    f"方案: {case['solution']}\n"
                    f"客户满意: {'是' if case['customer_satisfied'] else '否'} | 耗时: {case['resolution_time_hours']}h"
                )

            prompt = f"""请比较以下投诉与历史案例的相似度，返回最相似的{top_k}个案例。

当前投诉：{query}

历史案例：
{chr(10).join(case_descriptions)}

请返回JSON数组，按相似度从高到低排序：
[{{"index": 1, "similarity": 0.85, "reason": "相似原因"}}]

只返回JSON，不要包含其他内容。"""

            messages = [
                {"role": "system", "content": "你是案例相似度匹配引擎。分析文本语义相似度，返回JSON。"},
                {"role": "user", "content": prompt},
            ]

            response = await llm_gateway.chat_lite(
                messages=messages,
                temperature=0.1,
                max_tokens=1000,
            )

            # 累加 LLM 用量到当前 SessionRecordService（对话内后台 LLM 调用计费）
            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(response.get("usage") if isinstance(response, dict) else None)

            content = response.get("content", "")
            matches = self._parse_json_array(content)
            if not matches:
                return []

            results = []
            for match in matches[:top_k]:
                idx = match.get("index", 0) - 1
                if 0 <= idx < len(cases):
                    case = cases[idx]
                    results.append(CaseMatch(
                        complaint_id=case["complaint_id"],
                        similarity=float(match.get("similarity", 0.5)),
                        category=case["category"],
                        description=case["problem_summary"],
                        resolution=case["solution"],
                        customer_satisfied=case["customer_satisfied"],
                        resolution_time_hours=case["resolution_time_hours"],
                    ))
            return results
        except Exception as e:
            logger.opt(exception=True).error(f"LLM案例匹配失败: {e}")
            return []

    def _parse_json_array(self, text: str) -> List[Dict]:
        """从LLM响应中提取JSON数组"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return []
            return []


case_matching_service = CaseMatchingService()
