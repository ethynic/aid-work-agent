"""巡检商机 Repository 单测

测试层级：单元（DB 全 mock，遵循 tests/unit/conftest.py 的隔离规范）。
测试覆盖（按 Rule 6「测试验证意图」）：
1. 加密：raw_text 入库前被 encryption_manager.encrypt；明文不落库；列表查询不解密
2. 脱敏：默认查询返回 raw_text_masked，含 PII 不泄露
3. 去重：同 tenant + dedup_fingerprint 命中走 UPDATE 路径，不新建
4. 状态机集成：非法转换 transition_status 抛 InvalidLeadTransition
5. 租户隔离：tenant_a 的商机在 tenant_b 视角不可见（SQL 必含 tenant_id 过滤）
6. 分页排序：ORDER BY intent_score DESC, created_at DESC
7. 必备字段：user_id / tenant_id / created_at 写入正确
"""

from unittest.mock import MagicMock, patch

import pytest

from src.social_media.outbound.enums import (
    LeadSourceType,
    LeadStatus,
    OutreachExecutionStatus,
)
from src.social_media.outbound.repository import (
    LeadInteractionRepository,
    LeadRepository,
    OutreachActionRepository,
    _mask_raw_text,
    compute_dedup_fingerprint,
)
from src.social_media.outbound.state_machine import InvalidLeadTransition


# ============================================================
# helpers
# ============================================================


def _make_fake_conn(*, fetchone_return=None, fetchall_return=None):
    """构造 mock conn + cursor。

    返回 (mock_conn, mock_cursor)；cursor.fetchone/fetchall 返回值可后续修改。
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_return
    mock_cursor.fetchall.return_value = fetchall_return or []
    mock_cursor.rowcount = 1  # 默认 UPDATE 命中
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


@pytest.fixture
def lead_repo():
    return LeadRepository()


@pytest.fixture
def interaction_repo():
    return LeadInteractionRepository()


@pytest.fixture
def outreach_repo():
    return OutreachActionRepository()


# ============================================================
# LeadRepository.upsert — 加密
# ============================================================


class TestUpsertEncryption:
    """raw_text 必须加密入库，明文永不直接写入 SQL 参数。"""

    def test_upsert_encrypts_raw_text_before_insert(self, lead_repo):
        """新建：raw_text 经 encryption_manager.encrypt 加密后写入 INSERT 参数。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        raw_text = "求推荐 AI 工具，微信 13800138000"

        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with patch("src.social_media.outbound.repository.encryption_manager") as enc:
                enc.encrypt.return_value = "ENC_TOKEN"

                result = lead_repo.upsert(
                    tenant_id="tenant_a",
                    user_id="user_1",
                    platform="zhihu",
                    source_type=LeadSourceType.RADAR,
                    raw_text=raw_text,
                    external_content_id="zh_q_123",
                    intent_score=85,
                )

        # 校验：encrypt 被调用一次，参数为明文
        enc.encrypt.assert_called_once_with(raw_text)
        # INSERT 参数中应含 ENC_TOKEN，不含明文
        execute_calls = mock_cursor.execute.call_args_list
        insert_call = None
        for call in execute_calls:
            sql = call.args[0] if call.args else call.kwargs.get("sql")
            if sql and "INSERT INTO" in sql:
                insert_call = call
                break
        assert insert_call is not None, "应当执行 INSERT（无已有商机）"
        params = insert_call.args[1]
        assert "ENC_TOKEN" in params, "INSERT 参数必须含加密后的 token"
        assert raw_text not in params, "明文 raw_text 绝不可直接出现在 SQL 参数中"
        assert result["created"] is True

    def test_upsert_without_raw_text_skips_encrypt(self, lead_repo):
        """无 raw_text 时 encrypted_raw=None，不调 encrypt。"""
        mock_conn, _ = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with patch("src.social_media.outbound.repository.encryption_manager") as enc:
                enc.encrypt.return_value = "ENC"

                lead_repo.upsert(
                    tenant_id="tenant_a",
                    user_id="user_1",
                    platform="zhihu",
                    source_type="radar",
                    raw_text=None,
                    external_content_id="zh_q_1",
                )

        enc.encrypt.assert_not_called()


# ============================================================
# LeadRepository.upsert — 去重
# ============================================================


