"""C3 真实进程联测（计划 §6 验收要求）：隔离真实测试 DB + 真实 Runtime **子进程** + fake Provider。

链路全部真实，仅模型与微信桌面为受控 fake：
- 服务端：真实 FastAPI 路由（session-tasks 设备根 + local-tools 运行时根）+
  真实测试 DB；决策 worker 由本测试进程以 fake 模型驱动（等价 fake Provider 口径）。
- Runtime：node dist/src/cli.js 子进程（真实 engine/invocationRunner/permits 链/
  journal fsync/result outbox/DPAPI 凭证），Provider 为 tests/helpers/liveWeixinStub.mjs。
- 覆盖：claim→观察→合批→events→决策→prepare-send→定向 claim→write-authorize→
  journal→发送→operation-result→delivery succeeded；进程终止恢复（invocation
  queued 后 kill，重启接续原 invocation 恰好发送一次）。
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "clients" / "agent-tool-runtime"

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def _db_env():
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    db_url = os.getenv("DATABASE_URL", "")
    if "postgresql" not in db_url:
        pytest.skip("真实进程联测需要 PostgreSQL DATABASE_URL")
    os.environ.setdefault("DB_POOL_MIN", "2")
    os.environ.setdefault("DB_POOL_MAX", "6")
    os.environ.setdefault("RPA_SECRET_KEY", "test-session-tasks-master-key-32bytes")
    from src.db.database import init_postgres_pool, get_postgres_pool

    if get_postgres_pool() is None:
        init_postgres_pool()
    from src.session_tasks.init_tables import init_session_task_tables
    from src.weixin_conversation.init_tables import init_weixin_conversation_tables
    from src.desktop_automation.init_tables import init_desktop_automation_tables
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_desktop_automation_tables(conn)
        init_session_task_tables(conn)
        init_weixin_conversation_tables(conn)
    yield


@pytest.fixture(scope="session")
def live_server(_db_env):
    """真实 FastAPI 路由 + 真实 DB 的 uvicorn 服务器（session-tasks 设备根 + local-tools 运行时根）。"""
    import uvicorn
    from fastapi import FastAPI

    from src.session_tasks.api import device_router
    from src.local_tools.api import router as local_tools_router

    app = FastAPI()
    app.include_router(device_router)
    app.include_router(local_tools_router)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.2)
    else:
        pytest.fail("uvicorn 未在期限内就绪")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def live_tenant(_db_env, monkeypatch):
    """随机租户 + 门控放行（真实 service 路径）+ 测后清理。"""
    import src.session_tasks.service as service_mod
    import src.session_tasks.config as st_config
    import src.weixin_conversation.config as wx_config
    import src.session_tasks.decisions as decisions_mod
    from src.session_tasks.config import SessionTasksConfig
    from dataclasses import replace

    tenant_id = f"st_live_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(service_mod, "tenant_allowed", lambda t: True)
    monkeypatch.setattr(st_config, "tenant_allowed", lambda t: True)
    monkeypatch.setattr(wx_config, "scenario_enabled", lambda t, **k: True)
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: SessionTasksConfig(enabled=True))
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda t: True)
    monkeypatch.setattr("src.services.session_record.record_background_llm_usage", lambda usage, **kw: None)
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    import src.weixin_conversation.registration as registration

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: SessionTasksConfig(enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    registration.ensure_registered()
    yield tenant_id
    registration.reset_registration()
    from src.db.database import get_db_connection
    from tests.unit.session_tasks.conftest import SESSION_TASK_TABLES, _cleanup_tenant

    if not os.environ.get("ST_LIVE_DEBUG"):
        _cleanup_tenant(tenant_id)
    else:
        print(f"[ST_LIVE_DEBUG] tenant kept: {tenant_id}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for table in (
            "local_tool_invocations",
            "local_tool_operation_permits",
            "desktop_automation_attempts",
            "desktop_automation_evidence",
            "desktop_automation_deliveries",
            "desktop_automation_runs",
            "desktop_automation_occurrences",
            "desktop_automation_outbox",
            "desktop_automation_events",
            "desktop_automation_quota_buckets",
            "desktop_automation_audit_events",
            "local_tool_events",
        ):
            try:
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (tenant_id,))
            except Exception:  # noqa: BLE001
                conn.rollback()
        conn.commit()


@pytest.fixture()
def live_device(live_tenant):
    """真实设备行（能力按 runtime 真实上报口径）+ token。"""
    from src.local_tools.repository import create_device
    from src.local_tools.security import sha256_hex

    token = f"live-tok-{uuid.uuid4().hex}"
    device = create_device(
        live_tenant, "user-1", token_hash=sha256_hex(token), name="live-runtime",
        capabilities={
            "providers": ["weixin"],
            "protocol_version": 2,
            "capabilities": ["local_v2", "session_task_v1", "session_observer_v1", "weixin_message_send_v2"],
            "provider_manifests": {
                "weixin": {"provider_id": "ai.aidwork.weixin", "manifest_digest": "x" * 64, "protocol_version": 2}
            },
        },
    )
    return {"device_id": str(device["id"]), "token": token}


@pytest.fixture()
def live_binding(live_tenant, live_device, monkeypatch):
    import src.weixin_conversation.config as wx_config
    from src.weixin_conversation.bindings import create_binding
    from src.db.database import get_db_connection

    monkeypatch.setattr(wx_config, "scenario_enabled", lambda t, **k: True)
    account = str(uuid.uuid4())
    binding = create_binding(live_tenant, "user-1", live_device["device_id"], account, "direct", label="live")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_weixin_conversation_bindings
            SET verification_status='verified', verifier_version='t', identity_version=1,
                verified_at=CURRENT_TIMESTAMP, expires_at=%s
            WHERE tenant_id=%s AND id=%s
            """,
            (datetime.now(timezone.utc) + timedelta(days=1), live_tenant, binding["conversation_binding_id"]),
        )
        conn.commit()
    return {"conversation_binding_id": binding["conversation_binding_id"], "account_binding_id": account}


