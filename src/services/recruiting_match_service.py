"""招聘简历-职位匹配度评分服务层（简历-职位匹配设计 §3，Phase 2）

OCR 入库后自动评分：boss_resume_detail / boss_resume_batch 落库成功后逐份调用
evaluate_and_update；前端「重新评分」按钮经 POST /api/recruiting-operator/resumes/{id}/re-evaluate
手动触发（重评场景同函数复用）。

评分流程：
1. 读简历（ocr_text 截断 3000 字）
2. 组装职位上下文：job_requirements 优先（JSONB 原样拼入 prompt）；无 requirements
   或无 job_id 时退回 job_name + 该职位初次开场话术上下文拼一段隐含要求（设计 §3）
3. LLM 一次（主链路模型关思考 chat_no_thinking、temperature=0.1、max_tokens=1024）
4. 解析 JSON 并清洗（score 0-100 截断；key_info 按 §2 固定 schema 键白名单，宁缺勿编）
5. 按职位 match_threshold 判 match_status（≥阈值 matched / <50 rejected / 其余 unmatched）
6. UPDATE 该行 match_score / match_summary / match_status / key_info

稳定性铁律（设计 §3「失败不阻塞」）：
- 本服务不向调用方抛异常：LLM 异常 / JSON 不合法重试 1 次仍败 → match_* 留库中原值
  （重评场景保留旧分不清空，只在成功时覆盖），返回 {resume_id, score: None, note: 原因}
- job_id 与 job_name 皆无 → 直接 skipped 不调 LLM（match_* 留 NULL）；
  OCR 正文为空同理（「仅依据简历文本」铁律下无正文可评，宁缺勿编）

计费：每次成功的 LLM 调用后 record_background_llm_usage（source=recruiting_match，
model 显式传报告模型名，避免兜底分支误用 mid_term 摘要单价，见 session_record.py docstring）。

异步规范：async 入口层调同步 DB 一律 await asyncio.to_thread（.claude/rules/backend_dev.md）。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid as uuid_module
from typing import Any, Dict, List, Optional

import psycopg2.extras
from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection
from src.llm.gateway import llm_gateway
from src.services import recruiting_job_service, recruiting_resume_service
from src.services.session_record import record_background_llm_usage

# 简历 OCR 正文拼入 prompt 的截断上限（设计 §3）
_MAX_RESUME_CHARS = 3000
# LLM 输出 token 上限（score + ≤100 字 summary + key_info 足够）
_MAX_OUTPUT_TOKENS = 3072
# 隐含要求路径：单条初次开场话术拼入 prompt 的截断上限（最多取前 3 条）
_MAX_SCRIPT_CHARS = 200
_MAX_OPENING_SCRIPTS = 3
# 温度：结构化评审要确定性，与 classification_service 一致取 0.1
_TEMPERATURE = 0.1

# key_info 固定 schema（设计 §2）：标量字段（str）+ 数组字段 + 工作年限（int）
_KEY_INFO_STR_FIELDS = ("education", "current_company", "ai_tool_usage", "salary_expectation")
_KEY_INFO_LIST_FIELDS = ("core_skills", "highlights", "concerns")
_KEY_INFO_INT_FIELDS = ("years_of_experience",)

# 计费来源标识（chat_records.source）
_BILLING_SOURCE = "recruiting_match"

# 初次开场分类（隐含要求来源；固定四分类首位）
_OPENING_CATEGORY = recruiting_job_service.SCRIPT_CATEGORIES[0]


async def evaluate_and_update(tenant_id: str, resume_id: int) -> Dict[str, Any]:
    """对一份简历评分并回写 match_* / key_info 四列，不向调用方抛异常。

    返回：
    - 成功：{resume_id, match_score, match_status, match_summary, key_info}
    - 跳过/失败：{resume_id, score: None, note: 中文原因}（库中原值保留不动）
    """
    resume = await asyncio.to_thread(recruiting_resume_service.get_resume, tenant_id, resume_id)
    if resume is None:
        return {"resume_id": resume_id, "score": None, "note": "简历不存在"}

    job_id = resume.get("job_id")
    job_name = (resume.get("job_name") or "").strip() or None
    ocr_text = (resume.get("ocr_text") or "").strip()
    if not job_id and not job_name:
        return {"resume_id": resume_id, "score": None, "note": "简历未关联职位（job_id/job_name 皆无），跳过评分"}
    if not ocr_text:
        return {"resume_id": resume_id, "score": None, "note": "简历无 OCR 正文，跳过评分"}

    # 职位上下文（同步 DB 放线程池）：要求 / 阈值 / 隐含要求来源（备注 + 初次开场话术）
    job_ctx = await asyncio.to_thread(_load_job_context, tenant_id, job_id, job_name)
    # 显式 None 判断而非 or 兜底：match_threshold=0 是合法配置（全员及格），0 不能被吞成默认 70
    threshold = (job_ctx or {}).get("match_threshold")
    if threshold is None:
        threshold = recruiting_job_service.DEFAULT_MATCH_THRESHOLD

    messages = _build_messages(job_name, job_ctx, ocr_text)
    model_name = _safe_model_name(llm_gateway)

    # LLM 最多两次（首次 + 重试 1 次）；失败不外抛，返回 note 由调用方提示
    evaluation: Optional[Dict[str, Any]] = None
    last_error = "未知错误"
    for attempt in (1, 2):
        try:
            chat_kwargs: Dict[str, Any] = {"temperature": _TEMPERATURE, "max_tokens": _MAX_OUTPUT_TOKENS}
            # DeepSeek V4 思考模型的 reasoning 与正文共享 max_tokens 配额
            # （思考耗尽输出空间致 content 为空），评分是结构化抽取任务，
            # chat_no_thinking 沿用主链路模型并自动关思考，调用方无需关心 provider
            response = await llm_gateway.chat_no_thinking(messages=messages, **chat_kwargs)
        except Exception as e:  # noqa: BLE001 LLM 异常统一走重试/降级，绝不外抛
            last_error = f"LLM 调用失败: {type(e).__name__}: {e}"
            logger.warning(f"简历评分 LLM 调用失败（第 {attempt} 次）: resume_id={resume_id}, {last_error}")
            continue

        # 每次成功调用的 usage 都计费（含解析失败的那次——真实 token 已消耗）
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=tenant_id,
            user_id=resume.get("user_id"),
            source=_BILLING_SOURCE,
            user_message=f"简历匹配评分 resume_id={resume_id}",
            model=model_name or _safe_model_name(llm_gateway),
        )

        content = (response.get("content") or "") if isinstance(response, dict) else ""
        evaluation = _parse_evaluation(content)
        if evaluation is not None:
            break
        last_error = "LLM 输出 JSON 不合法或 score 非法"
        logger.warning(f"简历评分输出解析失败（第 {attempt} 次）: resume_id={resume_id}")

    if evaluation is None:
        logger.warning(
            f"简历评分失败（重试后仍败，保留库中原值）: tenant={tenant_id}, "
            f"resume_id={resume_id}, 原因={last_error}"
        )
        return {"resume_id": resume_id, "score": None, "note": f"评分失败：{last_error}"}

    match_status = _decide_match_status(evaluation["score"], threshold)
    updated = await asyncio.to_thread(
        _update_match_fields,
        tenant_id, resume_id,
        evaluation["score"], evaluation["match_summary"], match_status, evaluation["key_info"],
    )
    if not updated:
        return {"resume_id": resume_id, "score": None, "note": "简历不存在（评分期间被删除）"}

    logger.info(
        f"简历评分完成: tenant={tenant_id}, resume_id={resume_id}, "
        f"score={evaluation['score']}, status={match_status}, job={job_name}, threshold={threshold}"
    )
    return {
        "resume_id": resume_id,
        "match_score": evaluation["score"],
        "match_status": match_status,
        "match_summary": evaluation["match_summary"],
        "key_info": evaluation["key_info"],
    }


# ============== 重新评分 v2：优先 VL 看图（去 OCR 化设计 §4.2），无图回退文本路径 ==============

# 简历识别费台账科目/取价（与工具层、解析接口同科目同价，对账同源）
_RECOGNITION_TOOL_NAME = "boss_resume_recognition"


async def re_evaluate(tenant_id: str, resume_id: int) -> Dict[str, Any]:
    """前端「重新评分」入口（v2）：优先 VL 看库中简历图评估；无图历史记录回退文本路径。

    - VL 路径：库中 images[0] 读回 → 切片 → evaluate_resume（candidate_name 传入提示词，
      姓名门不过 → note 说明不入库不扣费）→ 回写 resume_summary/match_*/key_info →
      按份扣简历识别费（评估成功即扣，弹层自愈同款直记）；
    - 文本 fallback：历史记录无图 → evaluate_and_update 原文本评分路径（token 计费不变）；
    - 失败不抛异常（同 evaluate_and_update 语义）：返回 {resume_id, score: None, note}，
      库中原值保留。
    """
    resume = await asyncio.to_thread(recruiting_resume_service.get_resume, tenant_id, resume_id)
    if resume is None:
        return {"resume_id": resume_id, "score": None, "note": "简历不存在"}

    # VL 路径前置条件：库中有图（无图的历史记录走文本 fallback）
    images = resume.get("images") or []
    stitched_b64: Optional[str] = None
    if isinstance(images, list) and images and isinstance(images[0], dict):
        file_id = (images[0].get("file_id") or "").strip()
        if file_id:
            stitched_b64 = await asyncio.to_thread(_read_image_file_b64, tenant_id, file_id)
    if not stitched_b64:
        return await evaluate_and_update(tenant_id, resume_id)

    candidate_name = (resume.get("candidate_name") or "").strip()
    job_ctx = await asyncio.to_thread(
        _load_job_context, tenant_id, resume.get("job_id"), resume.get("job_name")
    )

    # 函数级 import：resume_vl_service 在模块级导入本模块的解析助手，此处延迟避免环
    from src.services import resume_vl_service

    try:
        bands = await asyncio.to_thread(resume_vl_service.slice_stitched_image, stitched_b64)
        evaluation = await resume_vl_service.evaluate_resume(bands, candidate_name, job_ctx)
    except resume_vl_service.ResumeVLError as e:
        logger.warning(f"简历 VL 重评失败（保留库中原值）: tenant={tenant_id}, resume_id={resume_id}, {e}")
        return {"resume_id": resume_id, "score": None, "note": f"VL 评估失败：{e}"}

    # 姓名门（决策⑨）：不符不入库不扣费，全量日志
    name_seen = evaluation.get("name_seen") or ""
    if not resume_vl_service.resume_name_matches(candidate_name, name_seen):
        logger.error(
            f"后端日志：简历重评姓名核对不匹配（不回写不扣费） tenant={tenant_id} "
            f"resume_id={resume_id} candidate_name={candidate_name} name_seen={name_seen} "
            f"bands={len(bands)} model={evaluation.get('model')}"
        )
        return {
            "resume_id": resume_id,
            "score": None,
            "note": f"姓名核对不匹配（页面：{candidate_name}，简历所示：{name_seen}），请人工核对",
        }

    # 计费：VL 评估成功即扣（与工具层/解析接口同科目同价；失败只告警不影响重评结果）
    try:
        from src.local_tools.pricing import resume_recognition_price
        from src.db.client_binding_db import ClientUsageLogDB

        price = resume_recognition_price()
        if price > 0:
            await asyncio.to_thread(
                ClientUsageLogDB.record_tool_usage,
                tenant_id=tenant_id,
                tool_name=_RECOGNITION_TOOL_NAME,
                credit_cost=price,
                user_id=resume.get("user_id"),
            )
    except Exception as e:  # noqa: BLE001 计费失败不影响重评结果（台账可对账）
        logger.opt(exception=True).error(
            f"后端日志：简历重评识别费落账失败（不影响结果）resume_id={resume_id}: {e}"
        )

    score = evaluation.get("score")
    if score is None:
        # 无职位上下文：只回写总结与 key_info，match_* 不动（沿袭「未关联职位跳过评分」）
        await asyncio.to_thread(
            _update_resume_summary_only, tenant_id, resume_id,
            evaluation.get("resume_summary"), evaluation.get("key_info"),
        )
        return {
            "resume_id": resume_id,
            "match_score": None,
            "match_status": None,
            "match_summary": None,
            "key_info": evaluation.get("key_info"),
            "note": "未关联职位，仅更新总结与关键信息（未评分）",
        }

    threshold = (job_ctx or {}).get("match_threshold")
    if threshold is None:
        threshold = recruiting_job_service.DEFAULT_MATCH_THRESHOLD
    status = _decide_match_status(score, threshold)
    updated = await asyncio.to_thread(
        _update_match_fields,
        tenant_id, resume_id, score, evaluation.get("match_summary"), status,
        evaluation.get("key_info"), evaluation.get("resume_summary"),
    )
    if not updated:
        return {"resume_id": resume_id, "score": None, "note": "简历不存在（评分期间被删除）"}

    logger.info(
        f"简历 VL 重评完成: tenant={tenant_id}, resume_id={resume_id}, "
        f"score={score}, status={status}, name_seen={evaluation.get('name_seen')}"
    )
    return {
        "resume_id": resume_id,
        "match_score": score,
        "match_status": status,
        "match_summary": evaluation.get("match_summary"),
        "key_info": evaluation.get("key_info"),
    }


def _update_resume_summary_only(
    tenant_id: str,
    resume_id: int,
    resume_summary: Optional[str],
    key_info: Optional[Dict[str, Any]],
) -> None:
    """仅回写 resume_summary 与 key_info（match_* 不动——无职位上下文时未评分）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_recruiting_operator_resumes
            SET resume_summary = %s, key_info = COALESCE(%s, key_info), updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (
                resume_summary,
                psycopg2.extras.Json(key_info) if key_info is not None else None,
                resume_id, tenant_id,
            ),
        )
        conn.commit()


