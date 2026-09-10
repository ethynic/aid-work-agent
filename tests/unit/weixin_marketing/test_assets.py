"""微信营销图片素材单测（P4-A，R57/R58 验收）

覆盖矩阵：
- 上传约束：MIME 实测（伪 mime 以实测为准）/损坏/截断/不支持格式/超大小/超像素/
  images_enabled=false 门禁/空内容；
- ACL：跨租户/非属主统一 404；文件落 storage/tenants/{tid}/weixin-marketing/；
- 引用保护：draft/published 引用期删除 409 ASSET_IN_USE；解引用（重编辑/发布新版
  置 superseded）后可删；硬删文件+行；重复删除 404；
- 过期清理：retention_until 过期+无引用才删；引用保留；未过期保留；enabled=false
  零动作；审计行；
- Runtime 下载校验链：归属设备/未终态/场景绑定/revision 资产集/hash 复核；
- 图片块编译：text/link/image 混排顺序、payload 字节 asset:<id> 与 hash 一致、
  serve_payload 受控字节、草稿引用缺失素材 422、发布复核素材被删 422。
"""

import hashlib
import io
import os
import shutil
import threading
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from PIL import Image

from src.weixin_marketing import assets as wxm_assets
from src.weixin_marketing import content as wxm_content
from src.weixin_marketing.config import get_weixin_marketing_config
from src.weixin_marketing.constants import ASSET_STORAGE_SCENE
from src.weixin_marketing.service import (
    AssetInUseError,
    NotFoundError,
    WeixinValidationError,
)
from tests.unit.weixin_marketing.conftest import create_and_publish, utcnow

pytestmark = pytest.mark.unit


# ==================== fixtures 与工具 ====================


@pytest.fixture()
def img_config(wx_config):
    """images_enabled=true 的模块配置（引用保护/上传链路测试用）"""
    return replace(wx_config, images_enabled=True, retention_days=90)


@pytest.fixture()
def img_adapter(wx_config):
    """images_enabled=true 的受信适配器（发布图片 revision 用）"""
    from dataclasses import replace as _replace

    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter

    instance = WeixinFixedContentAdapter(config=_replace(wx_config, images_enabled=True))
    TrustedAdapterRegistry.register(instance)
    yield instance
    TrustedAdapterRegistry.unregister(instance.scenario_key)


@pytest.fixture(autouse=True)
def asset_files_cleanup(tenant_id):
    """测后回收素材存储目录（storage/tenants/{tid}/，测试专用随机租户，
    整目录移除以不残留空父目录）"""
    yield
    tenant_dir = os.path.join("storage", "tenants", tenant_id)
    if os.path.isdir(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)


def make_png(width=120, height=80, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def make_tiff() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (1, 2, 3)).save(buf, format="TIFF")
    return buf.getvalue()


def upload_png(tenant_id, user_id="owner-1", *, config=None, color=(200, 30, 30)):
    cfg = config  # None → 生产配置路径（默认 images_enabled=false，由用例自行决定）
    return wxm_assets.upload_asset(
        tenant_id, user_id, "t.png", make_png(color=color),
        declared_mime="image/png", config=cfg,
    )


def patch_images_enabled(monkeypatch, cfg):
    """把 images_enabled 配置注入服务/校验/素材模块的全局配置读取点"""
    import src.weixin_marketing.service as wxm_service
    import src.weixin_marketing.triggers as wxm_triggers

    monkeypatch.setattr(wxm_service, "get_weixin_marketing_config", lambda: cfg)
    monkeypatch.setattr(wxm_triggers, "get_weixin_marketing_config", lambda: cfg)
    monkeypatch.setattr(wxm_assets, "get_weixin_marketing_config", lambda: cfg)


# ==================== 上传约束矩阵 ====================


