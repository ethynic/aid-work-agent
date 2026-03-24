import sys
sys.path.insert(0, 'c:/repos/aid-work-agent')

from src.db.database import get_db_connection

with get_db_connection() as conn:
    cursor = conn.cursor()

    # 查看 chat_messages 表
    print("=== chat_messages 表 ===")
    cursor.execute("SELECT message_id, session_id, role, content, created_at FROM chat_messages ORDER BY created_at DESC LIMIT 20")
    rows = cursor.fetchall()
    print(f"总记录数: {len(rows)}")
    for row in rows:
        content = row[3][:50] if row[3] else ""
        print(f"  session_id={row[1]}, role={row[2]}, content={content}...")

    # 查看具体的会话消息
    print("\n=== session_b73732fbc485 的消息 ===")
    cursor.execute("SELECT message_id, role, content, created_at FROM chat_messages WHERE session_id = 'session_b73732fbc485' ORDER BY created_at")
    msgs = cursor.fetchall()
    print(f"消息数: {len(msgs)}")
    for m in msgs:
        print(f"  role={m[1]}, content={m[2][:100]}...")