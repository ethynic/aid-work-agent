"""
网页端会话 API 集成测试（真实 PostgreSQL）

覆盖：
- GET /users 聚合（session_count / last_active_at 排序）+ keyword 用户名/昵称搜索
- 跨租户隔离（他租户用户会话不可见）
- GET /users/{id}/sessions 按 tenant+user 过滤 + subagent_id 精确筛选（含 master）+ subagent_name 反查
- GET /users/{id}/agents 会话出现过的智能体去重列表（下拉框选项）
- GET /sessions/{id}/messages 排除 tool 角色 + 跨租户访问会话 403
"""

import uuid

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant_for_web():
    """创建临时租户用于网页端会话测试，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]

    yield tenant_id

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_messages WHERE session_id IN "
                "(SELECT session_id FROM chat_sessions WHERE session_id LIKE 'wsess_%%')"
            )
            cursor.execute("DELETE FROM chat_sessions WHERE session_id LIKE 'wsess_%%'")
            cursor.execute("DELETE FROM users WHERE user_id LIKE 'webtest_%%'")
            cursor.execute("DELETE FROM subagent_definitions WHERE agent_id LIKE 'webtest_agent_%%'")
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


def _insert_user(tenant_id: str, user_id: str, username: str = None, nickname: str = None):
    """直接 SQL 写入 web 用户（source 为 NULL）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, tenant_id, username, nickname, source, role, status)
            VALUES (%s, %s, %s, %s, NULL, 'user', 'active')
            """,
            (user_id, tenant_id, username or user_id, nickname or username or user_id),
        )
        conn.commit()
    return user_id


def _insert_chat_session(session_id: str, user_id: str, tenant_id: str,
                         subagent_id: str = None, title: str = None):
    """直接 SQL 写入 web 会话"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chat_sessions (session_id, user_id, tenant_id, subagent_id, title)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, user_id, tenant_id, subagent_id, title),
        )
        conn.commit()
    return session_id


def _insert_chat_message(message_id: str, session_id: str, role: str, content: str):
    """直接 SQL 写入 web 消息"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chat_messages (message_id, session_id, role, content)
            VALUES (%s, %s, %s, %s)
            """,
            (message_id, session_id, role, content),
        )
        conn.commit()


