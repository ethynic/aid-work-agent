#!/usr/bin/env python3
"""
Excel ETL Phase 3 管线脚本（skill 子进程 CLI，excel-to-template-1.0.0）

把多来源脏 Excel（供应商增减员报表）抽取填充到标准模板，输出汇总 xlsx + 校验报告 .md。
编排库层（D2：src/tools/excel/ 纯库），本脚本只做"找文件 → 调库 → 输出 JSON 结果"。

双入口：
1. 文件入口（会话上传附件 / zip 包）：
   python scripts/pipeline.py --files a.xlsx b.xlsx --template t.xlsx --output-dir <ws> [--zip pack.zip]
   - zip 自动解包（防路径穿越校验成员名），只收 .xlsx/.xls
   - 模板识别确定性规则（D3）：schema 指纹匹配 → 是模板；不匹配的按来源处理；
     都不匹配时用 --template 显式指定的（此时触发一次 schema 会话内重抽，D14）
2. 邮件入口（从绑定邮箱按日期拉增减员报表附件）：
   python scripts/pipeline.py --email-since 2026-08-01 --template t.xlsx --output-dir <ws>
   - read（since 服务器端过滤）→ D23 两级判定：①扩展名+关键词确定性预筛直接下载；
     ②未命中但可能相关的走一次 LLM 批量判定（下载/跳过+理由）→ download_attachments 落盘
   - D24 UID 水位：输出目录 .email_uid_watermark 记录已处理 UID，重复执行跳过；
     全程 BODY.PEEK 不标已读（邮箱无副作用）

主流程（文件/邮件入口共用）：
  init DB 池（失败降级）→ render_llm_view（每 sheet）→ MaskSession.mask（Q3 脱敏出域）
  → run_extraction（LLM 抽取/校验/修复/同人标注，on_usage→record_skill_llm_usage 计量）
  → unmask（LLM 阶段全部结束后还原真值）→ validate_records 真值复检（仍 error 进人工清单）
  → fill_template（D15 纯代码填充）+ generate_report（D16 报告）
  → stdout 输出 JSON 结果（两文件路径 + stats），主智能体后续调 cp 交付

测试开关：--llm-fake <json 文件>（按调用顺序返回预置响应，跳过真实 LLM 与计量落库，
仅供 e2e 测试使用，生产不传）。schema 资产单一事实来源：
PROJECT_ROOT/src/tools/excel/assets/schema.json（本 skill 不复制，D14）。
"""

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


def _ensure_project_root() -> str:
    """把项目根加进 sys.path（skill 子进程 cwd 是 skill_ws，import src.* 需要显式接入）"""
    project_root = os.environ.get("PROJECT_ROOT") or str(Path(__file__).resolve().parents[4])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return project_root


PROJECT_ROOT = _ensure_project_root()

from loguru import logger  # noqa: E402

from src.tools.excel.excel_extract import (  # noqa: E402
    extract_schema_from_template,
    load_default_schema,
    run_extraction,
    schema_matches,
    validate_records,
)
from src.tools.excel.excel_fill import fill_template, generate_report, output_names  # noqa: E402
from src.tools.excel.excel_mask import MaskSession  # noqa: E402
from src.tools.excel.excel_reader import render_llm_view  # noqa: E402
from src.tools.excel.excel_template_ai import _default_llm, _extract_json  # noqa: E402
from src.tools.email import email_lib  # noqa: E402

# ------------------------------------------------------------
# 常量（D23/D24）
# ------------------------------------------------------------

# D23 第一级确定性预筛：扩展名 ∈ {.xlsx,.xls,.zip} 且文件名命中关键词
ATTACH_KEYWORD_RE = re.compile("增减|社保|公积金|人员|变动|通知|派单")
ATTACH_ALLOWED_EXTS = {".xlsx", ".xls", ".zip"}

# D24 UID 水位 marker 文件名（输出目录下）
WATERMARK_FILENAME = ".email_uid_watermark"


# ------------------------------------------------------------
# 基础设施：DB 池 / 计量 / LLM 调用
# ------------------------------------------------------------


