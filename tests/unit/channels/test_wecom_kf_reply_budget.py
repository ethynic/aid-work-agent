# -*- coding: utf-8 -*-
"""微信客服 WeComKfReplyBudget / send_status_message 测试（Phase 3，回归 #12）

覆盖设计 §9.3 降级规则 1–5 的组合场景：
- verbose 后 final 正文成功（预算 5→4→3）
- 长图渲染失败 → 单条截断纯文本附「内容较长，请在 Web 端查看」
- 多图片 / 多文件按剩余额度按序发送
- 预算耗尽：超预算资产不发送并记录 suppressed_reply_budget
- 预算不足 2 次：verbose 抑制（无法保证 final 正文时不发 verbose）
- 无 budget 注入时行为不变（对外兼容）
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.base import StatusDeliveryResult
from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.channels.wecom_kf.budget import WeComKfReplyBudget
from src.models.message import UnifiedResponse


pytestmark = pytest.mark.channels

VERBOSE_TEXT = "正在生成报价单，请耐心等待。"


def _adapter(render_enabled=True):
    a = WeComKfAdapter(corp_id="c", secret="s" * 32)
    a._render_enabled = render_enabled
    a.api_client = MagicMock()
    a.api_client.send_msg = AsyncMock(return_value={"errcode": 0})
    a.api_client.upload_media = AsyncMock(return_value={"media_id": "media_x"})
    return a


def _resp(text="", budget=None, images=None, files=None):
    from src.models.message import DownloadableFileInfo

    content = {}
    if text:
        content["text"] = text
    if budget is not None:
        content["_kf_reply_budget"] = budget
    resp = UnifiedResponse(message_id="m", reply_to="ext_user", content=content)
    if images:
        resp.content["images"] = images
    resp.downloadable_files = [
        DownloadableFileInfo(**f) for f in (files or [])
    ]
    return resp


# ============================================================
# budget 单元
# ============================================================


class TestBudgetUnit:
    def test_initial_total_five(self):
        b = WeComKfReplyBudget(total=5)
        assert b.remaining == 5
        assert b.can_reserve(2) and b.consume(1)
        assert b.remaining == 4

    def test_consume_beyond_remaining_rejected(self):
        b = WeComKfReplyBudget(total=1)
        assert b.consume(1) is True
        assert b.consume(1) is False
        assert b.remaining == 0

    def test_suppressed_assets_recorded(self):
        b = WeComKfReplyBudget(total=1)
        b.record_suppressed("file", file_id="f1", file_name="a.pdf")
        assert b.suppressed_assets == [
            {"kind": "file", "file_id": "f1", "file_name": "a.pdf",
             "reason": "suppressed_reply_budget"}
        ]

    def test_invalid_total_rejected(self):
        with pytest.raises(ValueError):
            WeComKfReplyBudget(total=0)


# ============================================================
# send_status_message（规则 1/5：final 预留）
# ============================================================


class TestKfSendStatusMessage:
    @pytest.mark.asyncio
    async def test_verbose_success_consumes_one_keeps_final(self):
        """规则 1：verbose 成功扣 1（5→4），final 至少保留 1 次。"""
        adapter = _adapter()
        budget = WeComKfReplyBudget(total=5)
        result = await adapter.send_status_message(
            _resp(VERBOSE_TEXT, budget=budget), reserve_for_final=1
        )
        assert result.status == "sent"
        assert budget.remaining == 4
        adapter.api_client.send_msg.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_verbose_suppressed_when_cannot_reserve_final(self):
        """规则 5：剩余额度不足以保证 final 正文 → 抑制 verbose（不扣减）。"""
        adapter = _adapter()
        budget = WeComKfReplyBudget(total=5)
        budget.consume(4)  # 剩 1：无法同时满足 verbose+final 预留
        result = await adapter.send_status_message(
            _resp(VERBOSE_TEXT, budget=budget), reserve_for_final=1
        )
        assert result.status == "suppressed_rate_limit"
        assert budget.remaining == 1
        adapter.api_client.send_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verbose_failed_does_not_consume(self):
        """失败不扣预算（规则 1：成功才扣 1）。"""
        adapter = _adapter()
        adapter.api_client.send_msg = AsyncMock(return_value={"errcode": 45009})
        budget = WeComKfReplyBudget(total=5)
        result = await adapter.send_status_message(
            _resp(VERBOSE_TEXT, budget=budget), reserve_for_final=1
        )
        assert result.status == "failed"
        assert budget.remaining == 5

    @pytest.mark.asyncio
    async def test_missing_budget_suppressed_unsupported(self):
        """无预算（老路径）不发送：无法保证 final 预留。"""
        adapter = _adapter()
        result = await adapter.send_status_message(
            _resp(VERBOSE_TEXT), reserve_for_final=1
        )
        assert result.status == "suppressed_unsupported"
        adapter.api_client.send_msg.assert_not_awaited()


# ============================================================
# final 正文 + 资产组合（规则 2/3/4）
# ============================================================


class TestKfFinalWithBudget:
    @pytest.mark.asyncio
    async def test_verbose_then_short_final_body_succeeds(self):
        """verbose(5→4) 后 final 短正文单条发送（4→3）。"""
        adapter = _adapter()
        budget = WeComKfReplyBudget(total=5)
        await adapter.send_status_message(_resp(VERBOSE_TEXT, budget=budget))
        ok = await adapter.send_message(_resp("最终回复", budget=budget))
        assert ok is True
        assert budget.remaining == 3
        # 两次发送：verbose 1 条 + final 正文 1 条
        assert adapter.api_client.send_msg.await_count == 2

    @pytest.mark.asyncio
    async def test_long_image_failure_falls_back_to_truncated_text(self):
        """规则 3：长图失败 → 单条截断纯文本并附「内容较长，请在 Web 端查看」。"""
        adapter = _adapter()
        budget = WeComKfReplyBudget(total=5)
        long_text = "很长的回复 " * 500  # 超 2048 字节
        # _renderer 是 renderer property 的缓存槽：注入 fake 渲染器（长图失败返回 None）
        fake_renderer = MagicMock()
        fake_renderer.render_markdown = AsyncMock(return_value=None)
        fake_renderer.render_table = AsyncMock(return_value=None)
        adapter._renderer = fake_renderer
        ok = await adapter.send_message(_resp(long_text, budget=budget))
        assert ok is True
        # 单条纯文本（1 条额度），且带 Web 端提示
        calls = adapter.api_client.send_msg.await_args_list
        assert len(calls) == 1, "不得无预算地自动分段"
        content = calls[0].kwargs["content"]["content"]
        assert "内容较长，请在 Web 端查看" in content
        assert len(content.encode("utf-8")) <= 2048
        assert budget.remaining == 4

    @pytest.mark.asyncio
    async def test_multiple_images_and_files_consume_remaining(self):
        """规则 4：正文后图片/文件按序消耗剩余额度。"""
        adapter = _adapter()
        budget = WeComKfReplyBudget(total=5)
        # 预先用掉 2 次（模拟 verbose + 一次额外发送），剩 3：正文 1 + 资产 2
        budget.consume(2)
        files = [
            {
                "file_id": f"f{i}", "file_name": f"doc{i}.pdf", "file_size": 10,
                "download_url": f"https://x/f{i}", "mime_type": "application/pdf",
            }
            for i in range(3)
        ]
        with patch(
            "src.channels.wecom_kf.adapter.redis_client"
        ) as fake_redis, patch.object(adapter, "_get_default_thumb_media_id", new=AsyncMock(return_value="thumb")):
            fake_redis.make_key.side_effect = lambda *a: ":".join(a)
            fake_redis.hgetall.return_value = {}
            ok = await adapter.send_message(_resp("正文", budget=budget, files=files))
        assert ok is True
        # 正文 1 + 前 2 个文件各 1 = 5 次全部用尽；第 3 个文件被抑制
        assert budget.remaining == 0
        suppressed = budget.suppressed_assets
        assert [s["file_id"] for s in suppressed] == ["f2"]
        assert all(s["reason"] == "suppressed_reply_budget" for s in suppressed)
        # send_msg 次数：正文 1 + 文件 link 2
        assert adapter.api_client.send_msg.await_count == 3

    @pytest.mark.asyncio
    async def test_no_budget_injection_keeps_legacy_behavior(self):
        """无 budget 注入：走既有分段/长图全流程，行为不变。"""
        adapter = _adapter()
        ok = await adapter.send_message(_resp("普通回复"))
        assert ok is True
        assert adapter.api_client.send_msg.await_count == 1

    @pytest.mark.asyncio
    async def test_body_always_sent_even_if_budget_exhausted(self):
        """final 正文权威：预算被占尽时正文仍发送（verbose 侧预留保证正常不触发）。"""
        adapter = _adapter()
        budget = WeComKfReplyBudget(total=5)
        budget.consume(5)  # 极端：预算清零
        ok = await adapter.send_message(_resp("最终正文", budget=budget))
        assert ok is True
        assert adapter.api_client.send_msg.await_count == 1