def _insert_subagent(agent_id: str, name: str):
    """直接 SQL 写入智能体定义"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO subagent_definitions (agent_id, name) VALUES (%s, %s)",
            (agent_id, name),
        )
        conn.commit()


def _call(fn, *args, **kwargs):
    """在补丁 require_admin 下同步调用异步 API 函数"""
    import asyncio
    # Own the loop: preceding pytest-asyncio cases may have closed and cleared
    # the thread's default loop. API assertions must not depend on test order.
    return asyncio.run(fn(*args, **kwargs))


class FakeRequest:
    pass


def _fake_admin(tenant_id):
    def fake_require_admin(request):
        return {"user_id": "admin_x", "role": "platform_admin", "tenant_id": tenant_id}
    return fake_require_admin


class TestListWebSessionUsers:
    """GET /users 聚合与搜索"""

    def test_aggregation_and_order(self, temp_tenant_for_web):
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        user_a = _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="员工A", nickname="张三")
        user_b = _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="员工B")
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id)
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id)
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_b, tenant_id)

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            resp = _call(web_sessions.list_web_session_users, FakeRequest(), page=1, page_size=20)

        assert resp["success"] is True
        users = {u["user_id"]: u for u in resp["users"]}
        assert users[user_a]["session_count"] == 2
        assert users[user_b]["session_count"] == 1
        assert users[user_a]["last_active_at"] is not None
        # 最近活跃倒序：user_b 最后插入一条会话，其 updated_at 应 >= user_a 的最新
        assert resp["users"][0]["user_id"] == user_b

    def test_keyword_matches_username_and_nickname(self, temp_tenant_for_web):
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        user_a = _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="zhangsan", nickname="张三")
        _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="lisi")
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id)

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            resp_kw = _call(web_sessions.list_web_session_users, FakeRequest(), keyword="张三")
            resp_name = _call(web_sessions.list_web_session_users, FakeRequest(), keyword="zhang")
            resp_none = _call(web_sessions.list_web_session_users, FakeRequest(), keyword="不存在的名字xyz")

        assert [u["user_id"] for u in resp_kw["users"]] == [user_a]
        assert [u["user_id"] for u in resp_name["users"]] == [user_a]
        assert resp_none["users"] == []

    def test_tenant_isolation(self, temp_tenant_for_web):
        """他租户的用户会话不可见"""
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        other_tenant = f"other_tenant_{uuid.uuid4().hex[:6]}"
        other_user = _insert_user(other_tenant, f"webtest_{uuid.uuid4().hex[:6]}", username="别家员工")
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", other_user, other_tenant)

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            resp = _call(web_sessions.list_web_session_users, FakeRequest(), page=1, page_size=50)

        assert all(u["user_id"] != other_user for u in resp["users"])

        # 清理他租户数据
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_sessions WHERE user_id = %s", (other_user,))
            cursor.execute("DELETE FROM users WHERE user_id = %s", (other_user,))
            conn.commit()


class TestGetUserWebSessions:
    """GET /users/{user_id}/sessions"""

    def test_sessions_filter_and_subagent_id(self, temp_tenant_for_web):
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        agent_id = f"webtest_agent_{uuid.uuid4().hex[:6]}"
        _insert_subagent(agent_id, "售前助手")
        user_a = _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="员工A")
        sess_agent = _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id,
                                          subagent_id=agent_id, title="售前咨询")
        sess_master = _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id,
                                           title="随便聊聊")

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            resp_all = _call(web_sessions.get_user_web_sessions, FakeRequest(), user_id=user_a)
            resp_agent = _call(web_sessions.get_user_web_sessions, FakeRequest(),
                               user_id=user_a, subagent_id=agent_id)
            resp_master = _call(web_sessions.get_user_web_sessions, FakeRequest(),
                                user_id=user_a, subagent_id="master")
            resp_miss = _call(web_sessions.get_user_web_sessions, FakeRequest(),
                              user_id=user_a, subagent_id="webtest_agent_not_exist")

        # 全量：两条会话，NULL subagent 也返回（前端显示主智能体）
        assert resp_all["total"] == 2
        sessions = {s["session_id"]: s for s in resp_all["sessions"]}
        assert sessions[sess_agent]["subagent_name"] == "售前助手"
        assert "subagent_name" not in sessions[sess_master] or sessions[sess_master]["subagent_name"] is None

        # 按智能体 ID 精确筛选：只命中智能体会话
        assert [s["session_id"] for s in resp_agent["sessions"]] == [sess_agent]

        # master 筛选：只命中主智能体会话
        assert [s["session_id"] for s in resp_master["sessions"]] == [sess_master]

        # 筛选无匹配智能体：空列表
        assert resp_miss["sessions"] == [] and resp_miss["total"] == 0

    def test_agents_dedup_list(self, temp_tenant_for_web):
        """GET /users/{id}/agents 返回会话出现过的智能体去重列表，master 排最前"""
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        agent_a = f"webtest_agent_{uuid.uuid4().hex[:6]}"
        agent_b = f"webtest_agent_{uuid.uuid4().hex[:6]}"
        _insert_subagent(agent_a, "售前助手")
        _insert_subagent(agent_b, "数据分析专家")
        user_a = _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="员工A")
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id,
                             subagent_id=agent_a, title="s1")
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id,
                             subagent_id=agent_a, title="s2")
        _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id, title="s3")

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            resp = _call(web_sessions.list_user_web_session_agents, FakeRequest(), user_id=user_a)

        assert resp["success"] is True
        agents = {a["agent_id"]: a for a in resp["agents"]}
        assert set(agents.keys()) == {"master", agent_a}
        assert agents["master"]["agent_name"] == "主智能体"
        assert agents[agent_a]["agent_name"] == "售前助手"
        assert agents[agent_a]["session_count"] == 2
        assert resp["agents"][0]["agent_id"] == "master"

        # 从未产生会话的智能体不出现在列表中
        assert agent_b not in agents

        # 租户隔离：他租户管理员查询同一用户，看不到本租户会话的智能体（返回空列表）
        other_tenant = f"other_tenant_{uuid.uuid4().hex[:6]}"
        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(other_tenant)):
            resp_other = _call(web_sessions.list_user_web_session_agents, FakeRequest(), user_id=user_a)
        assert resp_other["success"] is True
        assert resp_other["agents"] == []

    def test_user_tenant_check(self, temp_tenant_for_web):
        """用户不属于当前租户时 404"""
        from fastapi import HTTPException
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        other_user = f"webtest_{uuid.uuid4().hex[:6]}"

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            with pytest.raises(HTTPException) as exc_info:
                _call(web_sessions.get_user_web_sessions, FakeRequest(), user_id=other_user)

        assert exc_info.value.status_code == 404


class TestGetWebSessionMessages:
    """GET /sessions/{session_id}/messages"""

    def test_messages_exclude_tool_role(self, temp_tenant_for_web):
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        user_a = _insert_user(tenant_id, f"webtest_{uuid.uuid4().hex[:6]}", username="员工A")
        sess = _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", user_a, tenant_id)
        _insert_chat_message(f"wmsg_{uuid.uuid4().hex[:8]}", sess, "user", "你好")
        _insert_chat_message(f"wmsg_{uuid.uuid4().hex[:8]}", sess, "assistant", "您好，有什么可以帮您？")
        _insert_chat_message(f"wmsg_{uuid.uuid4().hex[:8]}", sess, "tool", "工具调用结果")

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            resp = _call(web_sessions.get_web_session_messages, FakeRequest(), session_id=sess)

        assert resp["success"] is True
        assert resp["total"] == 2
        roles = [m["role"] for m in resp["messages"]]
        assert "tool" not in roles
        # 按时间正序
        assert roles == ["user", "assistant"]

    def test_cross_tenant_session_403(self, temp_tenant_for_web):
        """访问他租户会话返回 403"""
        from fastapi import HTTPException
        from src.saas.api import web_sessions

        tenant_id = temp_tenant_for_web
        other_tenant = f"other_tenant_{uuid.uuid4().hex[:6]}"
        sess = _insert_chat_session(f"wsess_{uuid.uuid4().hex[:8]}", "anyone", other_tenant)

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(tenant_id)):
            with pytest.raises(HTTPException) as exc_info:
                _call(web_sessions.get_web_session_messages, FakeRequest(), session_id=sess)

        assert exc_info.value.status_code == 403

    def test_session_not_found_404(self, temp_tenant_for_web):
        from fastapi import HTTPException
        from src.saas.api import web_sessions

        with patch("src.saas.api.web_sessions.require_admin", _fake_admin(temp_tenant_for_web)):
            with pytest.raises(HTTPException) as exc_info:
                _call(web_sessions.get_web_session_messages, FakeRequest(), session_id="wsess_not_exist")

        assert exc_info.value.status_code == 404
