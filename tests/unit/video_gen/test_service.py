"""视频生成 VideoGenService 单元测试。

验证（mock DB + wanx + media + preprocess）：
- list_scenes 返回 2 个场景
- create_session 校验场景、生成 N 条 card（不同 seed）、写库
- create_session 未知场景 / card_count 越界抛 ValueError
- set_card_kept rowcount=0 抛 ValueError
- poll_pending_cards：SUCCEEDED 下载注册、FAILED 写 error_msg、过期标失败
- regenerate_card 复用 session 数据提交新 card
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.video_gen import service as svc_mod
from src.video_gen.service import VideoGenService

pytestmark = [pytest.mark.unit]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@contextmanager
def fake_db(rows_by_query=None, fetchone=None, rowcount=1):
    """mock get_db_connection，记录每次 execute 的 sql/params。

    fetchone 控制 cursor.fetchone；fetchall 控制不了按 query 区分，故用 rows 参数。
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = []
    cursor.rowcount = rowcount
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_conn():
        yield conn

    with patch.object(svc_mod, "get_db_connection", fake_conn):
        yield conn, cursor


class TestListScenes:
    def test_returns_two_scenes(self):
        # VideoGenService.__init__ 会构造 WanxProvider，需 mock api_key
        with patch.object(svc_mod.settings.llm.qwen, "api_keys", ["sk-test"]):
            svc = VideoGenService()
        scenes = svc.list_scenes()
        assert len(scenes) == 2
        ids = {s["scene_id"] for s in scenes}
        assert ids == {"product_showcase", "atmosphere"}


