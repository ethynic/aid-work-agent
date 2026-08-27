"""本机协会调查工作台的清单解析、任务状态与加密审计。"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import json
import re
import tempfile
import uuid
import zipfile
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, Sequence

from src.llm.gateway import llm_gateway
from src.services.association_batch_enrichment import (
    AssociationBatchAborted,
    AssociationBatchEnricher,
    AssociationEnrichmentRow,
    deduplicate_association_names,
    parse_association_input,
    write_enrichment_workbook,
)
from src.services.association_profile_extractor import _parse_json_object
from src.services.llm_usage_meter import (
    TokenUsage,
    install_usage_recorder,
    reset_usage_recorder,
    reset_usage_context,
    set_usage_context,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_XLSX_ENTRY_COUNT = 10_000
ALLOWED_UPLOAD_SUFFIXES = {".csv", ".xlsx"}
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_STAGE_PROGRESS = (
    (("发现官网",), 0.10),
    (("可见浏览器", "官网采集"), 0.30),
    (("网络检索",), 0.35),
    (("微信检索秘书长", "微信搜一搜·秘书长"), 0.60),
    (("微信检索会员", "微信搜一搜·会员"), 0.70),
    (("微信检索办公室", "微信搜一搜·办公室"), 0.80),
    (("写入 Excel",), 0.95),
)


def _stage_progress_fraction(stage: str) -> float:
    for terms, fraction in _STAGE_PROGRESS:
        if any(term in stage for term in terms):
            return fraction
    return 0.0


def redact_sensitive(text: object) -> str:
    value = str(text or "")
    return _MOBILE_RE.sub(
        lambda match: f"{match.group()[:3]}****{match.group()[-4:]}",
        value,
    )[:500]


def _evidence_identity(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


async def parse_association_evidence_with_llm(
    evidence_values: Sequence[str],
    *,
    gateway=llm_gateway,
) -> list[str]:
    """只从给定证据提取协会名，并让 LLM 做语义去重。"""
    evidence = [str(value).strip() for value in evidence_values if str(value).strip()]
    if not evidence:
        raise ValueError("INPUT_ASSOCIATION_LIST_EMPTY")
    response = await gateway.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "从用户证据中提取待查询的中国协会、学会、商会名称并语义去重。"
                    "只能使用证据中逐字存在的名称，禁止补全或臆造。语义重复时保留首次"
                    "出现的规范全称。只输出严格JSON对象："
                    '{"associations":[{"name":"原文名称","evidence":"包含该名称的原文"}]}。'
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"evidence": evidence}, ensure_ascii=False),
            },
        ],
        temperature=0,
        max_tokens=4000,
        enable_thinking=False,  # 协会名抽取小任务关思考（提速+防思考烧穿max_tokens）
    )
    content = response.get("content")
    if not isinstance(content, str):
        raise ValueError("INPUT_LLM_RESPONSE_INVALID")
    parsed = _parse_json_object(content)
    if set(parsed) != {"associations"} or not isinstance(
        parsed["associations"], list
    ):
        raise ValueError("INPUT_LLM_SCHEMA_INVALID")
    evidence_identities = [_evidence_identity(item) for item in evidence]
    names: list[str] = []
    for item in parsed["associations"]:
        if not isinstance(item, dict) or set(item) != {"name", "evidence"}:
            raise ValueError("INPUT_LLM_SCHEMA_INVALID")
        name = item["name"]
        quote = item["evidence"]
        if not isinstance(name, str) or not isinstance(quote, str):
            raise ValueError("INPUT_LLM_SCHEMA_INVALID")
        quote_identity = _evidence_identity(quote)
        name_identity = _evidence_identity(name)
        if (
            not name_identity
            or not any(quote_identity in source for source in evidence_identities)
            or name_identity not in quote_identity
        ):
            raise ValueError("INPUT_LLM_EVIDENCE_INVALID")
        names.append(name.strip())
    result = deduplicate_association_names(names)
    if not result:
        raise ValueError("INPUT_ASSOCIATION_LIST_EMPTY")
    if any(_MOBILE_RE.search(name) for name in result):
        raise ValueError("INPUT_ASSOCIATION_NAME_INVALID")
    return result


def extract_upload_evidence(path: Path) -> list[str]:
    """复用 CLI 安全解析器，将文件中的协会列转换为 LLM 证据。"""
    return parse_association_input(input_path=path)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("DPAPI_REQUIRES_WINDOWS")
    source, source_buffer = _blob(data)
    output = _DataBlob()
    function = (
        ctypes.windll.crypt32.CryptProtectData
        if protect
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    args = (
        (ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
        if protect
        else (ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
    )
    if not function(*args):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer


@dataclass
class AuditEvent:
    timestamp: str
    association: str
    stage: str
    kind: str
    summary: str
    detail_ref: str | None = None


class EncryptedAuditStore:
    def __init__(
        self,
        run_dir: Path,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ):
        self.run_dir = run_dir
        self.detail_dir = run_dir / "details"
        self.detail_dir.mkdir(parents=True, exist_ok=True)
        self._protect = protect or (lambda data: _dpapi_transform(data, protect=True))
        self._unprotect = unprotect or (
            lambda data: _dpapi_transform(data, protect=False)
        )

    def append(
        self,
        *,
        association: str,
        stage: str,
        kind: str,
        summary: str,
        detail: object | None = None,
    ) -> AuditEvent:
        detail_ref = None
        if detail is not None:
            detail_ref = uuid.uuid4().hex
            plaintext = json.dumps(detail, ensure_ascii=False).encode("utf-8")
            (self.detail_dir / f"{detail_ref}.dpapi").write_bytes(
                self._protect(plaintext)
            )
        return AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            association=association,
            stage=stage,
            kind=kind,
            summary=redact_sensitive(summary),
            detail_ref=detail_ref,
        )

    def read_detail(self, detail_ref: str) -> object:
        if not re.fullmatch(r"[a-f0-9]{32}", detail_ref):
            raise ValueError("AUDIT_DETAIL_REF_INVALID")
        path = self.detail_dir / f"{detail_ref}.dpapi"
        if not path.is_file():
            raise ValueError("AUDIT_DETAIL_NOT_FOUND")
        return json.loads(self._unprotect(path.read_bytes()).decode("utf-8"))


@dataclass
class UiRun:
    run_id: str
    associations: list[str]
    status: str = "pending"
    current_association: str | None = None
    current_stage: str = "pending"
    completed_count: int = 0
    rows: list[dict] = field(default_factory=list)
    events: list[AuditEvent] = field(default_factory=list)
    output_name: str | None = None
    error_code: str | None = None
    token_usage_by_association: dict[str, dict[str, int]] = field(default_factory=dict)
    batch_overhead_token_usage: dict[str, int] = field(
        default_factory=lambda: TokenUsage().as_dict()
    )

    def token_summary(self) -> dict:
        items = {
            name: TokenUsage(**self.token_usage_by_association.get(name, {})).as_dict()
            for name in self.associations
        }
        total = TokenUsage()
        for usage in items.values():
            total.add(TokenUsage(**usage))
        overhead = TokenUsage(**self.batch_overhead_token_usage)
        total.add(overhead)
        count = len(self.associations)
        return {
            "associations": items,
            "batch_overhead": overhead.as_dict(),
            "batch": {
                **total.as_dict(),
                "average_input_tokens": round(total.input_tokens / count, 2) if count else 0,
                "average_cached_input_tokens": round(total.cached_input_tokens / count, 2) if count else 0,
                "average_output_tokens": round(total.output_tokens / count, 2) if count else 0,
                "average_total_tokens": round(total.total_tokens / count, 2) if count else 0,
            },
        }

    def progress_percent(self) -> int:
        if self.status in {"completed", "partial", "failed"}:
            return 100
        if "写入 Excel" in self.current_stage:
            return 95
        stage_fraction = _stage_progress_fraction(self.current_stage)
        is_active_association = (
            self.current_association is not None
            and self.completed_count < len(self.associations)
            and self.associations[self.completed_count] == self.current_association
        )
        if is_active_association:
            stage_fraction = max(
                [stage_fraction]
                + [
                    _stage_progress_fraction(event.stage)
                    for event in self.events
                    if event.kind == "progress"
                    and event.association == self.current_association
                ]
            )
        elif self.current_association is not None:
            stage_fraction = 0.0
        total = len(self.associations)
        return min(94, round(
            (self.completed_count + stage_fraction) * 100 / total
        )) if total else 0

    def public_dict(self) -> dict:
        value = asdict(self)
        value["events"] = [asdict(event) for event in self.events]
        value["progress_percent"] = self.progress_percent()
        value["token_usage"] = self.token_summary()
        return value


EnricherFactory = Callable[
    [Callable[[str], None], Callable[..., None]], AssociationBatchEnricher
]


class AssociationUiRunManager:
    """单进程、单 RPA 任务的本地运行管理器。"""

    def __init__(self, root: Path, enricher_factory: EnricherFactory):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._factory = enricher_factory
        self._runs: dict[str, UiRun] = {}
        self._stores: dict[str, EncryptedAuditStore] = {}
        self._execution_lock = asyncio.Lock()
        self._reserved = False
        self._load_runs()

    def create(
        self,
        associations: Sequence[str],
        *,
        initial_usage: TokenUsage | None = None,
    ) -> UiRun:
        names = deduplicate_association_names(associations)
        if not names:
            raise ValueError("INPUT_ASSOCIATION_LIST_EMPTY")
        if any(_MOBILE_RE.search(name) for name in names):
            raise ValueError("INPUT_ASSOCIATION_NAME_INVALID")
        run = UiRun(
            run_id=uuid.uuid4().hex,
            associations=names,
            token_usage_by_association={name: TokenUsage().as_dict() for name in names},
        )
        if initial_usage is not None:
            run.batch_overhead_token_usage = initial_usage.as_dict()
        self._runs[run.run_id] = run
        self._stores[run.run_id] = EncryptedAuditStore(self.root / run.run_id)
        if initial_usage is not None and initial_usage.call_count:
            run.events.append(
                self._stores[run.run_id].append(
                    association="批次",
                    stage="清单解析",
                    kind="llm_usage",
                    summary=(
                        f"LLM tokens input={initial_usage.input_tokens}, "
                        f"cached={initial_usage.cached_input_tokens}, "
                        f"output={initial_usage.output_tokens}, total={initial_usage.total_tokens}"
                    ),
                )
            )
        self._persist(run)
        return run

    def get(self, run_id: str) -> UiRun:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError("RUN_NOT_FOUND")
        return run

    def store(self, run_id: str) -> EncryptedAuditStore:
        self.get(run_id)
        return self._stores[run_id]

    @property
    def busy(self) -> bool:
        return self._reserved or self._execution_lock.locked()

    def launch(self, run_id: str) -> asyncio.Task:
        """同步占用全局槽位，消除两个 HTTP start 请求之间的竞态。"""
        self.get(run_id)
        if self.busy:
            raise RuntimeError("RPA_BUSY")
        self._reserved = True
        return asyncio.create_task(self.start(run_id, _reserved=True))

    async def start(self, run_id: str, *, _reserved: bool = False) -> None:
        run = self.get(run_id)
        if run.status != "pending":
            raise ValueError("RUN_ALREADY_STARTED")
        if self._execution_lock.locked() or (self._reserved and not _reserved):
            raise RuntimeError("RPA_BUSY")
        try:
            async with self._execution_lock:
                run.status = "running"
                self._persist(run)
                store = self.store(run_id)

                def audit(**event) -> None:
                    if not event.get("association"):
                        event["association"] = current["association"]
                    run.events.append(store.append(**event))
                    self._persist(run)

                current = {"association": ""}

                def record_tokens(association: str, stage: str, usage: TokenUsage) -> None:
                    owner = association if association in run.associations else current["association"]
                    if owner not in run.associations:
                        return
                    aggregate = TokenUsage(**run.token_usage_by_association.get(owner, {}))
                    aggregate.add(usage)
                    run.token_usage_by_association[owner] = aggregate.as_dict()
                    audit(
                        association=owner,
                        stage=stage,
                        kind="llm_usage",
                        summary=(
                            f"LLM tokens input={usage.input_tokens}, cached={usage.cached_input_tokens}, "
                            f"output={usage.output_tokens}, total={usage.total_tokens}"
                        ),
                    )

                def progress(message: str) -> None:
                    match = re.match(r"\[([^\]]+)\]\s*(.*)", message)
                    association = match.group(1) if match else current["association"]
                    stage = match.group(2) if match else message
                    current["association"] = association
                    run.current_association = association
                    run.current_stage = stage
                    set_usage_context(association=association, stage=stage)
                    audit(
                        association=association,
                        stage=stage,
                        kind="progress",
                        summary=stage,
                    )

                enricher = self._factory(progress, audit)
                usage_token = install_usage_recorder(record_tokens)
                context_tokens = set_usage_context(association="", stage="llm")
                rows: list[AssociationEnrichmentRow] = []
                try:
                    for name in run.associations:
                        batch_aborted = False
                        current["association"] = name
                        run.current_association = name
                        set_usage_context(association=name, stage="协会处理")
                        try:
                            row = await enricher.enrich_one(name)
                        except AssociationBatchAborted as exc:
                            row = exc.row
                            batch_aborted = True
                            audit(
                                association=name,
                                stage=run.current_stage,
                                kind="error",
                                summary=exc.error.error_code,
                            )
                        except Exception as exc:
                            row = AssociationEnrichmentRow(
                                association_name=name,
                                processing_status="failed",
                                errors=[f"unexpected:{type(exc).__name__}"],
                                processed_at=datetime.now(timezone.utc).isoformat(),
                            )
                            audit(
                                association=name,
                                stage=run.current_stage,
                                kind="error",
                                summary=type(exc).__name__,
                            )
                        rows.append(row)
                        run.rows.append(
                            {
                                "association_name": row.association_name,
                                "processing_status": row.processing_status,
                            }
                        )
                        run.completed_count += 1
                        run.current_stage = "准备下一个协会"
                        self._persist(run)
                        if batch_aborted:
                            break
                    run.current_stage = "正在写入 Excel"
                    self._persist(run)
                    output = write_enrichment_workbook(
                        rows,
                        self.root / run_id / "association-results.xlsx",
                        token_usage=run.token_summary(),
                    )
                    run.output_name = output.name
                    run.status = (
                        "completed"
                        if rows
                        and all(row.processing_status == "complete" for row in rows)
                        else "failed"
                        if rows
                        and all(row.processing_status == "failed" for row in rows)
                        else "partial"
                    )
                    run.current_stage = "已生成 Excel"
                except Exception as exc:
                    run.status = "failed"
                    run.error_code = type(exc).__name__
                    audit(
                        association=current["association"],
                        stage=run.current_stage,
                        kind="error",
                        summary=type(exc).__name__,
                    )
                finally:
                    reset_usage_context(context_tokens)
                    reset_usage_recorder(usage_token)
                    self._persist(run)
        finally:
            if _reserved:
                self._reserved = False

    def output_path(self, run_id: str) -> Path:
        run = self.get(run_id)
        if not run.output_name:
            raise ValueError("RUN_OUTPUT_NOT_READY")
        run_dir = (self.root / run_id).resolve()
        path = (run_dir / run.output_name).resolve()
        if path.parent != run_dir or not path.is_file():
            raise ValueError("RUN_OUTPUT_NOT_READY")
        return path

    def _metadata_path(self, run_id: str) -> Path:
        return self.root / run_id / "run.json"

    def _persist(self, run: UiRun) -> None:
        path = self._metadata_path(run.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(run.public_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _load_runs(self) -> None:
        for path in self.root.glob("*/run.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                run_id = data.get("run_id")
                if (
                    not isinstance(run_id, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", run_id)
                    or run_id != path.parent.name
                ):
                    continue
                events = [AuditEvent(**event) for event in data.pop("events", [])]
                data.pop("progress_percent", None)
                data.pop("token_usage", None)
                run = UiRun(**data, events=events)
                if run.status == "running":
                    run.status = "failed"
                    run.error_code = "PROCESS_RESTARTED"
                self._runs[run.run_id] = run
                self._stores[run.run_id] = EncryptedAuditStore(path.parent)
                if data.get("status") == "running":
                    self._persist(run)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue


def temporary_upload_path(filename: str, content: bytes) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise ValueError("INPUT_FILE_TYPE_UNSUPPORTED")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("INPUT_FILE_TOO_LARGE")
    if suffix == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > MAX_XLSX_ENTRY_COUNT
                    or any(entry.flag_bits & 0x1 for entry in entries)
                    or sum(entry.file_size for entry in entries)
                    > MAX_XLSX_UNCOMPRESSED_BYTES
                ):
                    raise ValueError("INPUT_XLSX_ARCHIVE_UNSAFE")
        except zipfile.BadZipFile as exc:
            raise ValueError("INPUT_XLSX_ARCHIVE_INVALID") from exc
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(content)
        return Path(handle.name)
    finally:
        handle.close()