def _read_image_file_b64(tenant_id: str, file_id: str) -> Optional[str]:
    """从盘上读回简历图（file_id → storage 文件 → base64）；找不到/读取失败返回 None（走 fallback）"""
    import base64 as _base64

    try:
        from src.core.storage import find_uploaded_file_on_disk

        meta = find_uploaded_file_on_disk(file_id)
        if not meta or not meta.get("path"):
            return None
        with open(meta["path"], "rb") as f:
            raw = f.read()
        return _base64.b64encode(raw).decode("ascii") if raw else None
    except Exception as e:  # noqa: BLE001 读图失败转 fallback，不阻塞重评
        logger.warning(f"后端日志：简历图读回失败 file_id={file_id} tenant={tenant_id}: {e}")
        return None


# ============== prompt 组装 ==============

def _build_messages(
    job_name: Optional[str], job_ctx: Optional[Dict[str, Any]], ocr_text: str
) -> List[Dict[str, str]]:
    """组装评分对话（system + user）。user prompt 固定含「仅依据简历文本」铁律与输出 JSON 结构示例。"""
    requirements = (job_ctx or {}).get("job_requirements")
    requirement_lines: List[str] = []
    if isinstance(requirements, dict) and requirements:
        requirement_lines.append("结构化职位要求（JSON）：")
        requirement_lines.append(json.dumps(requirements, ensure_ascii=False))
    else:
        # 隐含要求路径（设计 §3）：job_name + 职位备注 + 初次开场话术内容。
        # 职位名兜底链：简历 job_name → job_id 命中职位的规范名（PATCH 置空 job_name
        # 但 job_id 仍在时避免 prompt 出现「职位名称：None」）
        display_job_name = job_name or (job_ctx or {}).get("job_name") or "（未知）"
        requirement_lines.append("职位未填写结构化要求，请从以下职位信息推断隐含要求：")
        requirement_lines.append(f"- 职位名称：{display_job_name}")
        notes = (job_ctx or {}).get("notes")
        if notes:
            requirement_lines.append(f"- 职位备注：{notes}")
        scripts = (job_ctx or {}).get("opening_scripts") or []
        for s in scripts[:_MAX_OPENING_SCRIPTS]:
            content = (s.get("content") or "")[:_MAX_SCRIPT_CHARS]
            requirement_lines.append(f"- 初次开场话术《{s.get('title') or ''}》：{content}")
        if not notes and not scripts:
            requirement_lines.append("- （无更多信息，仅按职位名称推断）")

    user_prompt = "\n".join([
        "请对候选人简历与职位的匹配度评分，并提取结构化关键信息。",
        "",
        "【职位信息】",
        "\n".join(requirement_lines),
        "",
        "【候选人简历（OCR 文本）】",
        ocr_text[:_MAX_RESUME_CHARS],
        "",
        "【输出要求】只输出一个 JSON 对象（不要 markdown 代码块、不要任何其他文字），结构示例：",
        '{"score": 82, "match_summary": "评分理由，不超过100字", "key_info": {'
        '"years_of_experience": 6, "education": "本科", "current_company": "xx科技", '
        '"core_skills": ["PHP", "Laravel"], "highlights": ["日活十万级 SaaS 主导"], '
        '"ai_tool_usage": "熟练：Cursor 日常开发", "salary_expectation": "15-25K", "concerns": []}}',
        "",
        "【铁律】",
        "- 仅依据简历文本评分与提取，简历中不可见的字段填 null，数组字段缺省给 []，严禁编造。",
        "- score 为 0-100 整数：与职位要求（含隐含要求）越匹配越高，硬性要求不满足时显著扣分。",
        "- match_summary 不超过 100 字，说明给分依据（满足/欠缺的关键点）。",
    ])
    return [
        {
            "role": "system",
            "content": "你是严谨的招聘简历评审引擎。按职位要求对候选人简历打分并提取结构化关键信息，只输出 JSON。",
        },
        {"role": "user", "content": user_prompt},
    ]


