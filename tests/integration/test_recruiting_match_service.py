"""
简历-职位匹配度评分服务层集成测试（简历-职位匹配设计 §3，Phase 2）

覆盖 src/services/recruiting_match_service.py：
- 评分成功路：合法 JSON → match_score/match_summary/match_status/key_info 落库正确；
  阈值三档（≥阈值 matched / 50~69 unmatched / <50 rejected，阈值取 jobs.match_threshold 自定义值，
  无职位记录时默认 70）；score 越界截断；key_info 未知键丢弃 / 非法数组清洗 / null 保留
- 失败两路：LLM 抛异常 / 返回垃圾文本 → 重试 1 次后 match_* 留 NULL、返回 note；
  重评失败保留库中旧分不清空；首败次成 → 重试内成功
- prompt 断言（RecordingGateway）：含 job_requirements 内容、OCR 截断 3000 字、
  含「仅依据简历文本」铁律；无 requirements 走 job_name + 初次开场话术隐含要求路；
  job_id 与 job_name 皆无 → skipped 不调 LLM
- 计费：record_background_llm_usage 被调且 source/model/租户参数正确（monkeypatch 断言）

LLM 全程 stub（monkeypatch 模块级 llm_gateway），不真调网；DB 用真实 PG 临时租户。
"""

import json
import uuid

import psycopg2.extras
import pytest

from src.services import recruiting_job_service as job_service
from src.services import recruiting_match_service as match_service
from src.services import recruiting_resume_service as resume_service

pytestmark = pytest.mark.integration


# 固定合法评分输出（score 可按用例覆盖）
_VALID_PAYLOAD = {
    "score": 82,
    "match_summary": "Laravel 五年经验，本科，技术栈高度匹配",
    "key_info": {
        "years_of_experience": 5,
        "education": "本科",
        "current_company": "xx科技",
        "core_skills": ["PHP", "Laravel", "MySQL"],
        "highlights": ["日活十万级 SaaS 主导"],
        "ai_tool_usage": "熟练：Cursor 日常开发",
        "salary_expectation": "15-25K",
        "concerns": [],
    },
}


# ============== Fixture ==============

