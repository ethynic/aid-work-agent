import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from src.services.association_batch_enrichment import (
    AssociationEnrichmentRow,
)
from src.services.association_enrichment_ui import (
    AssociationUiRunManager,
    EncryptedAuditStore,
    parse_association_evidence_with_llm,
    temporary_upload_path,
)
from src.services.llm_usage_meter import TokenUsage


class FakeGateway:
    def __init__(self, payload):
        self.payload = payload

    async def chat(self, **_kwargs):
        return {"content": json.dumps(self.payload, ensure_ascii=False)}


@pytest.mark.asyncio
async def test_llm_list_parser_rejects_name_not_grounded_in_input():
    with pytest.raises(ValueError, match="INPUT_LLM_EVIDENCE_INVALID"):
        await parse_association_evidence_with_llm(
            ["中国缝制机械协会"],
            gateway=FakeGateway(
                {
                    "associations": [
                        {
                            "name": "模型臆造协会",
                            "evidence": "中国缝制机械协会",
                        }
                    ]
                }
            ),
        )


@pytest.mark.asyncio
async def test_llm_list_parser_preserves_first_semantic_canonical_name():
    evidence = "中国缝制机械协会\n中国缝制机械协会"
    names = await parse_association_evidence_with_llm(
        [evidence],
        gateway=FakeGateway(
            {
                "associations": [
                    {"name": "中国缝制机械协会", "evidence": evidence},
                    {"name": "中国缝制机械协会", "evidence": evidence},
                ]
            }
        ),
    )
    assert names == ["中国缝制机械协会"]


@pytest.mark.asyncio
async def test_llm_list_parser_rejects_mobile_in_name_even_when_grounded():
    evidence = "测试协会 13912345678"
    with pytest.raises(ValueError, match="INPUT_ASSOCIATION_NAME_INVALID"):
        await parse_association_evidence_with_llm(
            [evidence],
            gateway=FakeGateway(
                {
                    "associations": [
                        {"name": evidence, "evidence": evidence},
                    ]
                }
            ),
        )


def test_encrypted_audit_metadata_never_contains_plain_mobile(tmp_path):
    store = EncryptedAuditStore(
        tmp_path,
        protect=lambda value: b"cipher:" + value[::-1],
        unprotect=lambda value: value.removeprefix(b"cipher:")[::-1],
    )
    event = store.append(
        association="测试协会",
        stage="微信",
        kind="wechat_artifact",
        summary="找到 13912345678",
        detail={"text": "联系人 13912345678"},
    )
    assert "13912345678" not in json.dumps(event.__dict__, ensure_ascii=False)
    assert not any(
        b"13912345678" in path.read_bytes()
        for path in (tmp_path / "details").iterdir()
    )
    assert store.read_detail(event.detail_ref)["text"] == "联系人 13912345678"


class FakeEnricher:
    async def enrich_one(self, name):
        return AssociationEnrichmentRow(
            association_name=name,
            processing_status="complete",
            processed_at="2026-07-30T00:00:00+00:00",
        )


@pytest.mark.asyncio
async def test_run_is_serial_and_persists_reloadable_safe_metadata(tmp_path):
    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    )
    run = manager.create(["测试协会", "测试协会"])
    await manager.start(run.run_id)
    assert run.completed_count == 1
    assert run.status == "completed"
    assert manager.output_path(run.run_id).is_file()

    reloaded = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    ).get(run.run_id)
    assert reloaded.status == "completed"
    assert reloaded.associations == ["测试协会"]


def test_upload_rejects_unsupported_type_and_oversize():
    with pytest.raises(ValueError, match="INPUT_FILE_TYPE_UNSUPPORTED"):
        temporary_upload_path("input.xls", b"x")
    with pytest.raises(ValueError, match="INPUT_FILE_TOO_LARGE"):
        temporary_upload_path("input.csv", b"x" * (10 * 1024 * 1024 + 1))