class TestUploadConstraints:
    def test_upload_ok_and_storage_layout(self, tenant_id, img_config):
        png = make_png()
        meta = wxm_assets.upload_asset(
            tenant_id, "owner-1", "图.png", png, declared_mime="image/png", config=img_config,
        )
        assert meta["mime"] == "image/png"
        assert (meta["width"], meta["height"]) == (120, 80)
        assert meta["size"] == len(png)
        assert meta["sha256"] == hashlib.sha256(png).hexdigest()
        assert meta["status"] == "active"
        assert meta["reference_count"] == 0
        # 文件落租户存储目录（V-P2-1：绝对路径登记，消除 cwd 依赖；不接受任意服务器路径）
        from src.core.storage import get_tenant_storage_abs_path

        detail = wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])
        expected_ref = get_tenant_storage_abs_path(
            tenant_id, ASSET_STORAGE_SCENE, f"{meta['id']}.png"
        )
        assert detail["storage_ref"] == expected_ref
        assert os.path.isabs(detail["storage_ref"])
        assert os.path.isfile(detail["storage_ref"])
        # retention_until ≈ now + retention_days
        from src.weixin_marketing.assets import _aware

        retention = _aware(detail["retention_until"])
        delta = retention - utcnow()
        assert timedelta(days=89) < delta <= timedelta(days=90, minutes=5)

    def test_fake_mime_measured_by_content(self, tenant_id, img_config):
        """伪 mime：声明 image/jpeg 实为 PNG → 按实测 image/png 收录（不以声明为准）"""
        meta = wxm_assets.upload_asset(
            tenant_id, "owner-1", "t.png", make_png(),
            declared_mime="image/jpeg", config=img_config,
        )
        assert meta["mime"] == "image/png"

    def test_text_bytes_rejected(self, tenant_id, img_config):
        with pytest.raises(WeixinValidationError, match="损坏|无法解码"):
            wxm_assets.upload_asset(
                tenant_id, "owner-1", "t.png", b"definitely not an image",
                config=img_config,
            )

    def test_truncated_png_rejected(self, tenant_id, img_config):
        with pytest.raises(WeixinValidationError):
            wxm_assets.upload_asset(
                tenant_id, "owner-1", "t.png", make_png()[:24], config=img_config,
            )

    def test_unsupported_format_rejected(self, tenant_id, img_config):
        with pytest.raises(WeixinValidationError, match="不支持的图片格式"):
            wxm_assets.upload_asset(
                tenant_id, "owner-1", "t.tiff", make_tiff(), config=img_config,
            )

    def test_oversize_rejected(self, tenant_id, img_config):
        tiny = replace(img_config, asset_max_bytes=16)
        with pytest.raises(WeixinValidationError, match="超过上限"):
            wxm_assets.upload_asset(
                tenant_id, "owner-1", "t.png", make_png(), config=tiny,
            )

    def test_over_pixels_rejected(self, tenant_id, img_config):
        tiny = replace(img_config, asset_max_pixels=1000)
        with pytest.raises(WeixinValidationError, match="像素"):
            wxm_assets.upload_asset(
                tenant_id, "owner-1", "t.png", make_png(), config=tiny,
            )

    def test_images_disabled_rejected(self, tenant_id, wx_config):
        with pytest.raises(WeixinValidationError, match="images_enabled"):
            upload_png(tenant_id, config=wx_config)

    def test_empty_content_rejected(self, tenant_id, img_config):
        with pytest.raises(WeixinValidationError, match="为空"):
            wxm_assets.upload_asset(
                tenant_id, "owner-1", "t.png", b"", config=img_config,
            )


# ==================== ACL（租户 + 属主）====================


class TestAssetAcl:
    def test_cross_tenant_and_non_owner_404(self, tenant_id, img_config):
        meta = upload_png(tenant_id, config=img_config)
        for user in ("owner-2",):
            with pytest.raises(NotFoundError):
                wxm_assets.get_asset(tenant_id, user, meta["id"])
            with pytest.raises(NotFoundError):
                wxm_assets.delete_asset(tenant_id, user, meta["id"])
        with pytest.raises(NotFoundError):
            wxm_assets.get_asset("wxm_other_tenant", "owner-1", meta["id"])
        # 非属主列表不可见
        result = wxm_assets.list_assets(tenant_id, "owner-2", config=img_config)
        assert result["total"] == 0

    def test_non_uuid_id_404(self, tenant_id, img_config):
        with pytest.raises(NotFoundError):
            wxm_assets.get_asset(tenant_id, "owner-1", "not-a-uuid")


# ==================== 引用保护 ====================