class TestUpsertDedup:
    """同 tenant + dedup_fingerprint 命中必须 UPDATE，不新建。"""

    def test_dedup_hit_triggers_update_not_insert(self, lead_repo):
        """同指纹命中：走 UPDATE 分支，created=False，不执行 INSERT。"""
        existing = {
            "lead_id": "lead_existing",
            "status": "new",
            "intent_score": 70,
            "raw_text_encrypted": "OLD_ENC",
        }
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=existing)

        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            result = lead_repo.upsert(
                tenant_id="tenant_a",
                user_id="user_2",
                platform="zhihu",
                source_type=LeadSourceType.RADAR,
                raw_text="new text",
                external_content_id="zh_q_123",  # 同 ID → 同指纹
                intent_score=80,
            )

        assert result == {"lead_id": "lead_existing", "created": False}
        sqls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("SELECT" in s for s in sqls), "必须先 SELECT 查重"
        assert any("UPDATE" in s for s in sqls), "命中去重后必须 UPDATE"
        assert not any("INSERT" in s for s in sqls), "命中去重不应再 INSERT"

    def test_dedup_update_takes_higher_intent_score(self, lead_repo):
        """去重更新时意向分取较大值，防止低分覆盖高分（防回退）。"""
        existing = {
            "lead_id": "lead_existing",
            "status": "contacted",
            "intent_score": 90,
            "raw_text_encrypted": "OLD",
        }
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=existing)

        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.upsert(
                tenant_id="tenant_a",
                user_id="user_1",
                platform="zhihu",
                source_type=LeadSourceType.RADAR,
                raw_text="new",
                external_content_id="zh_q_1",
                intent_score=30,  # 低于已有 90
            )

        update_call = None
        for c in mock_cursor.execute.call_args_list:
            if "UPDATE" in c.args[0]:
                update_call = c
                break
        assert update_call is not None
        update_sql = update_call.args[0]
        assert "intent_score" not in update_sql, (
            "意向分新值(30)低于已有(90)时不应触发 intent_score 更新（防回退）"
        )

    def test_dedup_does_not_touch_status_field(self, lead_repo):
        """去重 UPDATE 的 SET 子句不应含 status（状态机只能由 transition_status 推进）。"""
        existing = {
            "lead_id": "lead_existing",
            "status": "contacted",
            "intent_score": 50,
            "raw_text_encrypted": "OLD",
        }
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=existing)

        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.upsert(
                tenant_id="tenant_a",
                user_id="user_1",
                platform="zhihu",
                source_type=LeadSourceType.RADAR,
                raw_text="new",
                external_content_id="zh_q_1",
                intent_score=60,
            )

        update_call = None
        for c in mock_cursor.execute.call_args_list:
            if "UPDATE" in c.args[0]:
                update_call = c
                break
        assert update_call is not None
        update_sql = update_call.args[0]
        # 提取 SET ... WHERE 之间的片段
        set_clause = update_sql.split("SET", 1)[1].split("WHERE", 1)[0]
        assert "status" not in set_clause.lower(), "去重 UPDATE 不应含 status（防状态机被绕过）"

    def test_no_fingerprint_source_always_creates_new(self, lead_repo):
        """无 external_content_id/url/author 的商机无法去重，每次都新建。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            result = lead_repo.upsert(
                tenant_id="tenant_a",
                user_id="user_1",
                platform="zhihu",
                source_type=LeadSourceType.RADAR,
                raw_text="some text",
            )
        assert result["created"] is True
        sqls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert not any("dedup_fingerprint =" in s for s in sqls), (
            "无指纹来源不应执行 dedup_fingerprint 查重 SELECT"
        )


# ============================================================
# LeadRepository.upsert — 租户隔离
# ============================================================


class TestUpsertTenantIsolation:
    """upsert 的 SELECT 查重必须限定 tenant_id，不能跨租户去重。"""

    def test_dedup_select_includes_tenant_filter(self, lead_repo):
        """查重 SQL 必须含 tenant_id 过滤，防止 A 租户更新到 B 租户的商机。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.upsert(
                tenant_id="tenant_a",
                user_id="u1",
                platform="zhihu",
                source_type=LeadSourceType.RADAR,
                external_content_id="shared_id",
            )
        # 找到 dedup SELECT 调用
        dedup_call = None
        for c in mock_cursor.execute.call_args_list:
            if "dedup_fingerprint =" in c.args[0]:
                dedup_call = c
                break
        assert dedup_call is not None, "应当执行 dedup_fingerprint 查重 SQL"
        sql = dedup_call.args[0]
        assert "tenant_id = %s" in sql, "查重 SQL 必须限定 tenant_id"
        params = dedup_call.args[1]
        assert params[0] == "tenant_a", "第一个参数必须是 tenant_id"
        expected_fp = compute_dedup_fingerprint("zhihu", external_content_id="shared_id")
        assert params[1] == expected_fp, "第二个参数必须是计算出的指纹"


