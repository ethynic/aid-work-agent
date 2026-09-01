# -*- coding: utf-8 -*-
"""Agent 用户可见中间消息（verbose）Phase 0 契约测试 + 审计 P0 回归草案

Phase 0 契约测试：预期在 Phase 1 `src/core/verbose_feedback.py` 落地前失败（红），
属 TDD 门禁，不得为使其通过而添加生产 stub。

失败模式约定：
- 所有契约用例的失败原因必须是 ImportError / ModuleNotFoundError（实现尚不存在），
  不允许出现测试自身的语法或逻辑错误。
- 尚不存在的符号一律在用例内部延迟导入（见下方 _make_verbose_event_fn /
  _verbose_feedback_module），保证每条契约用例独立变红，而不是整个文件收集错误。
- 依赖 verbose 设施的审计 P0 回归草案：P0-1 已在 Phase 1 落地后激活运行；
  P0-2 已于 Phase 3 激活（RPA delivery_id 注入 pre_send 落地），断言逻辑完整可执行。

本文件同时承载两个审计 P0 回归草案（选择放同一文件的原因：Phase 0 白名单
只允许新增契约测试文件，契约与回归草案共用 fixture 与门禁命令，便于统一运行）：
- P0-1 追加消息不得导致 final 消失（设计 §3.3 陷阱 A / §7，计划 Phase 3 回归 #1）
- P0-2 RPA verbose/final 不得共用幂等键（设计 §3.3 陷阱 B / §9.4，计划 Phase 3 回归 #3；
  已于 Phase 3 激活：make_send_verbose / delivery_id 注入 RPA pre_send 已落地）

冻结的契约签名（Phase 1 实现必须满足；若实现签名与本文不同，
必须先修改本文件并按流程评审契约变更，不得静默偏离）：

1. 事件工厂（挂在现有模块 src/core/agent_events 上，计划 Phase 1）：
       make_verbose_event(event_id: str, data: str, source: str) -> dict
   返回 {"type": "verbose", "eventId": ..., "data": ..., "source": ..., "timestamp": 毫秒 int}
   - 恰好五个字段，不得夹带 phase/etaSeconds 等扩展字段（设计 §4 最小事件结构）；
   - source 仅允许 "policy" | "system"，其他值抛 ValueError
     （2026-09-01 产品决策后运行期只产生 "policy"；白名单向后兼容保留）；
   - data 违反「1~60 字 / 单句 / 无换行」时抛 ValueError（事件工厂是最后一道防线）；
   - eventId 原样透传调用方传入值；「本轮唯一」由调用方（Agent 编排层）
     生成唯一 id 保证，工厂不做去重、不补全（Phase 1 调用点不得传常量 id）。

2. 反馈状态机 src/core/verbose_feedback.py（设计 §7）：
       VerboseFeedbackState()               # owner 生命周期创建一次，所有 _processor 重跑 attempt 复用
       .event / .emitted_at / .response_started / .closed
       .try_emit(event: dict) -> bool       # PENDING→EMITTED 成功一次；其后一律返回 False
       .mark_response_started() -> None     # response 开始后拒绝再发
       .close() -> None                     # 幂等；CLOSED 后拒绝再发

3. 配置 src/core/verbose_feedback.py（设计 §11）：
       VerboseFeedbackConfig()              # dataclass(frozen=True)，owner 开始时冻结
       .enabled / .force_disabled / .max_per_turn / .max_text_chars
       .delivery_timeout_seconds / .fallback_message
       .effective_enabled                   # == enabled and not force_disabled；force_disabled 最高优先级
       （2026-09-01 产品决策：system watchdog 删除，initial_delay_seconds 字段下线）

4. 文本校验 src/core/verbose_feedback.py（设计 §6.1）：
       validate_feedback_text(text: str) -> str
   合法（1~60 字、单句、无换行/路径/命令/JSON/数字 ETA/敏感键）→ 原样返回；
   任何违规 → 返回空字符串（整体拒绝，不做局部清洗，调用方改用 fallback 文案）。
"""

import asyncio
import json
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.session_queue import SessionMessageQueue

pytestmark = pytest.mark.agent

# 合法策略文案样例（14 字，单句、无换行，符合 1~60 字约束）
VALID_TEXT = "正在生成报价单，请耐心等待。"


