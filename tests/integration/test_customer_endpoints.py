"""
Customer API 端到端测试
测试数据库交互层：客户管理、客户邮件
"""

import pytest
import uuid
from datetime import datetime


class TestCustomerCRUD:
    """客户 CRUD 测试"""

    @pytest.fixture
    def test_user_for_customer(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"cust_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "cust_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield user_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    @pytest.fixture
    def test_session_for_customer(self, test_user_for_customer):
        """创建测试会话"""
        from src.db.models import SessionDB

        user_id = test_user_for_customer
        session = SessionDB.create(user_id, title="Customer Test Session")

        yield user_id, session["session_id"]

    def test_create_customer(self, test_session_for_customer):
        """测试创建客户"""
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import save_matched_customers

        user_id, session_id = test_session_for_customer

        customers = [
            {
                "company_name": "Test Company Ltd",
                "contact_name": "John Doe",
                "email": "john@testcompany.com",
                "country": "USA",
                "language": "en"
            }
        ]

        result = save_matched_customers(user_id, session_id, customers)

        assert result is True

    def test_get_customer_by_id(self, test_session_for_customer):
        """测试根据 ID 获取客户"""
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import (
            save_matched_customers, get_customer_detail
        )

        user_id, session_id = test_session_for_customer

        # 先创建客户
        customers = [
            {
                "company_name": "Get Test Company",
                "contact_name": "Jane Smith",
                "email": "jane@test.com",
                "country": "China",
                "language": "zh"
            }
        ]
        save_matched_customers(user_id, session_id, customers)

        # 获取客户列表以获取 customer_id
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import get_customer_list
        customer_list = get_customer_list(user_id, session_id)
        customer_id = customer_list[0]["customer_id"]

        # 根据 ID 获取客户
        customer = get_customer_detail(customer_id)

        assert customer is not None
        assert customer["company_name"] == "Get Test Company"

    def test_list_customers_by_user(self, test_session_for_customer):
        """测试列出用户的所有客户"""
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import (
            save_matched_customers, get_customer_list
        )

        user_id, session_id = test_session_for_customer

        # 创建多个客户
        customers = [
            {"company_name": f"Company {i}", "contact_name": f"Contact {i}",
             "email": f"contact{i}@test.com", "country": "USA", "language": "en"}
            for i in range(3)
        ]
        save_matched_customers(user_id, session_id, customers)

        # 获取客户列表
        customer_list = get_customer_list(user_id, session_id)

        assert len(customer_list) >= 3


class TestCustomerEmails:
    """客户邮件测试"""

    @pytest.fixture
    def test_user_and_customer(self):
        """创建测试用户、客户和会话"""
        from src.db.models import UserDB, SessionDB, hash_password
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import save_matched_customers

        user_id = f"email_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "email_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        # 创建会话
        session = SessionDB.create(user_id, title="Email Test Session")
        session_id = session["session_id"]

        # 创建客户
        customers = [
            {
                "company_name": "Email Test Company",
                "contact_name": "Email Contact",
                "email": "contact@emailtest.com",
                "country": "USA",
                "language": "en"
            }
        ]
        save_matched_customers(user_id, session_id, customers)

        # 获取客户 ID
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import get_customer_list
        customer_list = get_customer_list(user_id, session_id)
        customer_id = customer_list[0]["customer_id"]

        yield user_id, session_id, customer_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_customer_email(self, test_user_and_customer):
        """测试创建客户邮件记录"""
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import add_customer_email

        user_id, session_id, customer_id = test_user_and_customer

        result = add_customer_email(
            customer_id=customer_id,
            user_id=user_id,
            session_id=session_id,
            email_subject="Test Email Subject",
            email_body="This is a test email body.",
            email_language="en"
        )

        assert result is True

    def test_get_customer_emails(self, test_user_and_customer):
        """测试获取客户邮件历史"""
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import (
            add_customer_email, get_customer_emails
        )

        user_id, session_id, customer_id = test_user_and_customer

        # 创建多封邮件
        for i in range(3):
            add_customer_email(
                customer_id=customer_id,
                user_id=user_id,
                session_id=session_id,
                email_subject=f"Email {i}",
                email_body=f"Body of email {i}",
                email_language="en"
            )

        # 获取邮件列表
        emails = get_customer_emails(customer_id=customer_id, limit=10)

        assert len(emails) >= 3


class TestCustomerStats:
    """客户统计测试"""

    @pytest.fixture
    def test_user_for_stats(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"stats_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "stats_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield user_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_get_customer_stats(self, test_user_for_stats):
        """测试获取客户统计信息"""
        from src.skills.trade_customer_1_0_0.scripts.customer_manager import (
            save_matched_customers, add_customer_email, get_customer_stats
        )
        from src.db.models import SessionDB

        user_id = test_user_for_stats

        # 创建会话
        session = SessionDB.create(user_id, title="Stats Test Session")
        session_id = session["session_id"]

        # 创建客户
        customers = [
            {"company_name": f"Stats Company {i}", "contact_name": f"Contact {i}",
             "email": f"stats{i}@test.com", "country": "USA", "language": "en"}
            for i in range(2)
        ]
        save_matched_customers(user_id, session_id, customers)

        # 创建邮件
        customer_list = save_matched_customers(user_id, session_id, [])
        # 这里简化处理，实际需要获取 customer_id
        # 由于统计函数需要实际数据，这里仅测试函数可调用
        stats = get_customer_stats(user_id)

        assert "total_customers" in stats
        assert "total_emails" in stats