"""
lead_manager.py Phase 2 命令单元测试（销售CRUD、分配规则、自动分配）

使用 mock 数据库连接，不依赖真实 PostgreSQL。
"""
import json
import pytest
from unittest.mock import patch, MagicMock, call

# 添加路径以便导入
import sys
from pathlib import Path
script_dir = Path(__file__).resolve().parents[2] / "src" / "skills" / "lead-management-1.0.0" / "scripts"
sys.path.insert(0, str(script_dir))

import lead_manager


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("CURRENT_TENANT_ID", "test_tenant_001")


@pytest.fixture
def mock_output_json():
    with patch.object(lead_manager, "output_json") as m:
        yield m


@pytest.fixture
def mock_db():
    """Mock get_db() 返回上下文管理器"""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch.object(lead_manager, "get_db", return_value=mock_conn):
        yield mock_conn, mock_cursor


def make_args(**kwargs):
    """创建模拟的 argparse Namespace"""
    from argparse import Namespace
    return Namespace(**kwargs)


# =============================================================
# Sales Rep Tests
# =============================================================

class TestAddSalesRep:
    def test_add_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(
            user_id="user_001", name="张三", department="华东销售部",
            role="sales", max_leads=50, region="华东", skills=None,
        )
        lead_manager.cmd_add_sales_rep(args)

        mock_output_json.assert_called_once()
        call_args = mock_output_json.call_args
        assert call_args[0][0] is True
        data = call_args[1].get("data") or call_args[0][1]
        assert data["rep_id"].startswith("rep_")

    def test_add_invalid_role(self, mock_db, mock_output_json):
        args = make_args(
            user_id="user_001", name="张三", department=None,
            role="invalid_role", max_leads=50, region=None, skills=None,
        )
        lead_manager.cmd_add_sales_rep(args)

        mock_output_json.assert_called_once()
        call_args = mock_output_json.call_args
        assert call_args[0][0] is False
        assert "无效角色" in call_args[1].get("error", "")


class TestListSalesReps:
    def test_list_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.description = [
            ("rep_id",), ("user_id",), ("name",), ("department",),
            ("role",), ("active_lead_count",), ("max_leads",),
            ("is_active",), ("skills",), ("region",), ("created_at",),
        ]
        mock_cursor.fetchall.return_value = [
            ("rep_001", "user_001", "张三", "华东", "sales", 5, 50, True, [], "华东", "2026-01-01"),
        ]

        args = make_args(active_only=True)
        lead_manager.cmd_list_sales_reps(args)

        mock_output_json.assert_called_once()
        call_args = mock_output_json.call_args
        assert call_args[0][0] is True


class TestUpdateSalesRep:
    def test_update_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.rowcount = 1
        args = make_args(
            rep_id="rep_001",
            fields='{"name": "张三丰", "max_leads": 100}',
        )
        lead_manager.cmd_update_sales_rep(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True

    def test_update_invalid_json(self, mock_db, mock_output_json):
        args = make_args(rep_id="rep_001", fields="not json")
        lead_manager.cmd_update_sales_rep(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False

    def test_update_invalid_role(self, mock_db, mock_output_json):
        args = make_args(
            rep_id="rep_001",
            fields='{"role": "hacker"}',
        )
        lead_manager.cmd_update_sales_rep(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False
        assert "无效角色" in mock_output_json.call_args[1].get("error", "")


class TestDeactivateSalesRep:
    def test_deactivate_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.rowcount = 1
        args = make_args(rep_id="rep_001")
        lead_manager.cmd_deactivate_sales_rep(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True

    def test_deactivate_not_found(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.rowcount = 0
        args = make_args(rep_id="rep_nonexist")
        lead_manager.cmd_deactivate_sales_rep(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False


# =============================================================
# Assign Rule Tests
# =============================================================

class TestAddAssignRule:
    def test_add_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(
            name="华东负载均衡",
            rule_type="load_balance",
            priority=10,
            conditions=None,
            target_rep_ids=None,
            auto_assign="true",
        )
        lead_manager.cmd_add_assign_rule(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["rule_id"].startswith("arule_")

    def test_add_invalid_type(self, mock_db, mock_output_json):
        args = make_args(
            name="bad rule", rule_type="invalid_type",
            priority=0, conditions=None, target_rep_ids=None, auto_assign="true",
        )
        lead_manager.cmd_add_assign_rule(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False
        assert "无效规则类型" in mock_output_json.call_args[1].get("error", "")


class TestListAssignRules:
    def test_list_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.description = [
            ("rule_id",), ("name",), ("rule_type",), ("priority",),
            ("is_active",), ("conditions",), ("target_rep_ids",),
            ("auto_assign",), ("created_at",),
        ]
        mock_cursor.fetchall.return_value = [
            ("arule_001", "负载均衡", "load_balance", 10, True, "{}", [], True, "2026-01-01"),
        ]

        args = make_args(active_only=True)
        lead_manager.cmd_list_assign_rules(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True


class TestUpdateAssignRule:
    def test_update_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.rowcount = 1
        args = make_args(rule_id="arule_001", fields='{"priority": 20}')
        lead_manager.cmd_update_assign_rule(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True


# =============================================================
# Auto Assign Tests
# =============================================================

class TestAutoAssignRep:
    def test_auto_assign_load_balance(self):
        """Test _auto_assign_rep with load_balance picks least loaded"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # No rule found -> empty target_rep_ids
        mock_cursor.fetchone.side_effect = [None]
        mock_cursor.description = [
            ("user_id",), ("active_lead_count",), ("max_leads",), ("region",), ("skills",),
        ]
        mock_cursor.fetchall.return_value = [
            ("user_a", 10, 50, "华东", []),
            ("user_b", 3, 50, "华东", []),
            ("user_c", 7, 50, "华东", []),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch.object(lead_manager, "get_db", return_value=mock_conn):
            result = lead_manager._auto_assign_rep("tenant_001", "load_balance")

        assert result == "user_b"  # least loaded

    def test_auto_assign_no_reps(self):
        """Test _auto_assign_rep returns None when no reps available"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [None]
        mock_cursor.description = [
            ("user_id",), ("active_lead_count",), ("max_leads",), ("region",), ("skills",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch.object(lead_manager, "get_db", return_value=mock_conn):
            result = lead_manager._auto_assign_rep("tenant_001", "load_balance")

        assert result is None

    def test_auto_assign_manual_returns_none(self):
        result = lead_manager._auto_assign_rep("tenant_001", "manual")
        assert result is None

    def test_auto_assign_region_based(self):
        """Test region_based assigns to matching region rep"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [None]
        mock_cursor.description = [
            ("user_id",), ("active_lead_count",), ("max_leads",), ("region",), ("skills",),
        ]
        mock_cursor.fetchall.return_value = [
            ("user_a", 5, 50, "华东", []),
            ("user_b", 3, 50, "华南", []),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch.object(lead_manager, "get_db", return_value=mock_conn):
            result = lead_manager._auto_assign_rep("tenant_001", "region_based", lead_region="华南")

        assert result == "user_b"  # region match


class TestBatchAssign:
    def test_batch_manual_rejected(self, mock_db, mock_output_json):
        args = make_args(rule="manual", unassigned_only=False)
        lead_manager.cmd_batch_assign(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False
        assert "manual" in mock_output_json.call_args[1].get("error", "")