class TestReferenceProtection:
    def _create_image_draft(self, service, tenant_id, group_id, asset_id, monkeypatch, img_config):
        from tests.unit.weixin_marketing.conftest import make_create_payload

        patch_images_enabled(monkeypatch, img_config)
        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(
                group_id,
                blocks=[
                    {"type": "text", "text_content": "文字条"},
                    {"type": "image", "asset_id": asset_id},
                ],
            ),
        )
        return str(detail["automation"]["id"]), detail["automation"]["version"]

    def test_draft_reference_blocks_delete(self, service, tenant_id, bindings, monkeypatch, img_config):
        _, group_id = bindings
        meta = upload_png(tenant_id, config=img_config)
        automation_id, version = self._create_image_draft(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        # 引用计数可见
        listing = wxm_assets.list_assets(tenant_id, "owner-1", config=img_config)
        assert listing["items"][0]["reference_count"] == 1
        detail = wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])
        assert detail["reference_count"] == 1
        # 删除被拒（409 ASSET_IN_USE）
        with pytest.raises(AssetInUseError, match="引用"):
            wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])
        assert os.path.isfile(detail["storage_ref"])

        # 解引用（草稿重写为纯文字）→ 可删；硬删文件+行；重复删除 404
        from src.weixin_marketing.models import DraftUpdateInput

        service.update_draft(
            tenant_id, automation_id, "owner-1",
            DraftUpdateInput(
                expected_version=version,
                blocks=[{"type": "text", "text_content": "只剩文字"}],
            ),
        )
        assert wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])["reference_count"] == 0
        result = wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])
        assert result == {"asset_id": meta["id"], "deleted": True}
        assert not os.path.exists(detail["storage_ref"])
        with pytest.raises(NotFoundError):
            wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])

    def test_concurrent_double_delete_loser_idempotent_404(self, tenant_id, img_config, monkeypatch):
        """V-P2-2：并发双删败者（DELETE 影响 0 行）→ 幂等 404，不追加审计行。

        模拟：败者已加载行（缓存），胜者并发删行落库——败者的 DELETE 0 行。
        """
        from src.db.database import get_db_connection

        meta = upload_png(tenant_id, config=img_config)
        cached_row = wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])
        # 胜者：并发窗口内已删行（直删模拟）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM bs_weixin_marketing_assets WHERE tenant_id = %s AND id = %s",
                (tenant_id, meta["id"]),
            )
            conn.commit()
        # 败者：_load_asset_on 命中缓存行（并发窗口）→ DELETE 0 行 → 404
        monkeypatch.setattr(
            wxm_assets, "_load_asset_on",
            lambda cursor, tid, uid, aid, **kwargs: dict(cached_row),
        )
        with pytest.raises(NotFoundError):
            wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])
        # 审计行只有 upload 一条（败者不写 asset_deleted）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT action FROM bs_weixin_marketing_audit_events WHERE tenant_id = %s",
                (tenant_id,),
            )
            actions = [r["action"] for r in cur.fetchall()]
        assert actions == ["asset_uploaded"]

    def test_published_reference_blocks_delete(self, service, tenant_id, bindings,
                                               monkeypatch, img_config, img_adapter):
        _, group_id = bindings
        meta = upload_png(tenant_id, config=img_config)
        automation_id, version = self._create_image_draft(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        from src.weixin_marketing.models import PublishInput

        service.publish(
            tenant_id, automation_id, "owner-1",
            PublishInput(expected_version=version),
        )
        with pytest.raises(AssetInUseError):
            wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])

    def test_superseded_only_reference_allows_delete(self, service, tenant_id, bindings,
                                                     monkeypatch, img_config, img_adapter):
        """superseded（已被新版替换）视为过期 revision：不构成引用保护——
        发布新版解引用后素材可删（R57「非过期 revision 引用即 409」的对偶面）"""
        _, group_id = bindings
        meta = upload_png(tenant_id, config=img_config)
        automation_id, version = self._create_image_draft(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        from src.weixin_marketing.models import DraftUpdateInput, PublishInput

        # 第一版：含图片 → 发布（create 后 version=1）
        service.publish(
            tenant_id, automation_id, "owner-1", PublishInput(expected_version=version)
        )
        # 第二版：去掉图片 → 发布（旧版置 superseded；publish 后 version=version+1，
        # 无草稿 → update 需完整 trigger/blocks/group_binding_id 新建草稿）
        detail = service.update_draft(
            tenant_id, automation_id, "owner-1",
            DraftUpdateInput(
                expected_version=version + 1,
                trigger={
                    "type": "once",
                    "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
                    "timezone": "UTC",
                },
                blocks=[{"type": "text", "text_content": "新版无图"}],
                group_binding_id=group_id,
            ),
        )
        service.publish(
            tenant_id, automation_id, "owner-1",
            PublishInput(expected_version=detail["automation"]["version"]),
        )
        assert wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])["deleted"] is True

    def test_delete_audited(self, tenant_id, img_config):
        from src.db.database import get_db_connection

        meta = upload_png(tenant_id, config=img_config)
        wxm_assets.delete_asset(tenant_id, "owner-1", meta["id"])
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT action FROM bs_weixin_marketing_audit_events "
                "WHERE tenant_id = %s ORDER BY id",
                (tenant_id,),
            )
            actions = [r["action"] for r in cur.fetchall()]
        assert "asset_uploaded" in actions and "asset_deleted" in actions


# ==================== 过期清理 ====================


