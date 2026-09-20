"""P1-5（六审，V1.10 §4.1/§5.3 冻结）：发布事务内话术版本强校验钩子。

- validate_publish_spec 在 publish_task 已锁 task、写 revision 前调用；
- 不存在版本 / 跨租户版本 / DB content_hash 不一致 → 发布失败（409）且零
  revision 副作用（specs 无新行、task.spec_revision 不变、状态不变）；
- 合法引用 → 正常发布（revision 前进）；
- 微信描述器 validate_publish_spec=None（行为不变）。
"""

import json
import uuid

import pytest

from src.boss_conversation.models import template_content_hash

from tests.unit.boss_conversation.conftest import (
    NO_SLOT_TEMPLATE,
    make_script_version,
    publish_boss_task,
)


def _script_ref(version):
    return {
        "script_version_id": version["id"],
        "content_hash": version["content_hash"],
        "frozen_template": version["template"],
        "slot_schema": {},
    }


def _spec_with_scripts(scripts):
    from tests.unit.boss_conversation.conftest import make_boss_spec

    return make_boss_spec("ignored-tenant", scripts=scripts)


def _task_state(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, spec_revision, version FROM session_tasks WHERE tenant_id=%s AND id=%s",
            (tenant_id, task_id),
        )
        return dict(cursor.fetchone())


def _spec_revision_count(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM session_task_specs WHERE tenant_id=%s AND task_id=%s",
            (tenant_id, task_id),
        )
        return int(cursor.fetchone()["n"])


def _publish_task_with_schema(tenant_id, boss_binding, slot_schema, *, template=None):
    """以给定 slot_schema 引用**新建版本**走 create → confirm → publish 链。
    返回 (created, confirmation, publish 调用)。"""
    from src.session_tasks import service
    from src.session_tasks.models import TaskDraftCreatePayload
    from tests.unit.boss_conversation.conftest import make_boss_spec, make_script_version

    version = make_script_version(
        tenant_id, template or "您好，{expected_time} 方便沟通吗？", slot_schema=dict(slot_schema)
    )
    scripts = [{
        "script_version_id": version["id"],
        "content_hash": version["content_hash"],
        "frozen_template": version["template"],
        "slot_schema": slot_schema,
    }]
    spec = make_boss_spec(tenant_id, scripts=scripts)
    payload = TaskDraftCreatePayload.model_validate({
        "scenario_key": "boss.chat_reply.v1",
        "device_id": boss_binding["device_id"],
        "account_binding_id": boss_binding["account_scope_id"],
        "conversation_binding_id": boss_binding["conversation_binding_id"],
        "spec": spec,
    })
    created = service.create_draft(tenant_id, "user-1", payload)
    confirmation = service.issue_publish_confirmation(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
    )
    return created, confirmation, lambda: service.publish_task(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
        uuid.UUID(confirmation["confirmation_id"]),
    )