class TestCreateSession:
    def _patch_externals(self, submit_task_ids=None):
        """mock read_as_base64、wanx.submit（不再做素材预处理，直接用用户原图）。

        read_as_base64 按 file_id 区分返回不同 data url，便于验证产品图/模特图异图场景。
        submit 调用记录在 submit_calls，含 reference/first_frame url。
        """
        if submit_task_ids is None:
            submit_task_ids = ["t1", "t2", "t3"]

        submit_calls = []

        class FakeSubmitResult:
            def __init__(self, tid):
                self.task_id = tid
                self.task_status = "PENDING"

        async def fake_submit(self, prompt, reference_image_data_url, first_frame_data_url,
                              seed, negative_prompt="", duration=5, resolution="720P"):
            submit_calls.append({
                "seed": seed,
                "reference": reference_image_data_url,
                "first_frame": first_frame_data_url,
                "duration": duration,
            })
            return FakeSubmitResult(submit_task_ids[len(submit_calls) - 1])

        # 按 file_id 区分返回不同 data url，便于断言异图
        def fake_read_base64(fid):
            return f"data:image/jpeg;base64,{fid}"

        patches = [
            patch.object(svc_mod, "get_scene", lambda sid: __import__("src.video_gen.scenes", fromlist=["get_scene"]).get_scene(sid)),
            patch.object(svc_mod.MediaRegistry, "read_as_base64", staticmethod(fake_read_base64)),
            patch.object(svc_mod.WanxProvider, "submit", fake_submit),
        ]
        for p in patches:
            p.start()
        return patches, submit_calls

    def test_creates_session_with_cards(self):
        patches, submit_calls = self._patch_externals(submit_task_ids=["t1", "t2", "t3"])
        try:
            with fake_db() as (conn, cursor):
                svc = VideoGenService()
                result = _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="file_p1", copywriting="假睫毛展示",
                    card_count=3,
                ))
                conn.commit.assert_called_once()

            assert result["status"] == "generating"
            assert result["card_count"] == 3
            assert len(result["cards"]) == 3
            # 3 条 card 用了不同 seed
            seeds = [c["seed"] for c in submit_calls]
            assert len(set(seeds)) == 3
            # 无模特图：first_frame 退化为产品图（reference == first_frame）
            for c in submit_calls:
                assert c["reference"] == c["first_frame"]
            # 每条 card 有 task_id 和 PENDING 状态
            for c in result["cards"]:
                assert c["provider_status"] == "PENDING"
                assert c["provider_task_id"] in ("t1", "t2", "t3")
        finally:
            for p in patches:
                p.stop()

    def test_model_image_uses_distinct_first_frame(self):
        """传了模特图时：reference=产品图，first_frame=模特图（异图）。"""
        patches, submit_calls = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            with fake_db() as (conn, cursor):
                svc = VideoGenService()
                result = _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="file_prod", copywriting="假睫毛",
                    card_count=2, model_image_fid="file_model",
                ))
                conn.commit.assert_called_once()
            # reference = 产品图，first_frame = 模特图（异图）
            for c in submit_calls:
                assert c["reference"] == "data:image/jpeg;base64,file_prod"
                assert c["first_frame"] == "data:image/jpeg;base64,file_model"
                assert c["reference"] != c["first_frame"]
            assert result["model_image_fid"] == "file_model"
        finally:
            for p in patches:
                p.stop()

    def test_unknown_scene_raises(self):
        patches, _ = self._patch_externals()
        try:
            svc = VideoGenService()
            with pytest.raises(ValueError):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="nonexistent",
                    product_image_fid="f", copywriting="x", card_count=2,
                ))
        finally:
            for p in patches:
                p.stop()

    def test_card_count_out_of_range_raises(self):
        patches, _ = self._patch_externals()
        try:
            svc = VideoGenService()
            with pytest.raises(ValueError):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=5,
                ))
        finally:
            for p in patches:
                p.stop()

    def test_submit_failure_marks_card_failed(self):
        from src.video_gen.wanx_provider import WanxProviderError
        patches, _ = self._patch_externals()
        # 覆盖 submit 抛错
        async def failing_submit(self, *a, **k):
            raise WanxProviderError("api error")
        try:
            with patch.object(svc_mod.WanxProvider, "submit", failing_submit):
                with fake_db() as (conn, cursor):
                    svc = VideoGenService()
                    result = _run(svc.create_session(
                        tenant_id="t1", user_id="u1", scene_id="product_showcase",
                        product_image_fid="f", copywriting="x", card_count=2,
                    ))
            # 提交失败但会话仍创建，card 标 FAILED
            for c in result["cards"]:
                assert c["provider_status"] == "FAILED"
                assert c["provider_task_id"] is None
                assert c["error_msg"]
        finally:
            for p in patches:
                p.stop()

    def test_submit_unexpected_exception_does_not_lose_session(self):
        """非 WanxProviderError 异常（如 JSONDecodeError）不应导致整批 session 回滚丢失。"""
        patches, _ = self._patch_externals()
        async def crashing_submit(self, *a, **k):
            raise ValueError("unexpected json error")
        try:
            with patch.object(svc_mod.WanxProvider, "submit", crashing_submit):
                with fake_db() as (conn, cursor):
                    svc = VideoGenService()
                    result = _run(svc.create_session(
                        tenant_id="t1", user_id="u1", scene_id="product_showcase",
                        product_image_fid="f", copywriting="x", card_count=2,
                    ))
                    # session 仍应成功创建并 commit
                    conn.commit.assert_called_once()
            # 非预期异常被兜底，card 标 FAILED 而非丢失
            assert len(result["cards"]) == 2
            for c in result["cards"]:
                assert c["provider_status"] == "FAILED"
                assert c["error_msg"]
        finally:
            for p in patches:
                p.stop()

    def test_duration_sec_out_of_range_raises(self):
        """duration_sec 不在 (5,10,15) 时应抛 ValueError（万相 r2v 上限 15s）。"""
        patches, _ = self._patch_externals()
        try:
            svc = VideoGenService()
            with pytest.raises(ValueError, match="duration_sec"):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    duration_sec=20,
                ))
        finally:
            for p in patches:
                p.stop()

    def test_duration_sec_15_passes(self):
        """duration_sec=15 应正常通过校验。"""
        patches, submit_calls = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            with fake_db() as (conn, cursor):
                svc = VideoGenService()
                result = _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    duration_sec=15,
                ))
                conn.commit.assert_called_once()
            assert result["status"] == "generating"
            # 验证 INSERT 持久化了 duration_sec（第 2 个 execute 是 gen_sessions INSERT）
            insert_calls = [c for c in cursor.execute.call_args_list if "INSERT INTO gen_sessions" in str(c.args[0])]
            assert len(insert_calls) == 1
            sql_args = insert_calls[0].args[1]
            # 参数顺序：session_id, tenant_id, user_id, scene_id, product_image_fid,
            #          model_image_fid, copywriting, prompt, card_count,
            #          enable_ai_label, duration_sec（末两位是本次新增字段）
            assert sql_args[-1] == 15  # duration_sec
            assert sql_args[-2] is True  # enable_ai_label 默认 True
        finally:
            for p in patches:
                p.stop()

    def test_enable_ai_label_false_persists(self):
        """enable_ai_label=False 应持久化到 gen_sessions（验证默认值被覆盖）。"""
        patches, _ = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            with fake_db() as (conn, cursor):
                svc = VideoGenService()
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    enable_ai_label=False,
                ))
            insert_calls = [c for c in cursor.execute.call_args_list if "INSERT INTO gen_sessions" in str(c.args[0])]
            assert len(insert_calls) == 1
            sql_args = insert_calls[0].args[1]
            # 参数顺序末位是 duration_sec，倒数第二位是 enable_ai_label
            assert sql_args[-2] is False  # enable_ai_label=False
        finally:
            for p in patches:
                p.stop()

    def test_duration_sec_passed_to_wanx_submit(self):
        """duration_sec 应透传给 wanx.submit 的 duration 参数（替代 scene.default_duration）。"""
        patches, submit_calls = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            with fake_db() as (conn, cursor):
                svc = VideoGenService()
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    duration_sec=10,
                ))
            # 两条 card 的 wanx.submit 都收到 duration=10
            assert len(submit_calls) == 2
            for call in submit_calls:
                assert call["duration"] == 10
        finally:
            for p in patches:
                p.stop()


