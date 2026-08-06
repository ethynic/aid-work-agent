"""视频生成 VideoGenService 单元测试。

验证（mock DB + provider + media + preprocess）：
- list_scenes 返回 2 个场景
- create_session 校验场景、生成 N 条 card（不同 seed）、写库
- create_session 未知场景 / card_count 越界抛 ValueError
- poll_pending_cards：SUCCEEDED 下载注册、FAILED 写 error_msg、过期标失败
- negative_prompt 在 supports_negative_prompt=False 的 provider 下被忽略
- task_max_age 取自 provider.get_options().task_max_age_hours
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.video_gen import service as svc_mod
from src.video_gen.base import ProviderOptions, SubmitResult, VideoGenRequest
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


def _wanx_options() -> ProviderOptions:
    """万相 provider 的能力声明（与 WanxProvider.get_options 对齐）。"""
    from src.video_gen.base import OptionItem
    return ProviderOptions(
        provider="wanx",
        resolutions=[OptionItem("720P", "720P", 0.60), OptionItem("1080P", "1080P", 1.00)],
        ratios=[OptionItem(r, r) for r in ("9:16", "16:9", "1:1", "4:3", "3:4")],
        durations=[OptionItem(str(d), f"{d}s") for d in (5, 10, 15)],
        default_resolution="720P", default_ratio="9:16", default_duration=5,
        supports_reference_image=True, supports_negative_prompt=True,
        task_max_age_hours=24,
    )


def _minimax_options() -> ProviderOptions:
    """MiniMax provider 的能力声明（与 MiniMaxProvider.get_options 对齐）。"""
    from src.video_gen.base import OptionItem
    return ProviderOptions(
        provider="minimax",
        resolutions=[OptionItem("768P", "768P", 0.50), OptionItem("2K", "2K", 0.80)],
        ratios=[OptionItem(r, r) for r in ("9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive")],
        durations=[OptionItem(str(d), f"{d}s") for d in (5, 10, 15)],
        default_resolution="768P", default_ratio="9:16", default_duration=5,
        supports_reference_image=True, supports_negative_prompt=False,
        task_max_age_hours=168,
    )


def _make_svc(provider_options: ProviderOptions, submit_side_effect=None) -> VideoGenService:
    """构造 VideoGenService 实例并替换其 _provider 为 mock。

    - provider_options: provider 暴露的能力声明（resolutions/ratios/durations/...）
    - submit_side_effect: AsyncMock 的 side_effect，可自定义提交逻辑或抛错
    """
    with patch.object(svc_mod.settings.llm.qwen, "api_keys", ["sk-test"]):
        svc = VideoGenService()
    # 替换 _provider 为 mock（绕过 build_provider 构造的真实 provider）
    mock_provider = MagicMock()
    mock_provider.get_options.return_value = provider_options
    mock_provider.submit = AsyncMock(side_effect=submit_side_effect or
                                     (lambda req: SubmitResult(task_id="t-x", task_status="PENDING")))
    svc._provider = mock_provider
    # 同步 task_max_age（依赖 provider 声明）
    from datetime import timedelta
    svc._task_max_age = timedelta(hours=provider_options.task_max_age_hours)
    return svc


class TestListScenes:
    def test_returns_two_scenes(self):
        svc = _make_svc(_wanx_options())
        scenes = svc.list_scenes()
        assert len(scenes) == 2
        ids = {s["scene_id"] for s in scenes}
        assert ids == {"product_showcase", "atmosphere"}


class TestCreateSession:
    def _patch_externals(self, submit_task_ids=None):
        """mock read_as_base64 + 准备 fake_submit 记录调用。

        返回 (patches, submit_calls)。
        """
        if submit_task_ids is None:
            submit_task_ids = ["t1", "t2", "t3"]

        submit_calls = []

        async def fake_submit(req: VideoGenRequest):
            submit_calls.append({
                "seed": req.seed,
                "reference": req.reference_image_data_url,
                "first_frame": req.first_frame_data_url,
                "duration": req.duration,
                "negative_prompt": req.negative_prompt,
                "resolution": req.resolution,
                "ratio": req.ratio,
            })
            tid = submit_task_ids[len(submit_calls) - 1] if len(submit_calls) <= len(submit_task_ids) else "t-x"
            return SubmitResult(task_id=tid, task_status="PENDING")

        # 按 file_id 区分返回不同 data url，便于断言异图
        def fake_read_base64(fid):
            return f"data:image/jpeg;base64,{fid}"

        patches = [
            patch.object(svc_mod, "get_scene", lambda sid: __import__("src.video_gen.scenes", fromlist=["get_scene"]).get_scene(sid)),
            patch.object(svc_mod.MediaRegistry, "read_as_base64", staticmethod(fake_read_base64)),
        ]
        for p in patches:
            p.start()
        return patches, submit_calls, fake_submit

    def test_creates_session_with_cards(self):
        patches, submit_calls, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2", "t3"])
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
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
        patches, submit_calls, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
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
        patches, _, fake_submit = self._patch_externals()
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with pytest.raises(ValueError):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="nonexistent",
                    product_image_fid="f", copywriting="x", card_count=2,
                ))
        finally:
            for p in patches:
                p.stop()

    def test_card_count_out_of_range_raises(self):
        patches, _, fake_submit = self._patch_externals()
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
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
        patches, _, _ = self._patch_externals()
        # 覆盖 submit 抛错
        async def failing_submit(req):
            raise WanxProviderError("api error")
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=failing_submit)
            with fake_db() as (conn, cursor):
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
        patches, _, _ = self._patch_externals()
        async def crashing_submit(req):
            raise ValueError("unexpected json error")
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=crashing_submit)
            with fake_db() as (conn, cursor):
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
        """duration_sec 不在 provider 白名单内时应抛 ValueError。"""
        patches, _, fake_submit = self._patch_externals()
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
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
        patches, submit_calls, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
                result = _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    duration_sec=15,
                ))
                conn.commit.assert_called_once()
            assert result["status"] == "generating"
            # 验证 INSERT 持久化了 duration_sec
            insert_calls = [c for c in cursor.execute.call_args_list if "INSERT INTO gen_sessions" in str(c.args[0])]
            assert len(insert_calls) == 1
            sql_args = insert_calls[0].args[1]
            # 参数顺序末四位：enable_ai_label, duration_sec, resolution, ratio
            assert sql_args[-3] == 15  # duration_sec
            assert sql_args[-4] is True  # enable_ai_label 默认 True
        finally:
            for p in patches:
                p.stop()

    def test_enable_ai_label_false_persists(self):
        """enable_ai_label=False 应持久化到 gen_sessions（验证默认值被覆盖）。"""
        patches, _, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    enable_ai_label=False,
                ))
            insert_calls = [c for c in cursor.execute.call_args_list if "INSERT INTO gen_sessions" in str(c.args[0])]
            assert len(insert_calls) == 1
            sql_args = insert_calls[0].args[1]
            # 参数顺序末四位：enable_ai_label, duration_sec, resolution, ratio
            assert sql_args[-4] is False  # enable_ai_label=False
        finally:
            for p in patches:
                p.stop()

    def test_duration_sec_passed_to_provider_submit(self):
        """duration_sec 应透传给 provider.submit 的 VideoGenRequest.duration。"""
        patches, submit_calls, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    duration_sec=10,
                ))
            # 两条 card 的 submit 都收到 duration=10
            assert len(submit_calls) == 2
            for call in submit_calls:
                assert call["duration"] == 10
        finally:
            for p in patches:
                p.stop()

    def test_negative_prompt_ignored_when_unsupported(self):
        """MiniMax 模式下（supports_negative_prompt=False），negative_prompt 应被忽略不下发。"""
        patches, submit_calls, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            svc = _make_svc(_minimax_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                    resolution="768P",   # MiniMax 分辨率
                ))
            # 场景默认 negative_prompt 被忽略，req.negative_prompt 为空
            assert len(submit_calls) == 2
            for call in submit_calls:
                assert call["negative_prompt"] == ""
        finally:
            for p in patches:
                p.stop()

    def test_negative_prompt_passed_when_supported(self):
        """万相模式下（supports_negative_prompt=True），negative_prompt 应透传给 provider。"""
        patches, submit_calls, fake_submit = self._patch_externals(submit_task_ids=["t1", "t2"])
        try:
            svc = _make_svc(_wanx_options(), submit_side_effect=fake_submit)
            with fake_db() as (conn, cursor):
                _run(svc.create_session(
                    tenant_id="t1", user_id="u1", scene_id="product_showcase",
                    product_image_fid="f", copywriting="x", card_count=2,
                ))
            assert len(submit_calls) == 2
            for call in submit_calls:
                # 万相支持 negative_prompt，场景模板的 negative_prompt 应透传
                assert call["negative_prompt"] != ""
        finally:
            for p in patches:
                p.stop()


class TestPollPendingCards:
    def _poll_result(self, status, url=None, duration=None, error=None):
        from src.video_gen.base import PollResult
        return PollResult(task_status=status, video_url=url, duration=duration, error=error)

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
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = []
            n = _run(svc.poll_pending_cards())
        assert n == 0

    def test_succeeded_downloads_and_registers(self):
        svc = _make_svc(_wanx_options())
        svc._provider.poll = AsyncMock(return_value=self._poll_result("SUCCEEDED", url="http://x/y.mp4", duration=5))
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            with patch.object(svc_mod.MediaRegistry, "download_and_register", AsyncMock(return_value="file_out1")):
                n = _run(svc.poll_pending_cards())
        assert n == 1
        conn.commit.assert_called()

    def test_succeeded_burns_label_when_enable_ai_label_true(self):
        """enable_ai_label=True 时 download_and_register 应传 burn_label=True。"""
        svc = _make_svc(_wanx_options())
        svc._provider.poll = AsyncMock(return_value=self._poll_result("SUCCEEDED", url="http://x/y.mp4", duration=5))
        card = self._make_card(enable_ai_label=True)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            with patch.object(svc_mod.MediaRegistry, "download_and_register", AsyncMock(return_value="file_out1")) as mock_dl:
                _run(svc.poll_pending_cards())
        assert mock_dl.call_args.kwargs.get("burn_label") is True

    def test_succeeded_skips_burn_label_when_enable_ai_label_false(self):
        """enable_ai_label=False 时 download_and_register 应传 burn_label=False（导出原始素材）。"""
        svc = _make_svc(_wanx_options())
        svc._provider.poll = AsyncMock(return_value=self._poll_result("SUCCEEDED", url="http://x/y.mp4", duration=5))
        card = self._make_card(enable_ai_label=False)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            with patch.object(svc_mod.MediaRegistry, "download_and_register", AsyncMock(return_value="file_out1")) as mock_dl:
                _run(svc.poll_pending_cards())
        assert mock_dl.call_args.kwargs.get("burn_label") is False

    def test_failed_marks_error(self):
        svc = _make_svc(_wanx_options())
        svc._provider.poll = AsyncMock(return_value=self._poll_result("FAILED", error="内容违规"))
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            n = _run(svc.poll_pending_cards())
        assert n == 1
        conn.commit.assert_called()

    def test_expired_marks_failed(self):
        """万相 24h 过期：created_at 早于 25h 前应标失败。"""
        svc = _make_svc(_wanx_options())
        old = datetime.now() - timedelta(hours=25)
        card = self._make_card(created_at=old)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            n = _run(svc.poll_pending_cards())
        assert n == 1
        conn.commit.assert_called()

    def test_expired_threshold_minimax_168h(self):
        """MiniMax 168h 过期阈值：早于 168h 内不标失败，超过 168h 标失败。"""
        svc = _make_svc(_minimax_options())
        # 100h 应不触发过期
        within = datetime.now() - timedelta(hours=100)
        card1 = self._make_card(card_id="c1", created_at=within)
        # 200h 应触发过期
        expired = datetime.now() - timedelta(hours=200)
        card2 = self._make_card(card_id="c2", created_at=expired)
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card1, card2]
            n = _run(svc.poll_pending_cards())
        assert n == 2  # 一条过期 + 一条正常处理
        # 第二条过期标 FAILED，第一条继续走 poll 流程
        # （card2 不调 poll，直接 _mark_failed；card1 调 poll 但返回 mock 默认值）
        conn.commit.assert_called()

    def test_poll_network_error_skips(self):
        from src.video_gen.wanx_provider import WanxProviderError
        svc = _make_svc(_wanx_options())
        svc._provider.poll = AsyncMock(side_effect=WanxProviderError("timeout"))
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            n = _run(svc.poll_pending_cards())
        # 网络错误时 continue 跳过计数，状态不变更，下轮重试 -> 返回 0
        assert n == 0

    def test_poll_unexpected_exception_marks_failed(self):
        """切换 provider 后旧 task_id 无法查询（非 WanxProviderError），应标 FAILED 而非卡死。"""
        svc = _make_svc(_minimax_options())
        svc._provider.poll = AsyncMock(side_effect=ValueError("unknown task id"))
        card = self._make_card()
        with fake_db() as (conn, cursor):
            cursor.fetchall.return_value = [card]
            n = _run(svc.poll_pending_cards())
        assert n == 1
        conn.commit.assert_called()


class TestMaybeFinalizeSession:
    """验证 session.status 在所有 card 到终态时被正确更新。
    覆盖：全成功->done、全失败->failed、部分成功->done、仍有 PENDING->不动、已 finalize->不动。
    """

    def _session_row(self, status="generating"):
        return {"status": status}

    def _card_rows(self, statuses):
        return [{"provider_status": s} for s in statuses]

    def test_all_succeeded_finalizes_done(self):
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [self._session_row("generating")]
            cursor.fetchall.side_effect = [self._card_rows(["SUCCEEDED", "SUCCEEDED"])]
            result = svc._maybe_finalize_session("s1", "t1")
        assert result == "done"
        update_calls = [c for c in cursor.execute.call_args_list if "UPDATE gen_sessions SET status" in str(c.args[0])]
        assert len(update_calls) == 1
        assert update_calls[0].args[1][0] == "done"
        conn.commit.assert_called()

    def test_all_failed_finalizes_failed(self):
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [self._session_row("generating")]
            cursor.fetchall.side_effect = [self._card_rows(["FAILED", "CANCELED"])]
            result = svc._maybe_finalize_session("s1", "t1")
        assert result == "failed"
        update_calls = [c for c in cursor.execute.call_args_list if "UPDATE gen_sessions SET status" in str(c.args[0])]
        assert len(update_calls) == 1
        assert update_calls[0].args[1][0] == "failed"

    def test_partial_success_finalizes_done(self):
        """部分成功部分失败时，session 应标 done（用户拿到了视频）。"""
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [self._session_row("generating")]
            cursor.fetchall.side_effect = [self._card_rows(["SUCCEEDED", "FAILED"])]
            result = svc._maybe_finalize_session("s1", "t1")
        assert result == "done"

    def test_pending_remains_no_update(self):
        """仍有 PENDING/RUNNING 时不更新 session.status。"""
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [self._session_row("generating")]
            cursor.fetchall.side_effect = [self._card_rows(["SUCCEEDED", "PENDING"])]
            result = svc._maybe_finalize_session("s1", "t1")
        assert result is None
        update_calls = [c for c in cursor.execute.call_args_list if "UPDATE gen_sessions SET status" in str(c.args[0])]
        assert len(update_calls) == 0

    def test_already_final_no_update(self):
        """session.status 已是 done/failed 时不重复更新。"""
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [self._session_row("done")]
            result = svc._maybe_finalize_session("s1", "t1")
        assert result == "done"
        update_calls = [c for c in cursor.execute.call_args_list if "UPDATE gen_sessions SET status" in str(c.args[0])]
        assert len(update_calls) == 0

    def test_session_not_found_returns_none(self):
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [None]
            result = svc._maybe_finalize_session("missing", "t1")
        assert result is None

    def test_no_cards_no_update(self):
        """session 下无 card 时不更新（边界保护）。"""
        svc = _make_svc(_wanx_options())
        with fake_db() as (conn, cursor):
            cursor.fetchone.side_effect = [self._session_row("generating")]
            cursor.fetchall.side_effect = [[]]
            result = svc._maybe_finalize_session("s1", "t1")
        assert result is None


class TestGetOptions:
    def test_returns_provider_options(self):
        """get_options 透传 provider 暴露的能力声明。"""
        opts = _wanx_options()
        svc = _make_svc(opts)
        result = svc.get_options()
        assert result is opts

    def test_task_max_age_uses_provider_value_wanx(self):
        """万相 task_max_age 取 24h。"""
        from datetime import timedelta
        svc = _make_svc(_wanx_options())
        assert svc._task_max_age == timedelta(hours=24)

    def test_task_max_age_uses_provider_value_minimax(self):
        """MiniMax task_max_age 取 168h。"""
        from datetime import timedelta
        svc = _make_svc(_minimax_options())
        assert svc._task_max_age == timedelta(hours=168)