class TestValidatePublishSpec:
    def test_nonexistent_version_publish_fails_zero_side_effects(self, tenant_id, boss_binding):
        """任意不存在的 UUID + 自洽模板/hash → 发布失败，零 revision 副作用。"""
        from src.session_tasks.constants import SessionTaskError

        fake_version = str(uuid.uuid4())
        scripts = [{
            "script_version_id": fake_version,
            "content_hash": template_content_hash(NO_SLOT_TEMPLATE),
            "frozen_template": NO_SLOT_TEMPLATE,
            "slot_schema": {},
        }]
        spec = _spec_with_scripts(scripts)
        from tests.unit.boss_conversation.conftest import make_boss_spec

        # 直接构造任务（不引用版本创建）：create → confirm → publish 应在 publish 失败
        from src.session_tasks import service
        from src.session_tasks.models import TaskDraftCreatePayload

        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": "boss.chat_reply.v1",
            "device_id": boss_binding["device_id"],
            "account_binding_id": boss_binding["account_scope_id"],
            "conversation_binding_id": boss_binding["conversation_binding_id"],
            "spec": spec,
        })
        created = service.create_draft(tenant_id, "user-1", payload)
        task_id = created["task_id"]
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(task_id), created["version"]
        )
        with pytest.raises(SessionTaskError) as exc:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(task_id), created["version"],
                uuid.UUID(confirmation["confirmation_id"]),
            )
        assert exc.value.status_code == 409
        state = _task_state(tenant_id, task_id)
        assert state["status"] == "draft"  # 状态未变
        assert int(state["spec_revision"] or 0) == 0  # 零 revision 副作用
        assert _spec_revision_count(tenant_id, task_id) == 0  # specs 无新行

    def test_cross_tenant_version_publish_fails(self, tenant_id, other_tenant_seed, boss_binding):
        """跨租户版本引用（版本属于另一租户）→ 发布失败 409。"""
        from src.session_tasks import service
        from src.session_tasks.constants import SessionTaskError
        from src.session_tasks.models import TaskDraftCreatePayload

        version = other_tenant_seed  # 另一租户的版本行
        scripts = [_script_ref(version)]
        from tests.unit.boss_conversation.conftest import make_boss_spec

        spec = make_boss_spec(tenant_id, scripts=scripts)
        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": "boss.chat_reply.v1",
            "device_id": boss_binding["device_id"],
            "account_binding_id": boss_binding["account_scope_id"],
            "conversation_binding_id": boss_binding["conversation_binding_id"],
            "spec": spec,
        })
        created = service.create_draft(tenant_id, "user-1", payload)
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        with pytest.raises(SessionTaskError) as exc:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                uuid.UUID(confirmation["confirmation_id"]),
            )
        assert exc.value.status_code == 409
        assert _spec_revision_count(tenant_id, created["task_id"]) == 0

    def test_db_hash_mismatch_publish_fails(self, tenant_id, boss_binding):
        """版本行 content_hash 被篡改（与 spec 冻结值不一致）→ 发布失败 409。"""
        from src.db.database import get_db_connection
        from src.session_tasks import service
        from src.session_tasks.constants import SessionTaskError
        from src.session_tasks.models import TaskDraftCreatePayload

        version = make_script_version(tenant_id, NO_SLOT_TEMPLATE)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_reply_script_versions SET content_hash='deadbeef-tampered' "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, version["id"]),
            )
            conn.commit()
        scripts = [_script_ref(version)]
        from tests.unit.boss_conversation.conftest import make_boss_spec

        spec = make_boss_spec(tenant_id, scripts=scripts)
        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": "boss.chat_reply.v1",
            "device_id": boss_binding["device_id"],
            "account_binding_id": boss_binding["account_scope_id"],
            "conversation_binding_id": boss_binding["conversation_binding_id"],
            "spec": spec,
        })
        created = service.create_draft(tenant_id, "user-1", payload)
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        with pytest.raises(SessionTaskError) as exc:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                uuid.UUID(confirmation["confirmation_id"]),
            )
        assert exc.value.status_code == 409
        assert _spec_revision_count(tenant_id, created["task_id"]) == 0

    def test_valid_reference_publishes(self, tenant_id, boss_binding):
        """合法引用（本租户已发布版本 + hash 一致）→ 正常发布，revision 前进。"""
        task_id = publish_boss_task(tenant_id, boss_binding)
        state = _task_state(tenant_id, task_id)
        assert state["status"] == "active"
        assert int(state["spec_revision"]) == 1
        assert _spec_revision_count(tenant_id, task_id) == 1

    def test_weixin_descriptor_hook_is_none(self):
        """微信描述器 validate_publish_spec=None（发布校验与锁面零变化）。"""
        from src.weixin_conversation.registration import ensure_registered
        from src.session_tasks.scenario_descriptor import get_descriptor

        ensure_registered()
        assert get_descriptor("weixin.conversation.v1").validate_publish_spec is None

    def test_boss_descriptor_hook_signature(self, tenant_id):
        """BOSS 钩子签名 (conn, tenant_id, spec)：合法 spec 直调通过；未知版本抛 409。"""
        from src.boss_conversation.registration import ensure_registered
        from src.db.database import get_db_connection
        from src.session_tasks.constants import SessionTaskError
        from src.session_tasks.scenario_descriptor import get_descriptor

        ensure_registered()
        hook = get_descriptor("boss.chat_reply.v1").validate_publish_spec
        assert callable(hook)
        version = make_script_version(tenant_id, NO_SLOT_TEMPLATE)
        spec = _spec_with_scripts([_script_ref(version)])
        with get_db_connection() as conn:
            hook(conn, tenant_id, spec)  # 不抛 = 通过
            # 同一连接换租户 → 版本不存在（租户归属过滤）
            with pytest.raises(SessionTaskError):
                hook(conn, f"other_{tenant_id}", spec)


