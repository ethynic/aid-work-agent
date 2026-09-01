from contextlib import contextmanager
from pathlib import Path

from src.desktop_agent.gateway import InvocationRecord, PostgresGatewayStore
from src.desktop_agent.turn import PostgresTurnStore


class FakeCursor:
    def __init__(self):
        self.statements = []
        self.rowcount = 1
        self._rows = []

    def execute(self, sql, parameters=()):
        self.statements.append((" ".join(sql.split()), parameters))
        if "RETURNING id" in sql:
            row = {"id": 1}
        elif "RETURNING status" in sql:
            row = {"status": "cancelled" if "status='cancelled'" in sql else "running"}
        else:
            row = None
        self._rows.append(row)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def test_postgres_stores_commit_reservation_completion_and_ticket(monkeypatch):
    connections = []

    @contextmanager
    def fake_connection():
        connection = FakeConnection()
        connections.append(connection)
        yield connection

    monkeypatch.setattr("src.db.database.get_db_connection", fake_connection)
    gateway_store = PostgresGatewayStore()
    pending = InvocationRecord("tenant", "user", "digest", None, [])
    assert gateway_store.reserve("tenant", "key", pending) is None
    assert gateway_store.begin("tenant", "key", "user") == "running"
    assert gateway_store.cancel("tenant", "key", "user") == "cancelled"
    gateway_store.save("tenant", "key", InvocationRecord("tenant", "user", "digest", {"ok": True}, []))
    gateway_store.consume_ticket("ticket")
    turn_store = PostgresTurnStore()
    assert turn_store.reserve("tenant", "user", "turn-key", "turn-digest") is None
    turn_store.save("tenant", "user", "turn-key", "turn-digest", {"ok": True})

    assert len(connections) == 7
    assert all(connection.commits == 1 for connection in connections)
    assert "ON CONFLICT (tenant_id,idempotency_key) DO NOTHING RETURNING id" in connections[0].cursor_instance.statements[0][0]
    assert "status='running'" in connections[1].cursor_instance.statements[0][0]
    assert "status='cancelled'" in connections[2].cursor_instance.statements[0][0]
    assert "status='completed'" in connections[3].cursor_instance.statements[0][0]
    assert "INSERT INTO desktop_authorization_ticket_consumptions" in connections[4].cursor_instance.statements[0][0]


def test_postgres_claim_reserves_and_consumes_ticket_in_one_transaction(monkeypatch):
    connection = FakeConnection()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr("src.db.database.get_db_connection", fake_connection)
    record = InvocationRecord("tenant", "user", "digest", None, [{"seq": 1}])
    assert PostgresGatewayStore().claim("ticket", "tenant", "key", record) is None
    assert connection.commits == 1
    statements = [statement for statement, _parameters in connection.cursor_instance.statements]
    assert "INSERT INTO desktop_remote_tool_invocations" in statements[0]
    assert "INSERT INTO desktop_authorization_ticket_consumptions" in statements[1]


def test_postgres_turn_abandon_only_deletes_owned_pending_reservation(monkeypatch):
    connection = FakeConnection()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr("src.db.database.get_db_connection", fake_connection)
    PostgresTurnStore().abandon("tenant", "user", "key", "digest")
    statement, parameters = connection.cursor_instance.statements[0]
    assert statement.startswith("DELETE FROM desktop_agent_turn_requests")
    assert "status='pending' AND response_json IS NULL" in statement
    assert parameters == ("tenant", "user", "key", "digest")
    assert connection.commits == 1


def test_d1_ddl_has_repeat_safe_constraints_and_reservation_state():
    d1 = Path("deploy/desktop_agent_d1.sql").read_text(encoding="utf-8")
    assert d1.count("CREATE TABLE IF NOT EXISTS") == 3
    assert "CREATE INDEX IF NOT EXISTS" in d1
    assert d1.count("ADD COLUMN IF NOT EXISTS status") == 2
    assert d1.count("ALTER COLUMN response_json DROP NOT NULL") == 2
    assert d1.count("UNIQUE (tenant_id, idempotency_key)") == 2
    assert "status IN ('pending', 'running', 'cancelled', 'completed')" in d1
    assert "desktop_remote_tool_invocations" not in Path("deploy/db_update.yaml").read_text(encoding="utf-8")
    for script in ("agent_update.sh", "agent2_update.sh", "agent3_update.sh"):
        assert "desktop_agent_d1.sql" not in Path("deploy", script).read_text(encoding="utf-8")
