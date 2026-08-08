"""
视频创作智能体知识中心 API 集成测试（Phase 1 §4）

覆盖三套 CRUD API：
- 素材库 (asset_library)：列表 / 详情 / 删除 / 手动上传
- 视频库 (work_outcomes where outcome_type='file' AND subagent_id='video-agent')：列表 / 详情（含提示词溯源） / 删除
- 提示词库 (prompt_library)：列表 / 详情 / 升级模版（权限校验） / 删除

测试策略：
- 使用真实 PostgreSQL，创建临时租户隔离测试数据
- mock get_current_tenant_id / get_current_user / is_tenant_admin
- 测试正常路径 + 异常路径（404 / 403 / 400）
"""

import os
import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ============== Fixture ==============

@pytest.fixture
def temp_tenant_with_user():
    """创建临时租户 + 测试用户，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"视频智能体测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]
    user_id = f"test_user_{uuid.uuid4().hex[:8]}"
    yield {"tenant_id": tenant_id, "user_id": user_id}

    # 清理：删测试业务数据 + 删租户
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM asset_library WHERE tenant_id = %s", (tenant_id,))
            cursor.execute(
                "DELETE FROM work_outcomes WHERE tenant_id = %s AND subagent_id = 'video-agent'",
                (tenant_id,),
            )
            cursor.execute("DELETE FROM prompt_library WHERE tenant_id = %s", (tenant_id,))
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


def _mock_tenant_ctx(tenant_id: str):
    """mock get_current_tenant_id 返回指定租户"""
    return patch("src.api.video_agent.get_current_tenant_id", return_value=tenant_id)


def _mock_user(user_id: str, role: str = "tenant_admin", tenant_id: str = ""):
    """mock get_current_user + is_tenant_admin"""
    user = {"user_id": user_id, "role": role, "tenant_id": tenant_id}
    return (
        patch("src.api.video_agent.get_current_user", return_value=user),
        patch("src.api.video_agent.is_tenant_admin", return_value=role in ("tenant_admin", "platform_admin")),
    )


def _unpack(response):
    """把 JSONResponse 解包为 dict；普通 dict 直接返回"""
    import json
    from fastapi.responses import JSONResponse

    if isinstance(response, JSONResponse):
        return json.loads(response.body)
    return response


def _insert_asset(tenant_id: str, user_id: str, display_name: str = "测试素材.png", scene: str = "product") -> int:
    """插入测试素材记录，返回 id"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO asset_library
                (tenant_id, user_id, file_id, display_name, mime_type, size_bytes, source, scene, width, height)
            VALUES (%s, %s, %s, %s, %s, %s, 'user_upload', %s, 1080, 1920)
            RETURNING id
            """,
            (tenant_id, user_id, f"file_{uuid.uuid4().hex[:12]}", display_name, "image/png", 1024, scene),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


def _insert_work_outcome(tenant_id: str, user_id: str, prompt_library_id=None) -> int:
    """插入测试视频库记录（work_outcomes），返回 id

    work_outcomes 实际列名：file_name/file_path（非 display_name），且 outcome_id/session_id/summary 必填。
    """
    from src.db.database import get_db_connection
    import json

    metadata = {"prompt_library_id": prompt_library_id} if prompt_library_id else {}
    outcome_id = f"wo_{uuid.uuid4().hex[:12]}"
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    file_id = f"file_{uuid.uuid4().hex[:12]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_outcomes
                (outcome_id, tenant_id, user_id, subagent_id, session_id, summary,
                 outcome_type, importance, file_id, file_name, file_path, metadata, source)
            VALUES (%s, %s, %s, 'video-agent', %s, %s, 'file', 'normal', %s, %s, %s, %s, 'cp_realtime')
            RETURNING id
            """,
            (
                outcome_id, tenant_id, user_id, session_id,
                "测试视频摘要",
                file_id, "测试视频.mp4", f"/tmp/{file_id}.mp4",
                json.dumps(metadata),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


def _insert_prompt(tenant_id: str, user_id: str, category: str = "kept", scene_tag: str = "product") -> int:
    """插入测试提示词记录，返回 id"""
    from src.db.database import get_db_connection
    import json

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO prompt_library
                (tenant_id, user_id, category, business_prompt, craft_prompt, model_params,
                 industry_tag, scene_tag, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, '电商', %s, %s)
            RETURNING id
            """,
            (
                tenant_id, user_id, category,
                "业务层：展示产品卖点",
                "工艺层：主体/元素参考/环境/景别/运镜/光影/色调/风格",
                json.dumps({"duration": 5, "ratio": "9:16", "resolution": "720P"}),
                scene_tag,
                json.dumps({}),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


# ============== 1. 素材库 API ==============

class TestAssetsAPI:
    """素材库 API 测试"""

    def test_list_assets_returns_paginated_items(self, temp_tenant_with_user):
        """GET /assets 返回分页素材列表"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        _insert_asset(ctx["tenant_id"], ctx["user_id"], "素材1.png", scene="product")
        _insert_asset(ctx["tenant_id"], ctx["user_id"], "素材2.png", scene="model")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.list_assets(request=None, page=1, page_size=20, scene=None, source=None)
            ))

        assert response["success"] is True
        assert response["data"]["total"] >= 2
        assert response["data"]["page"] == 1
        assert response["data"]["page_size"] == 20
        items = response["data"]["items"]
        assert all(it["tenant_id"] == ctx["tenant_id"] for it in items)

    def test_list_assets_filter_by_scene(self, temp_tenant_with_user):
        """GET /assets?scene=product 按 scene 筛选"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        _insert_asset(ctx["tenant_id"], ctx["user_id"], "产品图.png", scene="product")
        _insert_asset(ctx["tenant_id"], ctx["user_id"], "模特图.png", scene="model")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.list_assets(request=None, page=1, page_size=20, scene="product", source=None)
            ))

        assert response["success"] is True
        items = response["data"]["items"]
        assert all(it["scene"] == "product" for it in items)
        assert any(it["display_name"] == "产品图.png" for it in items)

    def test_list_assets_missing_tenant_returns_400(self):
        """GET /assets 无租户 ID 返回 400"""
        from src.api import video_agent

        with patch("src.api.video_agent.get_current_tenant_id", return_value=None):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.list_assets(request=None, page=1, page_size=20, scene=None, source=None)
            ))

        assert response["success"] is False
        assert response["error"] == "租户 ID 缺失"

    def test_get_asset_returns_detail(self, temp_tenant_with_user):
        """GET /assets/{id} 返回素材详情"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        asset_id = _insert_asset(ctx["tenant_id"], ctx["user_id"], "详情图.png")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_asset(asset_id, request=None)
            ))

        assert response["success"] is True
        assert response["data"]["id"] == asset_id
        assert response["data"]["display_name"] == "详情图.png"

    def test_get_asset_not_found_returns_404(self, temp_tenant_with_user):
        """GET /assets/{不存在的 id} 返回 404"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_asset(99999999, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "素材不存在"

    def test_delete_asset_success(self, temp_tenant_with_user):
        """DELETE /assets/{id} 删除素材成功"""
        from src.api import video_agent
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        asset_id = _insert_asset(ctx["tenant_id"], ctx["user_id"], "待删.png")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.delete_asset(asset_id, request=None)
            ))

        assert response["success"] is True

        # 确认 DB 中已删除
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM asset_library WHERE id = %s", (asset_id,))
            assert cursor.fetchone() is None

    def test_manual_upload_asset_creates_record(self, temp_tenant_with_user):
        """POST /assets/manual 手动上传素材创建记录"""
        from src.api import video_agent
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        req = video_agent.ManualUploadAssetRequest(
            file_id=f"file_{uuid.uuid4().hex[:12]}",
            display_name="手动上传.png",
            mime_type="image/jpeg",
            size_bytes=2048,
            scene="bgm",
            width=1920,
            height=1080,
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), \
             patch("src.api.video_agent.get_current_user", return_value={"user_id": ctx["user_id"], "role": "tenant_admin"}):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.manual_upload_asset(req, request=None)
            ))

        assert response["success"] is True
        asset_id = response["data"]["id"]
        assert asset_id is not None

        # 校验 DB 记录
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM asset_library WHERE id = %s", (asset_id,))
            row = cursor.fetchone()
            assert row is not None
            assert row["display_name"] == "手动上传.png"
            assert row["source"] == "user_upload"
            assert row["scene"] == "bgm"


# ============== 2. 视频库 API ==============

class TestVideosAPI:
    """视频库 API 测试"""

    def test_list_videos_returns_only_video_agent_files(self, temp_tenant_with_user):
        """GET /videos 只返回 video-agent 的 file 类型的 work_outcomes"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        _insert_work_outcome(ctx["tenant_id"], ctx["user_id"])
        _insert_work_outcome(ctx["tenant_id"], ctx["user_id"])

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.list_videos(request=None, page=1, page_size=20)
            ))

        assert response["success"] is True
        items = response["data"]["items"]
        assert response["data"]["total"] >= 2
        for it in items:
            assert it["subagent_id"] == "video-agent"
            assert it["outcome_type"] == "file"

    def test_get_video_with_prompt_trace(self, temp_tenant_with_user):
        """GET /videos/{id} 返回视频详情 + 提示词溯源"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        # 先建提示词，再建视频并关联 prompt_library_id
        prompt_id = _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="kept")
        video_id = _insert_work_outcome(ctx["tenant_id"], ctx["user_id"], prompt_library_id=prompt_id)

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_video(video_id, request=None)
            ))

        assert response["success"] is True
        video = response["data"]
        assert video["id"] == video_id
        # source_prompt 字段含溯源信息
        assert video["source_prompt"] is not None
        assert video["source_prompt"]["id"] == prompt_id
        assert video["source_prompt"]["category"] == "kept"

    def test_get_video_without_prompt_trace(self, temp_tenant_with_user):
        """GET /videos/{id} 无提示词关联时 source_prompt 为 None"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        video_id = _insert_work_outcome(ctx["tenant_id"], ctx["user_id"], prompt_library_id=None)

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_video(video_id, request=None)
            ))

        assert response["success"] is True
        assert response["data"]["source_prompt"] is None

    def test_get_video_not_found_returns_404(self, temp_tenant_with_user):
        """GET /videos/{不存在 id} 返回 404"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_video(99999999, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "视频不存在"

    def test_delete_video_success(self, temp_tenant_with_user):
        """DELETE /videos/{id} 删除视频成功"""
        from src.api import video_agent
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        video_id = _insert_work_outcome(ctx["tenant_id"], ctx["user_id"])

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.delete_video(video_id, request=None)
            ))

        assert response["success"] is True
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM work_outcomes WHERE id = %s", (video_id,))
            assert cursor.fetchone() is None


# ============== 3. 提示词库 API ==============

class TestPromptsAPI:
    """提示词库 API 测试"""

    def test_list_prompts_filter_by_category(self, temp_tenant_with_user):
        """GET /prompts?category=kept 按 category 筛选"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="kept")
        _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="blacklist")
        _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="template")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.list_prompts(request=None, category="kept", scene_tag=None, page=1, page_size=20)
            ))

        assert response["success"] is True
        items = response["data"]["items"]
        assert all(it["category"] == "kept" for it in items)

    def test_get_prompt_returns_detail(self, temp_tenant_with_user):
        """GET /prompts/{id} 返回提示词详情"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        prompt_id = _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="kept")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_prompt(prompt_id, request=None)
            ))

        assert response["success"] is True
        assert response["data"]["id"] == prompt_id
        assert response["data"]["category"] == "kept"
        # model_params 应被解析为 dict
        assert isinstance(response["data"]["model_params"], dict)
        assert response["data"]["model_params"]["duration"] == 5

    def test_get_prompt_not_found_returns_404(self, temp_tenant_with_user):
        """GET /prompts/{不存在 id} 返回 404"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.get_prompt(99999999, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "提示词不存在"

    def test_promote_prompt_by_tenant_admin_success(self, temp_tenant_with_user):
        """POST /prompts/{id}/promote 租户管理员升级模版成功"""
        from src.api import video_agent
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        prompt_id = _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="kept")

        mock_user, mock_admin = _mock_user(ctx["user_id"], role="tenant_admin", tenant_id=ctx["tenant_id"])
        with _mock_tenant_ctx(ctx["tenant_id"]), mock_user, mock_admin:
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.promote_prompt(prompt_id, request=None)
            ))

        assert response["success"] is True
        new_id = response["data"]["id"]
        assert new_id != prompt_id
        assert response["data"]["promoted_from_kept_id"] == prompt_id

        # 校验新记录 category=template 且 promoted_from_kept_id 链路完整
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prompt_library WHERE id = %s", (new_id,))
            row = cursor.fetchone()
            assert row["category"] == "template"
            assert row["promoted_from_kept_id"] == prompt_id
            assert row["promoted_by_user_id"] == ctx["user_id"]
            assert row["promoted_at"] is not None

    def test_promote_prompt_by_normal_user_rejected_403(self, temp_tenant_with_user):
        """POST /prompts/{id}/promote 普通用户被拒（403）"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        prompt_id = _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="kept")

        mock_user, mock_admin = _mock_user(ctx["user_id"], role="user", tenant_id=ctx["tenant_id"])
        with _mock_tenant_ctx(ctx["tenant_id"]), mock_user, mock_admin:
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.promote_prompt(prompt_id, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "无权限"

    def test_promote_prompt_on_non_kept_returns_400(self, temp_tenant_with_user):
        """POST /prompts/{id}/promote 升级非 kept 记录返回 400"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        # 已是 template，不能再次升级
        prompt_id = _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="template")

        mock_user, mock_admin = _mock_user(ctx["user_id"], role="tenant_admin", tenant_id=ctx["tenant_id"])
        with _mock_tenant_ctx(ctx["tenant_id"]), mock_user, mock_admin:
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.promote_prompt(prompt_id, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "仅留用记录可升级"

    def test_promote_prompt_not_found_returns_404(self, temp_tenant_with_user):
        """POST /prompts/{不存在 id}/promote 返回 404"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        mock_user, mock_admin = _mock_user(ctx["user_id"], role="tenant_admin", tenant_id=ctx["tenant_id"])
        with _mock_tenant_ctx(ctx["tenant_id"]), mock_user, mock_admin:
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.promote_prompt(99999999, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "提示词不存在"

    def test_delete_prompt_success(self, temp_tenant_with_user):
        """DELETE /prompts/{id} 删除提示词成功"""
        from src.api import video_agent
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        prompt_id = _insert_prompt(ctx["tenant_id"], ctx["user_id"], category="blacklist")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.delete_prompt(prompt_id, request=None)
            ))

        assert response["success"] is True
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM prompt_library WHERE id = %s", (prompt_id,))
            assert cursor.fetchone() is None

    def test_delete_prompt_not_found_returns_404(self, temp_tenant_with_user):
        """DELETE /prompts/{不存在 id} 返回 404"""
        from src.api import video_agent

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            import asyncio
            response = _unpack(asyncio.get_event_loop().run_until_complete(
                video_agent.delete_prompt(99999999, request=None)
            ))

        assert response["success"] is False
        assert response["error"] == "提示词不存在"


# ============== 租户隔离测试 ==============

class TestTenantIsolation:
    """租户隔离测试：A 租户不能访问 B 租户的数据"""

    def test_get_asset_cross_tenant_returns_404(self, temp_tenant_with_user):
        """A 租户访问 B 租户的素材返回 404"""
        from src.api import video_agent
        from src.saas.db.tenant_db import TenantDB
        from src.db.database import get_db_connection

        ctx_a = temp_tenant_with_user
        # 在 A 租户下建素材
        asset_id_a = _insert_asset(ctx_a["tenant_id"], ctx_a["user_id"])

        # 创建 B 租户
        tenant_code_b = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant_b = TenantDB.create(
            company_name=f"视频智能体测试租户B-{tenant_code_b}",
            tenant_code=tenant_code_b,
            contact_name="测试B",
            contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]

        try:
            # 用 B 租户身份访问 A 租户的素材，应返回 404
            with _mock_tenant_ctx(tenant_id_b):
                import asyncio
                response = _unpack(asyncio.get_event_loop().run_until_complete(
                    video_agent.get_asset(asset_id_a, request=None)
                ))
            assert response["success"] is False
            assert response["error"] == "素材不存在"
        finally:
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass
