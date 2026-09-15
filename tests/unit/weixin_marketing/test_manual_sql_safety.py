"""回滚手册 SQL 场景安全性测试（P5 复审三轮）

复审判例：手册批量取消/许可收敛 SQL 若缺 scenario_key 过滤，`business_kind=
'desktop_automation'` 为底座共享类型，微信回滚会误取消同租户其他场景（如
boss.chat_reply.v1）的排队任务与许可。

防漂移设计：测试**直接从 docs/ops/weixin-marketing-rollout.md 提取 ```sql 块**
执行（占位符 <tid> 替换为真实测试租户），保证被验证的 SQL 与文档永远一致——
手册改动即测试对象改动。断言：微信场景 queued invocation 被取消、他场景
queued invocation 与许可不受影响。
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from src.db.database import get_db_connection
from src.local_tools import repository as lt_repository

_MANUAL = Path(__file__).parents[3] / "docs" / "ops" / "weixin-marketing-rollout.md"


def _manual_sql_blocks() -> list:
    text = _MANUAL.read_text(encoding="utf-8")
    return re.findall(r"```sql\n(.*?)```", text, flags=re.DOTALL)


def _create_scenario_invocation(tenant_id, user_id, device_id, scenario_key, dedupe):
    return lt_repository.create_invocation(
        tenant_id, user_id, device_id, "weixin_message_send_v2",
        {"request_id": f"req-{dedupe}"},
        provider_key="weixin", business_kind="desktop_automation",
        business_ref={"scenario_key": scenario_key, "run_id": None},
        dedupe_key=f"manualsql:{dedupe}",
    )


class TestManualRollbackSqlScenarioSafety:
    def test_rollback_sql_only_cancels_weixin_scenario(self, tenant_id, adapter, wx_config):
        """手册全部 SQL 块按序执行后：微信 queued 被取消，他场景 queued/许可不动。"""
        user_id = "owner-1"
        device_id = str(__import__("uuid").uuid4())
        wx_inv = _create_scenario_invocation(
            tenant_id, user_id, device_id, "weixin.fixed_content.v1", "wx-1"
        )
        other_inv = _create_scenario_invocation(
            tenant_id, user_id, device_id, "boss.chat_reply.v1", "other-1"
        )
        # 双场景各挂一张已过期 issued 许可（若 SQL 缺场景过滤，他场景许可也会被置 expired）
        past = "2000-01-01T00:00:00+00:00"
        with get_db_connection() as conn:
            cur = conn.cursor()
            for inv, tag in ((wx_inv, "wx"), (other_inv, "other")):
                cur.execute(
                    """
                    INSERT INTO local_tool_operation_permits
                        (tenant_id, user_id, invocation_id, device_id,
                         claim_token_hash, request_id, target_ref, payload_hash,
                         resource_key, permit_token_hash, deadline, state)
                    VALUES (%s, %s, %s, %s, 'h', %s, 't', 'p', 'r', 'pt', %s, 'issued')
                    """,
                    (tenant_id, user_id, inv, device_id, f"req-{tag}-1", past),
                )
            conn.commit()

        blocks = _manual_sql_blocks()
        assert len(blocks) >= 4, f"手册 SQL 块数量异常: {len(blocks)}"
        with get_db_connection() as conn:
            cur = conn.cursor()
            for sql in blocks:
                cur.execute(sql.replace("<tid>", tenant_id))
            conn.commit()
            cur.execute(
                "SELECT state FROM local_tool_invocations WHERE id = %s", (wx_inv,)
            )
            wx_state = cur.fetchone()["state"]
            cur.execute(
                "SELECT state FROM local_tool_invocations WHERE id = %s", (other_inv,)
            )
            other_state = cur.fetchone()["state"]
            cur.execute(
                """
                SELECT p.state FROM local_tool_operation_permits p
                JOIN local_tool_invocations i ON i.id = p.invocation_id
                WHERE i.id = %s
                """,
                (other_inv,),
            )
            other_permit = cur.fetchone()["state"]
            cur.execute(
                """
                SELECT p.state FROM local_tool_operation_permits p
                JOIN local_tool_invocations i ON i.id = p.invocation_id
                WHERE i.id = %s
                """,
                (wx_inv,),
            )
            wx_permit = cur.fetchone()["state"]

        # 微信场景：queued→cancelled、过期许可→expired
        assert wx_state == "cancelled"
        assert wx_permit == "expired"
        # 他场景（boss.chat_reply.v1）：invocation 保持 queued、许可保持 issued
        assert other_state == "queued", "回滚 SQL 误取消了他场景排队任务"
        assert other_permit == "issued", "回滚 SQL 误过期了他场景许可"

        # 手册 SQL 本身必须包含场景过滤（静态断言，防未来编辑丢过滤）
        all_sql = "\n".join(blocks)
        cancel_block = next(
            b for b in blocks if "UPDATE local_tool_invocations" in b
        )
        assert "business_ref->>'scenario_key'" in cancel_block
        permit_block = next(
            b for b in blocks if "local_tool_operation_permits" in b
        )
        assert "business_ref->>'scenario_key'" in permit_block
        assert "tenant_id" in permit_block, "许可收敛 SQL 必须限定租户"