def _init_db_pool() -> bool:
    """初始化 DB 连接池（skill 子进程无 FastAPI 启动钩子，须自初始化，否则计量 credit=0）

    失败降级：只记 warning 不抛（无 DB 时管线照跑，计量不落库）。
    """
    try:
        from src.db.database import get_postgres_pool, init_postgres_pool

        if get_postgres_pool() is None:
            init_postgres_pool()
        return True
    except Exception as e:
        logger.warning(f"[excel-etl] DB 池初始化失败，LLM 计量将不落库（管线继续）: {e}")
        return False


class Metering:
    """LLM 用量累计 + record_skill_llm_usage 落库（D18：skill 子进程计量接线）

    - on_usage(usage, stage)：每次 LLM 调用（extract/repair/schema/email_judge）上报；
      落库失败只记日志（计量不能影响主流程）
    - summary()：报告计量小节用（调用次数/累计 token/credit 估算，不可用时省略 credit）
    """

    def __init__(self, record_usage: bool) -> None:
        self.record_usage = record_usage
        self.llm_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.credit = 0.0
        self.credit_available = False

    def on_usage(self, usage: Optional[Dict[str, Any]], stage: str) -> None:
        usage = dict(usage or {})
        self.llm_calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0) or (
            int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
        )
        if self.record_usage:
            try:
                from src.services.session_record import record_skill_llm_usage

                record_skill_llm_usage(usage, stage=stage, source="excel_etl")
            except Exception as e:
                logger.warning(f"[excel-etl] 计量落库失败(忽略): {e}")
        # credit 本地估算（报告小计；单价配置缺失时置不可用）
        try:
            from src.services.billing import calculate_credit_cost

            self.credit += calculate_credit_cost(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                model=str(usage.get("model") or "") or None,
                cached_input_tokens=int(usage.get("cached_tokens", 0) or 0),
                cache_creation_input_tokens=int(usage.get("cache_creation_tokens", 0) or 0),
            )
            self.credit_available = True
        except Exception:
            pass

    def summary(self) -> Optional[Dict[str, Any]]:
        if self.llm_calls == 0:
            return None
        data: Dict[str, Any] = {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
        if self.credit_available:
            data["credit"] = round(self.credit, 4)
        return data


def _call_llm(
    prompt: str,
    llm_callable: Optional[Callable[[str], Any]],
    *,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
    stage: str = "extract",
) -> str:
    """统一 LLM 入口（与 excel_extract._invoke_llm 同契约：可注入/默认/usage 计量）"""
    if llm_callable is not None:
        result = llm_callable(prompt)
        if isinstance(result, tuple) and len(result) == 2:
            content, usage = result
            usage = dict(usage or {})
        else:
            content, usage = result, {}
    else:
        content, usage = _default_llm(prompt, disable_thinking=True, return_usage=True)
    if on_usage is not None:
        try:
            on_usage(usage, stage)
        except Exception as e:
            logger.warning(f"[excel-etl] on_usage 回调异常(stage={stage}): {e}")
    return content


class FakeLLM:
    """--llm-fake 测试开关：按调用顺序返回预置响应（str 或 {"content","usage"}），不调真实 LLM"""

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.idx = 0

    def __call__(self, prompt: str):
        if self.idx >= len(self.responses):
            raise RuntimeError(f"fake LLM 预置响应已耗尽（第 {self.idx + 1} 次调用）")
        item = self.responses[self.idx]
        self.idx += 1
        if isinstance(item, dict) and "content" in item:
            return item["content"], dict(item.get("usage") or {})
        return item, {}


def _load_fake_llm(path: str) -> FakeLLM:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    responses = data.get("responses") if isinstance(data, dict) else data
    if not isinstance(responses, list):
        raise ValueError(f"--llm-fake 文件格式错误：应为响应数组或 {{\"responses\": [...]}}")
    return FakeLLM(responses)


# ------------------------------------------------------------
# 输入解析与模板识别（D3/D4）
# ------------------------------------------------------------


def _resolve_input_path(raw: str) -> Path:
    """输入文件路径解析：原路径 → cwd（skill_ws）→ test_uploads → 项目根各处查找

    主智能体传来的可能是相对路径/纯文件名（skill_executor 只对部分参数名自动解析）。
    纯文件名的 ``**/{name}`` 递归兜底**只在 cwd（本租户技能工作区）内进行**——
    项目根下含 storage/tenants/*（其他租户的会话附件），跨租户 glob 会把同名
    文件解析到他租户路径，破坏租户隔离。
    """
    path = Path(raw)
    if path.is_absolute() and path.exists():
        return path
    # 精确路径候选：cwd / cwd/test_uploads / 项目根 / 项目根 test_uploads
    exact_roots = [Path.cwd(), Path.cwd() / "test_uploads", Path(PROJECT_ROOT),
                   Path(PROJECT_ROOT) / "test_uploads"]
    for root in exact_roots:
        candidate = root / raw
        if candidate.exists():
            return candidate.resolve()
    # 纯文件名递归兜底：仅限 cwd 内（租户隔离，防跨租户同名文件误取）
    if not path.is_absolute():
        hit = list(Path.cwd().glob(f"**/{path.name}"))
        if hit:
            return hit[0].resolve()
    raise FileNotFoundError(f"输入文件不存在: {raw}")


def safe_unzip(zip_path: Path, dest_dir: Path) -> List[Path]:
    """解包 zip 到 dest_dir，只收 .xlsx/.xls；成员名路径穿越/绝对路径直接拒绝（防逃逸）"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_root = dest_dir.resolve()
    extracted: List[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            # 防路径穿越：绝对路径、.. 段、解包后落在目标目录外，一律拒绝整个 zip
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise ValueError(f"zip 成员名非法（疑似路径穿越）: {name}")
            dest = (dest_dir / name).resolve()
            if not str(dest).startswith(str(dest_root) + os.sep):
                raise ValueError(f"zip 成员名非法（解包目标越界）: {name}")
            if dest.suffix.lower() not in {".xlsx", ".xls"}:
                continue  # D4：zip 只取 Excel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            extracted.append(dest)
    logger.info(f"[excel-etl] zip 解包: {zip_path.name}, 提取 {len(extracted)} 个 Excel")
    return extracted


def identify_template(
    files: Sequence[Path],
    schema: Dict[str, Any],
    explicit: Optional[Path] = None,
) -> Tuple[Path, List[Path]]:
    """D3 模板识别（确定性零 LLM）：
    - 显式 --template 指定的优先（从来源中剔除）
    - 否则按 schema 指纹匹配：恰一个命中 → 模板；零个/多个 → 报错请用户指认
    """
    files = list(files)
    if explicit is not None:
        explicit = Path(explicit)
        sources = [f for f in files if f.resolve() != explicit.resolve()]
        return explicit, sources

    matches = [f for f in files if f.suffix.lower() == ".xlsx" and schema_matches(f, schema)]
    if len(matches) == 1:
        template = matches[0]
        sources = [f for f in files if f.resolve() != template.resolve()]
        return template, sources
    if not matches:
        raise ValueError(
            "未识别到标准模板（没有文件的表头指纹与内置 schema 匹配），"
            "请用 --template 明确指定模板文件"
        )
    raise ValueError(
        f"多个文件都匹配模板指纹（{', '.join(m.name for m in matches)}），"
        "请用 --template 明确指定其中一个作为模板"
    )


# ------------------------------------------------------------
# 邮件入口（D21/D22/D23/D24）
# ------------------------------------------------------------


def resolve_email_credentials():
    """从 AID_USER_ID 环境变量解析用户邮箱凭据（skill_executor 注入，子进程无请求上下文）"""
    from src.db.email_credential import EmailCredentialDB

    user_id = os.environ.get("AID_USER_ID")
    if not user_id:
        raise ValueError("邮件入口缺少 AID_USER_ID 环境变量（应由 skill_executor 注入）")
    user_email = EmailCredentialDB.get_user_email_model(user_id)
    if not user_email:
        raise ValueError("当前用户未绑定邮箱，请先在设置中绑定邮箱")
    return user_email


def load_watermark(output_dir: Path) -> set:
    """D24 读取已处理邮件 UID 集合（marker 文件不存在视为空）"""
    marker = output_dir / WATERMARK_FILENAME
    if not marker.exists():
        return set()
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return {str(u) for u in data.get("processed_uids") or []}
    except Exception as e:
        logger.warning(f"[excel-etl] 水位文件损坏，按空水位处理: {e}")
        return set()


def save_watermark(output_dir: Path, processed: set) -> None:
    """D24 写回已处理 UID 水位（幂等：重复执行跳过已处理邮件）"""
    marker = output_dir / WATERMARK_FILENAME
    marker.write_text(
        json.dumps({"processed_uids": sorted(processed)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _build_judge_prompt(emails: List[Dict[str, Any]]) -> str:
    """D23 第二级批量判定 prompt：主题+正文预览+附件清单 → 下载/跳过+理由"""
    lines = []
    for e in emails:
        atts = ", ".join(
            f"{a.get('filename')}({a.get('size')}B)" for a in (e.get("attachments") or [])
        )
        lines.append(
            f"- UID {e.get('uid')} | 主题: {e.get('subject', '')} | "
            f"正文预览: {str(e.get('body_preview', ''))[:300]} | 附件: {atts or '无'}"
        )
    return (
        "你是社保增减员报表助理。判断下列邮件中哪些附件是需要进入汇总管线的"
        "人员增减员/社保/公积金报表（.xlsx/.xls/.zip），与报表无关的附件（发票、合同、图片等）跳过。\n\n"
        "## 邮件列表\n" + "\n".join(lines) + "\n\n"
        '## 输出契约\n只返回 JSON：{"judgments": [{"uid": "12", '
        '"downloads": ["文件名.xlsx"], "reason": "一句话理由"}]}，'
        "不相关的邮件 downloads 为空数组。不要输出任何其他文字。"
    )


def judge_attachments_batch(
    emails: List[Dict[str, Any]],
    llm_callable: Optional[Callable[[str], Any]] = None,
    *,
    on_usage: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> Dict[str, List[str]]:
    """D23 第二级：一次 LLM 批量判定未命中关键词的邮件附件。返回 {uid: [附件名]}"""
    prompt = _build_judge_prompt(emails)
    content = _call_llm(prompt, llm_callable, on_usage=on_usage, stage="email_judge")
    parsed = _extract_json(content)
    judgments = parsed.get("judgments") if isinstance(parsed, dict) else parsed
    if not isinstance(judgments, list):
        logger.warning("[excel-etl] 附件判定输出格式异常，全部跳过")
        return {}
    verdict: Dict[str, List[str]] = {}
    for item in judgments:
        if not isinstance(item, dict) or "uid" not in item:
            continue
        downloads = [str(f) for f in (item.get("downloads") or []) if f]
        if downloads:
            verdict[str(item["uid"])] = downloads
    return verdict


def collect_email_files(
    user_email,
    email_since: str,
    output_dir: Path,
    *,
    llm_callable: Optional[Callable[[str], Any]] = None,
    metering: Optional[Metering] = None,
    folder: str = "INBOX",
    limit: int = 50,
    download_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """邮件入口收集来源文件：read(since) → D23 两级判定 → 下载落盘 → D24 水位更新

    Returns:
        {"success", "files": [Path...], "emails_seen", "emails_processed",
         "downloaded": n, "judged_emails": n, "skipped_attachments": [...]}
    """
    read_result = email_lib.read_emails(
        user_email, folder=folder, limit=limit, since=email_since
    )
    if not read_result.get("success"):
        return {"success": False, "error": read_result.get("error"), "files": []}

    emails = read_result.get("emails") or []
    processed = load_watermark(output_dir)
    pending = [e for e in emails if str(e.get("uid")) not in processed]

    # D23 第一级：确定性预筛（扩展名 + 关键词）直接下载
    downloads: List[Tuple[str, str]] = []
    judge_emails: List[Dict[str, Any]] = []
    for e in pending:
        atts = e.get("attachments") or []
        hit = [
            str(a.get("filename"))
            for a in atts
            if Path(str(a.get("filename"))).suffix.lower() in ATTACH_ALLOWED_EXTS
            and ATTACH_KEYWORD_RE.search(str(a.get("filename")))
        ]
        if hit:
            downloads.extend((str(e["uid"]), fn) for fn in hit)
        elif atts:
            judge_emails.append(e)  # 有附件但未命中关键词 → 第二级 LLM 判定

    # D23 第二级：一次 LLM 批量判定（输出 下载/跳过+理由）
    if judge_emails:
        verdict = judge_attachments_batch(
            judge_emails, llm_callable,
            on_usage=metering.on_usage if metering else None,
        )
        for uid, fns in verdict.items():
            downloads.extend((uid, fn) for fn in fns)

    # 按 uid 聚合 → download_attachments 落盘（D22，BODY.PEEK 不标已读）
    target_dir = Path(download_dir) if download_dir else output_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    by_uid: Dict[str, List[str]] = {}
    for uid, fn in downloads:
        by_uid.setdefault(uid, []).append(fn)
    files: List[Path] = []
    skipped_attachments: List[Dict[str, Any]] = []
    for uid, fns in by_uid.items():
        dl = email_lib.download_attachments(
            user_email, uid, str(target_dir), filenames=fns, folder=folder
        )
        if not dl.get("success"):
            logger.warning(f"[excel-etl] UID {uid} 附件下载失败: {dl.get('error')}")
            continue
        files.extend(Path(f["path"]) for f in dl.get("files") or [])
        skipped_attachments.extend(
            {"uid": uid, **s} for s in (dl.get("skipped") or [])
        )

    # D24：本轮处理过的 UID（含无附件/判跳过的）全部记水位，重复执行不重复判定
    save_watermark(output_dir, processed | {str(e["uid"]) for e in pending})

    logger.info(
        f"[excel-etl] 邮件入口: 读到 {len(emails)} 封, 处理 {len(pending)} 封, "
        f"LLM 判定 {len(judge_emails)} 封, 下载 {len(files)} 个文件"
    )
    return {
        "success": True,
        "files": files,
        "emails_seen": len(emails),
        "emails_processed": len(pending),
        "judged_emails": len(judge_emails),
        "downloaded": len(files),
        "skipped_attachments": skipped_attachments,
    }


# ------------------------------------------------------------
# 主流程（文件/邮件入口共用）
# ------------------------------------------------------------


def _source_file_name(source_label: str) -> str:
    return str(source_label).split("#")[0]


def run_etl(
    source_files: Sequence[Path],
    template_path: Path,
    output_dir: Path,
    *,
    llm_callable: Optional[Callable[[str], Any]] = None,
    metering: Optional[Metering] = None,
) -> Dict[str, Any]:
    """ETL 主流程：schema（指纹/重抽）→ 渲染脱敏 → 抽取 → unmask → 真值复检 → 填充 → 报告

    Returns:
        {"success", "xlsx", "report", "stats", "fill_checks", "next_step"}
    """
    on_usage = metering.on_usage if metering else None
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. schema：指纹匹配直接用；不匹配 → 一次 LLM 重抽（D14 只读部署降级为会话内使用）
    schema = load_default_schema()
    schema_note = None
    if not schema_matches(template_path, schema):
        logger.info("[excel-etl] 模板指纹不匹配，触发 schema 会话内重抽（D14）")
        schema = extract_schema_from_template(
            template_path, llm_callable=llm_callable, on_usage=on_usage
        )
        schema_note = (
            "模板表头与内置 schema 指纹不匹配，已按该模板表头会话内重抽字段定义"
            "（只读部署未写回资产，同模板下次会重抽）"
        )

    # 2. 渲染 + 脱敏（Q3：LLM 全程只见占位符域）
    mask_session = MaskSession()
    rendered_sheets: List[Dict[str, Any]] = []
    failed_sources: List[Dict[str, Any]] = []
    for f in source_files:
        rendered = render_llm_view(str(f))
        if not rendered.get("success"):
            failed_sources.append({"file": Path(f).name, "error": rendered.get("error")})
            logger.warning(f"[excel-etl] 来源渲染失败: {f}, {rendered.get('error')}")
            continue
        for sheet in rendered.get("sheets") or []:
            rendered_sheets.append({
                "name": sheet["name"],
                "text": mask_session.mask(sheet["text"]),
                "source_label": f"{Path(f).name}#{sheet['name']}",
            })

    if not rendered_sheets:
        return {
            "success": False,
            "error": "没有可抽取的来源数据（全部来源渲染失败或没有数据行）",
            "failed_sources": failed_sources,
        }

    # 3. 抽取管线（分块 LLM → 校验 → 修复回路 → 同人标注，全程脱敏域）
    result = run_extraction(rendered_sheets, schema, llm_callable=llm_callable, on_usage=on_usage)
    if not result.get("success"):
        return {"success": False, "error": result.get("error"),
                "failed_sources": failed_sources}

    # 4. unmask（⚠️ 必须在所有 LLM 阶段之后：修复回喂会把记录嵌进 prompt）
    records = [mask_session.unmask_record(r) for r in result["records"]]
    record_warnings = [
        [mask_session.unmask(w) for w in warns] for warns in result.get("record_warnings") or []
    ]
    manual_review = [
        dict(item, record=mask_session.unmask_record(item["record"]))
        for item in result.get("manual_review") or []
    ]

    # 5. 真值复检（Phase 2 契约）：unmask 后真值身份证校验位等复查，仍 error → 人工清单
    validations = validate_records(records, schema)
    kept_records: List[Dict[str, Any]] = []
    kept_warnings: List[List[str]] = []
    for rec, warns, v in zip(records, record_warnings, validations):
        if v.get("errors"):
            manual_review.append({
                "record": rec,
                "errors": v["errors"],
                "warnings": v.get("warnings") or [],
                "source_row": "",
                "_source": rec.get("_source"),
            })
        else:
            merged = list(warns) + [w for w in (v.get("warnings") or []) if w not in warns]
            kept_records.append(rec)
            kept_warnings.append(merged)

    # 6. 按来源统计（D16 摘要）
    per_source: Dict[str, Dict[str, Any]] = {}
    for sheet in rendered_sheets:
        per_source.setdefault(
            _source_file_name(sheet["source_label"]),
            {"file": _source_file_name(sheet["source_label"]),
             "extracted": 0, "written": 0, "skipped": 0},
        )
    for rec in result["records"]:
        per_source[_source_file_name(rec.get("_source"))]["extracted"] += 1
    for rec in kept_records:
        per_source[_source_file_name(rec.get("_source"))]["written"] += 1
    for item in manual_review:
        per_source[_source_file_name(item.get("_source"))]["skipped"] += 1
    sources_summary = list(per_source.values())
    for fs in failed_sources:
        sources_summary.append({"file": fs["file"], "extracted": 0, "written": 0,
                                "skipped": 0, "error": fs["error"]})

    # 7. D17 命名 + D15 填充 + D16 报告
    xlsx_name, report_name = output_names(Path(template_path).name)
    xlsx_path = output_dir / xlsx_name
    report_path = output_dir / report_name
    fill = fill_template(template_path, kept_records, schema, output_path=xlsx_path)

    generate_report({
        "template_name": Path(template_path).name,
        "xlsx_path": str(xlsx_path),
        "sources": sources_summary,
        "records": kept_records,
        "record_warnings": kept_warnings,
        "manual_review": manual_review,
        "duplicate_groups": result.get("duplicate_groups") or [],
        "metering": metering.summary() if metering else None,
        "fill_warnings": fill.get("warnings") or [],
        "schema_note": schema_note,
    }, output_path=report_path)

    stats = dict(result.get("stats") or {})
    stats.update({
        "written": len(kept_records),
        # manual_review_count 以最终清单为准：unmask 后真值复检（第 5 步）会把
        # 脱敏域无法校验的身份证校验位错误行补进 manual_review，run_extraction
        # 的原始计数会偏少
        "manual_review_count": len(manual_review),
        "sources_ok": len(per_source),
        "sources_failed": len(failed_sources),
    })
    if metering:
        stats["llm_calls"] = metering.llm_calls

    return {
        "success": fill.get("success", False),
        "xlsx": str(xlsx_path),
        "report": str(report_path),
        "stats": stats,
        "fill_checks": fill.get("checks"),
        "failed_sources": failed_sources,
        "next_step": (
            "把 xlsx 与校验报告两个文件用 cp 工具（register_download=true）注册交付给用户；"
            "人工清单与同人多条提示见报告 .md"
        ),
    }


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多来源脏 Excel → 标准模板抽取填充管线")
    parser.add_argument("--files", nargs="+", default=None,
                        help="来源 Excel 文件（可多个，支持 .zip 自动解包）")
    parser.add_argument("--template", default=None,
                        help="标准模板 xlsx（不确定哪个是模板时可省略，由指纹自动识别；"
                             "都不匹配时必须显式指定）")
    parser.add_argument("--zip", dest="zip_file", default=None,
                        help="zip 包路径（解包后全部 Excel 作为来源）")
    parser.add_argument("--email-since", dest="email_since", default=None,
                        help="邮件入口：起始日期 YYYY-MM-DD（从绑定邮箱拉增减员报表附件）")
    parser.add_argument("--email-folder", dest="email_folder", default="INBOX",
                        help="邮件文件夹（默认 INBOX）")
    parser.add_argument("--email-limit", dest="email_limit", type=int, default=50,
                        help="邮件入口最多检查的邮件数（默认 50）")
    parser.add_argument("--output-dir", dest="output_dir", default=".",
                        help="输出目录（默认当前目录，即 skill 工作区）")
    parser.add_argument("--llm-fake", dest="llm_fake", default=None,
                        help=argparse.SUPPRESS)  # 测试开关：预置 LLM 响应文件，生产不传
    return parser.parse_args(argv)


def _emit(result: Dict[str, Any]) -> None:
    """stdout 输出单段 JSON（主智能体解析后续调 cp 交付）"""
    print(json.dumps(result, ensure_ascii=False, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    llm_fake = _load_fake_llm(args.llm_fake) if args.llm_fake else None
    # 计量落库需要 DB 池；fake 模式（测试）不初始化不落库，防误写生产库
    db_ready = _init_db_pool() if llm_fake is None else False
    metering = Metering(record_usage=db_ready)

    try:
        # ---- 收集输入文件 ----
        files: List[Path] = []
        if args.zip_file:
            files.extend(safe_unzip(_resolve_input_path(args.zip_file),
                                    output_dir / "unpacked_zip"))
        if args.files:
            for raw in args.files:
                path = _resolve_input_path(raw)
                if path.suffix.lower() == ".zip":
                    files.extend(safe_unzip(path, output_dir / f"unpacked_{path.stem}"))
                else:
                    files.append(path)

        email_summary: Optional[Dict[str, Any]] = None
        if args.email_since:
            user_email = resolve_email_credentials()
            collected = collect_email_files(
                user_email, args.email_since, output_dir,
                llm_callable=llm_fake, metering=metering,
                folder=args.email_folder, limit=args.email_limit,
            )
            email_summary = {k: v for k, v in collected.items() if k != "files"}
            if not collected.get("success"):
                _emit({"success": False, "error": collected.get("error")})
                return 1
            files.extend(collected["files"])

        if not files:
            if email_summary is not None:
                # 邮件入口跑了但零新附件（D24 水位幂等重跑 / 无命中）：提示不误导
                _emit({
                    "success": False,
                    "email": email_summary,
                    "error": (
                        f"邮件入口本轮没有新附件可处理（读到 {email_summary.get('emails_seen', 0)} 封，"
                        f"本轮处理 {email_summary.get('emails_processed', 0)} 封，下载 0 个；"
                        "UID 水位内邮件不会重复下载）。若附件此前已下载到本目录，"
                        "请改用 --files 指定这些文件；或更换 --output-dir 重新从邮箱拉取"
                    ),
                })
            else:
                _emit({"success": False, "error": "没有输入文件（--files/--zip/--email-since 至少其一）"})
            return 1

        # ---- 模板识别（D3）+ 主流程 ----
        explicit = _resolve_input_path(args.template) if args.template else None
        template, sources = identify_template(files, load_default_schema(), explicit)
        if not sources:
            _emit({"success": False, "error": "除模板外没有来源数据文件"})
            return 1

        logger.info(f"[excel-etl] 模板: {template.name}, 来源: {[f.name for f in sources]}")
        result = run_etl(sources, template, output_dir,
                         llm_callable=llm_fake, metering=metering)
        result["metering"] = metering.summary()
        if email_summary:
            result["email"] = email_summary
        _emit(result)
        return 0 if result.get("success") else 1
    except Exception as e:
        logger.opt(exception=True).error(f"[excel-etl] 管线失败: {e}")
        _emit({"success": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