class TestSlotSchemaFrozen:
    """P1-B（七审）：slot_schema 冻结副本精确校验——引用真实版本但篡改槽位约束
    （必需 required=true 改 false，使渲染按可选缺值空串发送、绕过必需证据约束）
    一律发布失败。"""

    SLOT_SCHEMA = {"expected_time": {"required": True, "description": "候选人方便的时间"}}

    def _publish_with_schema(self, tenant_id, boss_binding, slot_schema):
        """create（版本带冻结 schema）→ spec 以给定 slot_schema 引用 → confirm →
        publish 调用。"""
        from src.session_tasks import service
        from src.session_tasks.models import TaskDraftCreatePayload
        from tests.unit.boss_conversation.conftest import make_boss_spec, make_script_version

        version = make_script_version(
            tenant_id, "您好，{expected_time} 方便沟通吗？", slot_schema=dict(self.SLOT_SCHEMA)
        )
        scripts = [{
            "script_version_id": version["id"],
            "content_hash": version["content_hash"],
            "frozen_template": version["template"],
            "slot_schema": slot_schema,
        }]
        spec = make_boss_spec(tenant_id, scripts=scripts)
        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": "boss.chat_reply.v1",
            "device_id": boss_binding["device_id"],
            "account_binding_id": boss_binding["account_scope_id"],
            "conversation_binding_id": boss_binding["conversation_binding_id"],
            "spec": spec,
        })
        created = service.create_draft(tenant_id, "user-1", payload)
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        return created, confirmation, lambda: service.publish_task(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
            uuid.UUID(confirmation["confirmation_id"]),
        )

    def test_required_to_optional_tamper_publish_fails_zero_side_effects(self, tenant_id, boss_binding):
        """必需槽位 required=true 篡改为 false → 发布失败 409 且 revision 数量仍为 0。"""
        from src.session_tasks.constants import SessionTaskError

        created, confirmation, publish = self._publish_with_schema(
            tenant_id, boss_binding,
            {"expected_time": {"required": False, "description": "候选人方便的时间"}},
        )
        with pytest.raises(SessionTaskError) as exc:
            publish()
        assert exc.value.status_code == 409
        assert _spec_revision_count(tenant_id, created["task_id"]) == 0
        state = _task_state(tenant_id, created["task_id"])
        assert state["status"] == "draft"

    def test_description_tamper_also_rejected(self, tenant_id, boss_binding):
        """description 篡改同样拒绝（精确比较，非仅 required 位）。"""
        from src.session_tasks.constants import SessionTaskError

        created, confirmation, publish = self._publish_with_schema(
            tenant_id, boss_binding,
            {"expected_time": {"required": True, "description": "被篡改的描述"}},
        )
        with pytest.raises(SessionTaskError) as exc:
            publish()
        assert exc.value.status_code == 409

    def test_key_order_insensitive_schema_publishes(self, tenant_id, boss_binding):
        """规范化比较对键序不敏感：同一 schema 键序不同 → 发布通过、revision=1。"""
        created, confirmation, publish = self._publish_with_schema(
            tenant_id, boss_binding,
            {"expected_time": {"description": "候选人方便的时间", "required": True}},
        )
        publish()
        state = _task_state(tenant_id, created["task_id"])
        assert state["status"] == "active"
        assert _spec_revision_count(tenant_id, created["task_id"]) == 1


