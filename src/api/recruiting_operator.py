"""招聘操作智能体简历库 API（薄壳）

保存从 BOSS 直聘 CLI 采集的候选人简历（截图图片、OCR 文本、基本信息、关联职位、获取日期），
供前端「简历库」业务页浏览 / 筛选 / 编辑状态。

分层说明（2026-08-16 架构升级）：
- 核心业务逻辑已抽至 src/services/recruiting_resume_service.py（服务层）
- 本模块只做 HTTP 适配：saas context 取租户 / auth 取用户 / ValueError → 400 / 未命中 → 404
- 智能体入库走本地工具 boss_resume_detail → 服务层，不走本 HTTP API
- 端点函数签名与响应格式保持不变（前端与既有集成测试兼容）

一套 CRUD API（表 bs_recruiting_operator_resumes）：
- 简历列表（分页 + keyword/job_name/status/fetched_at 区间筛选，轻量不含 ocr_text）
- 职位下拉（distinct job_name）
- 创建（支持 images=file_id 引用 与 images_base64 直传两路合并，base64 落盘转 file_id）
- 详情（含 ocr_text / images / candidate_info）
- 更新（status / remark / job_name / candidate_name / candidate_info）
- 删除

一套职位库 CRUD API（表 bs_recruiting_operator_jobs / bs_recruiting_operator_job_scripts，
服务层 src/services/recruiting_job_service.py，前端「职位库」业务页）：
- 职位列表（首次访问自动预置「PHP开发工程师（Laravel）」+ 13 条话术；含简历/匹配统计）
- 职位 CRUD（删职位级联删其话术）
- 话术 CRUD（固定四分类：初次开场/了解摸底/追问细节/邀约推进）
- 职位要求档位候选（静态：经验/学历/薪资下拉，简历-职位匹配 Phase 5）

所有 API 必须遵循租户隔离规范（[backend_dev.md SaaS 租户隔离规范]）：
- 通过 get_current_tenant_id() 取租户
- 所有查询带 tenant_id 过滤
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.saas.context import get_current_tenant_id
from src.api.auth import get_current_user
from src.services import recruiting_resume_service as resume_service
from src.services import recruiting_job_service as job_service
from src.services import recruiting_match_service as match_service
# 兼容再导出：既有调用方（src/db/database.py 启动初始化、集成测试）沿用旧导入路径
from src.services.recruiting_resume_service import (  # noqa: F401
    RESUME_SOURCES,
    RESUME_STATUSES,
    init_recruiting_operator_tables,
)
from src.services.recruiting_job_service import (  # noqa: F401
    SCRIPT_CATEGORIES,
    init_recruiting_job_tables,
)

router = APIRouter(prefix="/api/recruiting-operator", tags=["招聘操作智能体-简历库"])


# ============== HTTP 适配工具函数 ==============

def _sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg
    patterns = [
        r'password["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'secret["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', sanitized, flags=re.IGNORECASE)
    return sanitized


def _error_response(error: str, debug: str, status_code: int = 500) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": error, "debug": _sanitize_error_info(debug)},
    )


def _require_tenant() -> Optional[str]:
    """获取当前租户 ID（SaaS 模式下必填）"""
    tenant_id = get_current_tenant_id()
    return tenant_id


# ============== 请求模型 ==============

class ResumeImageRef(BaseModel):
    """已上传图片引用（前端先调 /api/upload 拿 file_id）"""
    file_id: str = Field(..., description="已上传文件的 file_id")
    name: Optional[str] = Field(None, description="显示名")


class ResumeImageBase64(BaseModel):
    """base64 直传图片（CLI 采集截图入库用）"""
    data: str = Field(..., description="base64 数据，兼容 data:image/png;base64, 前缀")
    name: Optional[str] = Field(None, description="显示名")
    mime_type: str = Field(..., description="MIME 类型，必须 image/*")


class CreateResumeRequest(BaseModel):
    candidate_name: str = Field(..., description="候选人姓名")
    job_name: Optional[str] = Field(None, description="关联职位")
    candidate_info: Optional[Dict[str, Any]] = Field(None, description="基本信息（学历/工作年限/期望薪资/城市等，key 灵活）")
    ocr_text: Optional[str] = Field(None, description="OCR 全文")
    images: Optional[List[ResumeImageRef]] = Field(None, description="已上传图片引用列表（有序）")
    images_base64: Optional[List[ResumeImageBase64]] = Field(None, description="base64 图片直传列表（服务端落盘转 file_id）")
    source: str = Field("manual", description="来源：boss=CLI 入库 / manual=页面补录")
    fetched_at: Optional[str] = Field(None, description="获取简历日期（ISO 字符串，缺省为当前时间）")
    remark: Optional[str] = Field(None, description="备注")


class UpdateResumeRequest(BaseModel):
    candidate_name: Optional[str] = Field(None, description="候选人姓名")
    job_name: Optional[str] = Field(None, description="关联职位")
    candidate_info: Optional[Dict[str, Any]] = Field(None, description="基本信息")
    status: Optional[str] = Field(None, description="状态：new/viewed/shortlisted/interviewed/rejected")
    remark: Optional[str] = Field(None, description="备注")


# ============== 简历库 API ==============

@router.get("/resumes")
async def list_resumes(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = Query(None, description="按候选人姓名模糊搜索"),
    job_name: Optional[str] = Query(None, description="按关联职位筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    fetched_at_from: Optional[str] = Query(None, description="获取日期起（YYYY-MM-DD 或 ISO）"),
    fetched_at_to: Optional[str] = Query(None, description="获取日期止（YYYY-MM-DD 或 ISO）"),
):
    """简历列表（分页 + 筛选，轻量不含 ocr_text，按 created_at DESC）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        try:
            data = resume_service.list_resumes(
                tenant_id,
                page=page, page_size=page_size, keyword=keyword, job_name=job_name,
                status=status, fetched_at_from=fetched_at_from, fetched_at_to=fetched_at_to,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)

        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"简历列表查询失败: {e}", exc_info=True)
        return _error_response("简历列表查询失败", str(e))


