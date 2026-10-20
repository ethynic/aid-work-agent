#!/usr/bin/env python3
"""宏陶商城知识库存量 metadata 清理：删除 raw_payload 中易变数值字段。

背景（2026-10-20 客户反馈）：商品接口的 stock/sales/comment_score/comment_num
（库存/销量/评分/评论数）源数据内容错误，已要求全链路不同步（fetcher 即弃，
同价格字段口径，见 docs/system/hongtao-shop/hongtao-shop-kb-design.md v1.10；
键列表与 src/tenant_custom/hongtao_shop/fetcher.py EXCLUDED_FIELDS 保持一致，
此处硬编码以便在旧代码环境的 agent2 上独立运行）。

行为：
- 只动 documents.metadata（TEXT JSON 的 raw_payload），不碰 chunks/chunks_vec
  （正文与向量不变，零重嵌）；
- status 任意（含软删 'deleted'）都清理，保持数据一致；
- 幂等：目标键不存在时计零、不写库，可重复执行；
- 默认 dry-run 只统计与抽样（脱敏：只展示键名与值前 50 字符），--apply 才写库。

顺序约束：**先部署新代码再 --apply**。旧代码仍在跑时，下一轮同步 skip 路径
metadata「先比对后写」（renderer 旧版仍输出四键）会把键写回。

用法（agent2 测试环境，容器内执行）:
  docker exec aid-agent-api2 python scripts/hongtao_kb_remove_volatile_fields.py           # dry-run
  docker exec aid-agent-api2 python scripts/hongtao_kb_remove_volatile_fields.py --apply   # 实际清理

legacy 检查：雏形脚本时代（source_type='k_products'，整文档入库，与新链路
不冲突但会重复检索）只统计条数报告，不做任何修改；如需清理 legacy 参考
scripts/hongtao_product_ingest.py 头部的整文档删除 SQL。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# 保证 scripts/ 目录直接运行时能 import src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_TENANT = "tenant_d18c257ff434"
DOC_ORIGIN = "hongtao_shop"

# 易变数值键（与 fetcher.EXCLUDED_FIELDS 中商品接口四字段一致；价格字段自 P1
# 起即排除、存量库中无价格，无需清理）
VOLATILE_KEYS = ("stock", "sales", "comment_score", "comment_num")

_SAMPLE_LIMIT = 2
_VALUE_PREVIEW_CHARS = 50


def clean_metadata_text(text: Optional[str]) -> Tuple[str, Optional[str], List[str]]:
    """解析一行 metadata JSON 并删除 raw_payload 中的易变键。

    返回 (status, new_text, removed_keys)：
    - changed：删除了至少一个目标键，new_text 为写回文本；
    - clean：raw_payload 存在但无目标键（幂等零写）；
    - no_raw_payload：metadata 为空 / 非 JSON 对象 / 无 raw_payload（跳过计数）；
    - bad_json：JSON 解析失败（跳过计数）。

    写回序列化用 ensure_ascii=False + sort_keys=True，与 service._dump_metadata
    （skip 路径「先比对后写」的逐字节比较口径）保持一致。
    """
    if not text:
        return "no_raw_payload", None, []
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return "bad_json", None, []
    if not isinstance(data, dict) or not isinstance(data.get("raw_payload"), dict):
        return "no_raw_payload", None, []
    raw_payload = data["raw_payload"]
    removed = [key for key in VOLATILE_KEYS if key in raw_payload]
    if not removed:
        return "clean", None, []
    for key in removed:
        raw_payload.pop(key)
    return "changed", json.dumps(data, ensure_ascii=False, sort_keys=True), removed


def _preview_sample(external_id: str, metadata_text: str, removed: List[str]) -> str:
    """抽样脱敏输出：只展示键名与值前 50 字符。"""
    try:
        raw_payload = json.loads(metadata_text).get("raw_payload") or {}
    except (TypeError, ValueError):
        raw_payload = {}
    parts = "; ".join(
        f"{key}={str(raw_payload.get(key))[:_VALUE_PREVIEW_CHARS]!r}" for key in removed
    )
    return f"{external_id}: {parts}"


def run(tenant_id: str, apply: bool = False) -> int:
    from src.db.database import get_db_connection, init_postgres_pool

    init_postgres_pool()
    stats = {"changed": 0, "clean": 0, "no_raw_payload": 0, "bad_json": 0}
    key_counts = {key: 0 for key in VOLATILE_KEYS}
    samples: List[str] = []
    legacy_cnt = 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, external_id, status, metadata FROM documents "
            "WHERE tenant_id = %s AND origin = %s AND external_id LIKE %s "
            "ORDER BY id",
            (tenant_id, DOC_ORIGIN, "product:%"),
        )
        rows = cursor.fetchall()
        for row in rows:
            status, new_text, removed = clean_metadata_text(row["metadata"])
            stats[status] += 1
            for key in removed:
                key_counts[key] += 1
            if status == "changed":
                if len(samples) < _SAMPLE_LIMIT:
                    samples.append(_preview_sample(row["external_id"], row["metadata"], removed))
                if apply:
                    cursor.execute(
                        "UPDATE documents SET metadata = %s, updated_at = now() "
                        "WHERE id = %s AND tenant_id = %s",
                        (new_text, row["id"], tenant_id),
                    )
        # legacy 雏形脚本时代（source_type='k_products'）：只统计不修改
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM documents "
            "WHERE tenant_id = %s AND source_type = 'k_products'",
            (tenant_id,),
        )
        legacy_cnt = cursor.fetchone()["cnt"]
        if apply:
            conn.commit()

    mode = "apply" if apply else "dry-run"
    print(f"[{mode}] tenant={tenant_id} origin={DOC_ORIGIN} external_id LIKE 'product:%' "
          f"共 {len(rows)} 行", flush=True)
    print(f"[{mode}] 按键待清理："
          + "，".join(f"{key}={key_counts[key]}" for key in VOLATILE_KEYS)
          + f"；含任一目标键的文档 {stats['changed']} 条", flush=True)
    print(f"[{mode}] 跳过：无 raw_payload {stats['no_raw_payload']} 条，"
          f"JSON 解析失败 {stats['bad_json']} 条；无目标键（幂等零写）{stats['clean']} 条",
          flush=True)
    for sample in samples:
        print(f"[{mode}] 抽样（脱敏，值截前 {_VALUE_PREVIEW_CHARS} 字符）：{sample}", flush=True)
    print(f"[{mode}] legacy 检查：source_type='k_products' 共 {legacy_cnt} 条（只报告不清理，"
          f"如需清理参考 scripts/hongtao_product_ingest.py 头部 SQL）", flush=True)
    if not apply:
        print(f"[{mode}] 未写库。确认统计无误后加 --apply 执行（务必先部署新代码，"
              f"否则旧代码下一轮同步会把键写回）", flush=True)
    else:
        print(f"[apply] 已写库（仅 documents.metadata，chunks/chunks_vec 未动、零重嵌）。"
              f"可重跑本脚本验证幂等（计数应为 0）", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="宏陶知识库存量 metadata 易变数值字段清理")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT,
                        help=f"目标租户（默认 {DEFAULT_TENANT}）")
    parser.add_argument("--apply", action="store_true",
                        help="实际写库（默认 dry-run 只统计与抽样）")
    args = parser.parse_args()
    return run(args.tenant_id, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
