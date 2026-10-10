"""Current trade-customer script contracts; mock only the database boundary.

These tests execute production functions, without PostgreSQL or CLI log writes.
They check SQL parameters and results, not database constraints or tenant ACLs.
"""

import importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "src/skills/trade-customer-1.0.0/scripts/customer_manager.py"
)
spec = importlib.util.spec_from_file_location("trade_customer_manager_under_test", SCRIPT)
customer_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(customer_manager)

pytestmark = [pytest.mark.unit, pytest.mark.skills]


@pytest.fixture
def db(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.__exit__.return_value = False
    get_connection = MagicMock(return_value=connection)
    monkeypatch.setattr(customer_manager, "get_db_connection", get_connection)
    return get_connection, connection


def _sql(connection, index=0):
    call = connection.execute.call_args_list[index]
    return " ".join(call.args[0].split()), call.args[1]


def test_save_customers_maps_fields_and_commits_batch(db):
    _, connection = db
    customers = [
        {
            "company_name": "O'Reilly; DROP TABLE customers; --",
            "contact_person": "Alice",
            "email": "alice@example.test",
            "country": "US",
            "language": "en",
            "industry": "Tools",
            "import_products": "Widgets",
            "company_size": "Small",
            "match_reason": "Imports widgets",
        },
        {"company_name": "Second", "contact_name": "Bob", "import_category": "Parts"},
    ]

    result = customer_manager.save_customers("user-1", "session-1", customers)

    assert result["success"] is True
    assert result["data"]["saved_count"] == 2
    assert connection.execute.call_count == 2
    saved = result["data"]["customers"]
    ids = [item["customer_id"] for item in saved]
    assert all(identifier.startswith("cust_") for identifier in ids)
    assert len(set(ids)) == 2
    assert saved == [
        {"customer_id": ids[0], "company_name": customers[0]["company_name"],
         "contact_name": "Alice", "email": "alice@example.test"},
        {"customer_id": ids[1], "company_name": "Second", "contact_name": "Bob", "email": ""},
    ]
    sql, params = _sql(connection)
    assert "INSERT INTO bs_trade_specialist_matched_customers" in sql
    assert customers[0]["company_name"] not in sql
    assert params[:-1] == (
        ids[0], "user-1", "session-1", customers[0]["company_name"], "Alice",
        "alice@example.test", "US", "en", "Tools", "Widgets", "Small", "Imports widgets",
    )
    datetime.strptime(params[-1], "%Y-%m-%d")
    _, second_params = _sql(connection, 1)
    assert second_params[:-1] == (
        ids[1], "user-1", "session-1", "Second", "Bob", "", "", "", "", "Parts", "", "",
    )
    connection.commit.assert_called_once_with()


@pytest.mark.parametrize("session_id,rows", [
    (None, [{"customer_id": "cust_1", "company_name": "First"}]),
    ("session-1", [{"customer_id": "cust_2", "company_name": "Second"}]),
    (None, []),
])
def test_list_customers_filters_and_returns_rows(db, session_id, rows):
    _, connection = db
    connection.fetchall.return_value = rows

    result = customer_manager.list_customers("user-1", session_id)

    assert result == {"success": True, "data": {"count": len(rows), "customers": rows}}
    sql, params = _sql(connection)
    assert "FROM bs_trade_specialist_matched_customers" in sql
    assert "WHERE user_id = %s" in sql
    assert "ORDER BY created_at DESC" in sql
    assert params == (("user-1", session_id) if session_id else ("user-1",))
    assert ("AND session_id = %s" in sql) is bool(session_id)
    connection.commit.assert_not_called()


@pytest.mark.parametrize("exists", [True, False])
def test_get_customer_returns_details_with_email_history_or_not_found(db, exists):
    _, connection = db
    row = {"customer_id": "cust_1", "company_name": "First"}
    emails = [{"email_id": "email_1", "email_subject": "Hello"}]
    connection.fetchone.return_value = row if exists else None
    connection.fetchall.return_value = emails

    result = customer_manager.get_customer("cust_1")

    sql, params = _sql(connection)
    assert "FROM bs_trade_specialist_matched_customers WHERE customer_id = %s" in sql
    assert params == ("cust_1",)
    if exists:
        assert result == {"success": True, "data": {**row, "emails": emails}}
        email_sql, email_params = _sql(connection, 1)
        assert "FROM bs_trade_specialist_customer_emails" in email_sql
        assert "WHERE customer_id = %s ORDER BY created_at DESC" in email_sql
        assert email_params == ("cust_1",)
        assert "emails" not in row  # Source DB row must not be mutated.
    else:
        assert result == {"success": False, "error": "客户 cust_1 不存在"}
        assert connection.execute.call_count == 1
        connection.fetchall.assert_not_called()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("exists,status,language,error", [
    (True, "success", "en", None),
    (True, "failed", "zh", "SMTP unavailable"),
    (False, "success", "en", None),
])
def test_record_email_checks_customer_and_commits_record(db, exists, status, language, error):
    _, connection = db
    connection.fetchone.return_value = {"customer_id": "cust_1"} if exists else None

    result = customer_manager.record_email(
        "cust_1", "user-1", "session-1", "Subject 'quoted'", "Body", language, status, error,
    )

    check_sql, check_params = _sql(connection)
    assert "WHERE customer_id = %s" in check_sql
    assert check_params == ("cust_1",)
    if not exists:
        assert result == {"success": False, "error": "客户 cust_1 不存在"}
        assert connection.execute.call_count == 1
        connection.commit.assert_not_called()
        return
    data = result["data"]
    assert result["success"] is True
    assert data["email_id"].startswith("email_")
    assert data["customer_id"] == "cust_1"
    assert data["send_status"] == status
    datetime.strptime(data["send_time"], "%Y-%m-%d %H:%M:%S")
    sql, params = _sql(connection, 1)
    assert "INSERT INTO bs_trade_specialist_customer_emails" in sql
    assert "Subject 'quoted'" not in sql
    assert params == (
        data["email_id"], "cust_1", "user-1", "session-1", "Subject 'quoted'", "Body",
        language, data["send_time"], status, error,
    )
    connection.commit.assert_called_once_with()


@pytest.mark.parametrize("customer_id,user_id,limit,rows", [
    ("cust_1", None, 10, [{"email_id": "email_1", "company_name": "First"}]),
    (None, "user-1", 100, []),
    ("cust_1", "user-1", 1, [{"email_id": "email_2"}]),
])
def test_list_emails_uses_selected_filter_and_limit(db, customer_id, user_id, limit, rows):
    _, connection = db
    connection.fetchall.return_value = rows

    result = customer_manager.list_emails(customer_id, user_id, limit)

    assert result == {"success": True, "data": {"count": len(rows), "emails": rows}}
    sql, params = _sql(connection)
    assert "LEFT JOIN bs_trade_specialist_matched_customers" in sql
    assert f"WHERE ce.{'customer_id' if customer_id else 'user_id'} = %s" in sql
    assert params == (customer_id or user_id,)
    assert sql.endswith(f"ORDER BY ce.created_at DESC LIMIT {limit}")
    connection.commit.assert_not_called()


def test_get_stats_returns_actual_counts_and_distribution(db):
    _, connection = db
    connection.fetchone.side_effect = [{"total": count} for count in (8, 5, 3, 2, 4, 1)]
    countries = [{"country": "US", "count": 5}, {"country": "CN", "count": 3}]
    connection.fetchall.return_value = countries

    result = customer_manager.get_stats("user-1")

    assert result == {"success": True, "data": {
        "total_customers": 8, "total_emails": 5, "success_emails": 3,
        "failed_emails": 2, "recent_customers": 4, "recent_emails": 1,
        "country_distribution": countries,
    }}
    queries = [_sql(connection, i) for i in range(7)]
    assert all(params == ("user-1",) for _, params in queries)
    assert all("WHERE user_id = %s" in sql for sql, _ in queries)
    assert "FROM bs_trade_specialist_matched_customers" in queries[0][0]
    assert "FROM bs_trade_specialist_customer_emails" in queries[1][0]
    assert "send_status = 'success'" in queries[2][0]
    assert "send_status = 'failed'" in queries[3][0]
    assert "created_at >= CURRENT_TIMESTAMP + INTERVAL '-7 days'" in queries[4][0]
    assert "created_at >= CURRENT_TIMESTAMP + INTERVAL '-7 days'" in queries[5][0]
    assert "GROUP BY country ORDER BY count DESC LIMIT 10" in queries[6][0]
    connection.commit.assert_not_called()


@pytest.mark.parametrize("function,args", [
    ("save_customers", ("", "session-1", [{"company_name": "First"}])),
    ("save_customers", ("user-1", "", [{"company_name": "First"}])),
    ("save_customers", ("user-1", "session-1", [])),
    ("list_customers", ("",)),
    ("get_customer", ("",)),
    ("record_email", ("", "user-1", "session-1", "Subject", "Body")),
    ("record_email", ("cust_1", "user-1", "session-1", "", "Body")),
    ("record_email", ("cust_1", "user-1", "session-1", "Subject", "")),
    ("list_emails", (None, None)),
    ("get_stats", ("",)),
])
def test_missing_required_input_rejected_before_database(db, function, args):
    get_connection, _ = db

    result = getattr(customer_manager, function)(*args)

    assert result["success"] is False
    assert result["error"]
    get_connection.assert_not_called()


@pytest.mark.parametrize("function,args,error", [
    ("save_customers", ("user-1", "session-1", [{"company_name": "First"}]), "保存客户信息失败"),
    ("list_customers", ("user-1",), "查询客户列表失败"),
    ("get_customer", ("cust_1",), "查询客户详情失败"),
    ("record_email", ("cust_1", "user-1", "session-1", "Subject", "Body"), "记录邮件发送失败"),
    ("list_emails", ("cust_1",), "查询邮件历史失败"),
    ("get_stats", ("user-1",), "获取统计信息失败"),
])
def test_database_error_returns_failure_without_commit(db, function, args, error):
    _, connection = db
    connection.execute.side_effect = RuntimeError("offline database")

    result = getattr(customer_manager, function)(*args)

    assert result == {"success": False, "error": error, "debug": "offline database"}
    connection.commit.assert_not_called()
