"""页面元数据 API — 列表查询 + AI 推荐"""
import json
import re
import yaml
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from src.llm.gateway import llm_gateway


# ============== YAML 加载与缓存 ==============

_metadata_cache: Optional[Dict[str, Any]] = None
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "page_metadata.yaml"


_domain_map: Optional[Dict[str, str]] = None


def _load_metadata() -> Dict[str, Any]:
    """加载 page_metadata.yaml（带模块级缓存，部署后不变）"""
    global _metadata_cache, _domain_map
    if _metadata_cache is not None:
        return _metadata_cache
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        _metadata_cache = yaml.safe_load(f)
    # 构建一次 domain_id→label 查找字典
    _domain_map = {d["id"]: d.get("label", d["id"]) for d in _metadata_cache.get("domains", [])}
    return _metadata_cache


def _get_published_pages(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """返回所有 published 页面，可选按 domain 过滤"""
    metadata = _load_metadata()
    pages = metadata.get("pages", [])
    result = []
    for p in pages:
        if p.get("status") != "published":
            continue
        if domain and p.get("domain") != domain:
            continue
        result.append({
            "page_id": p["page_id"],
            "title": p["title"],
            "description": p.get("description", ""),
            "route": p["route"],
            "icon": p.get("icon", ""),
            "domain": p["domain"],
            "domain_label": _domain_map.get(p["domain"], p["domain"]) if _domain_map else p["domain"],
        })
    return result


# ============== JSON 提取工具 ==============

def _extract_json(content: str):
    """分层从 LLM 输出中提取 JSON：先直接解析 → 再提取 code block → 最后正则兜底"""
    # 1. 直接解析
    text = content.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. 提取 ```json ... ``` 代码块
    code_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. 正则兜底：匹配第一个完整 JSON 对象（非贪婪）
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ============== 端点处理函数（供 agent_definitions.py 调用） ==============

async def list_pages(request: Request, domain: Optional[str] = None) -> JSONResponse:
    """返回已发布页面列表"""
    from src.api.agent_definitions import _require_admin, _success
    _require_admin(request)
    return _success(_get_published_pages(domain))


async def recommend_pages(request: Request, body: dict) -> JSONResponse:
    """AI 推荐相关页面"""
    from src.api.agent_definitions import _require_admin, _success, _error
    _require_admin(request)

    agent_description = body.get("agent_description", "")
    current_pages = body.get("current_pages", [])

    if not agent_description:
        return _error("请提供智能体描述")

    all_pages = _get_published_pages()
    if not all_pages:
        return _success({"recommended": []})

    # 排除已选页面
    current_set = set(current_pages)
    candidates = [p for p in all_pages if p["page_id"] not in current_set]
    if not candidates:
        return _success({"recommended": []})

    # 构建 LLM prompt
    page_list_str = "\n".join(
        f"- page_id: {p['page_id']}, title: {p['title']}, description: {p['description']}"
        for p in candidates
    )

    system_prompt = """你是一个业务系统配置助手。根据用户提供的智能体描述，从可用的业务页面列表中推荐最相关的页面。

## 输出要求
- 严格返回 JSON 格式：{"recommended": [{"page_id": "...", "reason": "推荐原因（一句话中文）", "relevance_score": 0.0到1.0}]}
- 按相关性从高到低排序
- 只推荐真正相关的页面，不要强行推荐不相关的
- 如果没有相关页面，返回空数组

只输出 JSON，不要输出其他内容。"""

    user_message = f"""## 智能体描述
{agent_description}

## 已选页面（不需要再推荐）
{', '.join(current_pages) if current_pages else '无'}

## 可用业务页面
{page_list_str}"""

    try:
        result = await llm_gateway.chat_no_thinking(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        from src.services.session_record import record_admin_llm_usage
        record_admin_llm_usage(
            result,
            tenant_id=getattr(request.state, "tenant_id", None),
            user_id=getattr(request.state, "user_id", None),
            source_label="recommend_pages",
            model=llm_gateway.get_model_name(),  # chat_no_thinking 沿用主链路模型，按主模型单价计费
        )
        content = result.get("content", "")

        # 分层提取 JSON
        parsed = _extract_json(content)
        if parsed is not None:
            recommended = parsed.get("recommended", [])
            page_map = {p["page_id"]: p for p in candidates}
            for r in recommended:
                pid = r.get("page_id", "")
                if pid in page_map:
                    r["title"] = page_map[pid]["title"]
            return _success({"recommended": recommended})
    except Exception as e:
        logger.error(f"AI 页面推荐失败: {e}")

    # 降级：返回所有未选页面（不排序）
    fallback = [
        {"page_id": p["page_id"], "title": p["title"], "reason": "", "relevance_score": 0.5}
        for p in candidates
    ]
    return _success({"recommended": fallback})