# ============================================================
# 延迟导入辅助：实现尚不存在时在用例内部抛 ImportError（红）
# ============================================================


def _make_verbose_event_fn():
    """延迟导入 make_verbose_event；Phase 1 落地前抛 ImportError（红）。"""
    from src.core.agent_events import make_verbose_event
    return make_verbose_event


def _verbose_feedback_module():
    """延迟导入 src.core.verbose_feedback；Phase 1 落地前抛 ModuleNotFoundError（红）。"""
    import src.core.verbose_feedback as verbose_feedback
    return verbose_feedback


# ============================================================
# 契约 1：verbose 事件 schema（设计 §4 事件协议）
# ============================================================


class TestVerboseEventSchema:
    """make_verbose_event 生成的用户可见中间消息事件契约。

    为什么重要：verbose 是唯一允许直达最终用户的中间事件，协议字段一旦
    混入 progress/tool_* 技术事件语义，渠道与 Web 的展示隔离就会被破坏。
    """

    def _make(self, **kwargs):
        make_verbose_event = _make_verbose_event_fn()
        params = {
            "event_id": "verbose_01J_contract",
            "data": VALID_TEXT,
            "source": "policy",
        }
        params.update(kwargs)
        return make_verbose_event(**params)

    def test_core_fields_and_types(self):
        """生成事件恰好包含五个字段：type/eventId/data/source/timestamp。"""
        event = self._make()
        assert event["type"] == "verbose", "type 固定 verbose，不得与 progress 混用"
        assert event["eventId"] == "verbose_01J_contract"
        assert event["data"] == VALID_TEXT
        assert event["source"] == "policy"
        assert isinstance(event["timestamp"], int)
        assert set(event.keys()) == {"type", "eventId", "data", "source", "timestamp"}, (
            "MVP 最小事件结构：不得夹带 phase/etaSeconds 等扩展字段（设计 §4）"
        )

    def test_event_id_echoed_verbatim(self):
        """eventId 原样透传调用方传入值，不得被工厂改写或固定。

        为什么重要：前端/渠道按 eventId 去重（设计 §4「本轮唯一」/ §10 合并规则 3）。
        工厂是纯函数，不负责生成唯一 id；「本轮唯一」由调用方（Agent 编排层 /
        编排层）生成唯一 id 保证，调用点不得传常量 id。
        """
        make_verbose_event = _make_verbose_event_fn()
        first = make_verbose_event(
            event_id="verbose_turn_a", data=VALID_TEXT, source="policy"
        )
        second = make_verbose_event(
            event_id="verbose_turn_b", data=VALID_TEXT, source="policy"
        )
        assert first["eventId"] == "verbose_turn_a"
        assert second["eventId"] == "verbose_turn_b"

    def test_timestamp_is_milliseconds(self):
        """timestamp 为毫秒级整数（与 make_event 一致，> 10^12）。"""
        event = self._make()
        assert event["timestamp"] > 10**12

    def test_source_whitelist(self):
        """source 仅允许 policy|system；llm 等其他来源必须在工厂处被拒绝。

        为什么重要：产品决策（2026-08-31）已删除 LLM 候选路径，verbose 只能
        来自 Tool/Skill 确定性策略（policy）；system 白名单仅向后兼容保留
        （2026-09-01 产品决策：watchdog 已删除）。
        """
        assert self._make(source="policy")["source"] == "policy"
        assert self._make(source="system")["source"] == "system"
        with pytest.raises(ValueError):
            self._make(source="llm")

    def test_data_constraint_rejects_newline_overlong_and_empty(self):
        """data 违反「单句 / 无换行 / 1~60 字」时工厂直接拒绝（最后一道防线）。"""
        with pytest.raises(ValueError):
            self._make(data="第一行\n第二行")
        with pytest.raises(ValueError):
            self._make(data="好" * 61)
        with pytest.raises(ValueError):
            self._make(data="")
        # 单句约束：多句文本必须被工厂拒绝
        with pytest.raises(ValueError):
            self._make(data="第一句。第二句。")
        # 边界内合法：1 字下边界与 60 字上边界
        assert self._make(data="好")["data"] == "好"
        event = self._make(data="好" * 59 + "。")
        assert len(event["data"]) == 60


# ============================================================
# 契约 2：反馈状态机（设计 §7）
# ============================================================