@router.get("/resumes/jobs")
async def list_resume_jobs(request: Request):
    """该租户已录入的 distinct 职位列表（筛选下拉用）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        jobs = resume_service.list_distinct_jobs(tenant_id)
        return {"success": True, "data": {"jobs": jobs}}
    except Exception as e:
        logger.error(f"简历职位列表查询失败: {e}", exc_info=True)
        return _error_response("简历职位列表查询失败", str(e))


@router.post("/resumes")
async def create_resume(req: CreateResumeRequest, request: Request):
    """创建简历记录（images 引用路与 images_base64 直传路合并入库，base64 优先落盘转 file_id）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        user = get_current_user(request)
        user_id = user.get("user_id") if user else None

        try:
            record = resume_service.create_resume_record(
                tenant_id,
                user_id,
                candidate_name=req.candidate_name,
                job_name=req.job_name,
                candidate_info=req.candidate_info,
                ocr_text=req.ocr_text,
                images=[item.model_dump() for item in (req.images or [])],
                images_base64=[item.model_dump() for item in (req.images_base64 or [])],
                source=req.source,
                fetched_at=req.fetched_at,
                remark=req.remark,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)

        return {"success": True, "data": record}
    except Exception as e:
        logger.error(f"简历创建失败: {e}", exc_info=True)
        return _error_response("简历创建失败", str(e))


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: int, request: Request):
    """简历详情（含 ocr_text / images / candidate_info）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        record = resume_service.get_resume(tenant_id, resume_id)
        if record is None:
            return _error_response("简历不存在", f"resume_id={resume_id} not found", 404)
        return {"success": True, "data": record}
    except Exception as e:
        logger.error(f"简历详情查询失败: {e}", exc_info=True)
        return _error_response("简历详情查询失败", str(e))


@router.patch("/resumes/{resume_id}")
async def update_resume(resume_id: int, req: UpdateResumeRequest, request: Request):
    """更新简历（仅传的字段：status/remark/job_name/candidate_name/candidate_info），updated_at=NOW()"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)

        try:
            record = resume_service.update_resume(
                tenant_id,
                resume_id,
                candidate_name=req.candidate_name,
                job_name=req.job_name,
                candidate_info=req.candidate_info,
                status=req.status,
                remark=req.remark,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if record is None:
            return _error_response("简历不存在", f"resume_id={resume_id} not found", 404)

        return {"success": True, "data": record}
    except Exception as e:
        logger.error(f"简历更新失败: {e}", exc_info=True)
        return _error_response("简历更新失败", str(e))


@router.delete("/resumes/{resume_id}")
async def delete_resume(resume_id: int, request: Request):
    """删除简历（仅删除库记录，不删除底层图片文件）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        if not resume_service.delete_resume(tenant_id, resume_id):
            return _error_response("简历不存在", f"resume_id={resume_id} not found", 404)
        return {"success": True}
    except Exception as e:
        logger.error(f"简历删除失败: {e}", exc_info=True)
        return _error_response("简历删除失败", str(e))


@router.post("/resumes/{resume_id}/re-evaluate")
async def re_evaluate_resume(resume_id: int, request: Request):
    """重新评分（前端「重新评分」按钮）：调评分服务回写 match_* / key_info。

    评分失败不抛错：返回 data 带 note 说明原因（库中原值保留），前端据此提示。
    """
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        if resume_service.get_resume(tenant_id, resume_id) is None:
            return _error_response("简历不存在", f"resume_id={resume_id} not found", 404)
        result = await match_service.evaluate_and_update(tenant_id, resume_id)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"简历重新评分失败: {e}", exc_info=True)
        return _error_response("简历重新评分失败", str(e))


# ============== 职位库请求模型 ==============

class CreateJobRequest(BaseModel):
    job_name: str = Field(..., description="职位名称")
    notes: Optional[str] = Field(None, description="职位备注（技术栈/团队说明等）")
    status: Optional[str] = Field(None, description="职位状态：active=正常可选（默认）/ paused=暂停存档")
    match_threshold: Optional[int] = Field(None, description="匹配及格线 0-100（默认 70，服务层校验取值范围）")
    job_requirements: Optional[Dict[str, Any]] = Field(
        None,
        description="结构化职位要求：{experience(str), educations(list[str]), salary(str), keywords(list[str]), notes(str)}，全部可缺省，值须为 BOSS 档位文本",
    )


class UpdateJobRequest(BaseModel):
    job_name: Optional[str] = Field(None, description="职位名称")
    notes: Optional[str] = Field(None, description="职位备注")
    status: Optional[str] = Field(None, description="职位状态：active/paused")
    match_threshold: Optional[int] = Field(None, description="匹配及格线 0-100（服务层校验取值范围）")
    job_requirements: Optional[Dict[str, Any]] = Field(
        None, description="结构化职位要求（结构校验同创建；传 {} 清空）"
    )


class CreateJobScriptRequest(BaseModel):
    category: str = Field(..., description="话术分类：初次开场/了解摸底/追问细节/邀约推进")
    title: str = Field(..., description="话术标题（分类内小标题）")
    content: str = Field(..., description="话术正文，支持 {{占位符}}（复制后手动替换）")
    sort_order: int = Field(0, description="分类内排序，默认 0")


class UpdateJobScriptRequest(BaseModel):
    category: Optional[str] = Field(None, description="话术分类")
    title: Optional[str] = Field(None, description="话术标题")
    content: Optional[str] = Field(None, description="话术正文")
    sort_order: Optional[int] = Field(None, description="分类内排序")


# ============== 职位库 API ==============

@router.get("/jobs")
async def list_jobs(request: Request):
    """职位列表（按 created_at DESC，含话术数与已用分类、简历数与匹配数；首次访问自动预置默认职位）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        jobs = job_service.list_jobs(tenant_id)
        return {"success": True, "data": {"items": jobs}}
    except Exception as e:
        logger.error(f"职位列表查询失败: {e}", exc_info=True)
        return _error_response("职位列表查询失败", str(e))


# 职位要求档位候选（简历-职位匹配 Phase 5，前端职位弹框下拉用）。
# 静态常量（无状态数据）：参考 BOSS 常见档位给出候选，真值档位由 CLI boss_filter_options
# 在筛选链路运行时校准 + 既有保底映射兜底（见设计 §2.1/§4.4），故此处不做硬校验数据源。
REQUIREMENT_EXPERIENCE_OPTIONS: List[str] = ["1年以内", "1-3年", "3-5年", "5-10年", "10年以上"]
REQUIREMENT_EDUCATION_OPTIONS: List[str] = ["大专", "本科", "硕士", "博士"]
REQUIREMENT_SALARY_OPTIONS: List[str] = ["3-5K", "5-10K", "10-15K", "15-25K", "25-50K", "50K以上"]


@router.get("/jobs/requirement-options")
async def get_requirement_options(request: Request):
    """职位要求档位候选（experience 单选 / educations 多选 / salary 单选，静态数据）。

    注意：本路由须声明在 /jobs/{job_id} 之前，避免 "requirement-options" 被当作 job_id 匹配。
    """
    return {
        "success": True,
        "data": {
            "experience": REQUIREMENT_EXPERIENCE_OPTIONS,
            "educations": REQUIREMENT_EDUCATION_OPTIONS,
            "salary": REQUIREMENT_SALARY_OPTIONS,
        },
    }


@router.post("/jobs")
async def create_job(req: CreateJobRequest, request: Request):
    """创建职位（tenant_id+job_name 唯一，重名 400；status/match_threshold/job_requirements 校验）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            job = job_service.create_job(
                tenant_id,
                job_name=req.job_name,
                notes=req.notes,
                status=req.status,
                match_threshold=req.match_threshold,
                job_requirements=req.job_requirements,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        return {"success": True, "data": job}
    except Exception as e:
        logger.error(f"职位创建失败: {e}", exc_info=True)
        return _error_response("职位创建失败", str(e))


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """职位详情（含全部话术 scripts 平铺 + script_groups 按分类分组）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            job = job_service.get_job(tenant_id, job_id)
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if job is None:
            return _error_response("职位不存在", f"job_id={job_id} not found", 404)
        return {"success": True, "data": job}
    except Exception as e:
        logger.error(f"职位详情查询失败: {e}", exc_info=True)
        return _error_response("职位详情查询失败", str(e))


@router.patch("/jobs/{job_id}")
async def update_job(job_id: str, req: UpdateJobRequest, request: Request):
    """更新职位（仅传的字段：job_name/notes/status/match_threshold/job_requirements），updated_at=NOW()"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            job = job_service.update_job(
                tenant_id,
                job_id,
                job_name=req.job_name,
                notes=req.notes,
                status=req.status,
                match_threshold=req.match_threshold,
                job_requirements=req.job_requirements,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if job is None:
            return _error_response("职位不存在", f"job_id={job_id} not found", 404)
        return {"success": True, "data": job}
    except Exception as e:
        logger.error(f"职位更新失败: {e}", exc_info=True)
        return _error_response("职位更新失败", str(e))


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, request: Request):
    """删除职位（物理删，级联删其全部话术）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            deleted = job_service.delete_job(tenant_id, job_id)
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if not deleted:
            return _error_response("职位不存在", f"job_id={job_id} not found", 404)
        return {"success": True}
    except Exception as e:
        logger.error(f"职位删除失败: {e}", exc_info=True)
        return _error_response("职位删除失败", str(e))


@router.post("/jobs/{job_id}/scripts")
async def create_job_script(job_id: str, req: CreateJobScriptRequest, request: Request):
    """给职位添加话术（固定四分类）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            script = job_service.create_script(
                tenant_id, job_id,
                category=req.category, title=req.title, content=req.content,
                sort_order=req.sort_order,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if script is None:
            return _error_response("职位不存在", f"job_id={job_id} not found", 404)
        return {"success": True, "data": script}
    except Exception as e:
        logger.error(f"话术创建失败: {e}", exc_info=True)
        return _error_response("话术创建失败", str(e))


@router.patch("/scripts/{script_id}")
async def update_job_script(script_id: str, req: UpdateJobScriptRequest, request: Request):
    """更新话术（仅传的字段：category/title/content/sort_order）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            script = job_service.update_script(
                tenant_id, script_id,
                category=req.category, title=req.title, content=req.content,
                sort_order=req.sort_order,
            )
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if script is None:
            return _error_response("话术不存在", f"script_id={script_id} not found", 404)
        return {"success": True, "data": script}
    except Exception as e:
        logger.error(f"话术更新失败: {e}", exc_info=True)
        return _error_response("话术更新失败", str(e))


@router.delete("/scripts/{script_id}")
async def delete_job_script(script_id: str, request: Request):
    """删除话术（物理删）"""
    try:
        tenant_id = _require_tenant()
        if not tenant_id:
            return _error_response("租户 ID 缺失", "tenant_id is None", 400)
        try:
            deleted = job_service.delete_script(tenant_id, script_id)
        except ValueError as e:
            return _error_response(str(e), str(e), 400)
        if not deleted:
            return _error_response("话术不存在", f"script_id={script_id} not found", 404)
        return {"success": True}
    except Exception as e:
        logger.error(f"话术删除失败: {e}", exc_info=True)
        return _error_response("话术删除失败", str(e))