# ============================================================
# LeadRepository.get — 加密/脱敏
# ============================================================


class TestLeadGetMasking:
    """get(include_raw=False) 不解密原文；get(include_raw=True) 解密。"""

    def _make_row(self):
        return {
            "lead_id": "lead_1",
            "tenant_id": "tenant_a",
            "platform": "zhihu",
            "source_type": "radar",
            "external_content_id": "zh_1",
            "external_url": None,
            "raw_text_encrypted": "ENC_BLOB",
            "intent_score": 80,
            "status": "new",
            "assigned_user_id": None,
            "dedup_fingerprint": "fp1",
            "contact_points": "主动接触",
            "risk_flags": None,
            "user_id": "u1",
            "created_at": "2026-07-20T10:00:00",
            "updated_at": "2026-07-20T10:00:00",
        }

    def test_get_default_masks_raw_text(self, lead_repo):
        """默认 include_raw=False：解密后做脱敏，返回 raw_text_masked；raw_text 为 None。"""
        mock_conn, _ = _make_fake_conn(fetchone_return=self._make_row())
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with patch("src.social_media.outbound.repository.encryption_manager") as enc:
                enc.decrypt.return_value = "求推荐 AI 工具，微信 13800138000"
                result = lead_repo.get("tenant_a", "lead_1")

        assert result is not None
        assert "raw_text_encrypted" not in result, "脱敏模式不应返回加密 blob 字段"
        assert result["raw_text"] is None
        assert result["raw_text_masked"] is not None
        assert "***" in result["raw_text_masked"]
        # 完整 PII 绝不可出现在脱敏输出
        assert "13800138000" not in result["raw_text_masked"]

    def test_get_with_include_raw_decrypts(self, lead_repo):
        """include_raw=True：解密返回 raw_text 明文（授权链路）。"""
        mock_conn, _ = _make_fake_conn(fetchone_return=self._make_row())
        plain = "求推荐 AI 工具，微信 13800138000"
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with patch("src.social_media.outbound.repository.encryption_manager") as enc:
                enc.decrypt.return_value = plain
                result = lead_repo.get("tenant_a", "lead_1", include_raw=True)

        assert result["raw_text"] == plain
        assert result["raw_text_masked"] is None
        assert "raw_text_encrypted" not in result

    def test_get_returns_none_when_not_found(self, lead_repo):
        """未命中或租户越权：fetchone=None → 返回 None。"""
        mock_conn, _ = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            assert lead_repo.get("tenant_b", "lead_1") is None

    def test_get_sql_enforces_tenant_filter(self, lead_repo):
        """SELECT 必须含 tenant_id 过滤条件，防越权。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=self._make_row())
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.get("tenant_a", "lead_1")
        sql = mock_cursor.execute.call_args.args[0]
        assert "tenant_id = %s" in sql, "get SQL 必须含 tenant_id 过滤"
        assert "lead_id = %s" in sql


# ============================================================
# LeadRepository.list — 分页排序 + 脱敏
# ============================================================


class TestLeadList:

    def _make_list_row(self, lead_id="lead_x", score=50):
        return {
            "lead_id": lead_id,
            "tenant_id": "tenant_a",
            "platform": "zhihu",
            "source_type": "radar",
            "external_content_id": None,
            "external_url": None,
            "raw_text_encrypted": "ENC",
            "intent_score": score,
            "status": "new",
            "assigned_user_id": None,
            "dedup_fingerprint": None,
            "contact_points": None,
            "risk_flags": None,
            "user_id": "u1",
            "created_at": "2026-07-20T10:00:00",
            "updated_at": "2026-07-20T10:00:00",
        }

    def test_list_default_order_by_intent_score_desc_then_created_at_desc(self, lead_repo):
        """默认排序：intent_score DESC NULLS LAST, created_at DESC（高意向优先）。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 1},
            fetchall_return=[self._make_list_row()],
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.list("tenant_a")

        sqls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        list_sql = [s for s in sqls if "ORDER BY" in s][0]
        assert "intent_score DESC" in list_sql, "默认排序必须含 intent_score DESC"
        assert "created_at DESC" in list_sql, "同分按 created_at DESC"
        assert "NULLS LAST" in list_sql, "NULL 分应排最后"

    def test_list_never_decrypts_raw_text_by_default(self, lead_repo):
        """列表默认不返回原文（PII 保护）：raw_text_encrypted 字段被剥离。"""
        mock_conn, _ = _make_fake_conn(
            fetchone_return={"total": 1},
            fetchall_return=[self._make_list_row()],
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with patch("src.social_media.outbound.repository.encryption_manager") as enc:
                enc.decrypt.return_value = "plain"
                result = lead_repo.list("tenant_a")

        item = result["items"][0]
        assert "raw_text_encrypted" not in item, "列表项绝不可含加密 blob 字段"
        assert item["raw_text"] is None, "默认列表不解密返回明文"
        assert item["raw_text_masked"] is not None

    def test_list_paginates_correctly(self, lead_repo):
        """page=3, page_size=10 → LIMIT 10 OFFSET 20。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 50},
            fetchall_return=[],
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.list("tenant_a", page=3, page_size=10)

        list_calls = [c for c in mock_cursor.execute.call_args_list if "ORDER BY" in c.args[0]]
        list_params = list_calls[0].args[1]
        assert list_params[-2] == 10, "LIMIT 应为 page_size=10"
        assert list_params[-1] == 20, "OFFSET 应为 (page-1)*page_size=20"

    def test_list_page_size_clamped_to_max(self, lead_repo):
        """page_size > 200 强制截到 200（防恶意大查询）。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 0},
            fetchall_return=[],
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.list("tenant_a", page_size=9999)

        list_calls = [c for c in mock_cursor.execute.call_args_list if "ORDER BY" in c.args[0]]
        list_params = list_calls[0].args[1]
        assert list_params[-2] == 200, "page_size 应被截到 200"

    def test_list_filter_by_status_and_assigned(self, lead_repo):
        """status / assigned_user_id 过滤条件正确拼接。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 0},
            fetchall_return=[],
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.list(
                "tenant_a",
                status=LeadStatus.CONTACTED,
                assigned_user_id="sales_1",
            )

        list_calls = [c for c in mock_cursor.execute.call_args_list if "ORDER BY" in c.args[0]]
        list_sql = list_calls[0].args[0]
        assert "status = %s" in list_sql
        assert "assigned_user_id = %s" in list_sql


# ============================================================
# LeadRepository.transition_status — 状态机集成
# ============================================================


class TestTransitionStatus:

    def _make_existing(self, status="new"):
        return {
            "lead_id": "lead_1",
            "tenant_id": "tenant_a",
            "platform": "zhihu",
            "source_type": "radar",
            "external_content_id": None,
            "external_url": None,
            "raw_text_encrypted": None,
            "intent_score": 50,
            "status": status,
            "assigned_user_id": None,
            "dedup_fingerprint": None,
            "contact_points": None,
            "risk_flags": None,
            "user_id": "u1",
            "created_at": "2026-07-20T10:00:00",
            "updated_at": "2026-07-20T10:00:00",
        }

    def test_legal_transition_calls_update(self, lead_repo):
        """合法转换 new → contacted：调用 UPDATE 写入新状态。

        关键：参数顺序必须与 SQL 占位符顺序一致
        （SET status=%s 在前 → target 必须是 params[0]；
        WHERE tenant_id=%s → tenant_id 在 target 之后；WHERE lead_id=%s 在最后）。
        之前出过 bug：tenant_id 放在 target 前，导致 status 被赋成 tenant_id 值、UPDATE 命中 0 行。
        """
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=self._make_existing("new"))
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.transition_status(
                "tenant_a", "lead_1", LeadStatus.CONTACTED, actor_user_id="sales_1"
            )

        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        assert len(update_calls) >= 1, "合法转换必须执行 UPDATE"
        update_params = update_calls[0].args[1]
        # 顺序锁定：[status, tenant_id, lead_id]
        assert update_params[0] == "contacted", "第 1 个参数应为 SET status 的值"
        assert update_params[1] == "tenant_a", "第 2 个参数应为 WHERE tenant_id"
        assert update_params[2] == "lead_1", "第 3 个参数应为 WHERE lead_id"

    def test_illegal_transition_raises(self, lead_repo):
        """非法转换 new → converted：抛 InvalidLeadTransition，不执行 UPDATE。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=self._make_existing("new"))
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with pytest.raises(InvalidLeadTransition) as exc:
                lead_repo.transition_status("tenant_a", "lead_1", "converted")

        assert exc.value.current == "new"
        assert exc.value.target == "converted"
        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        assert len(update_calls) == 0, "非法转换绝不应执行 UPDATE"

    def test_transition_unknown_target_rejected(self, lead_repo):
        """未知目标状态（如 typo）抛 InvalidLeadTransition。"""
        mock_conn, _ = _make_fake_conn(fetchone_return=self._make_existing("new"))
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with pytest.raises(InvalidLeadTransition):
                lead_repo.transition_status("tenant_a", "lead_1", "deleted")  # 拼写错

    def test_transition_nonexistent_lead_raises(self, lead_repo):
        """商机不存在或越权：抛 ValueError（非状态机异常）。"""
        mock_conn, _ = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with pytest.raises(ValueError, match="不存在或租户越权"):
                lead_repo.transition_status("tenant_a", "lead_ghost", "contacted")