# ============== LLM 输出解析与清洗 ==============

def _parse_json(text: str) -> Optional[Any]:
    """从 LLM 响应中提取 JSON（剥 ``` 代码块 + 首尾花括号边界提取，范式同 classification_service）"""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _extract_score(raw: Any) -> Optional[int]:
    """score 清洗：int / 整数 float / 纯数字字符串 → 截断到 0-100；其余非法返回 None（整体按解析失败重试）"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        score = raw
    elif isinstance(raw, float) and raw.is_integer():
        score = int(raw)
    elif isinstance(raw, str) and re.fullmatch(r"\s*\d+\s*", raw):
        score = int(raw.strip())
    else:
        return None
    return max(0, min(100, score))


def _clean_key_info(raw: Any) -> Optional[Dict[str, Any]]:
    """key_info 按 §2 固定 schema 清洗：未知键丢弃，标量非法置 null，数组字段非法/缺省转 []。

    非 dict 输入返回 None（key_info 留 NULL，评分其余字段照常入库）；dict 输入归一为
    全 8 键（缺省标量 null / 缺省数组 []），宁缺勿编。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    cleaned: Dict[str, Any] = {}
    for key in _KEY_INFO_INT_FIELDS:
        val = raw.get(key)
        if isinstance(val, bool):
            cleaned[key] = None
        elif isinstance(val, int) and val >= 0:
            cleaned[key] = val
        elif isinstance(val, float) and val.is_integer() and val >= 0:
            cleaned[key] = int(val)
        elif isinstance(val, str) and re.fullmatch(r"\s*\d+\s*", val):
            cleaned[key] = int(val.strip())
        else:
            cleaned[key] = None
    for key in _KEY_INFO_STR_FIELDS:
        val = raw.get(key)
        cleaned[key] = val.strip() if isinstance(val, str) and val.strip() else None
    for key in _KEY_INFO_LIST_FIELDS:
        val = raw.get(key)
        if isinstance(val, list):
            cleaned[key] = [i.strip() for i in val if isinstance(i, str) and i.strip()]
        else:
            cleaned[key] = []
    return cleaned