def test_upload_rejects_xlsx_zip_bomb_metadata():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", b"x" * (65 * 1024 * 1024))
    with pytest.raises(ValueError, match="INPUT_XLSX_ARCHIVE_UNSAFE"):
        temporary_upload_path("input.xlsx", content.getvalue())


def test_run_rejects_mobile_in_association_name_to_keep_metadata_safe(tmp_path):
    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    )
    with pytest.raises(ValueError, match="INPUT_ASSOCIATION_NAME_INVALID"):
        manager.create(["测试协会 13912345678"])


def test_output_path_rejects_persisted_path_traversal(tmp_path):
    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    )
    run = manager.create(["测试协会"])
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"not-a-workbook")
    run.output_name = "../outside.xlsx"
    with pytest.raises(ValueError, match="RUN_OUTPUT_NOT_READY"):
        manager.output_path(run.run_id)


def test_restart_marks_interrupted_run_failed_on_disk(tmp_path):
    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    )
    run = manager.create(["测试协会"])
    run.status = "running"
    manager._persist(run)

    reloaded = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    ).get(run.run_id)
    persisted = json.loads(
        (tmp_path / run.run_id / "run.json").read_text(encoding="utf-8")
    )
    assert reloaded.status == "failed"
    assert reloaded.error_code == "PROCESS_RESTARTED"
    assert persisted["status"] == "failed"
    assert persisted["error_code"] == "PROCESS_RESTARTED"


def test_restart_ignores_metadata_whose_run_id_does_not_match_directory(tmp_path):
    run_dir = tmp_path / ("a" * 32)
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "../outside",
                "associations": ["测试协会"],
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    )
    assert manager._runs == {}


@pytest.mark.asyncio
async def test_unexpected_failure_is_isolated_to_one_association(tmp_path):
    class SometimesBrokenEnricher:
        async def enrich_one(self, name):
            if name == "协会一":
                raise RuntimeError("unexpected")
            return AssociationEnrichmentRow(
                association_name=name,
                processing_status="complete",
                processed_at="2026-07-30T00:00:00+00:00",
            )

    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: SometimesBrokenEnricher(),
    )
    run = manager.create(["协会一", "协会二"])
    await manager.start(run.run_id)
    assert run.completed_count == 2
    assert run.status == "partial"
    assert run.rows == [
        {"association_name": "协会一", "processing_status": "failed"},
        {"association_name": "协会二", "processing_status": "complete"},
    ]