# ============================================================
# LeadRepository.update_intent_score — 边界校验
# ============================================================


class TestUpdateIntentScore:

    def test_out_of_range_score_rejected(self, lead_repo):
        """0-100 之外的分数拒绝（防止脏数据入库）。"""
        for bad in (-1, 101, 150):
            with pytest.raises(ValueError, match="0-100"):
                lead_repo.update_intent_score("tenant_a", "lead_1", bad)

    def test_non_integer_score_rejected(self, lead_repo):
        with pytest.raises(ValueError):
            lead_repo.update_intent_score("tenant_a", "lead_1", 50.5)  # type: ignore[arg-type]

    def test_update_intent_score_param_order(self, lead_repo):
        """参数顺序：[score, tenant_id, lead_id]，与 SET/WHERE 占位符一致（防历史 bug 回归）。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.update_intent_score("tenant_a", "lead_1", 75)

        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        params = update_calls[0].args[1]
        assert params[0] == 75, "第 1 参数应为 SET intent_score 值"
        assert params[1] == "tenant_a"
        assert params[2] == "lead_1"


class TestAssignParamOrder:
    """assign 的 UPDATE 参数顺序锁定（与 SET/WHERE 占位符一致）。"""

    def test_assign_param_order(self, lead_repo):
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            lead_repo.assign("tenant_a", "lead_1", "sales_99")

        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        params = update_calls[0].args[1]
        assert params[0] == "sales_99", "第 1 参数应为 SET assigned_user_id 值"
        assert params[1] == "tenant_a"
        assert params[2] == "lead_1"


# ============================================================
# _mask_raw_text — 脱敏边界
# ============================================================


class TestMaskRawText:

    def test_short_text_fully_masked(self):
        out = _mask_raw_text("短文本")
        assert "***" in out
        assert "短文本" not in out

    def test_long_text_keeps_head_and_tail(self):
        text = "ABCDEFGHIJ" * 5  # 50 chars
        out = _mask_raw_text(text)
        assert out.startswith("ABCD")
        assert "***" in out
        assert "ABCDEFGHIJ" not in out  # 中间被破坏

    def test_none_returns_none(self):
        assert _mask_raw_text(None) is None
        assert _mask_raw_text("") is None


# ============================================================
# compute_dedup_fingerprint — 优先级
# ============================================================


class TestComputeDedupFingerprint:

    def test_id_preferred_over_url(self):
        """有 external_content_id 时优先用 id，忽略 url。"""
        fp_id = compute_dedup_fingerprint("zhihu", external_content_id="id_1")
        fp_both = compute_dedup_fingerprint(
            "zhihu", external_content_id="id_1", external_url="http://x"
        )
        assert fp_id == fp_both

    def test_url_fallback_when_no_id(self):
        fp_url = compute_dedup_fingerprint("zhihu", external_url="http://x")
        assert len(fp_url) == 64

    def test_different_platforms_different_fingerprint(self):
        """同 ID 不同平台指纹不同（跨平台不去重）。"""
        fp_zhihu = compute_dedup_fingerprint("zhihu", external_content_id="shared")
        fp_xhs = compute_dedup_fingerprint("xiaohongshu", external_content_id="shared")
        assert fp_zhihu != fp_xhs

    def test_missing_all_raises(self):
        with pytest.raises(ValueError):
            compute_dedup_fingerprint("zhihu")


# ============================================================
# LeadInteractionRepository — 租户隔离 + 必备字段
# ============================================================


class TestInteractionRepository:

    def test_add_writes_required_fields(self, interaction_repo):
        """add 必须写入 tenant_id/user_id/lead_id/interaction_type。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            result = interaction_repo.add(
                tenant_id="tenant_a",
                user_id="u1",
                lead_id="lead_1",
                interaction_type="note",
                content="销售手记",
                actor_user_id="sales_1",
            )

        insert_calls = [c for c in mock_cursor.execute.call_args_list if "INSERT" in c.args[0]]
        assert insert_calls, "应当执行 INSERT"
        sql = insert_calls[0].args[0]
        params = insert_calls[0].args[1]
        for col in ["tenant_id", "user_id", "lead_id", "interaction_type", "content", "actor_user_id"]:
            assert col in sql, f"INSERT 必须写入字段 {col}"
        assert "tenant_a" in params
        assert "lead_1" in params
        assert "u1" in params
        assert result["interaction_id"].startswith("int_")

    def test_list_for_lead_enforces_tenant_filter(self, interaction_repo):
        """list_for_lead SQL 必须含 tenant_id 过滤（防跨租户读取互动）。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 0}, fetchall_return=[]
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            interaction_repo.list_for_lead("tenant_a", "lead_1")

        list_calls = [c for c in mock_cursor.execute.call_args_list if "ORDER BY" in c.args[0]]
        list_sql = list_calls[0].args[0]
        assert "tenant_id = %s" in list_sql
        assert "lead_id = %s" in list_sql

    def test_list_for_lead_orders_by_created_at_desc(self, interaction_repo):
        """互动记录按 created_at DESC（最新跟进在前）。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 0}, fetchall_return=[]
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            interaction_repo.list_for_lead("tenant_a", "lead_1")

        list_calls = [c for c in mock_cursor.execute.call_args_list if "ORDER BY" in c.args[0]]
        list_sql = list_calls[0].args[0]
        assert "created_at DESC" in list_sql