class TestVerboseFeedbackStateMachine:
    """反馈状态机契约：PENDING → EMITTED → CLOSED。

    为什么重要：「每轮最多一条 verbose」完全依赖该状态机；一旦 event 非空，
    本轮（owner 生命周期）拒绝任何后续 verbose。
    """

    def _state(self):
        module = _verbose_feedback_module()
        return module.VerboseFeedbackState()

    def _event(self, event_id):
        make_verbose_event = _make_verbose_event_fn()
        return make_verbose_event(event_id=event_id, data=VALID_TEXT, source="policy")

    def test_initial_state_is_pending(self):
        """初始为 PENDING：event/emitted_at 为空，未开始响应，未关闭。"""
        state = self._state()
        assert state.event is None
        assert state.emitted_at is None
        assert state.response_started is False
        assert state.closed is False

    def test_pending_to_emitted_then_closed_happy_path(self):
        """合法流转：try_emit 成功进入 EMITTED，close 后进入 CLOSED。"""
        state = self._state()
        event = self._event("verbose_sm_happy")
        assert state.try_emit(event) is True
        assert state.event is event
        assert state.emitted_at is not None
        assert state.closed is False
        state.close()
        assert state.closed is True

    def test_second_emit_rejected_first_event_kept(self):
        """一旦 event 非空，本轮拒绝任何后续 verbose，且保留第一条事件。"""
        state = self._state()
        first = self._event("verbose_sm_first")
        second = self._event("verbose_sm_second")
        assert state.try_emit(first) is True
        assert state.try_emit(second) is False, (
            "每轮最多一条：EMITTED 后再次 emit 必须被拒绝"
        )
        assert state.event is first, "被拒绝的第二条不得覆盖第一条事件"

    def test_emit_after_response_started_rejected(self):
        """response 开始后不再产生提示（成功标准 5 / 设计 §14.5）。"""
        state = self._state()
        assert state.try_emit(self._event("verbose_sm_resp1")) is True
        state.mark_response_started()
        assert state.response_started is True
        assert state.try_emit(self._event("verbose_sm_resp2")) is False

    def test_pending_with_response_started_rejects_emit(self):
        """PENDING 且 response 已开始的竞态窗口：try_emit 必须独立拒绝。

        为什么重要（设计 §14.5 / §6.3）：response 可能先于 policy 事件到达，
        此时 event 仍为 None；若 try_emit 只判「event 非空」而不独立检查
        response_started，response 之后的迟到事件仍会被补发。
        上面的 test_emit_after_response_started_rejected 中第二次 emit 本就会被
        event 非空拦下，无法暴露该缺陷，故必须另设本用例。
        """
        state = self._state()
        state.mark_response_started()
        assert state.try_emit(self._event("verbose_sm_race")) is False
        assert state.event is None, "被拒绝的迟到提示不得落入 state.event"

    def test_emit_after_closed_rejected_and_close_idempotent(self):
        """CLOSED 后拒绝 emit；close 幂等（异常/取消路径可能多次收尾）。"""
        state = self._state()
        state.close()
        state.close()
        assert state.closed is True
        assert state.try_emit(self._event("verbose_sm_closed")) is False
        assert state.event is None

    def test_state_shared_across_processor_attempts(self):
        """owner 生命周期共享：cancel/merge 导致 _processor 重跑时状态不得重置。

        为什么重要（设计 §7 复审阻断闭环）：session queue 的 cancel/merge 会让
        同一 owner 的 processor 重跑；若每次 attempt 新建状态，A 发过 verbose 后
        追加 B 重跑会二次提示，突破「每轮最多一条」上限。契约：同一个 state 对象
        上第二次 try_emit 必须被拒绝。
        """
        state = self._state()
        first = self._event("verbose_sm_attempt1")
        assert state.try_emit(first) is True          # attempt 1
        # attempt 2（模拟重跑 attempt 拿到同一个 owner 级 state）
        assert state.try_emit(self._event("verbose_sm_attempt2")) is False
        assert state.event is first, "重跑 attempt 不得重置已发出的事件"


# ============================================================
# 契约 3：配置（设计 §11）
# ============================================================


