"""DDL 幂等（空库/既有库重复运行的一半：init 重复执行必须无错且结构不变）。"""

from src.session_tasks.init_tables import init_session_task_tables
from src.weixin_conversation.init_tables import init_weixin_conversation_tables


def test_init_tables_idempotent():
    """init_session_task_tables 连续执行两次无错（CREATE IF NOT EXISTS 幂等）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_session_task_tables(conn)
        init_session_task_tables(conn)
        init_weixin_conversation_tables(conn)
        init_weixin_conversation_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) AS c FROM session_tasks WHERE tenant_id='__none__'")
            assert cursor.fetchone()["c"] == 0


def test_expected_columns_present():
    """关键契约列存在（占用索引/双唯一/合成批次列等）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='session_tasks' AND column_name IN
                ('draft_digest','control_epoch','server_control_seq','spec_revision')
                """
            )
            cols = {r["column_name"] for r in cursor.fetchall()}
            assert cols == {"draft_digest", "control_epoch", "server_control_seq", "spec_revision"}
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename='session_tasks' AND indexname='idx_session_tasks_occupancy'
                """
            )
            assert cursor.fetchone() is not None