class RuntimeProcess:
    def __init__(self, home: str, server_url: str, device_id: str, token: str, stub_home: str = None):
        self.home = home
        self.stub_home = stub_home
        self.proc = None
        self.log = Path(home) / "runtime-stdout.log"
        (Path(home) / "config.json").write_text(
            json.dumps({
                "server": server_url,
                "device_id": device_id,
                "sessionTasks": True,
                "providers": {"weixin": {"entry": str(RUNTIME_DIR / "tests" / "helpers" / "liveWeixinStub.mjs"), "v2Send": True}},
            }, indent=2),
            encoding="utf-8",
        )
        # 凭证经真实 saveDeviceToken（DPAPI CurrentUser）写入 credentials.bin
        # Windows 绝对路径须经 file:// URL 导入
        cred_url = (RUNTIME_DIR / "dist" / "src" / "credentials.js").as_uri()
        subprocess.run(
            ["node", "-e",
             f"import({json.dumps(cred_url)})"
             f".then(m => m.saveDeviceToken({json.dumps(token)}))"
             f".catch(e => {{ console.error(e); process.exit(1); }})"],
            env={**os.environ, "AIDWORK_RUNTIME_HOME": home},
            cwd=str(RUNTIME_DIR), check=True, capture_output=True, timeout=60,
        )

    def start(self):
        log_fh = open(self.log, "ab")
        # CREATE_NEW_PROCESS_GROUP：子进程不共享控制台信号组（kill/中断不串扰
        # 测试进程控制台）；stdout 重定向文件，stdin 关闭
        self.proc = subprocess.Popen(
            ["node", str(RUNTIME_DIR / "dist" / "src" / "cli.js"), "start"],
            cwd=str(RUNTIME_DIR),
            env={**os.environ, "AIDWORK_RUNTIME_HOME": self.home,
                 **({"STUB_HOME": self.stub_home} if self.stub_home else {"STUB_HOME": self.home})},
            stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        return self.proc

    def kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=15)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)