class TestVerboseFeedbackConfig:
    """配置契约：默认开启、force_disabled 最高优先级、1 条 / 60 字（无系统兜底）。"""

    def _config_cls(self):
        module = _verbose_feedback_module()
        return module.VerboseFeedbackConfig

    def test_defaults(self):
        """默认 enabled=true（2026-09-01 产品决策：全局默认启用）、max 1、文本上限 60 字。

        delivery_timeout_seconds=5 与 fallback_message 文案按设计 §11 / §6.3 逐字冻结：
        前者是 Phase 3 dispatcher 的发送超时上界，后者是 policy 文案违规时降级
        发给用户的模板文案，均属用户可见契约，实现期不得擅改。
        （2026-09-01 产品决策：system watchdog 删除，initial_delay_seconds 字段
        已随之下线，verbose 无系统兜底。）
        """
        config = self._config_cls()()
        assert config.enabled is True
        assert config.force_disabled is False
        assert config.max_per_turn == 1
        assert config.max_text_chars == 60
        assert config.delivery_timeout_seconds == 5
        assert config.fallback_message == (
            "正在处理你的请求，复杂任务可能需要一点时间，请耐心等待。"
        )

    def test_effective_enabled_follows_enabled(self):
        """未强制关闭时，effective_enabled 跟随 enabled；默认开启。"""
        assert self._config_cls()().effective_enabled is True
        assert self._config_cls()(enabled=True).effective_enabled is True
        assert self._config_cls()(enabled=False).effective_enabled is False

    def test_force_disabled_has_highest_priority(self):
        """force_disabled=true 必须压过 enabled=true（全局紧急回滚的唯一手段）。

        为什么重要：设计 §11 / §14.17——kill switch 覆盖请求级、渠道级和
        旧 waiting_indicator 配置；若普通 enabled 能反向覆盖，回滚就不可靠。
        """
        config = self._config_cls()(enabled=True, force_disabled=True)
        assert config.effective_enabled is False

    def test_config_is_frozen(self):
        """本轮配置在 owner 开始时冻结，运行期不可改写（设计 §11）。"""
        config = self._config_cls()()
        with pytest.raises(FrozenInstanceError):
            config.enabled = True


# ============================================================
# 契约 4：文本校验（设计 §6.1）
# ============================================================


class TestValidateFeedbackText:
    """文本校验契约：白名单式校验，失败返回空，不做局部清洗。

    为什么重要：Skill metadata 只是策略来源，不天然可信；运行时统一校验
    是防止路径/命令/敏感键/内部信息上屏的最后防线。整体拒绝（返回空串）
    而不是截断转义，避免「清洗后的半句病句/泄漏」直达用户。
    """

    def _validate(self):
        module = _verbose_feedback_module()
        return module.validate_feedback_text

    def test_valid_text_returns_unchanged(self):
        """合法文案原样返回（不做改写）。"""
        validate = self._validate()
        assert validate(VALID_TEXT) == VALID_TEXT

    def test_max_boundary_60_chars_accepted(self):
        """边界：1 字与恰好 60 字合法，61 字拒绝。"""
        validate = self._validate()
        # 下边界：1 字合法
        assert validate("好") == "好"
        text = "好" * 59 + "。"
        assert len(text) == 60
        assert validate(text) == text

    @pytest.mark.parametrize(
        "desc, bad_text",
        [
            ("空字符串", ""),
            ("纯空白", "   "),
            ("换行", "第一行\n第二行"),
            ("超长61字", "好" * 61),
            ("多句", "第一句。第二句。"),
            ("代码块", "正在生成 ```code``` 内容。"),
            ("html标签", "正在处理 <b>加急</b> 请求。"),
            ("百分比", "已完成 85%，请稍候。"),
            ("windows路径", "正在读取 C:\\repos\\x\\a.py，请稍候。"),
            ("unix路径", "正在读取 /usr/local/bin/app，请稍候。"),
            ("命令", "正在执行 rm -rf /tmp/data，请稍候。"),
            ("json片段", "正在处理 {\"task\": \"fill_template\"} 请求。"),
            ("数字eta秒", "预计 30 秒后完成。"),
            ("数字eta分钟", "大约还需要 5 分钟。"),
            ("敏感键password", "您的 password 已更新，处理中。"),
            ("敏感键api_key", "正在校验 api_key，请稍候。"),
        ],
    )
    def test_invalid_text_returns_empty(self, desc, bad_text):
        """任何违规文本必须整体返回空串，调用方改用 fallback 文案。"""
        validate = self._validate()
        assert validate(bad_text) == "", f"用例[{desc}]应被整体拒绝，不得部分放行"