class TestSetCardKept:
    def test_not_found_raises(self):
        with fake_db(rowcount=0) as (conn, cursor):
            svc = VideoGenService()
            with pytest.raises(ValueError):
                svc.set_card_kept("t1", "card_x", True)
            conn.rollback.assert_called_once()

    def test_update_success(self):
        with fake_db(rowcount=1) as (conn, cursor):
            svc = VideoGenService()
            result = svc.set_card_kept("t1", "card_1", True)
            conn.commit.assert_called_once()
        assert result == {"card_id": "card_1", "kept": True}


class TestPollPendingCards:
    def _poll_result(self, status, url=None, duration=None, error=None):
        r = MagicMock()
        r.task_status = status
        r.video_url = url
        r.duration = duration
        r.error = error
        return r

    def _make_card(self, **overrides):
        """构造 poll_pending_cards 的 fetchall 行（含 JOIN 出的 enable_ai_label）。"""
        card = {
            "card_id": "c1", "tenant_id": "t1", "session_id": "s1",
            "provider_task_id": "tk1", "created_at": datetime.now(),
            "enable_ai_label": True,
        }
        card.update(overrides)
        return card

    def test_no_pending_returns_zero(self):
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = []
            svc = VideoGenService()
            n = _run(svc.poll_pending_cards())
        assert n == 0

    def test_succeeded_downloads_and_registers(self):
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            svc = VideoGenService()
            with patch.object(svc_mod.WanxProvider, "poll", AsyncMock(return_value=self._poll_result("SUCCEEDED", url="http://x/y.mp4", duration=5))), \
                 patch.object(svc_mod.MediaRegistry, "download_and_register", AsyncMock(return_value="file_out1")):
                n = _run(svc.poll_pending_cards())
        assert n == 1
        # 应 commit 两次（download_and_register 内部 + card 更新）
        conn.commit.assert_called()

    def test_succeeded_burns_label_when_enable_ai_label_true(self):
        """enable_ai_label=True 时 download_and_register 应传 burn_label=True。"""
        card = self._make_card(enable_ai_label=True)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            svc = VideoGenService()
            with patch.object(svc_mod.WanxProvider, "poll", AsyncMock(return_value=self._poll_result("SUCCEEDED", url="http://x/y.mp4", duration=5))), \
                 patch.object(svc_mod.MediaRegistry, "download_and_register", AsyncMock(return_value="file_out1")) as mock_dl:
                _run(svc.poll_pending_cards())
        assert mock_dl.call_args.kwargs.get("burn_label") is True

    def test_succeeded_skips_burn_label_when_enable_ai_label_false(self):
        """enable_ai_label=False 时 download_and_register 应传 burn_label=False（导出原始素材）。"""
        card = self._make_card(enable_ai_label=False)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            svc = VideoGenService()
            with patch.object(svc_mod.WanxProvider, "poll", AsyncMock(return_value=self._poll_result("SUCCEEDED", url="http://x/y.mp4", duration=5))), \
                 patch.object(svc_mod.MediaRegistry, "download_and_register", AsyncMock(return_value="file_out1")) as mock_dl:
                _run(svc.poll_pending_cards())
        assert mock_dl.call_args.kwargs.get("burn_label") is False

    def test_failed_marks_error(self):
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            svc = VideoGenService()
            with patch.object(svc_mod.WanxProvider, "poll", AsyncMock(return_value=self._poll_result("FAILED", error="内容违规"))):
                n = _run(svc.poll_pending_cards())
        assert n == 1
        conn.commit.assert_called()

    def test_expired_marks_failed(self):
        old = datetime.now() - timedelta(hours=25)
        card = self._make_card(created_at=old)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            svc = VideoGenService()
            n = _run(svc.poll_pending_cards())
        assert n == 1
        conn.commit.assert_called()

    def test_poll_network_error_skips(self):
        from src.video_gen.wanx_provider import WanxProviderError
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            svc = VideoGenService()
            with patch.object(svc_mod.WanxProvider, "poll", AsyncMock(side_effect=WanxProviderError("timeout"))):
                n = _run(svc.poll_pending_cards())
        # 网络错误时 continue 跳过计数，状态不变更，下轮重试 → 返回 0
        assert n == 0
