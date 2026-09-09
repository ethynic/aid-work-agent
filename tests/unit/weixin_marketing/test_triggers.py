"""触发编译与时间语义测试（R48：once 只一次 / interval 不积压 / 周月 DST 一致；注入时钟）"""

from datetime import datetime, timedelta, timezone

import pytest

from src.desktop_automation import occurrences as da_occurrences
from src.desktop_automation import schedules as da_schedules
from src.weixin_marketing import triggers
from src.weixin_marketing.constants import SCENARIO_KEY
from src.weixin_marketing.triggers import TriggerConfigError
from tests.unit.weixin_marketing.conftest import (
    create_and_publish,
    make_create_payload,
    utcnow,
)

pytestmark = pytest.mark.unit


def _once(run_at, **extra):
    base = {"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"}
    base.update(extra)
    return base


def _interval(start_at, seconds, **extra):
    base = {"type": "interval", "start_at": start_at.isoformat(), "interval_seconds": seconds,
            "timezone": "UTC"}
    base.update(extra)
    return base


class TestSpecCompilation:
    def test_once_compiles_one_shot(self, wx_config):
        at = utcnow() + timedelta(hours=1)
        specs = triggers.compile_trigger_specs(
            triggers.parse_trigger(_once(at)), config=wx_config
        )
        assert len(specs) == 1
        assert specs[0]["one_shot"] is True
        assert da_schedules.ensure_utc(specs[0]["anchor_at"]) == at

    def test_interval_frequency_gate(self, wx_config):
        at = utcnow()
        trigger = triggers.parse_trigger(_interval(at, 60))
        with pytest.raises(TriggerConfigError):
            triggers.compile_trigger_specs(trigger, config=wx_config)
        permissive = wx_config
        from dataclasses import replace

        permissive = replace(permissive, min_interval_seconds=30)
        assert triggers.compile_trigger_specs(trigger, config=permissive)

    def test_interval_requires_anchor_and_valid_ends(self, wx_config):
        at = utcnow()
        bad = dict(_interval(at, 3600))
        bad["ends_at"] = (at - timedelta(hours=1)).isoformat()
        with pytest.raises(TriggerConfigError):
            triggers.compile_trigger_specs(triggers.parse_trigger(bad), config=wx_config)

    def test_calendar_cron_and_fields(self, wx_config):
        cron = triggers.compile_trigger_specs(
            triggers.parse_trigger({"type": "calendar", "cron_expr": "0 9 * * mon-fri",
                                    "timezone": "Asia/Shanghai"}),
            config=wx_config,
        )
        assert cron[0]["cron_expr"] == "0 9 * * mon-fri"
        fields = triggers.compile_trigger_specs(
            triggers.parse_trigger({"type": "calendar", "day_of_week": "mon,wed,fri",
                                    "hour": "9", "minute": "0", "timezone": "Asia/Shanghai"}),
            config=wx_config,
        )
        assert fields[0]["day_of_week"] == "mon,wed,fri" and fields[0]["hour"] == "9"

    def test_calendar_requires_cron_or_fields(self, wx_config):
        with pytest.raises(TriggerConfigError):
            triggers.compile_trigger_specs(
                triggers.parse_trigger({"type": "calendar", "timezone": "UTC"}), config=wx_config
            )

    def test_event_gate(self, wx_config):
        from dataclasses import replace

        disabled = replace(wx_config, event_triggers_enabled=False)
        with pytest.raises(TriggerConfigError):
            triggers.compile_trigger_specs(
                triggers.parse_trigger({"type": "event", "source_ref": "src-1"}),
                config=disabled,
            )
        specs = triggers.compile_trigger_specs(
            triggers.parse_trigger({"type": "event", "source_ref": "src-1", "event_type": "order.paid"}),
            config=wx_config,
        )
        assert specs[0]["kind"] == "event" and specs[0]["source_ref"] == "src-1"

    def test_time_gate(self, wx_config):
        from dataclasses import replace

        disabled = replace(wx_config, time_triggers_enabled=False)
        with pytest.raises(TriggerConfigError):
            triggers.compile_trigger_specs(
                triggers.parse_trigger(_once(utcnow() + timedelta(hours=1))), config=disabled
            )

    def test_max_blocks_gate(self, wx_config):
        blocks = [{"type": "text", "text_content": f"内容 {i}"} for i in range(21)]
        with pytest.raises(TriggerConfigError):
            triggers.validate_blocks(blocks, wx_config)

    def test_image_blocks_blocked_without_flag(self, wx_config):
        with pytest.raises(TriggerConfigError):
            triggers.validate_blocks([{"kind": "image", "asset_id": "a-1"}], wx_config)