# ============================================================
# 审计 P0-1 回归草案：追加消息不得导致 final 消失
# ============================================================


@pytest.fixture
def fake_redis():
    """内存级 fake redis（语义与 tests/unit/test_session_queue.py 同款）。

    供 P0-1 草案使用（自建 owner 级 state 闭包传入 processor，仅依赖 Phase 1
    设施，已于 Phase 1 激活）；提供队列所需的
    acquire_lock / exists / get / set / delete 与 finalizing Lua 语义。
    """
    store = {}
    fr = MagicMock()
    fr._store = store
    fr.make_key = MagicMock(
        side_effect=lambda prefix, session_id: f"{prefix}:{session_id}"
    )

    def acquire_lock(key, value, ex=None):
        if key in store:
            return False
        store[key] = value
        return True

    def release_lock(key, value):
        if store.get(key) == value:
            store.pop(key, None)
            return True
        return False

    fr.acquire_lock = MagicMock(side_effect=acquire_lock)
    fr.release_lock = MagicMock(side_effect=release_lock)
    fr.exists = MagicMock(side_effect=lambda key: key in store)
    fr.get = MagicMock(side_effect=lambda key: store.get(key))
    fr.set = MagicMock(side_effect=lambda key, value, ex=None: store.__setitem__(key, value))
    fr.delete = MagicMock(side_effect=lambda key: store.pop(key, None))

    def session_finalize_if_quiet(
        lock_key, lock_value, cancel_key, merge_key, pending_key,
        finalizing_key, state_guard_key, expected_input, ttl,
    ):
        if store.get(lock_key) != lock_value or state_guard_key in store:
            return False
        if pending_key in store:
            return False
        raw = store.get(merge_key) or {}
        data = json.loads(raw) if isinstance(raw, str) else raw
        if cancel_key in store and data.get("text", "") != expected_input:
            return False
        store[finalizing_key] = "1"
        return True

    fr.session_finalize_if_quiet = MagicMock(side_effect=session_finalize_if_quiet)

    def session_release_finalized(
        lock_key, lock_value, cancel_key, merge_key, responding_key, finalizing_key
    ):
        if store.get(lock_key) != lock_value:
            return False
        for key in (cancel_key, merge_key, responding_key, finalizing_key, lock_key):
            store.pop(key, None)
        return True

    fr.session_release_finalized = MagicMock(side_effect=session_release_finalized)

    with patch("src.core.session_queue.redis_client", fr):
        yield fr


