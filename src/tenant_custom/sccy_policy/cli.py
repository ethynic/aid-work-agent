"""水城创业网政策同步 CLI（薄壳；语义同 lyjy cli，库名默认 gt_sccy）。

用法:
  python -m src.tenant_custom.sccy_policy.cli --init-source \
      --tenant-id <租户> --db-host <host> --db-user <只读账号>
  python -m src.tenant_custom.sccy_policy.cli --dry-run --tenant-id <租户>
  python -m src.tenant_custom.sccy_policy.cli --limit 5 --tenant-id <租户>
  python -m src.tenant_custom.sccy_policy.cli --tenant-id <租户>
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys


def _init_source(args) -> int:
    from src.db.database import init_postgres_pool
    from src.tenant_custom.sccy_policy.bootstrap import init_source

    init_postgres_pool()
    password = args.db_password or getpass.getpass("客户库密码: ")
    inserted = init_source(args.tenant_id, {
        "host": args.db_host,
        "port": args.db_port,
        "database": args.db_name,
        "user": args.db_user,
        "password": password,
    })
    print(f"[source] {'created' if inserted else 'updated'} tenant={args.tenant_id}", flush=True)
    return 0


def _load_source_row(tenant_id: str) -> dict:
    from src.db.database import get_db_connection, init_postgres_pool

    init_postgres_pool()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_content_sync_sources "
            "WHERE tenant_id = %s AND module = 'sccy_policy'",
            (tenant_id,),
        )
        row = cursor.fetchone()
    if not row:
        raise SystemExit(f"源行不存在：先执行 --init-source（tenant={tenant_id}）")
    return dict(row)


async def _dry_run(args) -> int:
    """全量拉取 + 渲染样例（零写库、零计费）。"""
    from src.tenant_custom.lyjy_employment.fetcher import (
        build_engine,
        load_connection_config,
    )
    from src.tenant_custom.lyjy_employment.renderer import SOURCE_NAME_SCCY, render_article
    from src.tenant_custom.sccy_policy import fetcher

    connection = load_connection_config(_load_source_row(args.tenant_id))
    engine = build_engine(connection)
    try:
        cate_rows, article_rows = await asyncio.to_thread(
            lambda: (fetcher.fetch_cate_rows(engine), fetcher.fetch_articles(engine))
        )
    finally:
        engine.dispose()
    if args.limit:
        article_rows = article_rows[: args.limit]
    cate_maps = fetcher.build_cate_maps(cate_rows)
    valid = [r for r in article_rows if fetcher.article_valid(r, cate_maps["hidden_ids"])]
    print(
        f"[fetch] zc_article 全量 {len(article_rows)} 条（有效 {len(valid)}；"
        f"隐藏分类 {len(cate_maps['hidden_ids'])} 个）",
        flush=True,
    )
    for row in valid[:3]:
        rendered = render_article(
            "zc_article", row,
            category_path=cate_maps["path_of"].get(str(row.get("cate_id")), ""),
            region="",
            source_name=SOURCE_NAME_SCCY,
            id_column="article_id",
        )
        print(f"\n[render] {rendered.external_id} → {rendered.category_display_name} "
              f"hash={rendered.content_hash[:12]}…", flush=True)
        print(rendered.content_md[:600] + ("\n…" if len(rendered.content_md) > 600 else ""),
              flush=True)
    print("\n[dry-run] 未写库、未计费", flush=True)
    return 0


async def _run(args) -> int:
    from src.db.database import init_postgres_pool
    from src.tenant_custom.sccy_policy.service import sccy_policy_service

    init_postgres_pool()
    result = await sccy_policy_service.run_now(args.tenant_id, limit=args.limit)
    print(f"[done] {result}", flush=True)
    return 0 if result.get("status") in ("success", "queued") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="水城创业网政策同步（sccy_policy 模块）")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--init-source", action="store_true", help="建/更新源行（连接配置加密写入）")
    parser.add_argument("--db-host", default="")
    parser.add_argument("--db-port", type=int, default=3306)
    parser.add_argument("--db-name", default="gt_sccy")
    parser.add_argument("--db-user", default="")
    parser.add_argument("--db-password", default="", help="缺省交互输入（getpass）")
    parser.add_argument("--dry-run", action="store_true", help="拉取+渲染样例，不写库不计费")
    parser.add_argument("--limit", type=int, default=None, help="全量截断 N 条（冒烟；截断 run 不做软删对账）")
    args = parser.parse_args()

    if args.init_source:
        return _init_source(args)
    if args.dry_run:
        return asyncio.run(_dry_run(args))
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
