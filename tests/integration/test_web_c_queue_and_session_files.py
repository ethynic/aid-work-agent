"""web_c FIFO 排队原子落库 + 会话历史文件清单 SQL 集成测试（真实 PG 生产同构 schema）。

通过 docker compose -f docker-compose.test-db.yml run --rm sql 运行。
覆盖：
1. HistoryRepository.read_session_files / find_session_file 在真实 channel_messages 与
   chat_messages 表结构下正确返回 user 上传与 assistant 生成交付物，chat_messages 不查询
   不存在的 attachments 列。
2. persist_web_c_turn_in_tx 在同一 PG 事务内幂等写入延迟 user 与 completed assistant（含
   PDF downloadableFiles 附件），第二条排队消息在第一条 finalize 后完整读到
   [user1, assistant1] 与 PDF file_id，最终会话历史严格保持 [user1, assistant1, user2, assistant2]。
"""

import json
import uuid

import pytest

from src.core.agent_engine.contracts import Identity
from src.db.database import get_db_connection
from src.saas.services.web_c_runner_persist import (
    persist_runner_result,
    persist_web_c_turn_in_tx,
)
from src.services.agent_runner.runtime.history_repository import HistoryRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_sessions():
    """使用测试库既有生产同构 schema，仅写入本用例唯一前缀数据并在 teardown 按 ID 清理。"""
    suffix = uuid.uuid4().hex[:10]
    tenant = f"tenant_wq_{suffix}"
    channel_session = f"{tenant}_web_c_ext_lisi_sales"
    web_session = f"sess_web_manifest_{suffix}"
    user_id = f"user_{suffix}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_database() AS name")
        assert cursor.fetchone()["name"].startswith("aid_test_"), "Disposable test DB required"
        cursor.execute(
            """
            INSERT INTO channel_sessions (session_id, tenant_id, channel_type, channel_user_id, subagent_id)
            VALUES (%s, %s, 'web_c', 'ext_lisi', 'sales')
            """,
            (channel_session, tenant),
        )
        cursor.execute(
            """
            INSERT INTO chat_sessions (session_id, tenant_id, user_id, title)
            VALUES (%s, %s, %s, 'web test')
            """,
            (web_session, tenant, user_id),
        )
        conn.commit()
    try:
        yield {
            "tenant": tenant,
            "channel_session": channel_session,
            "web_session": web_session,
            "user_id": user_id,
        }
    finally:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM channel_messages WHERE session_id = %s", (channel_session,))
            cursor.execute("DELETE FROM channel_sessions WHERE session_id = %s", (channel_session,))
            cursor.execute("DELETE FROM chat_messages WHERE session_id = %s", (web_session,))
            cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (web_session,))
            conn.commit()