class TestP0AppendAfterVerboseKeepsFinal:
    """P0-1 回归草案：A 发 verbose 后用户追加 B，A 的 final 仍必须投递。

    场景（设计 §3.3 陷阱 A / §7）：A 处理中产生 verbose → 用户追加 B。
    verbose 绝不能触发 mark_responding（MVP 禁止），因此 B 到达时 owner 仍
    未 responding → B 走 cancel+merge 分支并立即返回 merged；owner 重跑合并
    输入后仍返回 success，final 由调用方投递；owner 级 VerboseFeedbackState
    跨 attempt 复用，累计仍最多一条 verbose。
    """

    @pytest.mark.asyncio
    async def test_verbose_then_append_b_final_still_delivered(self, fake_redis):
        q = SessionMessageQueue()
        q.MERGE_WINDOW = 0.3  # 加速：owner 约 0.4s 后进入 attempt 1
        state = _verbose_feedback_module().VerboseFeedbackState()  # owner 生命周期创建一次
        verbose_events = []
        attempt_inputs = []

        async def owner_processor(cancel_check, user_input_override=None):
            """owner 的 processor：每次 attempt 先尝试发 verbose，再返回最终回复。"""
            attempt_inputs.append(user_input_override)
            make_verbose_event = _make_verbose_event_fn()
            event = make_verbose_event(
                event_id=f"verbose_p0_{len(attempt_inputs)}",
                data=VALID_TEXT,
                source="policy",
            )
            if state.try_emit(event):
                verbose_events.append(event)
            await asyncio.sleep(0.8)
            return user_input_override or "final-A"

        async def run_owner():
            result = await q.enqueue_and_process(
                session_id="sid_p0_append", user_input="A",
                processor=owner_processor, msgid="evt_a",
            )
            q.finish_processing("sid_p0_append", result.lease_token)
            return result

        owner_task = asyncio.create_task(run_owner())
        # 等 owner 越过合并窗口（0.4s）进入 attempt 1，verbose 已在此时产生
        await asyncio.sleep(0.6)
        assert q.is_locked("sid_p0_append")
        # 陷阱 A 锁定：verbose 后不得 mark_responding，否则 B 会走 pending 分支，
        # A 的 final 会被 pending 重跑结果覆盖而永不发送
        assert q.is_cancel_allowed("sid_p0_append"), (
            "verbose 发出后不得 mark_responding：B 到达时必须仍允许 cancel+merge"
        )

        # 用户追加 B：应走 cancel+merge 分支（非 pending），B 立即返回 merged
        result_b = await q.enqueue_and_process(
            session_id="sid_p0_append", user_input="B",
            processor=AsyncMock(return_value="never"), msgid="evt_b",
        )
        assert result_b.status == "merged"

        result_a = await owner_task

        # —— 核心断言：A 的 final 仍被投递，不能因追加 B 而消失 ——
        assert result_a.status == "success"
        assert result_a.response_text != "", (
            "owner 的 final 不能为空：被 pending/merged 覆盖的典型症状是最终回复丢失"
        )
        assert "A" in result_a.merged_input and "B" in result_a.merged_input
        assert result_a.was_merged is True

        # —— owner 生命周期状态复用：重跑 attempt 不重发 verbose ——
        assert len(attempt_inputs) == 2, "B 追加后 owner 应恰好重跑一次合并输入"
        assert len(verbose_events) == 1, (
            "cancel/merge 重跑复用 owner 级状态，累计仍最多一条 verbose"
        )
        assert verbose_events[0]["eventId"] == "verbose_p0_1"
        assert state.event is verbose_events[0]


# ============================================================
# 审计 P0-2 回归草案：RPA verbose/final 不得共用幂等键
# ============================================================


class TestP0RpaVerboseFinalDistinctDedupKey:
    """P0-2 回归草案：企微个人 RPA 的 verbose 与 final 不得共用幂等键。

    现状（src/channels/wecom_personal_rpa/action_client.py）：
        dedup_key = f"wecom_personal_rpa:{tenant_id}:{request_id}"
    outbox 以 dedup_key 做 ON CONFLICT DO NOTHING；pre_send 目前每次写原始
    event_id。若 verbose 与 final 共用同一个 request_id，后发消息会被 outbox
    去重吞掉（设计 §3.3 陷阱 B / §9.4）。

    契约（设计 §9.4）：
        verbose request_id = f"{event_id}:verbose:1"
        final   request_id = f"{event_id}:final"
    两个 dedup_key 必须不同，且两次投递都能入队（各自幂等重试）。
    """

    @pytest.mark.asyncio
    async def test_verbose_and_final_produce_distinct_dedup_keys(self, monkeypatch):
        from src.channels.wecom_personal_rpa import action_client

        # 前置：deliver_actions → _build_reply_digests → _hmac_key() 在
        # WECOM_RPA_AUDIT_HMAC_KEY / RPA_SECRET_KEY 均缺失时抛 RuntimeError
        # （deliver_actions 不捕获）。与 tests/unit/channels/wecom_personal_rpa/
        # test_action_client.py 模块级 setdefault 同款。
        monkeypatch.setenv("RPA_SECRET_KEY", "test-rpa-audit-key-32-bytes-minimum")

        captured = []

        # 注意：真实 db.enqueue_action 是同步函数，fake 必须同为同步——
        # 若用 async fake，deliver_actions 拿到的是未 await 的协程对象（恒 truthy），
        # captured 永远为空，断言失效（Phase 0 草案从未执行过，激活时修正）。
        def fake_enqueue_action(**kwargs):
            captured.append(kwargs)
            return {"id": len(captured)}

        # 账号存在但 client_id=None：跳过在线直推，只走权威 outbox 入队路径
        monkeypatch.setattr(
            action_client.db, "get_account_for_tenant",
            lambda tenant_id, account_id: {
                "client_id": None, "wecom_user_id": "self_wecom_user",
            },
        )
        monkeypatch.setattr(action_client.db, "enqueue_action", fake_enqueue_action)

        event_id = "evt_rpa_p0"
        verbose_request_id = f"{event_id}:verbose:1"
        final_request_id = f"{event_id}:final"

        ok_verbose = await action_client.deliver_actions(
            tenant_id="tenant_p0", account_id="acc_p0",
            conversation_id="dm:peer_p0", request_id=verbose_request_id,
            session_id="wecom_personal_rpa:acc_p0:route_p0",
            actions=[{"type": "send_text", "text": VALID_TEXT}],
        )
        ok_final = await action_client.deliver_actions(
            tenant_id="tenant_p0", account_id="acc_p0",
            conversation_id="dm:peer_p0", request_id=final_request_id,
            session_id="wecom_personal_rpa:acc_p0:route_p0",
            actions=[{"type": "send_text", "text": "报价单已生成，请查收。"}],
        )

        assert ok_verbose is True and ok_final is True, "两次投递都应判定为逻辑成功"
        assert len(captured) == 2, (
            "verbose 与 final 必须是两次独立 outbox 入队；后者被去重吞掉即为本 P0"
        )
        assert [c["request_id"] for c in captured] == [
            verbose_request_id, final_request_id,
        ]
        # dedup_key 契约：以 request_id 派生（action_client 现有公式），断言 outbox
        # 层的幂等键，而不是 UnifiedResponse.message_id（设计 §9.4 明确要求）
        dedup_keys = [c["dedup_key"] for c in captured]
        assert dedup_keys[0] == f"wecom_personal_rpa:tenant_p0:{verbose_request_id}"
        assert dedup_keys[1] == f"wecom_personal_rpa:tenant_p0:{final_request_id}"
        assert len(set(dedup_keys)) == 2, "verbose/final 的 dedup_key 必须不同"
        # 契约字符串冻结（设计 §9.4）
        assert verbose_request_id == "evt_rpa_p0:verbose:1"
        assert final_request_id == "evt_rpa_p0:final"