@pytest.fixture()
def runtime_factory(live_server, live_device):
    homes = []
    stubs = []
    procs = []

    def _make(stub_home: str = None, home: str = None):
        # home 缺省新建；重启恢复场景显式复用原 home（生产语义：runtime home 固定，
        # 孤儿 assignment 扫描 + renew 接续依赖同一 home 的本地日志与 meta）
        home = home or tempfile.mkdtemp(prefix="st-live-runtime-")
        if home not in homes:
            homes.append(home)
        if stub_home:
            stubs.append(stub_home)
        rp = RuntimeProcess(home, live_server, live_device["device_id"], live_device["token"], stub_home=stub_home)
        rp.start()
        procs.append(rp)
        return rp

    yield _make
    for rp in procs:
        rp.kill()
    if not os.environ.get("ST_LIVE_DEBUG"):
        for home in homes:
            shutil.rmtree(home, ignore_errors=True)
    else:
        print(f"[ST_LIVE_DEBUG] runtime homes kept: {homes}")


def _stub_state(home: str, binding_id: str, messages):
    Path(home, "observe-state.json").write_text(
        json.dumps({
            "binding_id": binding_id, "binding_version": 0, "account_identity_version": 1,
            "messages": [{"sender": s, "text": t, "id": i} for s, t, i in messages],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _sends(home: str):
    f = Path(home, "sends.jsonl")
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_for(predicate, timeout_s, what):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    pytest.fail(f"等待超时: {what}")


def _publish_task(live_tenant, live_device, live_binding):
    """发布任务（不代领——claim 必须由真实 Runtime 进程完成，含能力检查）。"""
    from src.session_tasks import service
    from src.session_tasks.models import TaskDraftCreatePayload
    from tests.unit.session_tasks.conftest import build_spec

    spec = build_spec()
    payload = TaskDraftCreatePayload.model_validate({
        "scenario_key": "weixin.conversation.v1",
        "device_id": live_device["device_id"],
        "account_binding_id": live_binding["account_binding_id"],
        "conversation_binding_id": live_binding["conversation_binding_id"],
        "spec": spec,
    })
    created = service.create_draft(live_tenant, "user-1", payload)
    confirmation = service.issue_publish_confirmation(live_tenant, "user-1", uuid.UUID(created["task_id"]), created["version"])
    service.publish_task(live_tenant, "user-1", uuid.UUID(created["task_id"]), created["version"], uuid.UUID(confirmation["confirmation_id"]))
    return created["task_id"]


def _current_assignment(live_tenant, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.id AS assignment_id, a.fence, t.control_epoch, t.spec_revision, t.id AS task_id
            FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
            WHERE a.tenant_id=%s AND a.task_id=%s AND a.is_current=TRUE
            """,
            (live_tenant, task_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _wait_runtime_claim(live_tenant, task_id, timeout_s=90):
    _wait_for(lambda: _current_assignment(live_tenant, task_id) is not None, timeout_s, "真实 Runtime 进程完成任务领取（claim）")
    return _current_assignment(live_tenant, task_id)


def _baseline_synced(live_tenant, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM session_task_events WHERE tenant_id=%s AND task_id=%s AND event_type='baseline'",
            (live_tenant, task_id),
        )
        return cursor.fetchone() is not None


def _pending_decision(live_tenant, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, status FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND decision_kind='reply' ORDER BY created_at LIMIT 1",
            (live_tenant, task_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _decision_row(live_tenant, decision_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM session_task_decisions WHERE tenant_id=%s AND id=%s", (live_tenant, decision_id))
        return dict(cursor.fetchone())


def _task_links(live_tenant, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT l.decision_id, l.invocation_id, d.state AS delivery_state
            FROM session_task_execution_links l
            LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
            WHERE l.tenant_id=%s AND l.task_id=%s
            """,
            (live_tenant, task_id),
        )
        return [dict(r) for r in cursor.fetchall()]


class TestLiveRuntimeFullChain:
    def test_full_chain_and_process_kill_recovery(self, live_tenant, live_device, live_binding, runtime_factory):
        # 初始剧本必须先于进程启动落盘：首个成功观察把当时全部消息锚为历史基线
        stub_home = tempfile.mkdtemp(prefix="st-live-stub-")
        _stub_state(stub_home, live_binding["conversation_binding_id"], [("peer", "历史消息", "m0")])
        task_id = _publish_task(live_tenant, live_device, live_binding)
        rp = runtime_factory(stub_home=stub_home)
        # 真实 Runtime 进程领取（claim 能力检查走真实设备能力上报口径）
        _wait_runtime_claim(live_tenant, task_id)
        # 等基线事件真实落库（m0 锚定为历史）——之后才推进剧本，杜绝首观察竞态
        _wait_for(lambda: _baseline_synced(live_tenant, task_id), 60, "基线事件同步至云端")
        # 新消息（Runtime 自行观察/合批/同步/建决策，全真实 API）
        _stub_state(stub_home, live_binding["conversation_binding_id"], [
            ("peer", "历史消息", "m0"), ("peer", "在吗", "m1"),
        ])
        _wait_for(
            lambda: _pending_decision(live_tenant, task_id) is not None,
            60, "Runtime 观察合批并经真实 API 创建决策",
        )
        decision_id = _pending_decision(live_tenant, task_id)["id"]
        # 云端决策 worker（fake 模型——等价 fake Provider 口径；预算/许可链全真实）
        from src.session_tasks import decisions as decisions_mod

        decisions_mod.run_decision_tick(
            model_call=lambda m, t: {"content": json.dumps({"action": "reply", "reply_text": "您好，周五14:00可以吗"}, ensure_ascii=False),
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "live"}
        )
        _wait_for(lambda: _decision_row(live_tenant, decision_id)["status"] == "ready", 30, "决策 ready")
        # prepare-send 由 Runtime 执行（send_ready → executing → 定向 claim → 真实 v2 链）
        _wait_for(
            lambda: _invocation_state(live_tenant, task_id) in ("queued", "claimed", "running"),
            60, "prepare-send 物化并被领取",
        )
        # 进程终止恢复：invocation 在途即 kill，重启后按原 invocation 接续
        rp.kill()
        # 复用同一 runtime home（生产固定路径语义）：孤儿 assignment 扫描 → renew
        # 接续原 assignment（租约未过期）→ 回放本地日志恢复 executing → 原 invocation
        rp2 = runtime_factory(stub_home=stub_home, home=rp.home)
        _wait_for(
            lambda: _delivery_state(live_tenant, task_id) == "succeeded",
            120, "重启后完成发送（delivery succeeded）",
        )
        sends = _sends(stub_home)
        assert len(sends) >= 1, "fake Provider 应记录发送"
        assert len({s["request_id"] for s in sends}) == len(sends), "同一发送不重复执行（request_id 唯一）"
        # 不重建执行单元：单 invocation 单 link
        links = _task_links(live_tenant, task_id)
        assert len(links) == 1 and links[0]["invocation_id"], "单一执行链接"
        rp2.stop()


def _invocation_state(live_tenant, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.state FROM local_tool_invocations i
            JOIN session_task_execution_links l ON l.tenant_id=i.tenant_id AND l.invocation_id=i.id
            WHERE i.tenant_id=%s AND l.task_id=%s LIMIT 1
            """,
            (live_tenant, task_id),
        )
        row = cursor.fetchone()
        return row["state"] if row else None


def _delivery_state(live_tenant, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.state FROM desktop_automation_deliveries d
            JOIN session_task_execution_links l ON l.tenant_id=d.tenant_id AND l.delivery_id=d.id
            WHERE d.tenant_id=%s AND l.task_id=%s LIMIT 1
            """,
            (live_tenant, task_id),
        )
        row = cursor.fetchone()
        return row["state"] if row else None