def test_web_c_atomic_turn_persistence_and_cross_turn_pdf_visibility(isolated_sessions):
    """验证 web_c 排队两轮严格有序落库 + 下一轮能查到上一轮助手生成的 PDF 交付物。"""
    tenant = isolated_sessions["tenant"]
    channel_session = isolated_sessions["channel_session"]
    uid_prefix = uuid.uuid4().hex[:8]
    msg_u1 = f"msg_u1_{uid_prefix}"
    r1_id = f"runner_turn_1_{uid_prefix}"
    r2_id = f"runner_turn_2_{uid_prefix}"

    runner_1 = {
        "runner_id": r1_id,
        "source": "web_c",
        "tenant_id": tenant,
        "session_id": channel_session,
        "client_request_id": f"wc_req_turn_1_{uid_prefix}",
        "input": {
            "text": "帮我出济南锐联的专精特新诊断报告",
            "attachments": [
                {"type": "file", "file_id": "file_u11122233344", "name": "企业基础表.xlsx", "size": 2048}
            ],
        },
    }
    snapshot_1 = {
        "output": "已为您生成诊断评估报告。",
        "downloadableFiles": [
            {
                "file_id": "file_1fba1c4a843e",
                "file_name": "国数科技_专精特新诊断评估报告.pdf",
                "mime_type": "application/pdf",
                "file_size": 286944,
            }
        ],
    }
    result_1 = {"status": "completed", "output": "已为您生成诊断评估报告。"}

    # 模拟第一轮在 /chat 已写入用户消息，随后 finalize 事务幂等补写 assistant
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO channel_messages
            (message_id, session_id, tenant_id, role, content, message_type, attachments, metadata)
            VALUES (%s, %s, %s, 'user', %s, 'text', %s::jsonb, %s::jsonb)
            """,
            (
                msg_u1, channel_session, tenant, "帮我出济南锐联的专精特新诊断报告",
                json.dumps(runner_1["input"]["attachments"], ensure_ascii=False),
                json.dumps({"client_request_id": runner_1["client_request_id"]}),
            ),
        )
        persist_web_c_turn_in_tx(cur, runner_1, result_1, snapshot_1)
        conn.commit()

    # 重复调用代理层 persist_runner_result 必须幂等不双写
    persist_runner_result(tenant, channel_session, {
        "runner_id": r1_id,
        "status": "completed",
        "client_request_id": runner_1["client_request_id"],
        "input": runner_1["input"],
        "snapshot": snapshot_1,
        "result": result_1,
    })

    # 模拟第二轮（排队消息）开始执行前，读取会话文件清单：必须同时看见用户附件和助手 PDF
    repo = HistoryRepository(Identity(tenant, "u1", channel_session, "web_c", "channel"))
    files = repo.read_session_files(channel_session)
    assert [f["file_id"] for f in files] == ["file_u11122233344", "file_1fba1c4a843e"]
    assert files[0]["origin"] == "user_upload"
    assert files[1]["origin"] == "assistant_generated"
    assert files[1]["name"] == "国数科技_专精特新诊断评估报告.pdf"
    found_pdf = repo.find_session_file(channel_session, "file_1fba1c4a843e")
    assert found_pdf is not None and found_pdf["origin"] == "assistant_generated"

    # 第二轮延迟用户消息 + 助手回复在同一 finalize 事务内原子落库
    runner_2 = {
        "runner_id": r2_id,
        "source": "web_c",
        "tenant_id": tenant,
        "session_id": channel_session,
        "client_request_id": f"wc_req_turn_2_{uid_prefix}",
        "input": {"text": "把刚才的 PDF 改一下落款日期", "attachments": []},
    }
    with get_db_connection() as conn:
        cur = conn.cursor()
        persist_web_c_turn_in_tx(
            cur, runner_2,
            {"status": "completed", "output": "已基于原报告更新落款日期。"},
            {"output": "已基于原报告更新落款日期。"},
        )
        conn.commit()

    # 验证最终 4 条消息严格按 [user1, assistant1, user2, assistant2] 顺序且无重复
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content FROM channel_messages WHERE session_id = %s ORDER BY id ASC",
            (channel_session,),
        )
        rows = [(r["role"], r["content"]) for r in cur.fetchall()]
    assert rows == [
        ("user", "帮我出济南锐联的专精特新诊断报告"),
        ("assistant", "已为您生成诊断评估报告。"),
        ("user", "把刚才的 PDF 改一下落款日期"),
        ("assistant", "已基于原报告更新落款日期。"),
    ]


def test_web_chat_messages_read_session_files_real_pg(isolated_sessions):
    """验证 chat_messages 真实表结构（无 attachments 列）下 read_session_files 正常解析 metadata。"""
    tenant = isolated_sessions["tenant"]
    web_session = isolated_sessions["web_session"]
    user_id = isolated_sessions["user_id"]
    uid_prefix = uuid.uuid4().hex[:8]
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chat_messages (message_id, session_id, role, content, metadata)
            VALUES
            (%s, %s, 'user', '请看附件', %s::jsonb),
            (%s, %s, 'assistant', '已生成报告', %s::jsonb)
            """,
            (
                f"cm_1_{uid_prefix}", web_session,
                json.dumps({"attachments": [{"file_id": "file_webu11112222", "name": "输入.xlsx", "size": 512}]}),
                f"cm_2_{uid_prefix}", web_session,
                json.dumps({"downloadableFiles": [{"file_id": "file_weba33334444", "file_name": "输出.pdf", "file_size": 4096}]}),
            ),
        )
        conn.commit()

    repo = HistoryRepository(Identity(tenant, user_id, web_session, "chat", "web"))
    files = repo.read_session_files(web_session)
    assert [f["file_id"] for f in files] == ["file_webu11112222", "file_weba33334444"]
    assert files[0]["origin"] == "user_upload"
    assert files[1]["origin"] == "assistant_generated"
    assert files[1]["name"] == "输出.pdf"