@pytest.mark.asyncio
async def test_session_fatal_stops_twenty_item_ui_run_and_exports_partial_rows(tmp_path):
    from src.services.association_batch_enrichment import (
        AssociationBatchAborted,
        WechatRpaError,
    )

    calls = []

    class SessionFatalEnricher:
        async def enrich_one(self, name):
            calls.append(name)
            error = WechatRpaError(
                "PLUGIN_CLOSE_TIMEOUT",
                stage="cleanup",
                session_fatal=True,
                recovered_mobile="18512345678",
            )
            row = AssociationEnrichmentRow(
                association_name=name,
                processing_status="partial",
                processed_at="2026-08-03T00:00:00+00:00",
            )
            row.values["president_mobile"] = error.recovered_mobile
            raise AssociationBatchAborted(row, error)

    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: SessionFatalEnricher(),
    )
    names = [f"测试协会{index}" for index in range(20)]
    run = manager.create(names)

    await manager.start(run.run_id)

    assert calls == [names[0]]
    assert run.completed_count == 1
    assert run.rows == [
        {"association_name": names[0], "processing_status": "partial"}
    ]
    assert run.status == "partial"
    assert run.progress_percent() == 100
    assert run.output_name == "association-results.xlsx"
    assert (tmp_path / run.run_id / run.output_name).is_file()
    assert "18512345678" not in json.dumps(run.public_dict(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_launch_reserves_global_rpa_slot_before_task_starts(tmp_path):
    release = asyncio.Event()

    class BlockingEnricher:
        async def enrich_one(self, name):
            await release.wait()
            return AssociationEnrichmentRow(
                association_name=name,
                processing_status="complete",
                processed_at="2026-07-30T00:00:00+00:00",
            )

    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: BlockingEnricher(),
    )
    first = manager.create(["协会一"])
    second = manager.create(["协会二"])
    task = manager.launch(first.run_id)
    assert manager.busy is True
    with pytest.raises(RuntimeError, match="RPA_BUSY"):
        manager.launch(second.run_id)
    release.set()
    await task
    assert manager.busy is False


@pytest.mark.asyncio
async def test_progress_events_preserve_association_and_stage_order(tmp_path):
    class ReportingEnricher:
        def __init__(self, progress):
            self.progress = progress

        async def enrich_one(self, name):
            self.progress(f"[{name}] 查找官网")
            self.progress(f"[{name}] 微信检索")
            return AssociationEnrichmentRow(
                association_name=name,
                processing_status="complete",
                processed_at="2026-07-30T00:00:00+00:00",
            )

    manager = AssociationUiRunManager(
        tmp_path,
        lambda progress, _audit: ReportingEnricher(progress),
    )
    run = manager.create(["协会一", "协会二"])
    await manager.start(run.run_id)
    progress = [
        (event.association, event.stage)
        for event in run.events
        if event.kind == "progress"
    ]
    assert progress == [
        ("协会一", "查找官网"),
        ("协会一", "微信检索"),
        ("协会二", "查找官网"),
        ("协会二", "微信检索"),
    ]


@pytest.mark.asyncio
async def test_progress_does_not_regress_for_repeated_or_unknown_stage(tmp_path):
    observed = []

    class OutOfOrderEnricher:
        def __init__(self, progress):
            self.progress = progress

        async def enrich_one(self, name):
            for stage in (
                "正在使用可见浏览器采集官网",
                "正在发现官网",
                "临时重试",
                "正在微信检索会长",
            ):
                self.progress(f"[{name}] {stage}")
                observed.append(run.progress_percent())
            return AssociationEnrichmentRow(
                association_name=name,
                processing_status="complete",
                processed_at="2026-07-30T00:00:00+00:00",
            )

    manager = AssociationUiRunManager(
        tmp_path,
        lambda progress, _audit: OutOfOrderEnricher(progress),
    )
    run = manager.create(["协会一"])
    await manager.start(run.run_id)
    observed.append(run.progress_percent())
    assert observed == sorted(observed)
    assert observed == [30, 30, 30, 60, 100]


def test_audit_detail_ref_cannot_be_read_from_another_run(tmp_path):
    manager = AssociationUiRunManager(
        tmp_path,
        lambda _progress, _audit: FakeEnricher(),
    )
    first = manager.create(["协会一"])
    second = manager.create(["协会二"])
    event = manager.store(first.run_id).append(
        association="协会一",
        stage="微信",
        kind="wechat_artifact",
        summary="已读取",
        detail={"text": "联系人 13912345678"},
    )
    with pytest.raises(ValueError, match="AUDIT_DETAIL_NOT_FOUND"):
        manager.store(second.run_id).read_detail(event.detail_ref)


def test_overall_progress_uses_current_stage_and_never_regresses_between_items():
    from src.services.association_enrichment_ui import UiRun

    run = UiRun(run_id="a" * 32, associations=["协会一", "协会二"])
    observed = []
    for stage in (
        "正在发现官网",
        "正在使用可见浏览器采集官网",
        "正在使用网络检索补充基础信息",
        "正在微信检索会长",
        "正在微信检索秘书长",
    ):
        run.current_stage = stage
        observed.append(run.progress_percent())
    run.completed_count = 1
    run.current_stage = "准备下一个协会"
    observed.append(run.progress_percent())
    run.current_stage = "正在发现官网"
    observed.append(run.progress_percent())
    assert observed == sorted(observed)
    assert observed == [5, 15, 18, 30, 40, 50, 55]


def test_completed_item_history_is_not_added_to_next_segment():
    from src.services.association_enrichment_ui import AuditEvent, UiRun

    run = UiRun(
        run_id="a" * 32,
        associations=["协会一", "协会二"],
        current_association="协会一",
        current_stage="准备下一个协会",
        completed_count=1,
        status="running",
        events=[
            AuditEvent(
                timestamp="2026-07-30T00:00:00+00:00",
                association="协会一",
                stage="正在微信检索秘书长",
                kind="progress",
                summary="正在微信检索秘书长",
            )
        ],
    )
    assert run.progress_percent() == 50

    run.current_association = "协会二"
    run.current_stage = "正在发现官网"
    assert run.progress_percent() == 55


def test_excel_stage_is_95_and_terminal_output_is_100():
    from src.services.association_enrichment_ui import UiRun

    run = UiRun(
        run_id="a" * 32,
        associations=["协会一"],
        completed_count=1,
        current_stage="正在写入 Excel",
        status="running",
    )
    assert run.progress_percent() == 95
    run.output_name = "association-results.xlsx"
    run.status = "completed"
    assert run.progress_percent() == 100


def test_batch_token_summary_and_parse_usage_are_attributed_without_double_count(tmp_path):
    manager = AssociationUiRunManager(tmp_path, lambda _progress, _audit: FakeEnricher())
    run = manager.create(
        ["协会一", "协会二"],
        initial_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            total_tokens=120,
            call_count=1,
        ),
    )
    run.token_usage_by_association["协会二"] = TokenUsage(
        input_tokens=60,
        cached_input_tokens=10,
        output_tokens=15,
        total_tokens=75,
        call_count=1,
    ).as_dict()
    summary = run.token_summary()
    assert summary["batch"]["input_tokens"] == 160
    assert summary["batch"]["cached_input_tokens"] == 50
    assert summary["batch"]["output_tokens"] == 35
    assert summary["batch"]["total_tokens"] == 195
    assert summary["batch"]["average_input_tokens"] == 80
    assert summary["associations"]["协会一"]["input_tokens"] == 0
    assert summary["batch_overhead"]["input_tokens"] == 100
    manager._persist(run)
    reloaded = AssociationUiRunManager(
        tmp_path, lambda _progress, _audit: FakeEnricher()
    ).get(run.run_id)
    assert reloaded.token_summary()["batch"]["total_tokens"] == 195

    run.output_name = None
    run.status = "failed"
    run.error_code = "OSError"
    assert run.progress_percent() == 100


