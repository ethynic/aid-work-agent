"""Durable in-app notices; no external channel dispatch."""
from . import service


DDL = """
CREATE TABLE IF NOT EXISTS session_task_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    control_epoch INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, task_id, control_epoch),
    FOREIGN KEY (tenant_id, task_id) REFERENCES session_tasks (tenant_id, id) ON DELETE CASCADE
)
"""


def record_notice(conn, tenant_id, task_id, status, reason, control_epoch):
    if status not in ("completed", "stopped", "human_required", "blocked"):
        return
    cursor = conn.cursor()
    cursor.execute("""INSERT INTO session_task_notifications
        (tenant_id,task_id,user_id,control_epoch,status,reason)
        SELECT tenant_id,id,user_id,%s,%s,%s FROM session_tasks WHERE tenant_id=%s AND id=%s
        ON CONFLICT (tenant_id,task_id,control_epoch) DO NOTHING""",
        (control_epoch, status, reason, tenant_id, task_id))


def list_notices(tenant_id, user_id, limit=20, offset=0):
    with service._conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS n FROM session_task_notifications WHERE tenant_id=%s AND user_id=%s", (tenant_id, user_id))
        total = cursor.fetchone()["n"]
        cursor.execute("""SELECT id,task_id,control_epoch,status,reason,created_at
            FROM session_task_notifications WHERE tenant_id=%s AND user_id=%s
            ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s""", (tenant_id, user_id, min(max(limit,1),100), max(offset,0)))
        return {"items": [dict(r) for r in cursor.fetchall()], "total": total}