# ============================================================
# OutreachActionRepository — 审计字段
# ============================================================


class TestOutreachActionRepository:

    def test_add_defaults_to_draft_status(self, outreach_repo):
        """不传 execution_status 默认为 draft（防误执行未审核动作）。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            outreach_repo.add(
                tenant_id="tenant_a",
                user_id="u1",
                lead_id="lead_1",
                action_type="comment",
                channel="zhihu",
                content_snapshot="评论内容",
            )

        insert_calls = [c for c in mock_cursor.execute.call_args_list if "INSERT" in c.args[0]]
        params = insert_calls[0].args[1]
        assert "draft" in params, "默认 execution_status 应为 draft"

    def test_update_status_records_reviewer(self, outreach_repo):
        """update_status 带 reviewer_user_id 时一并写入审核人。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            outreach_repo.update_status(
                "tenant_a",
                "action_1",
                OutreachExecutionStatus.EXECUTED,
                reviewer_user_id="reviewer_1",
            )

        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        sql = update_calls[0].args[0]
        assert "reviewer_user_id" in sql, "带 reviewer 时 UPDATE 必须含 reviewer_user_id 字段"

    def test_update_status_zero_rowcount_raises(self, outreach_repo):
        """rowcount=0（租户越权或动作不存在）必须抛 ValueError（Fail loud）。"""
        mock_conn, mock_cursor = _make_fake_conn(fetchone_return=None)
        mock_cursor.rowcount = 0  # 模拟未命中
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            with pytest.raises(ValueError, match="不存在或租户越权"):
                outreach_repo.update_status(
                    "tenant_b",  # 不同租户
                    "action_1",
                    OutreachExecutionStatus.EXECUTED,
                )

    def test_list_for_lead_enforces_tenant_filter(self, outreach_repo):
        """审计查询同样强制 tenant_id 过滤。"""
        mock_conn, mock_cursor = _make_fake_conn(
            fetchone_return={"total": 0}, fetchall_return=[]
        )
        with patch("src.social_media.outbound.repository.get_db_connection") as m:
            m.return_value.__enter__.return_value = mock_conn
            outreach_repo.list_for_lead("tenant_a", "lead_1")

        list_calls = [c for c in mock_cursor.execute.call_args_list if "ORDER BY" in c.args[0]]
        list_sql = list_calls[0].args[0]
        assert "tenant_id = %s" in list_sql