class TestConcurrentDeleteVsDraftSave:
    """P1-2 锁协议：delete（素材行 FOR UPDATE）与草稿保存（assert FOR SHARE 与
    块写入同事务）——两种交错终态均合法，绝不出现「删除成功且草稿引用悬空」。"""

    def _run_round(self, service, tenant_id, group_id, asset_id, round_no):
        """barrier 同步双线程（A 删除 / B 建引用草稿），返回两种终态判定。"""
        from tests.unit.weixin_marketing.conftest import make_create_payload

        barrier = threading.Barrier(2)
        result = {}

        def do_delete():
            try:
                barrier.wait(timeout=10)
                result["deleted"] = wxm_assets.delete_asset(
                    tenant_id, "owner-1", asset_id
                )
            except Exception as e:  # noqa: BLE001
                result["delete_exc"] = e

        def do_save():
            try:
                barrier.wait(timeout=10)
                detail = service.create_automation(
                    tenant_id, "owner-1",
                    make_create_payload(
                        group_id, blocks=[{"type": "image", "asset_id": asset_id}],
                        name=f"竞态轮{round_no}",
                    ),
                )
                result["saved"] = detail
            except Exception as e:  # noqa: BLE001
                result["save_exc"] = e

        threads = [
            threading.Thread(target=do_delete, daemon=True),
            threading.Thread(target=do_save, daemon=True),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
        assert not any(th.is_alive() for th in threads), "并发线程超时（疑似锁死）"
        return result

    def _reference_rows(self, tenant_id, asset_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM bs_weixin_marketing_content_blocks "
                "WHERE tenant_id = %s AND asset_id = %s",
                (tenant_id, asset_id),
            )
            return int(cur.fetchone()["c"])

    def _asset_exists(self, tenant_id, asset_id) -> bool:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM bs_weixin_marketing_assets "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, asset_id),
            )
            return cur.fetchone() is not None

    def test_no_dangling_reference_under_race(
        self, service, tenant_id, bindings, monkeypatch, img_config, img_adapter
    ):
        """5 轮采样：终态二选一（保存先锁→删除 409+草稿在；删除先锁→保存 422+
        素材删），悬空引用为不可达态（红绿基准：回退锁修复即转红）。"""
        _, group_id = bindings
        patch_images_enabled(monkeypatch, img_config)
        saw_delete_win = saw_save_win = 0
        for round_no in range(5):
            meta = wxm_assets.upload_asset(
                tenant_id, "owner-1", "r.png", make_png(), config=img_config,
            )
            result = self._run_round(
                service, tenant_id, group_id, meta["id"], round_no
            )
            deleted = "deleted" in result
            saved = "saved" in result
            # 不变量：不存在「删除成功 且 草稿落了引用」的悬空终态
            if deleted:
                # 删除先锁并提交：保存侧 FOR SHARE 无行 → 422 fail-closed，零引用行
                assert not saved
                assert isinstance(result.get("save_exc"), WeixinValidationError)
                assert not self._asset_exists(tenant_id, meta["id"])
                assert self._reference_rows(tenant_id, meta["id"]) == 0
                saw_delete_win += 1
            else:
                # 保存先锁（草稿块同事务提交）：删除计数见引用 → 409，素材在
                assert saved
                assert isinstance(result.get("delete_exc"), AssetInUseError)
                assert self._asset_exists(tenant_id, meta["id"])
                assert self._reference_rows(tenant_id, meta["id"]) == 1
                saw_save_win += 1
        # 采样有效性：至少发生了一种交错（两种都出现不强制——锁协议保证任一
        # 交错闭合，barrier 下两线程必然其一先取到锁）
        assert saw_delete_win + saw_save_win == 5

    def test_lock_mutex_deterministic(self, service, tenant_id, bindings,
                                      monkeypatch, img_config):
        """P1-2 锁互斥确定性锚点（红绿基准）：删除锁根（素材行 FOR UPDATE）与
        草稿/发布校验共享锁（FOR SHARE）双向互斥——A 持删除锁 → 草稿校验阻塞；
        B 持共享锁 → 删除阻塞；锁释放后各自按序完成（barrier 竞态用例的确定性
        对照，回退任一锁子句即转红）。"""
        from src.db.database import get_db_connection
        from tests.unit.weixin_marketing.conftest import make_create_payload

        _, group_id = bindings
        patch_images_enabled(monkeypatch, img_config)
        meta_a = wxm_assets.upload_asset(
            tenant_id, "owner-1", "a.png", make_png(color=(1, 2, 3)), config=img_config,
        )
        meta_b = wxm_assets.upload_asset(
            tenant_id, "owner-1", "b.png", make_png(color=(4, 5, 6)), config=img_config,
        )

        # 方向一：外部持 FOR UPDATE（模拟删除事务行锁窗口）→ 草稿保存（校验+
        # 块写入同事务）必须阻塞在 FOR SHARE 上
        held_a = get_db_connection()
        conn_a = held_a.__enter__()
        try:
            cur = conn_a.cursor()
            cur.execute(
                "SELECT id FROM bs_weixin_marketing_assets "
                "WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, meta_a["id"]),
            )
            assert cur.fetchone() is not None
            save_result = {}

            def do_save():
                try:
                    service.create_automation(
                        tenant_id, "owner-1",
                        make_create_payload(
                            group_id, blocks=[{"type": "image", "asset_id": meta_a["id"]}],
                            name="锁互斥方向一",
                        ),
                    )
                    save_result["done"] = True
                except Exception as e:  # noqa: BLE001
                    save_result["exc"] = e

            th = threading.Thread(target=do_save, daemon=True)
            th.start()
            th.join(timeout=1.0)
            assert th.is_alive(), "草稿校验未与删除行锁互斥（FOR SHARE 缺失？）"
            conn_a.rollback()  # 释放行锁（行未删，仅锁窗口模拟）
        finally:
            held_a.__exit__(None, None, None)
        th.join(timeout=20)
        assert not th.is_alive(), "锁释放后草稿保存未恢复完成"
        assert save_result.get("done") is True, save_result  # 行在 → 保存成功

        # 方向二：外部持 FOR SHARE（模拟草稿校验至块写入提交的锁窗口）→ 删除
        # 必须阻塞在 FOR UPDATE 上
        held_b = get_db_connection()
        conn_b = held_b.__enter__()
        try:
            cur = conn_b.cursor()
            cur.execute(
                "SELECT id FROM bs_weixin_marketing_assets "
                "WHERE tenant_id = %s AND id = %s FOR SHARE",
                (tenant_id, meta_b["id"]),
            )
            assert cur.fetchone() is not None
            del_result = {}

            def do_delete():
                try:
                    wxm_assets.delete_asset(tenant_id, "owner-1", meta_b["id"])
                    del_result["done"] = True
                except Exception as e:  # noqa: BLE001
                    del_result["exc"] = e

            th2 = threading.Thread(target=do_delete, daemon=True)
            th2.start()
            th2.join(timeout=1.0)
            assert th2.is_alive(), "删除未与校验共享锁互斥（FOR UPDATE 缺失？）"
            conn_b.rollback()  # 释放共享锁（无引用落库 → 删除按序成功）
        finally:
            held_b.__exit__(None, None, None)
        th2.join(timeout=20)
        assert not th2.is_alive(), "锁释放后删除未恢复完成"
        assert del_result.get("done") is True, del_result  # 零引用 → 删除成功