class TestPreview:
    def test_preview_next_fires_count(self, wx_config):
        at = datetime(2026, 9, 8, 8, 0, 0, tzinfo=timezone.utc)
        trigger = triggers.parse_trigger(_interval(at, 3600))
        fires = triggers.preview_next_fires(trigger, count=5, now=at)
        assert len(fires) == 5
        assert fires[0] == at
        assert fires[1] == at + timedelta(hours=1)
        assert fires[4] == at + timedelta(hours=4)

    def test_preview_once_past_empty(self, wx_config):
        past = utcnow() - timedelta(hours=1)
        fires = triggers.preview_next_fires(
            triggers.parse_trigger(_once(past)), count=5, now=utcnow()
        )
        assert fires == []

    def test_preview_calendar_month_end(self, wx_config):
        """cron L（月末）预览：1/31 → 2/28 → 3/31（2027 非闰年）"""
        trigger = triggers.parse_trigger({"type": "calendar", "cron_expr": "0 9 L * *",
                                          "timezone": "Asia/Shanghai"})
        fires = triggers.preview_next_fires(
            trigger, count=3, now=datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
        )
        assert len(fires) == 3
        days = [f.day for f in fires]
        months = [f.month for f in fires]
        assert days == [31, 28, 31]
        assert months == [1, 2, 3]

    def test_preview_calendar_dst_consistency(self, wx_config):
        """America/New_York 09:00 每日 cron：DST 切换（2026-03-08）前后本地时刻恒为
        09:00，UTC 时刻从 14:00（-5）变 13:00（-4）——快照断言"""
        from zoneinfo import ZoneInfo

        ny = ZoneInfo("America/New_York")
        trigger = triggers.parse_trigger({"type": "calendar", "cron_expr": "0 9 * * *",
                                          "timezone": "America/New_York"})
        before = triggers.preview_next_fires(
            trigger, count=1, now=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)
        )[0]
        after = triggers.preview_next_fires(
            trigger, count=1, now=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        )[0]
        # 返回 UTC；换算回纽约本地恒 09:00
        local_before = before.astimezone(ny)
        local_after = after.astimezone(ny)
        assert (local_before.hour, local_before.minute) == (9, 0)
        assert (local_after.hour, local_after.minute) == (9, 0)
        assert local_before.utcoffset() == timedelta(hours=-5)
        assert local_after.utcoffset() == timedelta(hours=-4)
        assert (before.hour, after.hour) == (14, 13)