@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """模块级幂等建表（测试库可能未跑过服务启动初始化；jobs 先建——resumes.job_id 外键引用 jobs 表）"""
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables

    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        init_recruiting_operator_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant():
    """创建临时租户，测试后清理（含职位/话术/简历）"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"匹配评分测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]
    yield {"tenant_id": tenant_id}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_resumes WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_job_scripts WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_jobs WHERE tenant_id = %s",
                (tenant_id,),
            )
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


class _StubGateway:
    """假 LLM 网关：返回固定内容并记录每次调用 kwargs（prompt/参数断言用）。

    - payload：dict → json.dumps 为 content（合法路）
    - content：str → 原样返回（垃圾文本路）
    - error：Exception → 每次 chat 都抛（LLM 异常路）
    - calls：记录每次调用的完整 kwargs
    """

    def __init__(self, payload=None, content=None, error=None, usage=None):
        self.payload = payload if payload is not None else _VALID_PAYLOAD
        self.content = content
        self.error = error
        self.usage = usage
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.content is not None:
            return {"content": self.content, "usage": self.usage}
        return {"content": json.dumps(self.payload, ensure_ascii=False), "usage": self.usage}

    async def chat_lite(self, **kwargs):
        # 评分服务已改走 chat_lite；真实网关内部解析 model/thinking，stub 复刻 chat 行为即可
        return await self.chat(**kwargs)


def _call(coro):
    """同步驱动 async 函数（独立新 event loop，避免跨文件事件循环状态污染）"""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _insert_resume(
    tenant_id: str,
    *,
    candidate_name: str = "张三",
    user_id: str = "u_match_test",
    job_id=None,
    job_name=None,
    ocr_text: str = "张三 男 本科 5年 PHP/Laravel 开发经验，现任 xx科技 后端工程师",
    match_score=None,
    match_status=None,
    match_summary=None,
    key_info=None,
) -> int:
    """直插简历记录（可预置旧评分，供「重评失败不清旧分」断言），返回 id"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resumes
                (tenant_id, user_id, candidate_name, job_id, job_name, ocr_text,
                 source, status, match_score, match_summary, match_status, key_info)
            VALUES (%s, %s, %s, %s, %s, %s, 'boss', 'new', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id, user_id, candidate_name, job_id, job_name, ocr_text,
                match_score, match_summary, match_status,
                psycopg2.extras.Json(key_info) if key_info is not None else None,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


def _stub_llm(monkeypatch, gateway: _StubGateway) -> _StubGateway:
    """把假网关注入评分服务模块（monkeypatch 模块级 llm_gateway）"""
    monkeypatch.setattr(match_service, "llm_gateway", gateway)
    return gateway


# ============== 1. 评分成功路 ==============

class TestEvaluateSuccess:
    """成功路：落库正确 / 阈值三档 / score 截断 / key_info 清洗"""

    def test_success_writes_four_columns(self, temp_tenant, monkeypatch):
        """合法 JSON + 带 requirements 的职位 → 四列落库正确，返回完整结果 dict"""
        ctx = temp_tenant
        job = job_service.create_job(
            ctx["tenant_id"], job_name="PHP工程师",
            match_threshold=70,
            job_requirements={"experience": "3-5年", "educations": ["本科"], "keywords": ["Laravel"]},
        )
        rid = _insert_resume(ctx["tenant_id"], job_id=job["id"], job_name="PHP工程师")
        gw = _stub_llm(monkeypatch, _StubGateway())

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["resume_id"] == rid
        assert result["match_score"] == 82
        assert result["match_status"] == "matched"
        assert result["match_summary"] == _VALID_PAYLOAD["match_summary"]
        assert result["key_info"] == _VALID_PAYLOAD["key_info"]
        # DB 四列已回写
        record = resume_service.get_resume(ctx["tenant_id"], rid)
        assert record["match_score"] == 82
        assert record["match_status"] == "matched"
        assert record["match_summary"] == _VALID_PAYLOAD["match_summary"]
        assert record["key_info"] == _VALID_PAYLOAD["key_info"]
        # 一次成功即止（无重试）
        assert len(gw.calls) == 1
        # 调用参数：低温度 + 便宜报告模型 + token 上限
        assert gw.calls[0]["temperature"] == 0.1
        assert gw.calls[0]["max_tokens"] == match_service._MAX_OUTPUT_TOKENS

    @pytest.mark.parametrize(
        "score,threshold,expected_status",
        [
            (75, 60, "matched"),    # > 阈值
            (60, 60, "matched"),    # = 阈值（含）
            (55, 60, "unmatched"),  # 50~阈值-1
            (50, 60, "unmatched"),  # 边界 50 归 unmatched
            (49, 60, "rejected"),   # < 50
            (55, 0, "matched"),     # 阈值 0 合法配置（全员及格），不得被 or 兜底吞成默认 70
            (82, None, "matched"),  # 无职位记录 → 默认阈值 70
            (69, None, "unmatched"),
            (49, None, "rejected"),
        ],
    )
    def test_threshold_three_bands(self, temp_tenant, monkeypatch, score, threshold, expected_status):
        """阈值判断：jobs.match_threshold 自定义值三档 + 无关联职位默认 70"""
        ctx = temp_tenant
        if threshold is not None:
            job = job_service.create_job(
                ctx["tenant_id"], job_name=f"阈值职位{threshold}", match_threshold=threshold,
            )
            rid = _insert_resume(ctx["tenant_id"], job_id=job["id"], job_name=job["job_name"])
        else:
            # 无职位记录：job_name 不命中任何职位 → 阈值默认 70
            rid = _insert_resume(ctx["tenant_id"], job_name="自由文本职位")
        _stub_llm(monkeypatch, _StubGateway(payload={**_VALID_PAYLOAD, "score": score}))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))
        record = resume_service.get_resume(ctx["tenant_id"], rid)

        assert result["match_score"] == score
        assert result["match_status"] == expected_status
        assert record["match_score"] == score
        assert record["match_status"] == expected_status

    @pytest.mark.parametrize("raw_score,clamped", [(120, 100), (-5, 0), ("88", 88), (88.0, 88)])
    def test_score_clamped_to_0_100(self, temp_tenant, monkeypatch, raw_score, clamped):
        """score 越界/字符串数字：截断到 0-100 仍成功入库"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")
        _stub_llm(monkeypatch, _StubGateway(payload={**_VALID_PAYLOAD, "score": raw_score}))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))
        assert result["match_score"] == clamped

    def test_key_info_cleaned_whitelist(self, temp_tenant, monkeypatch):
        """key_info 清洗：未知键丢弃 / 非法数组转 [] / 非法项剔除 / 标量非法置 null / null 保留"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")
        _stub_llm(monkeypatch, _StubGateway(payload={
            "score": 66,
            "match_summary": "基本匹配",
            "key_info": {
                "years_of_experience": "6",          # 数字字符串 → 6
                "education": " 本科 ",               # strip
                "current_company": None,             # null 保留
                "core_skills": "PHP",                # 非法（非数组）→ []
                "highlights": ["好亮点", 42, "  "],   # 非法/空项剔除
                "ai_tool_usage": 123,                # 非法标量 → null
                "salary_expectation": "15-25K",
                "concerns": None,                    # null → []
                "unknown_key": "应丢弃",              # 未知键丢弃
            },
        }))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["key_info"] == {
            "years_of_experience": 6,
            "education": "本科",
            "current_company": None,
            "core_skills": [],
            "highlights": ["好亮点"],
            "ai_tool_usage": None,
            "salary_expectation": "15-25K",
            "concerns": [],
        }
        assert "unknown_key" not in result["key_info"]

    def test_key_info_non_dict_stored_null_but_score_saved(self, temp_tenant, monkeypatch):
        """key_info 非 dict → 存 NULL，score/summary/status 照常入库"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")
        _stub_llm(monkeypatch, _StubGateway(payload={
            "score": 71, "match_summary": "匹配", "key_info": ["不是对象"],
        }))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))
        assert result["match_score"] == 71
        assert result["key_info"] is None
        assert resume_service.get_resume(ctx["tenant_id"], rid)["key_info"] is None


# ============== 2. 失败两路（不阻塞 / 不清旧分） ==============

class TestEvaluateFailure:
    """LLM 异常 / 垃圾文本 → 重试 1 次后留 NULL（旧值保留），返回 note 不抛异常"""

    def test_llm_exception_retries_once_then_note(self, temp_tenant, monkeypatch):
        """LLM 每次都抛异常：共调用 2 次（首次+重试1次）→ match_* 留 NULL"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")
        gw = _stub_llm(monkeypatch, _StubGateway(error=RuntimeError("gateway down")))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["score"] is None
        assert "评分失败" in result["note"]
        assert len(gw.calls) == 2
        record = resume_service.get_resume(ctx["tenant_id"], rid)
        assert record["match_score"] is None
        assert record["match_status"] is None
        assert record["match_summary"] is None
        assert record["key_info"] is None

    def test_garbage_text_retries_once_then_note(self, temp_tenant, monkeypatch):
        """LLM 返回垃圾文本（非 JSON）：重试 1 次仍败 → 留 NULL + note"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")
        gw = _stub_llm(monkeypatch, _StubGateway(content="抱歉，我无法以 JSON 格式回答……"))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["score"] is None
        assert "评分失败" in result["note"]
        assert len(gw.calls) == 2
        assert resume_service.get_resume(ctx["tenant_id"], rid)["match_score"] is None

    def test_invalid_score_counts_as_failure(self, temp_tenant, monkeypatch):
        """score 非数字（无法清洗）→ 整体按解析失败重试，仍败返回 note"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")
        _stub_llm(monkeypatch, _StubGateway(payload={"score": "很高", "match_summary": "x"}))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))
        assert result["score"] is None
        assert resume_service.get_resume(ctx["tenant_id"], rid)["match_score"] is None

    def test_retry_succeeds_on_second_attempt(self, temp_tenant, monkeypatch):
        """首败次成：第 1 次垃圾、第 2 次合法 → 重试内成功入库，共 2 次调用"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师")

        class _FlakyGateway(_StubGateway):
            """第 1 次返回垃圾文本，第 2 次返回合法 JSON"""

            async def chat(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return {"content": "垃圾文本", "usage": None}
                return {"content": json.dumps(self.payload, ensure_ascii=False), "usage": None}

        gw = _stub_llm(monkeypatch, _FlakyGateway())

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["match_score"] == 82
        assert len(gw.calls) == 2
        assert resume_service.get_resume(ctx["tenant_id"], rid)["match_score"] == 82

    def test_reevaluate_failure_keeps_old_score(self, temp_tenant, monkeypatch):
        """重评失败不清旧分：库中原 score/summary/status/key_info 原样保留"""
        ctx = temp_tenant
        old_key_info = {"education": "本科", "core_skills": ["PHP"]}
        rid = _insert_resume(
            ctx["tenant_id"], job_name="PHP工程师",
            match_score=66, match_status="matched", match_summary="旧评分理由",
            key_info=old_key_info,
        )
        _stub_llm(monkeypatch, _StubGateway(error=RuntimeError("gateway down")))

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["score"] is None
        record = resume_service.get_resume(ctx["tenant_id"], rid)
        assert record["match_score"] == 66
        assert record["match_status"] == "matched"
        assert record["match_summary"] == "旧评分理由"
        assert record["key_info"] == old_key_info


# ============== 3. prompt 组装断言 ==============

class TestPromptBuilding:
    """RecordingGateway 断言：requirements 拼入 / OCR 截断 3000 / 铁律文案 / 隐含要求路"""

    def test_prompt_contains_requirements_and_constraints(self, temp_tenant, monkeypatch):
        """带 requirements 的职位：prompt 含 requirements JSON 内容与「仅依据简历文本」铁律"""
        ctx = temp_tenant
        job = job_service.create_job(
            ctx["tenant_id"], job_name="Laravel 高级工程师",
            job_requirements={
                "experience": "3-5年", "educations": ["本科", "硕士"],
                "salary": "10-20K", "keywords": ["Laravel", "MySQL"],
                "notes": "接受 AI 工具深度使用者优先",
            },
        )
        rid = _insert_resume(ctx["tenant_id"], job_id=job["id"], job_name=job["job_name"])
        gw = _stub_llm(monkeypatch, _StubGateway())

        _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        prompt = gw.calls[0]["messages"][1]["content"]
        # requirements JSONB 原样拼入
        assert '"experience": "3-5年"' in prompt
        assert '"educations": ["本科", "硕士"]' in prompt
        assert "接受 AI 工具深度使用者优先" in prompt
        # 固定铁律
        assert "仅依据简历文本" in prompt
        assert "严禁编造" in prompt
        # 输出结构示例
        assert "match_summary" in prompt and "key_info" in prompt

    def test_prompt_truncates_ocr_to_3000_chars(self, temp_tenant, monkeypatch):
        """OCR 正文超长：截断 3000 字（3000 在、3001 不在）"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师", ocr_text="甲" * 4000)
        gw = _stub_llm(monkeypatch, _StubGateway())

        _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        prompt = gw.calls[0]["messages"][1]["content"]
        assert "甲" * 3000 in prompt
        assert "甲" * 3001 not in prompt

    def test_no_requirements_falls_back_to_job_name_and_scripts(self, temp_tenant, monkeypatch):
        """无 requirements：job_name + 初次开场话术内容拼隐含要求（含职位备注）"""
        ctx = temp_tenant
        job = job_service.create_job(
            ctx["tenant_id"], job_name="无要求职位", notes="技术栈：PHP 8 / Laravel / MySQL",
        )
        job_service.create_script(
            ctx["tenant_id"], job["id"],
            category="初次开场", title="开场·技术栈匹配",
            content="看到您的 PHP 开发经验和我们很匹配，我们团队主力技术栈是 PHP 8 + Laravel。",
        )
        # 简历无 job_id，仅 job_name 精确匹配到职位 → 同样走隐含要求路
        rid = _insert_resume(ctx["tenant_id"], job_name="无要求职位")
        gw = _stub_llm(monkeypatch, _StubGateway())

        _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        prompt = gw.calls[0]["messages"][1]["content"]
        assert "结构化职位要求" not in prompt  # 未走 requirements 路
        assert "隐含要求" in prompt
        assert "无要求职位" in prompt
        assert "技术栈：PHP 8 / Laravel / MySQL" in prompt          # 职位备注
        assert "开场·技术栈匹配" in prompt                            # 初次开场话术标题
        assert "PHP 8 + Laravel" in prompt                           # 话术内容
        # 该简历按 job_name 命中职位 → 阈值取自职位（默认 70），82 分 → matched
        record = resume_service.get_resume(ctx["tenant_id"], rid)
        assert record["match_score"] == 82 and record["match_status"] == "matched"

    def test_job_name_not_in_jobs_uses_name_only(self, temp_tenant, monkeypatch):
        """job_name 未命中任何职位（job_id 为空）：仅按职位名评分，不报错"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="BOSS 页面自由文本职位名")
        gw = _stub_llm(monkeypatch, _StubGateway())

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["match_score"] == 82
        prompt = gw.calls[0]["messages"][1]["content"]
        assert "BOSS 页面自由文本职位名" in prompt


# ============== 4. 跳过路（不调 LLM） ==============

class TestSkippedPaths:
    """skipped：job_id 与 job_name 皆无 / 无 OCR 正文 / 简历不存在 → 不调 LLM、match_* 留 NULL"""

    def test_no_job_at_all_skips_without_llm(self, temp_tenant, monkeypatch):
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_id=None, job_name=None)
        gw = _stub_llm(monkeypatch, _StubGateway())

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["score"] is None
        assert "跳过评分" in result["note"]
        assert gw.calls == []  # 未调 LLM
        record = resume_service.get_resume(ctx["tenant_id"], rid)
        assert record["match_score"] is None

    def test_empty_ocr_skips_without_llm(self, temp_tenant, monkeypatch):
        """OCR 正文为空：无正文可评（宁缺勿编），跳过不调 LLM"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_name="PHP工程师", ocr_text="   ")
        gw = _stub_llm(monkeypatch, _StubGateway())

        result = _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert result["score"] is None
        assert "OCR" in result["note"]
        assert gw.calls == []

    def test_resume_not_found(self, temp_tenant, monkeypatch):
        _stub_llm(monkeypatch, _StubGateway())
        result = _call(match_service.evaluate_and_update(temp_tenant["tenant_id"], 99999999))
        assert result["score"] is None
        assert "简历不存在" in result["note"]