class TestIllegalDbSlotSchema:
    """P1（九审/CR 修正）：DB slot_schema 非法（JSON null/数组/字符串等损坏或
    非法存量）发布 → 受控 409 CONFLICT，不得静默当空 schema（否则损坏行 +
    spec {} 错误通过）；合法 dict 省略默认值的历史数据仍兼容。

    可达性说明（CR 实证纠正）：DDL `slot_schema JSONB NOT NULL` 只拒 SQL NULL；
    JSON null（'null'::jsonb）是合法 JSONB 值，经 SQL DML **可写入**（psycopg2
    读回 Python None）——故 JSON null 与数组/字符串同样走真实发布全路径
    （篡改 UPDATE + 409 + revision=0 + task 保持 draft）。桩游标单测仅作为
    DB 形态无关的防御路径补充锁定。"""

    def _tamper_slot_schema(self, tenant_id, json_value):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_reply_script_versions SET slot_schema=%s::jsonb "
                "WHERE tenant_id=%s",
                # json.dumps(None)='null' → 存为 JSON null（非 SQL NULL，NOT NULL
                # 不拒；psycopg2 读回 None）
                (json.dumps(json_value), tenant_id),
            )
            conn.commit()

    @pytest.mark.parametrize("corrupt_value", [None, [1, 2], "text"])
    def test_illegal_db_slot_schema_publish_409_zero_side_effects(
            self, tenant_id, boss_binding, corrupt_value):
        created, _confirmation, publish = _publish_task_with_schema(
            tenant_id, boss_binding, {"expected_time": {"required": True}}
        )
        self._tamper_slot_schema(tenant_id, corrupt_value)
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:
            publish()
        assert exc.value.status_code == 409 and exc.value.code == "CONFLICT"
        assert _spec_revision_count(tenant_id, created["task_id"]) == 0  # revision 数量为 0
        state = _task_state(tenant_id, created["task_id"])
        assert state["status"] == "draft"  # task 保持 draft

    def test_db_slot_schema_json_null_409_defensive(self):
        """JSON null 防御路径补充锁定（DB 形态无关）：桩游标直接驱动
        verify_spec_scripts_published，row.slot_schema=None（JSON null 读回形态）
        → 409 CONFLICT。真实全路径覆盖见上方 parametrize 的 None 场景。"""
        from src.boss_conversation import script_versions as sv
        from src.session_tasks.constants import SessionTaskError

        template = NO_SLOT_TEMPLATE
        captured = {}

        class _FakeCursor:
            def execute(self, query, params=None):
                captured["params"] = params
                return None

            def fetchone(self):
                return {"id": (captured["params"] or ("", None))[1],
                        "content_hash": template_content_hash(template),
                        "slot_schema": None}

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

        spec = {"scripts": [{
            "script_version_id": "v-1", "content_hash": template_content_hash(template),
            "frozen_template": template, "slot_schema": {},
        }]}
        with pytest.raises(SessionTaskError) as exc:
            sv.verify_spec_scripts_published("t", spec, conn=_FakeConn())
        assert exc.value.status_code == 409 and exc.value.code == "CONFLICT"

    def test_dict_with_illegal_inner_field_publish_409(self, tenant_id, boss_binding):
        """dict 内字段非法（历史脏数据）→ 归一化失败同样转 409。"""
        created, _confirmation, publish = _publish_task_with_schema(
            tenant_id, boss_binding, {"expected_time": {"required": True}}
        )
        self._tamper_slot_schema(tenant_id, {"expected_time": {"required": "not-a-bool"}})
        from src.session_tasks.constants import SessionTaskError

        # "not-a-bool" 非 pydantic bool 可强转词法 → 归一化失败转 409
        with pytest.raises(SessionTaskError) as exc:
            publish()
        assert exc.value.status_code == 409

    def test_legacy_dict_without_defaults_still_publishes(self, tenant_id, boss_binding):
        """只兼容合法 dict：省略 required/description 的历史数据（normalize 补默认值）
        与 Pydantic 补全后的 spec 同形态 → 发布通过。"""
        created, _confirmation, publish = _publish_task_with_schema(
            tenant_id, boss_binding, {"expected_time": {"required": True}}
        )
        self._tamper_slot_schema(tenant_id, {"expected_time": {"required": True}})
        publish()
        state = _task_state(tenant_id, created["task_id"])
        assert state["status"] == "active"
        assert _spec_revision_count(tenant_id, created["task_id"]) == 1


