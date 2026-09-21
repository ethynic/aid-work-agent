"""hongtao_shop service 单测：三态判定 + 对账三路径 + 门禁 + knowledge 回写分支。

注入点（monkeypatch）：service 模块命名空间的 build_client/fetch_products/
fetch_posts/describe_images_cached；构造器注入 fake embedding client。
真实 DB（documents/chunks/chunks_vec/knowledge_categories + 六表）。
"""

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pytest

import src.tenant_custom.hongtao_shop.service as service_mod
from src.tenant_custom.hongtao_shop.fetcher import FetchResult
from src.tenant_custom.hongtao_shop.service import HongtaoShopSyncService
from src.tenant_custom.hongtao_shop.vision import CachedVision


def _load_real_vector_db():
    """按文件位置加载真实 vector_db 模块（根 conftest 已把
    src.knowledge.vector_db.vector_db 替换为 stub，无法常规导入；照 wechat_mp 同款）。"""
    file_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "knowledge" / "vector_db" / "vector_db.py"
    )
    spec = importlib.util.spec_from_file_location("_real_vector_db_for_hts_test", str(file_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _real_vector_db_patch(monkeypatch):
    """service 与 knowledge.service 内 get_vector_db 替换为真实 pgvector 实现
    （根 conftest 的是 stub；knowledge 侧 delete_document 回写分支用例需要）。"""
    module = _load_real_vector_db()
    monkeypatch.setattr(service_mod, "get_vector_db", module.get_vector_db)
    import src.knowledge.service as knowledge_mod
    monkeypatch.setattr(knowledge_mod, "get_vector_db", module.get_vector_db)


# ------------------------------- fakes -------------------------------


@dataclass
class FetchScript:
    """一次 run 的脚本化接口返回。"""

    products: List[dict] = field(default_factory=list)
    posts: List[dict] = field(default_factory=list)
    fetch_complete: bool = True


def _product(pid, name="TFZJ1890014欧典米灰", status="1", **overrides):
    item = {
        "id": pid,
        "name": name,
        "procode": "",
        "sellpoint": "通体大理石",
        "cid": "12",
        "status": status,
        "pic": f"https://oss/pic{pid}.jpg",
        "pics": [f"https://oss/p{pid}_1.jpg"],
        "detail": json.dumps([{"content": '<img src="https://oss/d%d.jpg"/><p>防滑耐磨</p>' % pid}]),
        "video": "",
        "stock": "1000",
        "sales": "9",
        "comment_score": "5.0",
        "comment_num": "2",
        "createtime": "1758432000",
        # 价格字段故意注入：fetcher 即弃后全链路（目录/渲染/入库）不得再见
        "market_price": "399.00",
        "sell_price": "299.00",
    }
    item.update(overrides)
    return item


def _post(post_id, content, catename="工地实景"):
    return {"id": post_id, "content": content, "catename": catename,
            "pics": ["https://oss/f1.jpg"], "video": ""}


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = 0
        self.last_usage_tokens = 100

    def reset_usage(self):
        pass

    async def embed_batch(self, texts, batch_size=10):
        self.calls += len(texts)
        return [[0.01] * 1024 for _ in texts]


async def _fake_describe_cached(tenant_id, urls, parser=None):
    """无 VL 新图：全部按缓存命中返回固定描述。"""
    vision_map = {
        u: CachedVision(image_url=u, description=f"第{u[-5]}张砖图",
                        model="GLM-5.3-Flash", status="ok", is_billed=True)
        for u in urls
    }
    return vision_map, [], []


@pytest.fixture()
def harness(tenant_id, monkeypatch):
    """注入脚本化 fetch + fake VL + fake embedding；提供便捷查询。"""
    scripts: List[FetchScript] = []
    current: List[FetchScript] = []  # 本轮 run 正在消费的脚本（products 消费后 posts 复用）

    async def fake_fetch_products(client, limit=None):
        script = scripts.pop(0) if scripts else FetchScript()
        current.clear()
        current.append(script)
        return FetchResult(
            items=list(script.products),
            fetch_complete=script.fetch_complete and (limit is None),
            total_reported=len(script.products),
        )

    async def fake_fetch_posts(client, limit=None):
        script = current[0] if current else FetchScript()
        return FetchResult(items=list(script.posts), fetch_complete=True,
                           total_reported=len(script.posts))

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(service_mod, "build_client", lambda: _FakeClient())
    monkeypatch.setattr(service_mod, "fetch_products", fake_fetch_products)
    monkeypatch.setattr(service_mod, "fetch_posts", fake_fetch_posts)
    monkeypatch.setattr(service_mod, "describe_images_cached", _fake_describe_cached)

    service = HongtaoShopSyncService(embedding_client=FakeEmbeddingClient())

    from src.db.database import get_db_connection

    def create_source(selection_mode="all", selected_ids=None):
        # 余额预检依赖 tenants 行（照 wechat_mp 测试同款造法）
        from src.core.cache_utils import invalidate_tenant_cache

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tenants (tenant_id, company_name, credit_balance)
                VALUES (%s, 'hongtao_shop 测试', 10000)
                ON CONFLICT (tenant_id) DO UPDATE
                SET credit_balance = EXCLUDED.credit_balance
                """,
                (tenant_id,),
            )
            cursor.execute(
                """
                INSERT INTO bs_content_sync_sources
                    (tenant_id, module, enabled, sync_interval_hours,
                     selection_mode, selected_ids)
                VALUES (%s, 'hongtao_shop', TRUE, 24, %s, %s)
                ON CONFLICT (tenant_id, module) DO UPDATE
                SET selection_mode = EXCLUDED.selection_mode,
                    selected_ids = EXCLUDED.selected_ids
                """,
                (tenant_id, selection_mode,
                 json.dumps(selected_ids) if selected_ids else None),
            )
            conn.commit()
        invalidate_tenant_cache(tenant_id)

    def query(sql, params=()):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            if cursor.description is None:
                conn.commit()
                return []
            return [dict(r) for r in cursor.fetchall()]

    return {
        "tenant_id": tenant_id, "service": service, "scripts": scripts,
        "create_source": create_source, "query": query,
        "embedding": service._embedding_client,
    }


async def test_full_pipeline_new_products(harness):
    """全链路新建：run success、三表落库、分类惰性建、论坛关联进 metadata。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(
        products=[_product(1), _product(2, name="TPJ157042米克萨斯")],
        posts=[_post(419, "TPJ157042地面铺贴实景")],
    ))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["status"] == "success"
    assert result["counts"]["new"] == 2

    docs = harness["query"](
        "SELECT title, origin, external_id, status, source_type, metadata "
        "FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop' ORDER BY id",
        (harness["tenant_id"],),
    )
    assert len(docs) == 2
    assert {d["external_id"] for d in docs} == {"product:1", "product:2"}
    assert all(d["status"] == "active" for d in docs)
    # 分类：租户知识库顶级「产品」普通分类（查无则建），文档全部归属它
    cats = harness["query"](
        "SELECT source_type FROM knowledge_categories "
        "WHERE tenant_id = %s AND display_name = '产品' AND parent_id IS NULL",
        (harness["tenant_id"],),
    )
    assert len(cats) == 1
    assert all(d["source_type"] == cats[0]["source_type"] for d in docs)
    # 论坛关联：TPJ157042 帖挂到产品 2
    meta2 = json.loads(next(d["metadata"] for d in docs if d["external_id"] == "product:2"))
    assert len(meta2["forum_media"]) == 1 and meta2["forum_media"][0]["post_id"] == "419"
    # chunks：一产品一 chunk（whole）
    chunk_rows = harness["query"](
        "SELECT c.doc_id, COUNT(*) AS cnt FROM chunks c "
        "JOIN documents d ON d.id = c.doc_id "
        "WHERE d.tenant_id = %s AND d.origin = 'hongtao_shop' GROUP BY c.doc_id",
        (harness["tenant_id"],),
    )
    assert len(chunk_rows) == 2
    assert all(row["cnt"] == 1 for row in chunk_rows)
    # 价格三处零出现之 chunks 正文查（fixture 已注入 market_price/sell_price）
    chunk_texts = harness["query"](
        "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.tenant_id = %s AND d.origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )
    assert chunk_texts
    for row in chunk_texts:
        assert "399" not in row["text"] and "299" not in row["text"]
        assert "market_price" not in row["text"] and "sell_price" not in row["text"]
    # products 账本：hash/doc_id 就位
    products = harness["query"](
        "SELECT native_id, content_hash, doc_id, miss_streak, processing_status "
        "FROM bs_content_sync_records WHERE tenant_id = %s",
        (harness["tenant_id"],),
    )
    assert all(p["content_hash"] and p["doc_id"] and p["miss_streak"] == 0
               and p["processing_status"] == "success" for p in products)
    # 论坛关联为内存态（零关系表）：已由上方 metadata.forum_media 断言覆盖
    # 价格零出现（chunks + metadata 双查）
    for d in docs:
        assert "price" not in (d["metadata"] or "").lower()


async def test_idempotent_rerun_zero_embedding(harness):
    """重跑幂等：hash 未变全 skip，零 embedding、不写 documents 行。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    doc = harness["query"](
        "SELECT id, updated_at, metadata FROM documents "
        "WHERE tenant_id = %s AND origin = 'hongtao_shop'", (harness["tenant_id"],),
    )[0]

    harness["scripts"].append(FetchScript(products=[_product(1)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 1 and result["counts"]["new"] == 0
    assert harness["embedding"].calls == calls_after_first  # 零重嵌

    doc2 = harness["query"](
        "SELECT id, updated_at FROM documents WHERE id = %s", (doc["id"],),
    )[0]
    assert doc2["updated_at"] == doc["updated_at"]  # metadata 未变不写行不抖 updated_at


async def test_metadata_only_change_updates_without_reembed(harness):
    """易变数值（stock）变化：metadata 更新、零重嵌（设计 §5.1 先比对后写）。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    harness["scripts"].append(FetchScript(products=[_product(1, stock="42")]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 1  # hash 未变仍 skip
    assert harness["embedding"].calls == calls_after_first
    doc = harness["query"](
        "SELECT metadata FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )[0]
    assert json.loads(doc["metadata"])["stock"] == "42"


async def test_off_shelf_soft_delete_and_restore(harness):
    """下架立即软删；重新上架同内容 restore 复用旧 chunks 零计费。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    # 下架
    harness["scripts"].append(FetchScript(products=[_product(1, status="0")]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["delete"] == 1
    doc = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )[0]
    assert doc["status"] == "deleted"
    chunks_kept = harness["query"](
        "SELECT COUNT(*) AS cnt FROM chunks WHERE doc_id IN "
        "(SELECT id FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop')",
        (harness["tenant_id"],),
    )[0]["cnt"]
    assert chunks_kept == 1  # 软删保留 chunks（restore 复用前提）

    # 重新上架（内容未变）
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["restore"] == 1
    doc = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )[0]
    assert doc["status"] == "active"
    assert harness["embedding"].calls == calls_after_first  # restore 零计费


async def test_missing_two_strike(harness):
    """消失两击：第一轮 miss_streak=1 不删；第二轮软删。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    await harness["service"].run_now(harness["tenant_id"])

    # 产品 2 从接口消失（total=1 一致，fetch_complete）
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    doc2 = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND external_id = 'product:2'",
        (harness["tenant_id"],),
    )[0]
    assert doc2["status"] == "active"  # 第一击不删
    product2 = harness["query"](
        "SELECT miss_streak FROM bs_content_sync_records "
        "WHERE tenant_id = %s AND native_id = '2'", (harness["tenant_id"],),
    )[0]
    assert product2["miss_streak"] == 1

    harness["scripts"].append(FetchScript(products=[_product(1)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["delete"] == 1
    doc2 = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND external_id = 'product:2'",
        (harness["tenant_id"],),
    )[0]
    assert doc2["status"] == "deleted"


async def test_truncated_run_does_not_advance_miss_streak(harness):
    """截断 run（fetch_complete=False）：不推进 miss_streak、不做删除对账。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    await harness["service"].run_now(harness["tenant_id"])

    # 截断：fetch_complete=False
    script = FetchScript(products=[_product(1)])
    script.fetch_complete = False
    harness["scripts"].append(script)
    await harness["service"].run_now(harness["tenant_id"])
    product2 = harness["query"](
        "SELECT miss_streak FROM bs_content_sync_records "
        "WHERE tenant_id = %s AND native_id = '2'", (harness["tenant_id"],),
    )[0]
    assert product2["miss_streak"] == 0  # 门禁拦截，不推进
    doc2 = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND external_id = 'product:2'",
        (harness["tenant_id"],),
    )[0]
    assert doc2["status"] == "active"


async def test_deselection_soft_deletes(harness):
    """取消勾选：selection_mode=ids 未选中的已入库产品立即软删。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    await harness["service"].run_now(harness["tenant_id"])

    harness["create_source"](selection_mode="ids", selected_ids=["1"])
    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["delete"] == 1
    doc2 = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND external_id = 'product:2'",
        (harness["tenant_id"],),
    )[0]
    assert doc2["status"] == "deleted"
    doc1 = harness["query"](
        "SELECT status FROM documents WHERE tenant_id = %s AND external_id = 'product:1'",
        (harness["tenant_id"],),
    )[0]
    assert doc1["status"] == "active"


async def test_user_deleted_suppress_and_rebuild(harness):
    """用户知识库删除：hash 未变静默跳过零计费；hash 变视为重新发布重建。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    # 模拟用户删除：doc 物理删 + 回写 user_deleted（走 knowledge 分支的等价状态）
    doc = harness["query"](
        "SELECT id FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )[0]
    harness["query"]("DELETE FROM chunks WHERE doc_id = %s", (doc["id"],))
    harness["query"]("DELETE FROM documents WHERE id = %s", (doc["id"],))
    harness["query"](
        "UPDATE bs_content_sync_records SET user_deleted = TRUE "
        "WHERE tenant_id = %s AND native_id = '1'", (harness["tenant_id"],),
    )

    # hash 未变：静默跳过，不重建不重嵌
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 1
    assert harness["embedding"].calls == calls_after_first
    assert harness["query"](
        "SELECT COUNT(*) AS cnt FROM documents WHERE tenant_id = %s "
        "AND origin = 'hongtao_shop'", (harness["tenant_id"],),
    )[0]["cnt"] == 0

    # hash 变（商品改名）：视为重新发布，重建并清 user_deleted
    harness["scripts"].append(FetchScript(products=[_product(1, name="TFZJ1890014欧典米灰升级版")]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["new"] == 1
    product = harness["query"](
        "SELECT user_deleted, doc_id FROM bs_content_sync_records "
        "WHERE tenant_id = %s AND native_id = '1'", (harness["tenant_id"],),
    )[0]
    assert product["user_deleted"] is False and product["doc_id"] is not None


async def test_kb_delete_detected_by_sync_self_healing(harness):
    """用户从知识库删除（硬删三表、无任何回写）→ 下轮 sync 自愈判定置 user_deleted
    并静默跳过零计费（平台 knowledge 层零侵入的删除抑制）。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    # 模拟用户在知识库界面删除：硬删 chunks/documents（与 knowledge delete_document
    # 同款三表硬删；本用例验证的是不依赖任何回写的同步侧检测）
    doc = harness["query"](
        "SELECT id FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )[0]
    harness["query"]("DELETE FROM chunks WHERE doc_id = %s", (doc["id"],))
    harness["query"]("DELETE FROM documents WHERE id = %s", (doc["id"],))

    harness["scripts"].append(FetchScript(products=[_product(1)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 1
    assert harness["embedding"].calls == calls_after_first  # 零重建零计费
    product = harness["query"](
        "SELECT user_deleted FROM bs_content_sync_records "
        "WHERE tenant_id = %s AND native_id = '1'", (harness["tenant_id"],),
    )[0]
    assert product["user_deleted"] is True  # 自愈置位


async def test_update_path_rebuilds_chunks(harness):
    """update 路径：hash 变 → 同 doc 行覆盖更新、chunks/vec 删旧重建、action=update。"""
    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1)]))
    await harness["service"].run_now(harness["tenant_id"])
    doc = harness["query"](
        "SELECT id, title FROM documents WHERE tenant_id = %s AND origin = 'hongtao_shop'",
        (harness["tenant_id"],),
    )[0]
    old_chunk = harness["query"](
        "SELECT id FROM chunks WHERE doc_id = %s ORDER BY id", (doc["id"],),
    )[0]
    assert harness["query"](
        "SELECT COUNT(*) AS cnt FROM chunks_vec WHERE chunk_id = %s",
        (old_chunk["id"],),
    )[0]["cnt"] == 1

    harness["scripts"].append(
        FetchScript(products=[_product(1, name="TFZJ1890014欧典米灰升级版")])
    )
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["update"] == 1 and result["status"] == "success"

    doc2 = harness["query"]("SELECT id, title FROM documents WHERE id = %s", (doc["id"],))[0]
    assert doc2["id"] == doc["id"]  # 同行覆盖，不新建
    assert doc2["title"] == "TFZJ1890014欧典米灰升级版"
    new_chunks = harness["query"](
        "SELECT id FROM chunks WHERE doc_id = %s ORDER BY id", (doc["id"],)
    )
    assert len(new_chunks) == 1 and new_chunks[0]["id"] != old_chunk["id"]  # 删旧重建
    assert harness["query"](
        "SELECT COUNT(*) AS cnt FROM chunks_vec WHERE chunk_id = %s",
        (new_chunks[0]["id"],),
    )[0]["cnt"] == 1
    # 观测账本：action/embedding tokens 落列，计费状态可确认（不停留 pending）
    item = harness["query"](
        "SELECT i.action, i.billing_status, i.embedding_tokens, i.vl_images "
        "FROM bs_content_sync_items i WHERE i.run_id = "
        "(SELECT MAX(id) FROM bs_content_sync_runs WHERE tenant_id = %s)",
        (harness["tenant_id"],),
    )[0]
    assert item["action"] == "update"
    assert item["embedding_tokens"] > 0
    assert item["billing_status"] in ("charged", "not_required", "unknown")
    assert item["billing_status"] != "pending"


async def test_backoff_blocks_processing(harness):
    """失败退避强制消费：next_retry_at 未来 → 本轮跳过；过期 → 恢复处理。"""
    from src.db.database import get_db_connection

    harness["create_source"]()
    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    # 产品 2 置退避（next_retry_at 1 小时后）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_records SET next_retry_at = now() + interval '1 hour', "
            "processing_status = 'sync_failed' WHERE tenant_id = %s AND native_id = '2'",
            (harness["tenant_id"],),
        )
        conn.commit()

    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 1  # 只有产品 1 被处理，产品 2 退避跳过
    assert harness["embedding"].calls == calls_after_first

    # 退避过期 → 恢复处理（hash 未变走 skip 零计费）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_records SET next_retry_at = now() - interval '1 second' "
            "WHERE tenant_id = %s AND native_id = '2'", (harness["tenant_id"],),
        )
        conn.commit()
    harness["scripts"].append(FetchScript(products=[_product(1), _product(2)]))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 2


def test_trigger_dedupes_queued_run(harness):
    """在队去重：已有 queued/running 时 trigger 返回既有 run（防连点堆积整轮同步）。"""
    harness["create_source"]()
    first = harness["service"].trigger_sync(harness["tenant_id"])
    assert first["status"] == "queued" and not first.get("deduped")
    second = harness["service"].trigger_sync(harness["tenant_id"])
    assert second["status"] == "queued" and second.get("deduped") is True
    assert second["run_id"] == first["run_id"]
    runs = harness["query"](
        "SELECT COUNT(*) AS cnt FROM bs_content_sync_runs WHERE tenant_id = %s",
        (harness["tenant_id"],),
    )
    assert runs[0]["cnt"] == 1


async def test_claim_conflict_with_existing_running(harness):
    """同租户已有 running → 领取返回 conflict（唯一索引闸门）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'running')", (harness["tenant_id"],),
        )
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'queued')", (harness["tenant_id"],),
        )
        conn.commit()
    claimed = harness["service"]._claim_next_run(harness["tenant_id"], "owner-x")
    assert claimed == "conflict"


async def test_no_credit_skips_run(harness, monkeypatch):
    """余额预检：≤0 → run 直接终态 skipped_no_credit 不拉取。"""
    monkeypatch.setattr(
        HongtaoShopSyncService, "_check_credit", lambda self, t: (False, "积分余额已耗尽")
    )
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["status"] == "skipped_no_credit"
    runs = harness["query"](
        "SELECT status FROM bs_content_sync_runs WHERE tenant_id = %s",
        (harness["tenant_id"],),
    )
    assert runs and runs[0]["status"] == "skipped_no_credit"
