#!/usr/bin/env python3
"""宏陶商城产品知识库同步 CLI（薄壳，业务全在 src/tenant_custom/hongtao_shop/）。

重构自一次性雏形脚本（upload_document + title 幂等 + LLM 摘要路径已废弃）：
- 全链路走 HongtaoShopSyncService.run_now（origin='hongtao_shop' 幂等 upsert、
  VL 图级缓存、三态判定、对账三路径、计费）；
- 雏形残留清点（若曾在 agent2 跑过）：旧数据在分类 k_products 下、origin=
  manual_upload，与新链路不冲突但会重复检索，清理 SQL：
    DELETE FROM chunks_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id IN
      (SELECT id FROM documents WHERE tenant_id='<租户>' AND source_type='k_products'));
    DELETE FROM chunks WHERE doc_id IN
      (SELECT id FROM documents WHERE tenant_id='<租户>' AND source_type='k_products');
    DELETE FROM documents WHERE tenant_id='<租户>' AND source_type='k_products';
    DELETE FROM knowledge_categories WHERE tenant_id='<租户>' AND source_type='k_products';

用法（agent2 测试环境，宿主机执行）:
  python scripts/hongtao_product_ingest.py --init-source          # 建默认源行（幂等）
  python scripts/hongtao_product_ingest.py --dry-run              # 拉取+渲染样例，零 VL 零写库
  python scripts/hongtao_product_ingest.py --limit 3              # 冒烟（截断不触发对账删除）
  docker exec -d aid-agent-api2 sh -c \
    'python scripts/hongtao_product_ingest.py > /tmp/hongtao_sync.log 2>&1'   # 全量后台
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# 保证 scripts/ 目录直接运行时能 import src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_TENANT = "tenant_d18c257ff434"


async def _dry_run() -> int:
    """拉取双接口 + 渲染 ≤3 条样例（无 VL、不写库）。"""
    from src.tenant_custom.hongtao_shop.fetcher import build_client, fetch_posts, fetch_products
    from src.tenant_custom.hongtao_shop.joiner import build_model_index, match_post
    from src.tenant_custom.hongtao_shop.renderer import render_product

    async with build_client() as client:
        products_fr = await fetch_products(client)
        posts_fr = await fetch_posts(client)
    print(f"[fetch] 商品 {len(products_fr.items)} 条（complete={products_fr.fetch_complete} "
          f"total={products_fr.total_reported}）；论坛帖 {len(posts_fr.items)} 条", flush=True)

    index = build_model_index(products_fr.items)
    linked = sum(1 for p in posts_fr.items if match_post(str(p.get("content") or ""), index).linked_native_ids)
    print(f"[join] 论坛帖可关联（含多挂）：{linked}/{len(posts_fr.items)}", flush=True)

    sync_date = datetime.now().strftime("%Y-%m-%d")
    for item in products_fr.items[:3]:
        rendered = render_product(item, {}, [], sync_date)
        print(f"\n[dry-run] external_id={rendered.metadata['trace']['native_id'] and 'product:' + rendered.metadata['trace']['native_id']}"
              f" hash={rendered.content_hash[:12]}…", flush=True)
        print(rendered.content_md[:400] + ("\n…" if len(rendered.content_md) > 400 else ""), flush=True)
    print("\n[dry-run] 未写库、未调 VL、未计费", flush=True)
    return 0


def _init_source(tenant_id: str) -> int:
    from src.db.database import get_db_connection, init_postgres_pool
    from src.tenant_custom.hongtao_shop.bootstrap import ensure_billing_seed

    init_postgres_pool()
    ensure_billing_seed()  # 计费种子行由模块自举（平台 DDL 不含租户专名）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_content_sync_sources
                (tenant_id, module, enabled, sync_interval_hours,
                 selection_mode, selected_ids)
            VALUES (%s, 'hongtao_shop', TRUE, 24, 'all', NULL)
            ON CONFLICT (tenant_id, module) DO NOTHING
            RETURNING id
            """,
            (tenant_id,),
        )
        row = cursor.fetchone()
        conn.commit()
    print(f"[source] {'已创建' if row else '已存在'}：tenant={tenant_id} enabled=TRUE interval=24h mode=all", flush=True)
    return 0


async def _run(tenant_id: str, limit: int | None) -> int:
    from src.db.database import init_postgres_pool
    from src.tenant_custom.hongtao_shop.service import hongtao_shop_service

    init_postgres_pool()
    result = await hongtao_shop_service.run_now(tenant_id, limit=limit)
    print(f"[done] {result}", flush=True)
    return 0 if result.get("status") in ("success", "queued") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="宏陶商城产品知识库同步（hongtao_shop 模块）")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT)
    parser.add_argument("--init-source", action="store_true", help="建默认源行（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="拉取+渲染样例，不写库不调 VL")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个商品（冒烟；截断不触发对账删除）")
    args = parser.parse_args()

    if args.init_source:
        return _init_source(args.tenant_id)
    if args.dry_run:
        return asyncio.run(_dry_run())
    return asyncio.run(_run(args.tenant_id, args.limit))


if __name__ == "__main__":
    sys.exit(main())