class TestTimeScanAcceptance:
    """经底座 scan_and_accept_time_slots 的端到端时间语义（假时钟注入）。

    断言一律按本租户 occurrence/schedule 行核实（全局扫描结果可能混入其他租户）。
    """

    def _count_time_occurrences(self, tenant_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_occurrences "
                "WHERE tenant_id = %s AND trigger_kind = 'time'",
                (tenant_id,),
            )
            return int(cur.fetchone()["c"])

    def test_once_fires_exactly_once(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        run_at = utcnow() + timedelta(minutes=30)
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, trigger=_once(run_at)
        )
        # 到期后首轮扫描：接纳 1 个 occurrence；schedule consumed
        da_occurrences.scan_and_accept_time_slots(run_at + timedelta(seconds=5), limit=100)
        assert self._count_time_occurrences(tenant_id) == 1
        occurrence = da_occurrences.get_occurrence_by_trigger_key(
            tenant_id, SCENARIO_KEY, automation_id,
            f"time:{revision_id}:{run_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        )
        assert occurrence is not None
        # 再次扫描：无新 occurrence（one_shot consumed + trigger key 幂等）
        da_occurrences.scan_and_accept_time_slots(run_at + timedelta(minutes=5), limit=100)
        assert self._count_time_occurrences(tenant_id) == 1
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        assert rows[0]["consumed"] is True

    def test_interval_restart_no_backlog(self, service, tenant_id, bindings, adapter):
        """停机 5 个周期后重启：只接纳最近槽 1 次，next_fire 前移不积压"""
        _, group_id = bindings
        anchor = utcnow().replace(second=0, microsecond=0) - timedelta(hours=1)
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id,
            trigger=_interval(anchor, 3600, grace_seconds=7200, miss_policy="catch_up_latest"),
        )
        now = anchor + timedelta(hours=5, minutes=2)
        da_occurrences.scan_and_accept_time_slots(now, limit=100)
        assert self._count_time_occurrences(tenant_id) == 1  # 恰一次，无积压队列
        # 最近槽 = anchor + 5h（<= now 的最大网格槽），next_fire 前移到 anchor + 6h
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        assert da_schedules.ensure_utc(rows[0]["next_fire_at"]) == anchor + timedelta(hours=6)
        occurrence = da_occurrences.get_occurrence_by_trigger_key(
            tenant_id, SCENARIO_KEY, automation_id,
            f"time:{revision_id}:{(anchor + timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        )
        assert occurrence is not None

    def test_calendar_weekly_fires(self, service, tenant_id, bindings, adapter):
        """周一 09:00 Asia/Shanghai 的周触发：首轮扫描接纳一次"""
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id,
            trigger={"type": "calendar", "cron_expr": "0 9 * * mon", "timezone": "Asia/Shanghai"},
        )
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        next_fire = da_schedules.ensure_utc(rows[0]["next_fire_at"])
        # 下一次周一 09:00 上海时间（UTC+8 = 周一 01:00 UTC）
        assert next_fire.weekday() == 0
        assert next_fire.hour == 1 and next_fire.minute == 0
        da_occurrences.scan_and_accept_time_slots(next_fire + timedelta(seconds=1), limit=100)
        assert self._count_time_occurrences(tenant_id) == 1
        # 接纳后 next_fire 推进到下一周（不重复）
        rows_after = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        assert da_schedules.ensure_utc(rows_after[0]["next_fire_at"]) == next_fire + timedelta(days=7)

    def test_event_trigger_compiles_subscription_row(self, service, tenant_id, bindings, adapter):
        """event 触发：发布后落 kind=event 订阅行（不进时间扫描；worker 接线在 P4）"""
        _, group_id = bindings
        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(group_id, trigger={
                "type": "event", "source_ref": "crm-orders", "event_type": "order.paid",
            }),
        )
        automation = detail["automation"]
        from src.weixin_marketing.models import PublishInput

        service.publish(tenant_id, str(automation["id"]), "owner-1", PublishInput(expected_version=1))
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, str(automation["id"]))
        assert len(rows) == 1
        assert rows[0]["kind"] == "event" and rows[0]["status"] == "active"
        assert rows[0]["source_ref"] == "crm-orders" and rows[0]["event_type"] == "order.paid"
        # 事件行不进时间扫描（next_fire_at 为 NULL）
        assert rows[0]["next_fire_at"] is None
        results = da_occurrences.scan_and_accept_time_slots(utcnow() + timedelta(days=1), limit=100)
        assert all(r.get("reason") in (None, "") or not r.get("created") for r in results)
        assert self._count_time_occurrences(tenant_id) == 0


class TestValidateAutomation:
    def test_validate_returns_next_fives_and_binding_error(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        run_at = utcnow() + timedelta(hours=2)
        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(group_id, trigger=_interval(run_at, 3600)),
        )
        automation_id = str(detail["automation"]["id"])
        report = service.validate_automation(tenant_id, automation_id, "owner-1", now=run_at)
        assert report["ok"] is True
        assert len(report["next_fires"]) == 5
        # 绑定置 pending → 校验报字段错误
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_group_bindings SET state = 'pending' "
                "WHERE tenant_id = %s AND id = %s", (tenant_id, group_id),
            )
            conn.commit()
        report2 = service.validate_automation(tenant_id, automation_id, "owner-1", now=run_at)
        assert report2["ok"] is False
        assert any(e["field"] == "group_binding_id" for e in report2["errors"])

    def test_validate_once_past_warns(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        past = utcnow() - timedelta(hours=1)
        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(group_id, trigger=_once(past)),
        )
        report = service.validate_automation(
            tenant_id, str(detail["automation"]["id"]), "owner-1", now=utcnow()
        )
        assert any(w["field"] == "trigger" for w in report["warnings"])