# ============================================================
# Phase 2 契约：Web 端 metadata delivery 命名（设计 §10）
# ============================================================


class TestWebVerboseMetadataDeliveryContract:
    """Phase 2 Web 端 verboseMessages 条目契约。

    为什么放契约文件：设计 §10 明确「Web 端没有 adapter delivery，可记录
    delivery="streamed"，命名在实现前由契约测试冻结」。该命名是 DB 历史
    metadata 的用户可见结构，一旦变更即破坏历史消息兼容性，故在此冻结；
    src.main 是 Web 交付命名的唯一生产落点（延迟导入，模块不存在时独立变红）。
    """

    def test_web_delivery_value_frozen_to_streamed(self):
        """Web 端 delivery 冻结为 "streamed"（不是 "displayed"）：Web 无法可靠
        确认 DOM 是否实际渲染，只记录「已随 SSE 流式下发」这一事实。"""
        import src.main as main_module

        assert main_module.WEB_VERBOSE_DELIVERY == "streamed"

    def test_web_entry_shape_and_dedup_rule(self):
        """metadata 条目恰好为协议五字段 + delivery；同 eventId 去重保留首条
        （设计 §10 合并规则 3：verboseMessages 按 eventId 去重）。"""
        import src.main as main_module

        first = {
            "type": "verbose", "eventId": "verbose_c1", "data": VALID_TEXT,
            "source": "policy", "timestamp": 1788144000000,
        }
        duplicate = dict(first, data="重复帧不应覆盖首条。")
        other = dict(first, eventId="verbose_c2")
        entries = main_module._build_verbose_metadata_entries(
            [first, duplicate, other])
        assert [entry["eventId"] for entry in entries] == ["verbose_c1", "verbose_c2"]
        assert entries[0]["data"] == VALID_TEXT
        for entry in entries:
            assert set(entry.keys()) == {
                "eventId", "data", "source", "timestamp", "delivery",
            }
            assert entry["delivery"] == "streamed"

    def test_no_verbose_turn_yields_no_entries(self):
        """本轮无 verbose 时空列表：调用方保持 metadata 无 verboseMessages 键
        （Phase 2 约束：关闭/无 verbose 时历史消息结构不变）。"""
        import src.main as main_module

        assert main_module._build_verbose_metadata_entries([]) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
