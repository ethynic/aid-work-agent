"""Protect full-answer delivery, default zero-model preview and billed opt-in summaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.channels.wecom_kf.budget import WeComKfReplyBudget
from src.channels.wecom_kf.reply_delivery import prepare_reply, _model_preview
from src.channels.wecom_kf.reply_format import FILE_NOTICE
from src.models.message import UnifiedResponse, DownloadableFileInfo
from src.services.session_record import SessionRecordService

pytestmark = pytest.mark.channels


@pytest.fixture(autouse=True)
def summary_price(monkeypatch):
    # The physical summary test model is billable; absent prices are tested
    # separately and must prevent the API call, rather than produce free usage.
    monkeypatch.setattr("src.db.models.TokenCostPriceDB.get_by_model_name",
                        lambda model: {"input_price_per_m": 1, "output_price_per_m": 2})


class Registry:
    def __init__(self):
        self.values = {}
        self.fail_write = False

    def make_key(self, prefix, file_id):
        return prefix + ":" + file_id

    def hset(self, key, field, value):
        if not self.fail_write:
            self.values.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.values.get(key, {}))

    def expire(self, key, seconds):
        return key in self.values

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def registry(monkeypatch, tmp_path):
    store = Registry()
    monkeypatch.setattr("src.core.redis_client.redis_client", store)
    monkeypatch.setattr("src.channels.wecom_kf.adapter.redis_client", store)

    def directory(tenant):
        if "/" in tenant or "\\" in tenant or tenant in {".", ".."}:
            raise ValueError("bad tenant")
        path = tmp_path / tenant / "conversation"
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr("src.core.storage.get_conversation_dir", directory)
    monkeypatch.setattr("src.channels.wecom_kf.reply_delivery.get_conversation_dir", directory)
    return store


async def prepare(text, files=None, **kwargs):
    return await prepare_reply(text, files if files is not None else [],
        tenant_id="tenant_a", user_id="user_a", session_id="session_a", owner_id="owner_a", **kwargs)


def record():
    value = SessionRecordService("session_a", "user_a", "问题", tenant_id="tenant_a", source_type="wecom_kf")
    value.skip_save = True
    return value


@pytest.fixture
def adapter():
    value = WeComKfAdapter(corp_id="test", secret="test")
    value._tenant_id = "tenant_a"
    value.current_open_kfid = "kf_a"
    value.api_client = SimpleNamespace(
        upload_media=AsyncMock(return_value={"errcode": 0, "media_id": "md_media"}),
        send_msg=AsyncMock(return_value={"errcode": 0}),
    )
    value._get_default_thumb_media_id = AsyncMock(return_value="thumb")
    return value


def response(text, files, delivery, budget=None, images=None):
    return UnifiedResponse(message_id="reply_a", reply_to="external_a",
        content={"text": text, "_kf_delivery": delivery,
                 "_kf_reply_budget": budget or WeComKfReplyBudget(), "images": images or []},
        downloadable_files=[DownloadableFileInfo(**item) for item in files])


@pytest.mark.parametrize("text", ["a" * 2048, "a" * 501, "你" * 600, "a" * 2030])
async def test_existing_threshold_preserves_short_complete_answer(text, registry):
    files = []
    assert await prepare(text, files) == {}
    assert files == []


async def test_short_code_image_example_remains_text_in_budget_send(registry, adapter):
    full = "示例：\n```markdown\n![图](https://example.com/example.png)\n```"
    files = []
    delivery = await prepare(full, files)
    adapter._send_full_text_as_image = AsyncMock(side_effect=AssertionError("code is not an image"))
    message = response(full, files, delivery)
    if not delivery:
        message.content.pop("_kf_delivery")
    assert await adapter.send_message(message)
    assert [call.kwargs["msgtype"] for call in adapter.api_client.send_msg.await_args_list] == ["text"]


async def test_custom_byte_limit_still_sends_preview_and_full_file(registry, adapter):
    adapter._max_bytes = 1024
    full = "😀" * 1000
    files = []
    delivery = await prepare(full, files, max_bytes=1024)
    assert len(delivery["text"]) <= 500
    assert len(delivery["text"].encode("utf-8")) <= 1024
    assert delivery["text"].endswith(FILE_NOTICE)
    assert await adapter.send_message(response(full, files, delivery))
    assert [call.kwargs["msgtype"] for call in adapter.api_client.send_msg.await_args_list] == ["text", "file"]


async def test_overflow_default_is_prefix_and_complete_utf8_md(registry, monkeypatch):
    monkeypatch.setattr("src.channels.wecom_kf.reply_delivery._model_preview", AsyncMock(side_effect=AssertionError("model forbidden")))
    full = "您好，先确认订单。\n\n" + "这是真正的完整答复。" * 200
    files = []
    usage = record()
    delivery = await prepare(full, files, record_service=usage)
    assert delivery["mode"] == "prefix"
    assert delivery["text"].startswith("您好，先确认订单。")
    assert delivery["text"].endswith("…" + FILE_NOTICE)
    assert len(delivery["text"]) <= 500
    assert usage.skip_save and usage.total_token_count == 0
    info = files[0]
    meta = registry.hgetall("uploaded_file:" + info["file_id"])
    from pathlib import Path
    assert Path(meta["path"]).read_text(encoding="utf-8") == full
    assert meta["tenant_id"] == "tenant_a" and meta["owner_id"] == "owner_a"
    assert info["mime_type"] == "text/markdown" and info["file_name"] == "详细答复.md"


async def test_short_table_uses_md_notice_without_model_or_image(registry, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr("src.channels.wecom_kf.reply_delivery._model_preview", model)
    image = AsyncMock()
    files = []
    delivery = await prepare("方案如下：\n|项目|价格|\n|---|---|\n|A|20|", files, summary_mode="llm", prepare_image=image)
    assert delivery["mode"] == "notice" and len(files) == 1
    model.assert_not_awaited()
    image.assert_not_awaited()


async def test_code_image_example_never_selects_real_image(registry):
    image = AsyncMock()
    delivery = await prepare("```md\n![例子](https://example.com/a.png)\n```\n" + "a" * 2049, prepare_image=image)
    assert delivery["mode"] == "prefix"
    image.assert_not_awaited()


async def test_real_image_preparation_contains_file_notice_and_no_model(registry, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr("src.channels.wecom_kf.reply_delivery._model_preview", model)
    image = AsyncMock(return_value="image_media")
    delivery = await prepare("![图](https://example.com/a.png)\n" + "a" * 2049, summary_mode="llm", prepare_image=image)
    assert delivery["mode"] == "image" and delivery["media_id"] == "image_media"
    assert image.call_args.args[0].endswith(FILE_NOTICE)
    model.assert_not_awaited()


async def test_render_failure_returns_default_prefix(registry):
    delivery = await prepare("![图](https://example.com/a.png)\n" + "a" * 2049, prepare_image=AsyncMock(return_value=None))
    assert delivery["mode"] == "prefix"


async def test_inline_asset_maps_to_authorized_url_and_renders_without_public_url(registry, monkeypatch, tmp_path):
    image_path = tmp_path / "tenant_a" / "conversation" / "image.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    registry.values["uploaded_file:file_image"] = {"path": str(image_path), "mime_type": "image/png"}
    monkeypatch.setattr("src.channels.base.build_public_url", lambda _: "")
    renderer = AsyncMock(return_value="image_media")
    files = []
    delivery = await prepare("![图](file_id:file_image)", files, prepare_image=renderer)
    assert delivery["mode"] == "image"
    assert "file_id:file_image" in renderer.call_args.args[0]
    from pathlib import Path
    markdown = Path(registry.values["uploaded_file:" + delivery["file_id"]]["path"]).read_text(encoding="utf-8")
    assert "file_id:" not in markdown and "图片暂无法在文件中显示" in markdown


async def test_inline_foreign_asset_is_removed_before_render_and_md(registry, tmp_path):
    foreign_path = tmp_path / "tenant_b" / "conversation" / "secret.png"
    foreign_path.parent.mkdir(parents=True)
    foreign_path.write_bytes(b"foreign image")
    registry.values["uploaded_file:file_other"] = {"path": str(foreign_path), "mime_type": "image/png"}
    renderer = AsyncMock(return_value="image_media")
    delivery = await prepare("![示意图](file_id:file_other)", prepare_image=renderer)
    assert "file_id:file_other" not in renderer.call_args.args[0]
    assert "图片暂无法在文件中显示" in renderer.call_args.args[0]
    assert delivery["mode"] == "image"


async def test_registration_failure_never_claims_attachment(registry):
    registry.fail_write = True
    files = []
    delivery = await prepare("a" * 2049, files)
    assert delivery["mode"] == "failed" and files == []
    assert "查看发送的文件" not in delivery["text"]


async def test_prepare_reentry_does_not_duplicate_full_attachment(registry):
    files = []
    first = await prepare("a" * 2049, files)
    second = await prepare("a" * 2049, files)
    assert first["file_id"] == second["file_id"] and len(files) == 1


async def test_native_md_priority_before_independent_images_and_files(registry, adapter):
    files = []
    full = "您好。" * 1000
    delivery = await prepare(full, files)
    files.append({"file_id": "other", "file_name": "a.pdf", "download_url": "https://example.com/a.pdf"})
    adapter._send_image_ref_as_image = AsyncMock(return_value=True)
    assert await adapter.send_message(response(full, files, delivery, images=[{"file_id": "independent"}]))
    calls = adapter.api_client.send_msg.await_args_list
    assert [item.kwargs["msgtype"] for item in calls] == ["text", "file", "link"]
    assert calls[0].kwargs["content"]["content"] == delivery["text"]
    assert adapter.api_client.upload_media.call_args.kwargs == {"display_name": "详细答复.md", "mime_type": "text/markdown"}
    adapter._send_image_ref_as_image.assert_awaited_once()


async def test_native_md_does_not_require_public_url_or_thumbnail(registry, adapter, monkeypatch):
    files = []
    delivery = await prepare("a" * 2049, files)
    monkeypatch.setattr("src.channels.wecom_kf.adapter.build_public_url", lambda _: (_ for _ in ()).throw(AssertionError("no public URL")))
    assert await adapter.send_message(response("a" * 2049, files, delivery))
    adapter._get_default_thumb_media_id.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("tenant_id", "tenant_b"), ("session_id", "other"), ("owner_id", "other"), ("purpose", "other")])
async def test_mismatched_registered_owner_is_never_uploaded(registry, adapter, field, value):
    files = []
    delivery = await prepare("a" * 2049, files)
    registry.hset("uploaded_file:" + delivery["file_id"], field, value)
    assert not await adapter.send_message(response("a" * 2049, files, delivery))
    adapter.api_client.upload_media.assert_not_awaited()
    adapter.api_client.send_msg.assert_not_awaited()


async def test_budget_shortage_prioritizes_complete_file_and_reports_partial(registry, adapter):
    files = []
    delivery = await prepare("a" * 2049, files)
    budget = WeComKfReplyBudget(total=1)
    assert not await adapter.send_message(response("a" * 2049, files, delivery, budget=budget))
    assert [item.kwargs["msgtype"] for item in adapter.api_client.send_msg.await_args_list] == ["file"]
    assert budget.remaining == 0


async def test_file_rejection_is_partial_delivery_without_link_replay(registry, adapter):
    files = []
    delivery = await prepare("a" * 2049, files)
    adapter.api_client.send_msg.side_effect = [{"errcode": 0}, {"errcode": -1, "delivery_unknown": True}]
    assert not await adapter.send_message(response("a" * 2049, files, delivery))
    assert [item.kwargs["msgtype"] for item in adapter.api_client.send_msg.await_args_list] == ["text", "file"]


async def test_native_upload_failure_explains_download_fallback(registry, adapter, monkeypatch):
    files = []
    delivery = await prepare("a" * 2049, files)
    adapter.api_client.upload_media.return_value = {"errcode": 40001}
    monkeypatch.setattr("src.channels.wecom_kf.adapter.build_public_url", lambda url: "https://example.com" + url)
    assert not await adapter.send_message(response("a" * 2049, files, delivery))
    calls = adapter.api_client.send_msg.await_args_list
    assert len(calls) == 2 and "暂无法直接发送" in calls[1].kwargs["content"]["content"]


def fake_gateway(monkeypatch, content="您好，您可以先核对订单状态。", usage=None):
    from src.llm.call_observer import observed_call
    usage = usage or {"prompt_tokens": 1200, "completion_tokens": 35, "cached_tokens": 200, "cache_creation_tokens": 50}

    async def physical(**kwargs):
        return {"content": content, "usage": usage}

    async def chat(**kwargs):
        return await observed_call(physical, provider="qwen", model="summary_test_model", kwargs=kwargs)

    gateway = SimpleNamespace(chat_no_thinking=AsyncMock(side_effect=chat))
    monkeypatch.setattr("src.llm.gateway.LLMGateway", lambda: gateway)
    return gateway


async def test_explicit_model_summary_charges_actual_model_even_when_falling_back(registry, monkeypatch):
    gateway = fake_gateway(monkeypatch, content="x" * 600)
    usage = record()
    delivery = await prepare("答复" * 1500, summary_mode="llm", record_service=usage, question="如何办理？")
    assert delivery["mode"] == "prefix"
    assert not usage.skip_save and usage.model == "summary_test_model" and usage.provider == "qwen"
    assert usage.prompt_tokens == 1200 and usage.cached_input_tokens == 200
    assert usage.cache_creation_input_tokens == 50 and usage.completion_tokens == 35
    gateway.chat_no_thinking.assert_awaited_once()
    assert gateway.chat_no_thinking.call_args.kwargs["tools"] is None
    assert "保留原语言、称谓、人称" in gateway.chat_no_thinking.call_args.kwargs["messages"][0]["content"]


async def test_valid_model_summary_is_direct_answer_and_includes_bounded_notice(registry, monkeypatch):
    fake_gateway(monkeypatch)
    usage = record()
    delivery = await prepare("答复" * 1500, summary_mode="llm", record_service=usage)
    assert delivery["mode"] == "llm"
    assert delivery["text"] == "您好，您可以先核对订单状态。" + FILE_NOTICE
    assert usage.total_token_count == 1235


@pytest.mark.parametrize("content", ["---", "<!-- 空摘要 -->", "<script>ignored()</script>"])
async def test_empty_summary_projection_falls_back_but_consumed_usage_is_kept(registry, monkeypatch, content):
    fake_gateway(monkeypatch, content=content)
    usage = record()
    delivery = await prepare("您好，先核对订单。" * 300, summary_mode="llm", record_service=usage)
    assert delivery["mode"] == "prefix" and delivery["text"].startswith("您好，先核对订单。")
    assert usage.total_token_count == 1235 and not usage.skip_save


async def test_missing_actual_model_price_prevents_physical_summary_call(registry, monkeypatch):
    from src.channels.wecom_kf.reply_delivery import _SummaryUsageObserver
    monkeypatch.setattr("src.db.models.TokenCostPriceDB.get_by_model_name", lambda model: None)
    physical = AsyncMock()
    usage = record()
    with pytest.raises(ValueError, match="KF_SUMMARY_MODEL_PRICE_REQUIRED"):
        await _SummaryUsageObserver(usage).call(physical, provider="qwen", model="unpriced",
            kwargs={}, owner="llm", purpose="llm")
    physical.assert_not_awaited()
    assert usage.skip_save and usage.total_token_count == 0
    fake_gateway(monkeypatch)
    delivery = await prepare("您好，先核对订单。" * 300, summary_mode="llm", record_service=usage)
    assert delivery["mode"] == "prefix"
    assert usage.skip_save


async def test_timeout_with_consumed_usage_keeps_billing(monkeypatch):
    from src.llm.call_observer import observed_call

    async def physical(**kwargs):
        return {"content": "答复", "usage": {"prompt_tokens": 100, "completion_tokens": 10}}

    async def chat(**kwargs):
        await observed_call(physical, provider="qwen", model="summary_test_model", kwargs=kwargs)
        await asyncio.sleep(10)

    monkeypatch.setattr("src.llm.gateway.LLMGateway", lambda: SimpleNamespace(chat_no_thinking=chat))
    usage = record()
    assert await _model_preview("答复", record_service=usage, question="问题", timeout=1) is None
    assert not usage.skip_save and usage.total_token_count == 110


async def test_summary_usage_reaches_existing_record_and_balance_transaction(registry, monkeypatch):
    from src.db.models import ChatRecordDB
    from src.services import session_record

    fake_gateway(monkeypatch)
    usage = record()
    await prepare("答复" * 1500, summary_mode="llm", record_service=usage)
    price = MagicMock(return_value=(1.25, {"credits": {"input": 1.0, "output": 0.25}}))
    monkeypatch.setattr(session_record, "calculate_llm_credit_cost_with_breakdown", price)
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = {"record_id": "record_summary"}
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr("src.db.models.get_db_connection", lambda: context)
    monkeypatch.setattr(ChatRecordDB, "create_in_tx", lambda *args, **kwargs: {"record_id": "record_summary"})
    debit = MagicMock()
    monkeypatch.setattr(ChatRecordDB, "debit_in_tx", debit)
    monkeypatch.setattr("src.db.models.invalidate_tenant_cache", lambda *args: None)
    saved = usage.save()
    assert saved["record_id"] == "record_summary"
    price.assert_called_once()
    assert price.call_args.kwargs["model"] == "summary_test_model"
    debit.assert_called_once_with(cursor, "tenant_a", 1.25)
    connection.commit.assert_called_once()


async def test_config_override_keeps_original_threshold(registry):
    assert await prepare("a" * 1024, max_bytes=1024) == {}
    assert (await prepare("a" * 1025, max_bytes=1024))["mode"] == "prefix"


def test_only_supported_summary_modes_are_accepted():
    assert WeComKfAdapter(corp_id="c", secret="s").summary_mode == "prefix"
    assert WeComKfAdapter(corp_id="c", secret="s", summary_mode="llm").summary_mode == "llm"
    with pytest.raises(ValueError):
        WeComKfAdapter(corp_id="c", secret="s", summary_mode="invalid")


async def test_api_send_timeout_is_not_replayed(monkeypatch):
    import httpx
    from src.channels.wecom_kf.api_client import WeComKfApiClient

    client = WeComKfApiClient("test", "secret")
    monkeypatch.setattr(client, "get_access_token", AsyncMock(return_value="test_token"))
    transport = SimpleNamespace(post=AsyncMock(side_effect=httpx.ReadTimeout("timed out")))
    monkeypatch.setattr(client, "_get_client", AsyncMock(return_value=transport))
    result = await client.send_msg("external", "kf", "text", {"content": "答复"})
    assert result["errcode"] == -1 and result["delivery_unknown"] is True
    transport.post.assert_awaited_once()


async def test_upload_native_md_passes_display_name_and_mime(monkeypatch, tmp_path):
    from src.channels.wecom_kf.api_client import WeComKfApiClient

    path = tmp_path / "file_physical.md"
    path.write_text("|项目|价格|\n|---|---|\n|A|20|", encoding="utf-8")
    client = WeComKfApiClient("test", "secret")
    monkeypatch.setattr(client, "get_access_token", AsyncMock(return_value="test_token"))
    captured = {}

    async def post(url, **kwargs):
        name, stream, mime = kwargs["files"]["media"]
        captured.update(name=name, mime=mime, contents=stream.read())
        return SimpleNamespace(json=lambda: {"errcode": 0, "media_id": "media"})

    monkeypatch.setattr(client, "_get_client", AsyncMock(return_value=SimpleNamespace(post=post)))
    result = await client.upload_media(str(path), "file", display_name="详细答复.md", mime_type="text/markdown")
    assert result["media_id"] == "media" and captured["name"] == "详细答复.md"
    assert captured["mime"] == "text/markdown" and captured["contents"].decode("utf-8").startswith("|项目|")