@pytest.fixture()
def other_tenant_seed(tenant_id):
    """另一租户的已发布话术版本（随当前租户清理后一并清理）。"""
    other = f"boss_other_{uuid.uuid4().hex[:12]}"
    version = make_script_version(other, NO_SLOT_TEMPLATE)
    yield version
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        conn.cursor().execute(
            "DELETE FROM bs_boss_reply_script_versions WHERE tenant_id=%s", (other,)
        )
        conn.commit()


class TestCreateVersionLineage:
    """非阻断 d（六审）：不存在 lineage 404（不再被 MAX() 静默建成 v1）；
    并发同 lineage 发布无版本号冲突（UNIQUE 兜底 + 重读重试）。"""

    def test_nonexistent_lineage_404(self, tenant_id):
        from src.boss_conversation import script_versions
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:
            script_versions.create_version(
                tenant_id, "user-1", lineage_id=str(uuid.uuid4()),
                template="不存在的 lineage", source_job_name="x",
            )
        assert exc.value.status_code == 404

    def test_lineage_version_no_increments(self, tenant_id):
        from src.boss_conversation import script_versions

        v1 = make_script_version(tenant_id, "版本一")
        v2 = script_versions.create_version(
            tenant_id, "user-1", lineage_id=v1["lineage_id"],
            template="版本二", source_job_name="x",
        )
        assert int(v2["version_no"]) == int(v1["version_no"]) + 1

    @staticmethod
    def _race_publish_threads(tenant_id, lineage, template_prefix, monkeypatch, retries=None):
        """P2-2（八审）竞争屏障：monkeypatch _canonical_slot_schema（create_version
        内 MAX(version_no) 读取**之后**、INSERT 之前的注入点）为 threading.Barrier
        同步——4 线程全部读到相同 MAX 值后才放行插入，制造必然的版本号唯一冲突。
        首轮会合后 action 复位 armed，重试路径不再等待。"""
        import concurrent.futures
        import threading

        from src.boss_conversation import script_versions

        orig_serialize = script_versions._canonical_slot_schema
        state = {"armed": True}

        def _disarm():
            state["armed"] = False

        barrier = threading.Barrier(4, action=_disarm)

        def _barrier_serialize(value):
            if state["armed"]:
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:  # noqa: PERF203 降级为时序竞争
                    pass
            return orig_serialize(value)

        monkeypatch.setattr(script_versions, "_canonical_slot_schema", _barrier_serialize)
        if retries is not None:
            monkeypatch.setattr(script_versions, "VERSION_NO_CONFLICT_RETRIES", retries)

        def _publish(i):
            try:
                return script_versions.create_version(
                    tenant_id, "user-1", lineage_id=lineage,
                    template=f"{template_prefix}{i}", source_job_name="x",
                )
            except Exception as exc:  # noqa: BLE001
                return exc

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(_publish, range(4)))

    def test_concurrent_same_lineage_publish_no_conflict(self, tenant_id, monkeypatch):
        """并发 4 线程同 lineage 发布（屏障同步：全员读到相同 MAX 后放行）：全部
        成功、版本号 2..5 恰好各一次——SAVEPOINT 重读重试吸收必然冲突。"""
        from src.boss_conversation import script_versions

        v1 = make_script_version(tenant_id, "基线版本")
        results = self._race_publish_threads(tenant_id, v1["lineage_id"], "并发版本", monkeypatch)
        errors = [r for r in results if isinstance(r, Exception)]
        assert errors == [], f"屏障竞争下重试应吸收全部冲突: {errors!r}"
        versions = sorted(int(r["version_no"]) for r in results)
        assert len(versions) == len(set(versions))  # 版本号无重复（UNIQUE + 重试生效）
        assert versions == [2, 3, 4, 5]  # 连续递增

    def test_concurrent_lineage_mutation_without_retry_fails(self, tenant_id, monkeypatch):
        """P2-2（八审）变异验证：同一屏障竞争场景下重试上限置 1（禁重试）——必须
        恰一方成功占据 version_no=2、三方受控 409——证明 SAVEPOINT 重读重试是
        承重逻辑（无重试则并发发布真实失败），裸 IntegrityError 不得外泄。"""
        from src.boss_conversation import script_versions
        from src.session_tasks.constants import SessionTaskError

        v1 = make_script_version(tenant_id, "基线版本")
        results = self._race_publish_threads(
            tenant_id, v1["lineage_id"], "变异并发版本", monkeypatch, retries=1
        )
        errors = [r for r in results if isinstance(r, Exception)]
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) == 1  # 恰一方占据 version_no=2
        assert len(errors) == 3  # 其余三方在无重试下必须失败
        for exc in errors:
            assert isinstance(exc, SessionTaskError), f"非受控异常: {exc!r}"
            assert exc.status_code == 409 and exc.code == "CONFLICT"
        assert int(successes[0]["version_no"]) == 2