def _parse_evaluation(content: str) -> Optional[Dict[str, Any]]:
    """整包解析：JSON dict + 合法 score → {score, match_summary, key_info}；不合法返回 None（走重试）"""
    data = _parse_json(content)
    if not isinstance(data, dict):
        return None
    score = _extract_score(data.get("score"))
    if score is None:
        return None
    summary = data.get("match_summary")
    if isinstance(summary, str):
        summary = summary.strip()[:100] or None
    else:
        summary = None
    return {"score": score, "match_summary": summary, "key_info": _clean_key_info(data.get("key_info"))}


def _decide_match_status(score: int, threshold: int) -> str:
    """阈值判断（设计 §3）：≥ match_threshold → matched；< 50 → rejected；其余 unmatched"""
    if score >= threshold:
        return "matched"
    if score < 50:
        return "rejected"
    return "unmatched"


# ============== 同步 DB 辅助（调用方经 asyncio.to_thread） ==============

def _parse_json_field(val: Any) -> Any:
    """把 JSONB 字段从 str 解析为 dict/list（PostgreSQL JSONB 一般已是 dict，兜底）"""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return val


def _load_job_context(
    tenant_id: str, job_id: Optional[str], job_name: Optional[str]
) -> Optional[Dict[str, Any]]:
    """定位简历关联的职位上下文（job_id 优先精确查；无 job_id 按 job_name 精确匹配兜底）。

    返回 {job_id, job_name, notes, match_threshold, job_requirements, opening_scripts}；
    未命中任何职位返回 None（调用方按 job_name 文本评分，阈值用默认 70）。
    直查 SQL 不走 list_jobs/get_job（职位服务面向管理页 CRUD，评分路径绝不建职位）。
    """
    job_row = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if job_id:
            try:
                job_uuid = str(uuid_module.UUID(str(job_id)))
            except (ValueError, AttributeError, TypeError):
                job_uuid = None
            if job_uuid:
                cursor.execute(
                    """
                    SELECT id, job_name, notes, match_threshold, job_requirements
                    FROM bs_recruiting_operator_jobs
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (job_uuid, tenant_id),
                )
                job_row = cursor.fetchone()
        if job_row is None and job_name:
            cursor.execute(
                """
                SELECT id, job_name, notes, match_threshold, job_requirements
                FROM bs_recruiting_operator_jobs
                WHERE tenant_id = %s AND job_name = %s
                """,
                (tenant_id, job_name),
            )
            job_row = cursor.fetchone()
        if job_row is None:
            return None
        job = dict(job_row)
        # 隐含要求来源：初次开场话术（最多 3 条）
        cursor.execute(
            """
            SELECT title, content FROM bs_recruiting_operator_job_scripts
            WHERE job_id = %s AND tenant_id = %s AND category = %s
            ORDER BY sort_order ASC, created_at ASC
            LIMIT %s
            """,
            (job["id"], tenant_id, _OPENING_CATEGORY, _MAX_OPENING_SCRIPTS),
        )
        scripts = [{"title": r["title"], "content": r["content"]} for r in cursor.fetchall()]

    return {
        "job_id": str(job["id"]),
        "job_name": job["job_name"],
        "notes": job.get("notes"),
        # 显式 None 判断：match_threshold=0 合法（列 NOT NULL 不会 None，防御性兜底默认值）
        "match_threshold": (
            job["match_threshold"]
            if job.get("match_threshold") is not None
            else recruiting_job_service.DEFAULT_MATCH_THRESHOLD
        ),
        "job_requirements": _parse_json_field(job.get("job_requirements")),
        "opening_scripts": scripts,
    }


def _update_match_fields(
    tenant_id: str,
    resume_id: int,
    match_score: int,
    match_summary: Optional[str],
    match_status: str,
    key_info: Optional[Dict[str, Any]],
    resume_summary: Optional[str] = None,
) -> bool:
    """回写评分四列（updated_at=NOW()），返回是否命中行（简历被删则 False）。

    resume_summary（v2）：VL 人物总结，传入时一并回写（None 不动原值——文本 fallback
    路径没有总结，不能把 VL 路径已写入的总结清掉）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_recruiting_operator_resumes
            SET match_score = %s, match_summary = %s, match_status = %s, key_info = %s,
                resume_summary = COALESCE(%s, resume_summary),
                updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (
                match_score, match_summary, match_status,
                psycopg2.extras.Json(key_info) if key_info is not None else None,
                resume_summary,
                resume_id, tenant_id,
            ),
        )
        updated = cursor.rowcount > 0
        conn.commit()
    return updated


def _safe_model_name(gateway: Any) -> Optional[str]:
    """网关异常时返回 None 兜底，正常返回主链路当前模型名，不影响主流程"""
    try:
        return gateway.get_model_name()
    except Exception:  # noqa: BLE001 计费辅助信息，取不到不强求
        return None