# ============== 5. 计费 ==============

class TestBilling:
    """record_background_llm_usage：source/model/租户归属参数正确"""

    def test_billing_recorded_with_source_and_model(self, temp_tenant, monkeypatch):
        ctx = temp_tenant
        job = job_service.create_job(ctx["tenant_id"], job_name="计费职位")
        rid = _insert_resume(
            ctx["tenant_id"], user_id="u_billing", job_id=job["id"], job_name=job["job_name"],
        )
        _stub_llm(monkeypatch, _StubGateway(usage={"prompt_tokens": 100, "completion_tokens": 20}))

        captured = []
        monkeypatch.setattr(
            match_service, "record_background_llm_usage",
            lambda usage, **kwargs: captured.append((usage, kwargs)),
        )

        _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))

        assert len(captured) == 1
        usage, kwargs = captured[0]
        assert usage == {"prompt_tokens": 100, "completion_tokens": 20}
        assert kwargs["source"] == "recruiting_match"
        assert kwargs["tenant_id"] == ctx["tenant_id"]
        assert kwargs["user_id"] == "u_billing"  # user_id 从 resume 行取
        assert f"resume_id={rid}" in kwargs["user_message"]
        # model 显式传 lite 模型名（不传会误用 mid_term 摘要单价）
        from src.reports.summarizer import get_lite_model
        assert kwargs["model"] == get_lite_model()

    def test_no_billing_when_skipped(self, temp_tenant, monkeypatch):
        """skipped（未调 LLM）绝不计费"""
        ctx = temp_tenant
        rid = _insert_resume(ctx["tenant_id"], job_id=None, job_name=None)
        _stub_llm(monkeypatch, _StubGateway())
        captured = []
        monkeypatch.setattr(
            match_service, "record_background_llm_usage",
            lambda usage, **kwargs: captured.append((usage, kwargs)),
        )
        _call(match_service.evaluate_and_update(ctx["tenant_id"], rid))
        assert captured == []