class TestCleanup:
    """过期清理断言只看本租户行/文件（cleanup_expired_assets 是全局扫描的系统任务，
    共享 DB 下并行测试包的过期行会进入同一批——全局计数断言天然竞态，不采用）。"""

    def _expire(self, tenant_id, asset_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_assets SET retention_until = NOW() - INTERVAL '1 day' "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, asset_id),
            )
            conn.commit()

    def test_cleanup_deletes_expired_unreferenced(self, tenant_id, img_config):
        meta = upload_png(tenant_id, config=img_config)
        detail = wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])
        self._expire(tenant_id, meta["id"])
        result = wxm_assets.cleanup_expired_assets(config=img_config)
        # 本租户素材被删（行 404 + 文件回收）；全局 deleted 计数在共享 DB 下含他
        # 租户过期行，只下界断言
        assert result["deleted"] >= 1
        with pytest.raises(NotFoundError):
            wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])
        assert not os.path.exists(detail["storage_ref"])

    def test_cleanup_keeps_referenced(self, tenant_id, img_config, service, bindings, monkeypatch):
        _, group_id = bindings
        meta = upload_png(tenant_id, config=img_config)
        TestReferenceProtection()._create_image_draft(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        self._expire(tenant_id, meta["id"])
        wxm_assets.cleanup_expired_assets(config=img_config)
        # 引用保护：本租户被 draft 引用的过期素材不被清理
        assert wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])["id"] == meta["id"]

    def test_cleanup_keeps_unexpired(self, tenant_id, img_config):
        meta = upload_png(tenant_id, config=img_config)  # retention 90 天后
        wxm_assets.cleanup_expired_assets(config=img_config)
        # 未过期素材保留（无论全局批次是否清了他租户行）
        assert wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])["id"] == meta["id"]

    def test_cleanup_not_starved_by_referenced_head(self, tenant_id, img_config, service, bindings, monkeypatch):
        """P2 清理饥饿：候选 SQL 在 LIMIT 前 NOT EXISTS 排除被引用素材——batch=1
        时位置靠前的被引用素材不再占用配额，靠后的无引用素材照删（旧行为两条
        都不删——红绿基准：回退 NOT EXISTS 即转红）。"""
        _, group_id = bindings
        referenced = upload_png(tenant_id, config=img_config, color=(11, 22, 33))
        unreferenced = upload_png(tenant_id, config=img_config, color=(44, 55, 66))
        # 让被引用素材排在批次前面（retention_until 更早）
        TestReferenceProtection()._create_image_draft(
            service, tenant_id, group_id, referenced["id"], monkeypatch, img_config
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_assets SET retention_until = NOW() - INTERVAL '2 day' "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, referenced["id"]),
            )
            cur.execute(
                "UPDATE bs_weixin_marketing_assets SET retention_until = NOW() - INTERVAL '1 day' "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, unreferenced["id"]),
            )
            conn.commit()
        result = wxm_assets.cleanup_expired_assets(batch=1, config=img_config)
        # 本租户：无引用素材被删，被引用素材保留（全局计数共享 DB 下只作参考）
        assert not self._row_exists(tenant_id, unreferenced["id"])
        assert self._row_exists(tenant_id, referenced["id"])
        assert result["deleted"] >= 1

    @staticmethod
    def _row_exists(tenant_id, asset_id) -> bool:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM bs_weixin_marketing_assets "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, asset_id),
            )
            return cur.fetchone() is not None

    def test_cleanup_tick_gate(self, tenant_id, img_config):
        from src.weixin_marketing import dispatch as wxm_dispatch

        meta = upload_png(tenant_id, config=img_config)
        self._expire(tenant_id, meta["id"])
        # enabled=false：tick 本地短路零动作（返回值确定，不受共享 DB 影响）
        assert wxm_dispatch.assets_cleanup_tick(config=replace(img_config, enabled=False)) == {
            "enabled": False, "deleted": 0, "skipped_referenced": 0,
        }
        stats = wxm_dispatch.assets_cleanup_tick(config=img_config)
        assert stats["enabled"] is True
        # 本租户过期无引用素材被清（他租户可能并发先清，行不存在即达成语义）
        with pytest.raises(NotFoundError):
            wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])


