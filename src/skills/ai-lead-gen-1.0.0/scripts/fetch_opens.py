#!/usr/bin/env python3
"""从追踪服务回拉打开记录，合并进 bs_ai_lead_gen_emails（opens_at / open_count）。

移植自上游 b2b-lead-gen fetch_opens.py，改造点：
- 默认端点改为我方域名 https://agent.aidingyi.cn/api/track/opens；
- tracking_key 复用 tracking_key.py（租户级 key store，与发送共用同一把 key）；
- 合并目标从 opens.csv 改为数据库：按归一化 message_id 匹配
  bs_ai_lead_gen_emails（tenant_id 归属），回写 opens_at（最近打开时间）与
  open_count（累计打开次数）。每次运行从游标 0 全量聚合，幂等可重复跑。

用法
----
  python fetch_opens.py --campaign-id ID                 # 回拉并合并
  python fetch_opens.py --campaign-id ID --dry-run       # 只看追踪端数据，不写库
  python fetch_opens.py --campaign-id ID --key <t>.<s>   # 手动指定 key（高级）
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tracking_key import get_or_create_key  # noqa: E402
from lead_db import get_identity, get_conn  # noqa: E402

# 像素端点：邮件正文里嵌的是 https://agent.aidingyi.cn/api/track/p.gif
DEFAULT_ENDPOINT = "https://agent.aidingyi.cn/api/track/opens"
# 分页护栏：打开事件量级远达不到 200 页（每页默认 2000 条），上限只为
# 防止服务端游标异常时无限翻页
MAX_PAGES = 200


def norm_message_id(value):
    """Message-ID 归一化：去尖括号与空白（与 reply_monitor 同一口径）。"""
    return (value or "").strip().strip("<>").strip()


def split_key(key):
    """tracking_key 格式 tenant.secret -> (tenant, secret)。"""
    if not key or "." not in key:
        raise SystemExit("ERROR: tracking_key 格式应为 tenant.secret（8位hex.32位hex）")
    tenant, _, secret = key.partition(".")
    return tenant, secret


def fetch_page(endpoint, key, limit=2000, cursor=0, campaign_filter=""):
    """拉取一页打开记录（游标分页）。"""
    tenant, secret = split_key(key)
    params = {
        "k": key,
        "secret": secret,
        "limit": limit,
        "cursor": cursor,
    }
    if campaign_filter:
        params["c"] = campaign_filter
    url = endpoint + "?" + urllib.parse.urlencode(params)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            print("ERROR: 403 鉴权失败 —— tracking_key 的 secret 不对，"
                  "或格式不是 tenant.secret。", file=sys.stderr)
        elif exc.code == 404:
            print(f"ERROR: 404 —— 端点不存在：{endpoint}。"
                  "请确认追踪服务已部署（默认 https://agent.aidingyi.cn/api/track/opens）。",
                  file=sys.stderr)
        else:
            print(f"ERROR: 追踪服务返回 HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 无法访问追踪端点 {endpoint}\n       {exc}", file=sys.stderr)
        raise SystemExit(1)


def fetch_all(endpoint, key, limit=2000, campaign_filter="", max_pages=MAX_PAGES):
    """翻完所有页，返回全部打开记录列表。

    护栏（上游 v3.3.7 口径）：游标不推进 / 重复立即停止；总页数上限兜底。
    """
    rows = []
    cursor = 0
    seen_cursors = set()
    for page in range(1, max(1, max_pages) + 1):
        data = fetch_page(endpoint, key, limit, cursor, campaign_filter)
        batch = data.get("opens") or []
        rows.extend(batch)
        next_cursor = data.get("next_cursor") or ""
        if not batch or not next_cursor:
            break
        try:
            next_cursor = int(next_cursor)
        except (TypeError, ValueError):
            print(f"WARN: 第 {page} 页游标非法（{next_cursor!r}），停止翻页", file=sys.stderr)
            break
        if next_cursor == cursor or next_cursor in seen_cursors:
            print(f"WARN: 第 {page} 页游标未推进（{next_cursor}），停止翻页；"
                  f"已拉取 {len(rows)} 条，请检查追踪服务端分页实现。", file=sys.stderr)
            break
        seen_cursors.add(cursor)
        cursor = next_cursor
    else:
        print(f"WARN: 达到最大页数 {max_pages}，停止翻页；已拉取 {len(rows)} 条，"
              f"可能有未取回的记录，请重跑或调大 max_pages。", file=sys.stderr)
    return rows


def aggregate(records):
    """按 message_id 聚合：{mid: {"open_count": N, "opens_at": 最近打开}}。"""
    agg = {}
    for rec in records:
        mid = norm_message_id(rec.get("message_id", ""))
        if not mid:
            continue
        opened = (rec.get("opened_at") or "").strip()
        cur = agg.setdefault(mid, {"open_count": 0, "opens_at": ""})
        cur["open_count"] += 1
        if opened > cur["opens_at"]:
            cur["opens_at"] = opened
    return agg


def merge_to_db(tenant_id, agg):
    """把聚合结果回写 bs_ai_lead_gen_emails（按归一化 message_id 匹配）。

    返回 (matched, unmatched)：matched 中包含云端有记录但本地未匹配到的
    message_id 清单（常见于测试邮件 / 追踪 key 换代，如实提示即可）。
    """
    matched = 0
    unmatched = []
    with get_conn() as conn, conn.cursor() as cur:
        for mid, info in agg.items():
            # btrim(message_id, '<>') 与 norm_message_id 同口径（库内存的是原始 <> 包裹值）
            cur.execute(
                "UPDATE bs_ai_lead_gen_emails SET opens_at = %s, open_count = %s, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE tenant_id = %s AND btrim(message_id, '<>') = %s",
                (info["opens_at"] or None, info["open_count"], tenant_id, mid),
            )
            if cur.rowcount > 0:
                matched += cur.rowcount
            else:
                unmatched.append(mid)
        conn.commit()
    return matched, unmatched


def main():
    ap = argparse.ArgumentParser(description="Fetch open records from track service")
    ap.add_argument("--campaign-id", required=True,
                    help="获客活动 id（bs_ai_lead_gen_campaigns.id，用于汇报口径；"
                         "合并按 tenant_id + message_id 全局匹配，不限单 campaign）")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                    help=f"读取端点（默认 {DEFAULT_ENDPOINT}）")
    ap.add_argument("--key", help="tracking_key（格式 tenant.secret）。留空则按发件"
                                  "邮箱自动解析（与发送共用同一把 key）")
    ap.add_argument("--limit", type=int, default=2000, help="每页条数")
    ap.add_argument("--filter-campaign", default="",
                    help="只拉取某个 campaign 的打开记录（传像素 c 参数值）")
    ap.add_argument("--dry-run", action="store_true", help="只看追踪端数据，不写库")
    args = ap.parse_args()

    tenant_id, _user_id = get_identity()

    # 解析 tracking_key：--key 优先；否则按发件邮箱自动解析（三层解析与发送同源）
    if args.key:
        key = args.key
    else:
        from mailbox_resolver import resolve_mailbox_or_die
        mailbox = resolve_mailbox_or_die()
        from_email = (mailbox.get("email_address") or "").strip().lower()
        if not from_email:
            raise SystemExit("ERROR: 邮箱三层解析未返回发件地址，无法解析 tracking_key")
        _t, _s, key = get_or_create_key(from_email, None)
        print(f"[tracking_key 自动解析] tenant={_t}（来自 {from_email}）")

    records = fetch_all(args.endpoint, key, args.limit, args.filter_campaign)
    print(f"追踪端打开记录: {len(records)} 条")
    agg = aggregate(records)
    if agg:
        top = sorted(agg.items(), key=lambda kv: -kv[1]["open_count"])[:5]
        for mid, info in top:
            print(f"  {mid[:40]:<40} opens={info['open_count']} last={info['opens_at'][:19]}")
    else:
        print("暂无打开记录（新发出的邮件需要等收件方打开；纯文本客户端不触发像素）")
    if args.dry_run:
        return 0

    matched, unmatched = merge_to_db(tenant_id, agg)
    print(f"已合并进 bs_ai_lead_gen_emails：匹配 {matched} 封，"
          f"未匹配 {len(unmatched)} 条")
    if unmatched:
        print("  未匹配 message_id（本地无对应已发邮件，忽略或人工核对）:")
        for mid in unmatched[:10]:
            print(f"    {mid}")
    if matched:
        print("下一步：opens_at / open_count 已回写，可在邮件记录中查看打开情况")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