@pytest.mark.asyncio
async def test_llm_usage_is_attributed_to_each_active_association_and_stage(tmp_path):
    from src.services.llm_usage_meter import record_usage

    class MeteredEnricher:
        def __init__(self, progress):
            self.progress = progress

        async def enrich_one(self, name):
            self.progress(f"[{name}] 官网识别")
            record_usage({"prompt_tokens": 10, "completion_tokens": 2})
            self.progress(f"[{name}] 微信检索会长")
            record_usage({"prompt_tokens": 5, "completion_tokens": 1})
            return AssociationEnrichmentRow(
                association_name=name,
                processing_status="complete",
                processed_at="2026-08-03T00:00:00+00:00",
            )

    manager = AssociationUiRunManager(
        tmp_path,
        lambda progress, _audit: MeteredEnricher(progress),
    )
    run = manager.create(
        ["协会一", "协会二"],
        initial_usage=TokenUsage(
            input_tokens=3,
            output_tokens=1,
            total_tokens=4,
            call_count=1,
        ),
    )
    await manager.start(run.run_id)
    summary = run.token_summary()
    assert summary["associations"]["协会一"]["total_tokens"] == 18
    assert summary["associations"]["协会二"]["total_tokens"] == 18
    assert summary["batch_overhead"]["total_tokens"] == 4
    assert summary["batch"]["total_tokens"] == 40
    assert summary["batch"]["call_count"] == 5
    assert [
        event.stage for event in run.events if event.kind == "llm_usage"
    ] == ["清单解析", "官网识别", "微信检索会长", "官网识别", "微信检索会长"]