# ==================== Runtime 下载校验链 ====================


class TestServeInvocationAsset:
    @staticmethod
    def _make_invocation(tenant_id, user_id, device_id, *, business_ref, tool="weixin_message_send_v2"):
        from src.local_tools.service import LocalInvocationService

        return LocalInvocationService().enqueue(
            tenant_id=tenant_id, user_id=user_id, device_id=device_id,
            tool_name=tool, arguments={"protocol_version": 2},
            provider_key="weixin", business_kind="desktop_automation",
            business_ref=business_ref, dedupe_key=f"t-{uuid.uuid4().hex[:8]}",
        )

    @staticmethod
    def _set_state(tenant_id, invocation_id, state):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_invocations SET state = %s "
                "WHERE tenant_id = %s AND id = %s",
                (state, tenant_id, invocation_id),
            )
            conn.commit()

    def _publish_image_automation(self, service, tenant_id, group_id, asset_id,
                                  monkeypatch, img_config):
        patch_images_enabled(monkeypatch, img_config)
        from src.weixin_marketing.models import PublishInput

        from tests.unit.weixin_marketing.conftest import make_create_payload

        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(
                group_id,
                blocks=[{"type": "image", "asset_id": asset_id}],
            ),
        )
        automation_id = str(detail["automation"]["id"])
        service.publish(
            tenant_id, automation_id, "owner-1",
            PublishInput(expected_version=detail["automation"]["version"]),
        )
        revision_id = str(
            service.get_automation_detail(tenant_id, automation_id, "owner-1")[
                "automation"
            ]["active_revision_id"]
        )
        return automation_id, revision_id

    def test_serve_ok(self, service, tenant_id, bindings, monkeypatch, img_config, img_adapter):
        _, group_id = bindings
        device_id = str(uuid.uuid4())
        png = make_png()
        meta = wxm_assets.upload_asset(
            tenant_id, "owner-1", "t.png", png, config=img_config,
        )
        automation_id, revision_id = self._publish_image_automation(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        invocation = self._make_invocation(
            tenant_id, "owner-1", device_id,
            business_ref={
                "scenario_key": "weixin.fixed_content.v1",
                "task_ref": automation_id,
                "revision_ref": revision_id,
            },
        )
        self._set_state(tenant_id, str(invocation["id"]), "running")
        resolution = wxm_assets.serve_invocation_asset(
            tenant_id, device_id, str(invocation["id"]), meta["id"]
        )
        assert resolution.data == png
        assert resolution.mime == "image/png"
        assert resolution.sha256 == hashlib.sha256(png).hexdigest()

    def test_serve_wrong_device_404(self, service, tenant_id, bindings, monkeypatch, img_config, img_adapter):
        _, group_id = bindings
        device_id = str(uuid.uuid4())
        meta = upload_png(tenant_id, config=img_config)
        automation_id, revision_id = self._publish_image_automation(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        invocation = self._make_invocation(
            tenant_id, "owner-1", device_id,
            business_ref={
                "scenario_key": "weixin.fixed_content.v1",
                "task_ref": automation_id, "revision_ref": revision_id,
            },
        )
        self._set_state(tenant_id, str(invocation["id"]), "running")
        with pytest.raises(wxm_assets.AssetEndpointError) as ei:
            wxm_assets.serve_invocation_asset(
                tenant_id, str(uuid.uuid4()), str(invocation["id"]), meta["id"]
            )
        assert ei.value.code == "INVOCATION_NOT_FOUND" and ei.value.http_status == 404

    def test_serve_queued_and_terminal_rejected(self, service, tenant_id, bindings,
                                                monkeypatch, img_config, img_adapter):
        _, group_id = bindings
        device_id = str(uuid.uuid4())
        meta = upload_png(tenant_id, config=img_config)
        automation_id, revision_id = self._publish_image_automation(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        invocation = self._make_invocation(
            tenant_id, "owner-1", device_id,
            business_ref={
                "scenario_key": "weixin.fixed_content.v1",
                "task_ref": automation_id, "revision_ref": revision_id,
            },
        )
        # queued → 409 未领取
        with pytest.raises(wxm_assets.AssetEndpointError) as queued:
            wxm_assets.serve_invocation_asset(
                tenant_id, device_id, str(invocation["id"]), meta["id"]
            )
        assert queued.value.code == "INVOCATION_NOT_CLAIMED"
        # 终态 → 409
        self._set_state(tenant_id, str(invocation["id"]), "succeeded")
        with pytest.raises(wxm_assets.AssetEndpointError) as done:
            wxm_assets.serve_invocation_asset(
                tenant_id, device_id, str(invocation["id"]), meta["id"]
            )
        assert done.value.code == "INVOCATION_TERMINATED"

    def test_serve_unbound_asset_404(self, service, tenant_id, bindings, monkeypatch, img_config, img_adapter):
        """revision 未引用的素材（含同租户他人素材）→ 统一 404（任务绑定资产集校验）"""
        _, group_id = bindings
        device_id = str(uuid.uuid4())
        bound = upload_png(tenant_id, config=img_config, color=(1, 2, 3))
        unbound = upload_png(tenant_id, config=img_config, color=(4, 5, 6))
        automation_id, revision_id = self._publish_image_automation(
            service, tenant_id, group_id, bound["id"], monkeypatch, img_config
        )
        invocation = self._make_invocation(
            tenant_id, "owner-1", device_id,
            business_ref={
                "scenario_key": "weixin.fixed_content.v1",
                "task_ref": automation_id, "revision_ref": revision_id,
            },
        )
        self._set_state(tenant_id, str(invocation["id"]), "running")
        with pytest.raises(wxm_assets.AssetEndpointError) as ei:
            wxm_assets.serve_invocation_asset(
                tenant_id, device_id, str(invocation["id"]), unbound["id"]
            )
        assert ei.value.code == "ASSET_NOT_FOUND" and ei.value.http_status == 404

    def test_serve_scenario_mismatch(self, tenant_id, bindings, img_config):
        """business_ref 场景非微信固定内容 → 409（fail-closed）"""
        _, _group_id = bindings
        device_id = str(uuid.uuid4())
        meta = upload_png(tenant_id, config=img_config)
        invocation = self._make_invocation(
            tenant_id, "owner-1", device_id,
            business_ref={
                "scenario_key": "boss.other.v1",
                "task_ref": str(uuid.uuid4()), "revision_ref": str(uuid.uuid4()),
            },
        )
        self._set_state(tenant_id, str(invocation["id"]), "running")
        with pytest.raises(wxm_assets.AssetEndpointError) as ei:
            wxm_assets.serve_invocation_asset(
                tenant_id, device_id, str(invocation["id"]), meta["id"]
            )
        assert ei.value.code == "ASSET_SCENARIO_MISMATCH"

    def test_serve_hash_mismatch_500(self, service, tenant_id, bindings, monkeypatch, img_config, img_adapter):
        """文件字节与登记 hash 漂移 → 500 拒绝下发 + 上下文供审计"""
        _, group_id = bindings
        device_id = str(uuid.uuid4())
        meta = upload_png(tenant_id, config=img_config)
        automation_id, revision_id = self._publish_image_automation(
            service, tenant_id, group_id, meta["id"], monkeypatch, img_config
        )
        invocation = self._make_invocation(
            tenant_id, "owner-1", device_id,
            business_ref={
                "scenario_key": "weixin.fixed_content.v1",
                "task_ref": automation_id, "revision_ref": revision_id,
            },
        )
        self._set_state(tenant_id, str(invocation["id"]), "running")
        detail = wxm_assets.get_asset(tenant_id, "owner-1", meta["id"])
        with open(detail["storage_ref"], "wb") as fh:
            fh.write(b"tampered-bytes")
        with pytest.raises(wxm_assets.AssetEndpointError) as ei:
            wxm_assets.serve_invocation_asset(
                tenant_id, device_id, str(invocation["id"]), meta["id"]
            )
        assert ei.value.code == "ASSET_HASH_MISMATCH" and ei.value.http_status == 500
        assert ei.value.context["asset_id"] == meta["id"]


# ==================== 图片块编译 / 草稿与发布复核 ====================


class TestImageBlockCompile:
    def test_mixed_blocks_compile_in_order(self, service, tenant_id, bindings,
                                           monkeypatch, img_config, img_adapter):
        """text/link/image 混排顺序执行（fake）：编译产物含图片位，payload 字节
        asset:<id> 与冻结 hash 一致；serve_payload 返回受控引用字节。"""
        from dataclasses import asdict

        _, group_id = bindings
        meta = upload_png(tenant_id, config=img_config)
        patch_images_enabled(monkeypatch, img_config)
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[
                {"type": "text", "text_content": "第一条"},
                {"type": "image", "asset_id": meta["id"]},
                {"type": "link", "url": "https://e.com/x"},
            ],
        )
        revision_config = service.load_revision_config(tenant_id, revision_id)
        ops = img_adapter.compile_operations(
            _ctx(tenant_id, automation_id, revision_id), revision_config,
        )
        assert [op.position for op in ops] == [1, 2, 3]
        image_op = next(op for op in ops if op.position == 2)
        expected_bytes = f"asset:{meta['id']}".encode("utf-8")
        assert image_op.payload_hash == hashlib.sha256(expected_bytes).hexdigest()
        assert image_op.payload_hash == wxm_content.payload_hash_of(
            {"kind": "image", "asset_id": meta["id"]}
        )
        # serve_payload 受控字节（hash 复核通过）
        served = img_adapter.serve_payload(
            _ctx(tenant_id, automation_id, revision_id), image_op.payload_ref
        )
        assert served == expected_bytes

    def test_draft_missing_asset_rejected(self, service, tenant_id, bindings, monkeypatch, img_config):
        """草稿引用不存在素材 → 422（保存时即校验，不留悬空引用）"""
        _, group_id = bindings
        patch_images_enabled(monkeypatch, img_config)
        from tests.unit.weixin_marketing.conftest import make_create_payload

        with pytest.raises(WeixinValidationError, match="素材"):
            service.create_automation(
                tenant_id, "owner-1",
                make_create_payload(
                    group_id,
                    blocks=[{"type": "image", "asset_id": str(uuid.uuid4())}],
                ),
            )

    def test_publish_recheck_asset_removed(self, service, tenant_id, bindings,
                                           monkeypatch, img_config, img_adapter):
        """发布复核：草稿保存后素材行被外力删除（模拟旁路）→ 发布 422 拒绝"""
        from src.db.database import get_db_connection
        from src.weixin_marketing.models import PublishInput

        from tests.unit.weixin_marketing.conftest import make_create_payload

        _, group_id = bindings
        meta = upload_png(tenant_id, config=img_config)
        patch_images_enabled(monkeypatch, img_config)
        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(
                group_id, blocks=[{"type": "image", "asset_id": meta["id"]}],
            ),
        )
        automation_id = str(detail["automation"]["id"])
        # 旁路删除素材行（绕过引用保护，模拟异常数据）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM bs_weixin_marketing_assets WHERE tenant_id = %s AND id = %s",
                (tenant_id, meta["id"]),
            )
            conn.commit()
        with pytest.raises(WeixinValidationError, match="素材"):
            service.publish(
                tenant_id, automation_id, "owner-1",
                PublishInput(expected_version=detail["automation"]["version"]),
            )

    def test_images_disabled_blocks_image_draft(self, service, tenant_id, bindings, img_config):
        """images_enabled=false：编辑/发布拒绝图片块（沿既有门禁）"""
        _, group_id = bindings
        from tests.unit.weixin_marketing.conftest import make_create_payload

        with pytest.raises(WeixinValidationError, match="images_enabled"):
            service.create_automation(
                tenant_id, "owner-1",
                make_create_payload(
                    group_id,
                    blocks=[{"type": "image", "asset_id": str(uuid.uuid4())}],
                ),
            )


def _ctx(tenant_id, automation_id, revision_id):
    from src.desktop_automation.adapters import AdapterContext
    from src.weixin_marketing.constants import SCENARIO_KEY

    return AdapterContext(
        tenant_id=tenant_id, user_id="owner-1", scenario_key=SCENARIO_KEY,
        task_ref=str(automation_id), revision_ref=str(revision_id),
    )