class TestSlotSchemaNormalization:
    """P1（八审）：normalize_slot_schema 唯一入口——校验（槽位名/数量/BossSlotField
    字段）+ 补默认值（required=true、description=""，与发布契约 BossScriptRef 的
    Pydantic 默认一致）；create 存储与 publish 比较同函数同形态。此前只排序键：
    创建省略 description 的版本经 Pydantic 补全后 canonical 串不同 → 合法版本
    永远无法发布（本类回归锁定）。"""

    def test_normalize_fills_defaults_matching_pydantic(self):
        from src.boss_conversation.models import BossSlotField
        from src.boss_conversation.script_versions import normalize_slot_schema

        raw = {"expected_time": {"required": True}}
        field = BossSlotField.model_validate({"required": True})
        assert normalize_slot_schema(raw) == {
            "expected_time": {"required": field.required, "description": field.description}
        }
        # 与显式补全字段的形态完全一致（canonical 幂等）
        assert normalize_slot_schema(raw) == normalize_slot_schema(
            {"expected_time": {"required": True, "description": ""}}
        )
        # 空值/缺省槽位定义 → 全默认（required=true、description=""）
        assert normalize_slot_schema({"company": {}}) == {"company": {"required": True, "description": ""}}
        assert normalize_slot_schema(None) == {}

    def test_omitted_description_version_still_publishes(self, tenant_id, boss_binding):
        """创建省略 description（仅 required）→ 经规范化与 Pydantic 补全后的 spec
        同形态 → 正常发布（回归：修复前 canonical 不同导致发布必 409）。"""
        created, _confirmation, publish = _publish_task_with_schema(
            tenant_id, boss_binding, {"expected_time": {"required": True}}
        )
        publish()
        state = _task_state(tenant_id, created["task_id"])
        assert state["status"] == "active"
        assert _spec_revision_count(tenant_id, created["task_id"]) == 1

    def test_omitted_required_defaults_true_and_publishes(self, tenant_id, boss_binding):
        """创建省略 required → 默认 true 形态；spec 显式给 required=true（经 Pydantic
        补 description=""）→ 同形态发布通过。"""
        created, _confirmation, publish = _publish_task_with_schema(
            tenant_id, boss_binding, {"expected_time": {}},
            template="您好，{expected_time} 方便沟通吗？",
        )
        # 双确认：DB 存储已补全默认值
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT slot_schema FROM bs_boss_reply_script_versions WHERE tenant_id=%s",
                (tenant_id,),
            )
            stored = cursor.fetchone()["slot_schema"]
        assert stored == {"expected_time": {"required": True, "description": ""}}
        publish()
        state = _task_state(tenant_id, created["task_id"])
        assert state["status"] == "active"

    def test_invalid_slot_name_create_400(self, tenant_id):
        from src.boss_conversation.script_versions import normalize_slot_schema
        from src.session_tasks.constants import SessionTaskError

        for bad in ("Company", "1abc", "含中文", "a-b"):
            with pytest.raises(SessionTaskError) as exc:
                normalize_slot_schema({bad: {"required": True}})
            assert exc.value.status_code == 400 and exc.value.code == "VALIDATION_FAILED"

    def test_extra_field_and_bad_value_create_400(self, tenant_id):
        from src.boss_conversation.script_versions import normalize_slot_schema
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:  # extra=forbid
            normalize_slot_schema({"expected_time": {"required": True, "foo": 1}})
        assert exc.value.status_code == 400
        with pytest.raises(SessionTaskError) as exc:  # required=None 非 bool（lax 模式不接 None）
            normalize_slot_schema({"expected_time": {"required": None}})
        assert exc.value.status_code == 400
        with pytest.raises(SessionTaskError) as exc:  # description 类型错误
            normalize_slot_schema({"expected_time": {"description": {"nested": 1}}})
        assert exc.value.status_code == 400
        with pytest.raises(SessionTaskError) as exc:  # description 超长
            normalize_slot_schema({"expected_time": {"description": "x" * 201}})
        assert exc.value.status_code == 400

    def test_too_many_slots_create_400(self, tenant_id):
        from src.boss_conversation.script_versions import normalize_slot_schema
        from src.session_tasks.constants import SessionTaskError

        oversized = {f"s{i}": {"required": True} for i in range(21)}
        with pytest.raises(SessionTaskError) as exc:
            normalize_slot_schema(oversized)
        assert exc.value.status_code == 400

    def test_per_slot_none_definition_400(self, tenant_id):
        """非阻断 1（九审）：槽位定义 None 不再被 `or {}` 静默当空对象（发布契约
        BossSlotField 拒绝 None，两侧同形态）——受控 400。"""
        from src.boss_conversation.script_versions import normalize_slot_schema
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:
            normalize_slot_schema({"expected_time": None})
        assert exc.value.status_code == 400 and exc.value.code == "VALIDATION_FAILED"

    def test_unknown_template_placeholder_create_400(self, tenant_id):
        """模板占位符 ⊆ slot_schema 在创建时校验：未知占位符受控 400，不创建永远
        不能发布的死版本。"""
        from src.boss_conversation.script_versions import create_version
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:
            create_version(
                tenant_id, "user-1", template="您好{name}，方便沟通吗？",
                slot_schema={}, source_job_name="x",
            )
        assert exc.value.status_code == 400 and exc.value.code == "VALIDATION_FAILED"
        # 库中无残留
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_reply_script_versions WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0

    def test_normalize_via_pydantic_equivalence(self, tenant_id):
        """端到端形态对齐：create_version 传入的 raw schema 经 normalize 后，与
        spec 侧 BossScriptRef Pydantic 校验产物（model_dump plain dict）逐槽位一致。"""
        from src.boss_conversation.models import BossScriptRef
        from src.boss_conversation.script_versions import normalize_slot_schema

        raw = {"expected_time": {"required": True}, "company": {}}
        ref = BossScriptRef(
            script_version_id=str(uuid.uuid4()),
            content_hash=template_content_hash("您好{expected_time}，我是{company}招聘"),
            frozen_template="您好{expected_time}，我是{company}招聘",
            slot_schema=raw,
        )
        pydantic_plain = {k: v.model_dump() for k, v in ref.slot_schema.items()}
        assert normalize_slot_schema(raw) == pydantic_plain


class TestCreateEntrySchemaValidation:
    """非阻断 4（九审）：非法 schema 穿过 create_version 入口受控 400——防止创建
    入口漏接 normalize_slot_schema 校验（归一化函数单测之外的真实入口）。"""

    @pytest.mark.parametrize(
        "bad_schema",
        [
            {"Bad": {"required": True}},  # 非法槽位名（大写/连字符等）
            {"expected_time": {"required": True, "foo": 1}},  # 额外字段（extra=forbid）
            {"expected_time": None},  # 槽位定义 None（九审非阻断 1）
            {"expected_time": {"required": None}},  # 非法 required 类型
            {f"s{i}": {"required": True} for i in range(21)},  # 超 20 槽位上限
        ],
    )
    def test_create_version_rejects_illegal_schema(self, tenant_id, bad_schema):
        from src.boss_conversation.script_versions import create_version
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:
            create_version(
                tenant_id, "user-1",
                template="您好{expected_time}，方便沟通吗？",
                slot_schema=bad_schema, source_job_name="x",
            )
        assert exc.value.status_code == 400 and exc.value.code == "VALIDATION_FAILED"
        # 库中零残留
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_reply_script_versions WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0
