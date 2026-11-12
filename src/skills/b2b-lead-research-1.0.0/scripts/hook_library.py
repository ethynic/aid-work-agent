#!/usr/bin/env python3
"""强钩子库：按业务（business）分区，自动积累 + 自动推荐。

为什么需要这个库：
  强钩子不是通用模板，它高度依赖「你卖什么 + 卖给谁」。同一句 cost 型钩子对
  密封件厂有效，对 AI 营销智能体未必。所以钩子实例必须按业务隔离沉淀，
  并用真实战绩（打开率 / 回复率）排序，而不是靠人肉记忆。

闭环（我方 DB 管线口径）：
  1. register   登记一个业务（产品 + ICP + 语言）-> 建立该业务的钩子库分区
  2. seed/add   写新邮件时，把种子或新造的钩子实例录进库（seed/manual）
  3. record     发送 + 核验后，用 lead_db.py hook-stats 的 JSON 回写真实战绩
  4. recommend  下一轮写邮件前，查库拿「该业务已验证有效」的钩子实例直接套用

库的位置（租户级，跨 campaign 复用，技能升级不会覆盖）：
  storage/tenants/{tenant_id}/lead-gen/hook_library/
  ├── index.json                       业务清单
  └── businesses/<business_key>.json   单个业务的钩子 + 战绩
  可用 B2B_HOOK_LIBRARY 环境变量覆盖（测试隔离用）。

record 输入（lead_db.py hook-stats --campaign-id N 的输出，可传文件或 stdin）：
  {"campaign_id": 12, "hooks": [
      {"hook_id": "cost", "drafted": 30, "sent": 30, "opens": 9, "replies": 2}, ...]}
  我方 replies 表不区分有效/无效回复，战绩中 valid 与 replies 同值。
  market/segment 维度由 --market/--segment 参数标注（同一 campaign 内多市场
  分批发送时，分批 record 各自标注即可，同 campaign 重跑覆盖不叠加）。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def resolve_tenant_id():
    tenant_id = (os.environ.get("AID_TENANT_ID")
                 or os.environ.get("CURRENT_TENANT_ID") or "").strip()
    if not tenant_id:
        print("ERROR: 缺少租户身份（AID_TENANT_ID），"
              "本工具必须经 skill_execute 在智能体执行上下文中调用")
        sys.exit(2)
    return tenant_id


def library_root():
    """钩子库根目录：租户级存储；B2B_HOOK_LIBRARY 仅供测试隔离覆盖。"""
    env = os.environ.get("B2B_HOOK_LIBRARY")
    if env:
        return Path(env).expanduser()
    from src.core.storage import configured_storage_root, normalize_tenant_id
    return (configured_storage_root() / "tenants" / normalize_tenant_id(resolve_tenant_id())
            / "lead-gen" / "hook_library")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(text):
    s = re.sub(r"[^0-9a-zA-Z一-鿿]+", "-", (text or "").strip().lower())
    return s.strip("-") or "default"


def business_path(root, key):
    return Path(root) / "businesses" / f"{key}.json"


def load_business(root, key, create=False):
    p = business_path(root, key)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    if not create:
        return None
    return {
        "business_key": key,
        "name": key,
        "product": "",
        "icp": "",
        "lang": "",
        "segments": [],
        "markets": [],
        "patterns": {},     # pattern(hook_id) -> {"seeds": [...], "manual": [...]}
        "campaigns": {},    # campaign_name -> 一次投放的完整战绩（幂等重算）
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def save_business(root, data):
    root = Path(root)
    (root / "businesses").mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    business_path(root, data["business_key"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    idx_path = root / "index.json"
    index = {}
    if idx_path.exists():
        try:
            index = json.loads(idx_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {}
    index[data["business_key"]] = {
        "name": data.get("name") or data["business_key"],
        "product": data.get("product", ""),
        "icp": data.get("icp", ""),
        "updated_at": data["updated_at"],
    }
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def aggregate(data):
    """把各 campaign 的战绩累加成业务级汇总。

    分组键为 "market|segment|hook_id"：市场（国家/语言）是独立维度——德语钩子
    与英语钩子的效果不能混算，同一钩子换国家后效果常常天差地别。
    """
    agg = {}
    for camp in (data.get("campaigns") or {}).values():
        for key, st in (camp.get("stats") or {}).items():
            d = agg.setdefault(key, {"sent": 0, "opened": 0, "replies": 0,
                                     "valid": 0, "examples": []})
            for f in ("sent", "opened", "replies", "valid"):
                d[f] += st.get(f, 0)
    out = {}
    for key, d in agg.items():
        n = d["sent"] or 1
        out[key] = {
            "sent": d["sent"], "opened": d["opened"], "replies": d["replies"],
            "valid": d["valid"],
            "open_rate": d["opened"] / n,
            "reply_rate": d["replies"] / n,
            "valid_reply_rate": d["valid"] / n,
            "examples": [],
        }
    return out


def split_lib_key(key):
    """把库内分组键 "market|segment|hook_id" 拆成三段。"""
    parts = key.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return "", "", key


def cmd_register(args):
    root = library_root()
    key = args.key or slug(args.name)
    data = load_business(root, key, create=True)
    if args.name:
        data["name"] = args.name
    if args.product:
        data["product"] = args.product
    if args.icp:
        data["icp"] = args.icp
    if args.lang:
        data["lang"] = args.lang
    for s in (args.segment or []):
        if s not in data.setdefault("segments", []):
            data["segments"].append(s)
    save_business(root, data)
    print(f"business registered: {key}  ->  {business_path(root, key)}")
    print(f"  name   : {data['name']}")
    print(f"  product: {data['product']}")
    print(f"  icp    : {data['icp']}")
    print(f"  segment: {', '.join(data['segments']) or '(未指定)'}")
    return 0


def cmd_add_market(args):
    """登记某业务的目标市场（国家码）。市场是每轮问答动态确定的输入。"""
    root = library_root()
    data = load_business(root, args.business, create=True)
    if data is None:
        print(f"ERROR: unknown business '{args.business}'.")
        return 1
    mk = (args.market or "").strip().upper()
    if not mk:
        print("ERROR: --market required (2-letter country code, e.g. DE)")
        return 1
    markets = data.setdefault("markets", [])
    if mk not in markets:
        markets.append(mk)
    lang = args.lang or market_lang(mk)
    data.setdefault("market_langs", {})[mk] = lang
    save_business(root, data)
    print(f"market added: business={args.business} market={mk} lang={lang}")
    print(f"  已知市场: {', '.join(markets)}")
    return 0


def cmd_add(args):
    root = library_root()
    key = args.business
    data = load_business(root, key, create=True)
    if data is None:
        print(f"ERROR: unknown business '{key}'. Run register first.")
        return 1
    if args.segment and args.segment not in data.setdefault("segments", []):
        data["segments"].append(args.segment)
    if args.market and args.market not in data.setdefault("markets", []):
        data["markets"].append(args.market)
    pats = data.setdefault("patterns", {})
    p = pats.setdefault(args.pattern, {"seeds": [], "manual": []})
    rec = {
        "subject": args.subject or "",
        "hook": args.hook or "",
        "cta": args.cta or "",
        "segment": args.segment or "",
        "market": args.market or "",
        "lang": (args.lang or data.get("lang") or ""),
        "note": args.note or "",
        "source": "manual",
        "added_at": now_iso(),
    }
    p["manual"].append(rec)
    save_business(root, data)
    print(f"hook instance added -> business={key} pattern={args.pattern} "
          f"market={rec['market'] or '(未指定)'} lang={rec['lang'] or '-'}")
    print(f"  Subject: {rec['subject']}")
    print(f"  Hook   : {rec['hook']}")
    return 0


def cmd_record(args):
    """从 hook-stats JSON 回写真实战绩（幂等：同一 campaign 重跑覆盖不叠加）。

    一个 campaign 只归属一个业务（我方 campaigns 表按 campaign_id 隔离），
    与上游按 send_log business 列分组不同：这里由 --business 显式指定分区。
    """
    root = library_root()
    if args.stats_file and args.stats_file != "-":
        payload = json.loads(Path(args.stats_file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read())
    hooks = payload.get("hooks") or []
    if not hooks:
        print("ERROR: hook-stats JSON 中无 hooks 数据"
              "（先执行 lead_db.py hook-stats --campaign-id N）")
        return 1

    camp_name = f"campaign_{payload.get('campaign_id')}"
    market = (args.market or "").strip().upper()
    segment = (args.segment or "").strip()

    data = load_business(root, args.business, create=True)
    if args.name:
        data["name"] = args.name
    if args.icp:
        data["icp"] = args.icp

    stats = {}
    for h in hooks:
        hook_id = (h.get("hook_id") or "untagged").strip()
        sent = int(h.get("sent") or 0)
        opened = int(h.get("opens") or 0)
        replies = int(h.get("replies") or 0)
        n = sent or 1
        # 我方 replies 不区分有效/无效，valid 与 replies 同值（见模块 docstring）
        st = {"sent": sent, "opened": opened, "replies": replies,
              "valid": replies,
              "open_rate": opened / n, "reply_rate": replies / n,
              "valid_reply_rate": replies / n,
              "market": market, "segment": segment, "hook_id": hook_id,
              "examples": []}
        stats[f"{market}|{segment}|{hook_id}"] = st
        if segment and segment not in data.setdefault("segments", []):
            data["segments"].append(segment)
        if market and market not in data.setdefault("markets", []):
            data["markets"].append(market)

    data.setdefault("campaigns", {})[camp_name] = {
        "recorded_at": now_iso(),
        "stats": stats,
        "sent": sum(s["sent"] for s in stats.values()),
    }
    save_business(root, data)

    print(f"recorded '{camp_name}' -> business '{args.business}'"
          f"  market={market or '(未指定)'} segment={segment or '(未指定)'}")
    for key, st in sorted(stats.items()):
        print(f"  {key:<36} sent={st['sent']:<4} open={st['open_rate']*100:>5.1f}% "
              f"reply={st['reply_rate']*100:>5.1f}%")
    print(f"  library: {business_path(root, args.business)}")
    return 0


def load_subject_templates():
    """载入结构模板库（没有主题样板时的起点）。"""
    p = SCRIPT_DIR.parent / "assets" / "subject_templates.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def market_lang(market, explicit=""):
    """由市场码推断目标语言（可被 --lang 覆盖）。

    语言只是「渲染目标」，不是「资产」。结构库是语言中立的（见
    assets/subject_templates.json），发送时由智能体按 rendering_rules 重写成
    目标语言。推断不出来时默认 en 即可。
    """
    if explicit:
        return explicit.lower()
    m = (market or "").strip().lower()
    table = {
        # 德语区
        "de": "de", "at": "de", "ch": "de", "liechtenstein": "de", "li": "de", "lu": "de",
        # 英语区
        "us": "en", "gb": "en", "uk": "en", "ie": "en", "au": "en",
        "nz": "en", "sg": "en", "in": "en", "za": "en", "ae": "en",
        "ng": "en", "ke": "en", "gh": "en", "ph": "en", "my": "en",
        "mt": "en", "cy": "en", "bw": "en",
        # 法语区
        "fr": "fr", "be": "fr", "mc": "fr", "sn": "fr", "ci": "fr",
        # 西语区
        "es": "es", "mx": "es", "ar": "es", "cl": "es", "co": "es",
        "pe": "es", "ve": "es", "ec": "es", "uy": "es", "bo": "es",
        "cr": "es", "cu": "es", "do": "es", "gt": "es", "py": "es",
        # 其他欧洲
        "it": "it", "nl": "nl", "pt": "pt", "pl": "pl", "se": "sv",
        "cz": "cs", "sk": "cs", "si": "sl", "hr": "hr", "ro": "ro",
        "bg": "bg", "dk": "da", "fi": "fi", "no": "no", "is": "is",
        "ee": "et", "lv": "lv", "lt": "lt", "ua": "uk", "tr": "tr",
        "ru": "ru", "by": "ru", "kz": "ru", "rs": "sr", "ba": "bs",
        "mk": "mk", "al": "sq", "md": "ro", "ge": "ka", "am": "hy",
        "gr": "el",
        # 亚洲
        "jp": "ja", "kr": "ko", "cn": "zh", "tw": "zh", "hk": "zh",
        "mo": "zh", "vn": "vi", "th": "th", "id": "id",
        "il": "he",
        "sa": "ar", "qa": "ar", "kw": "ar", "bh": "ar", "om": "ar",
        "jo": "ar", "eg": "ar", "ma": "ar", "dz": "ar", "iq": "ar",
        "br": "pt",
    }
    return table.get(m, "en")


def cmd_recommend(args):
    root = library_root()
    data = load_business(root, args.business, create=False)
    if not data:
        print(f"ERROR: unknown business '{args.business}'. "
              f"Run: register --key {args.business}")
        return 1
    agg = aggregate(data)
    # 未指定市场时无法判断语言：只用库里的实例与种子，不给结构模板
    # （给英文模板给中文买家是错的）
    lang = market_lang(args.market, args.lang) if (args.market or args.lang) else ""

    all_rows = []
    for key, st in agg.items():
        market, segment, hook_id = split_lib_key(key)
        all_rows.append({"key": key, "market": market, "segment": segment,
                         "hook_id": hook_id, **st})

    # 市场筛选：优先精确匹配该市场；该市场无数据时回落全市场并标注
    mkt_rows = [r for r in all_rows if r["market"] == args.market
                and (not args.segment or r["segment"] == args.segment)]
    scope = "market"
    if not mkt_rows:
        mkt_rows = [r for r in all_rows
                    if not args.segment or r["segment"] == args.segment]
        scope = "all-markets" if mkt_rows else "none"

    rows = sorted(mkt_rows, key=lambda r: (r["valid_reply_rate"], r["open_rate"],
                                           r["sent"]), reverse=True)

    print("=" * 78)
    print(f"钩子推荐 — 业务: {data.get('name') or data['business_key']}")
    if data.get("product"):
        print(f"产品: {data['product']}")
    if data.get("icp"):
        print(f"ICP : {data['icp']}")
    print(f"市场: {args.market or '(未指定)'}   语言: {lang}   "
          f"客群: {args.segment or '全部'}")
    print("=" * 78)

    tpl_all = load_subject_templates()

    if scope == "all-markets" and args.market:
        print(f"⚠ 该市场 [{args.market}] 尚无实测数据。下面显示的是其他市场的"
              f"战绩（同一钩子换国家后效果可能完全不同，仅供参考）。\n")
    elif scope == "none" and args.market:
        print(f"⚠ 该市场 [{args.market}] 尚无实测数据。本次从结构骨架起步，"
              f"用 {lang} 渲染后发送。\n")
    elif not args.market:
        print("(未指定市场 — 显示全部市场的战绩。record 时务必标注 --market，"
              "否则各市场数据会混算。)\n")

    if rows:
        print(f"{'市场|客群|钩子':<38}{'已发':>5}{'打开率':>8}{'回复率':>8}  结论")
        print("-" * 78)
        for r in rows:
            if r["sent"] >= args.min_sent and r["valid"] > 0:
                verdict = "已验证有效"
            elif r["sent"] >= args.min_sent:
                verdict = "样本够·无效"
            elif r["valid"] > 0:
                verdict = "有信号·样本少"
            else:
                verdict = "未见效果"
            label = f"{r['market'] or '-'}|{r['segment']}|{r['hook_id']}"
            print(f"{label:<38}{r['sent']:>5}{r['open_rate']*100:>7.1f}%"
                  f"{r['reply_rate']*100:>7.1f}%  {verdict}")
        if not any(r["sent"] >= args.min_sent for r in rows):
            print(f"\n(无钩子达到 min-sent={args.min_sent}，样本不足，"
                  f"结论仅供参考，继续累积)")

    # ---------- 实例输出（种子/手写实例；我方战绩不含实例明细，无该市场实测原句） ----------
    print("\n" + "-" * 78)
    print("可直接用的钩子实例")
    print("-" * 78)
    out = []

    # 种子/手写实例：语言匹配的优先；语言不匹配的仅作「机制参考」
    seed_lang = (data.get("lang") or "").lower()
    for pattern, p in (data.get("patterns") or {}).items():
        for rec in (p.get("seeds") or []) + (p.get("manual") or []):
            seg = rec.get("segment") or "default"
            if args.segment and seg != args.segment:
                continue
            rlang = (rec.get("lang") or seed_lang or "").lower()
            same_lang = (not lang) or (not rlang) or rlang == lang
            if same_lang:
                src = "种子实例 · 已是目标语言"
            else:
                # 机制可参考，但原句不能直接发给不同语言的买家
                src = f"跨语言机制参考（原句 {rlang or '?'}，需重写为 {lang}）"
            out.append((pattern, seg,
                        {"subject": rec.get("subject", ""),
                         "hook": rec.get("hook", ""),
                         "cta": rec.get("cta", ""),
                         "company": "", "note": rec.get("note", ""),
                         "lang": rlang},
                        False, src))

    if not out:
        print("(库中暂无实例 — 见下方结构骨架，或用 add/seed 录入)")
    else:
        for pattern, segment, ex, _won, src in out:
            tag = "○ 参考"
            print(f"\n[{pattern} / {segment}]  {tag}")
            print(f"  来源: {src}")
            if ex.get("subject"):
                print(f"  Subject : {ex['subject']}")
            if ex.get("hook"):
                print(f"  Hook    : {ex['hook']}")
            if ex.get("cta"):
                print(f"  CTA     : {ex['cta']}")
            if ex.get("note"):
                print(f"  备注    : {ex['note']}")

    # ---------- 结构骨架（语言中立，渲染成目标语言再发） ----------
    pats = tpl_all.get("patterns") or {}
    if pats:
        print("\n" + "-" * 78)
        print(f"结构骨架（语言中立 — 用 {lang} 渲染后发送）")
        print("-" * 78)
        rr = tpl_all.get("rendering_rules") or {}
        print(f"原则: {rr.get('principle', '')}")
        for step in rr.get("steps", []):
            print(f"  {step}")
        print()

    for pattern, pdata in pats.items():
        print(f"\n【{pattern}】 {pdata.get('intent', '')}")
        print(f"  机制: {pdata.get('mechanism', '')}")
        if pdata.get("when_to_use"):
            print(f"  适用: {pdata['when_to_use']}")
        if pdata.get("avoid"):
            print(f"  忌用: {pdata['avoid']}")
        for st in pdata.get("structures", []):
            print(f"\n  ── {st.get('id')} ──")
            print(f"  Subject骨架 : {st.get('subject_skeleton', '')}")
            print(f"  Hook骨架    : {st.get('hook_lead_skeleton', '')}")
            if st.get("slots"):
                print(f"  槽位        : {', '.join(st['slots'])}")
            if st.get("adapt_notes"):
                print(f"  渲染注意    : {st['adapt_notes']}")

    # ---------- 渲染规则 ----------
    rr = tpl_all.get("rendering_rules") or {}
    if rr:
        print("\n" + "-" * 78)
        print(f"渲染规则（目标语言: {lang}）")
        print("-" * 78)
        print("硬规则:")
        for r in rr.get("hard_rules", []):
            print(f"  - {r}")
        loc = rr.get("localization_by_family") or {}
        if loc:
            print("\n按语言族:")
            for k, v in loc.items():
                print(f"  [{k}] {v}")
        sh = rr.get("slot_handling") or {}
        if sh:
            print("\n槽位取值:")
            for k, v in sh.items():
                print(f"  {k:<12} {v}")
    return 0


def cmd_list(args):
    root = library_root()
    idx_path = Path(root) / "index.json"
    if not idx_path.exists():
        print("钩子库为空。先执行 register 建立一个业务分区。")
        return 0
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    print("=" * 78)
    print(f"钩子库: {root}")
    print("=" * 78)
    for key, meta in index.items():
        data = load_business(root, key)
        agg = aggregate(data) if data else {}
        total_sent = sum(s["sent"] for s in agg.values())
        total_valid = sum(s["valid"] for s in agg.values())
        patterns = len(agg)
        print(f"\n● {key}  ({meta.get('name','')})")
        print(f"  产品: {meta.get('product','') or '-'}")
        print(f"  ICP : {meta.get('icp','') or '-'}")
        print(f"  已投钩子数: {patterns}   累计已发: {total_sent}   "
              f"累计回复: {total_valid}")
        best = sorted(agg.items(),
                      key=lambda kv: kv[1]["valid_reply_rate"],
                      reverse=True)[:3]
        for k, st in best:
            print(f"    - {k:<24} 回复率 {st['valid_reply_rate']*100:.1f}% "
                  f"(已发 {st['sent']})")
    return 0


def cmd_seed(args):
    """批量导入种子钩子实例（从一个 JSON 文件）。"""
    root = library_root()
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    key = args.business or payload.get("business_key") or slug(payload.get("name", ""))
    data = load_business(root, key, create=True)
    if payload.get("name"):
        data["name"] = payload["name"]
    if payload.get("product"):
        data["product"] = payload["product"]
    if payload.get("icp"):
        data["icp"] = payload["icp"]
    if payload.get("lang"):
        data["lang"] = payload["lang"]
    pats = data.setdefault("patterns", {})
    n = 0
    skipped = 0
    for rec in payload.get("seeds", []):
        pat = rec.get("pattern") or "untagged"
        p = pats.setdefault(pat, {"seeds": [], "manual": []})
        # 幂等：同一 subject+segment+market 已存在则跳过（重复导入会污染库）
        dup = any((s.get("subject") == rec.get("subject", "")
                   and s.get("segment") == rec.get("segment", "")
                   and s.get("market", "") == rec.get("market", ""))
                  for s in p["seeds"])
        if dup:
            skipped += 1
            continue
        if rec.get("segment") and rec["segment"] not in data.setdefault("segments", []):
            data["segments"].append(rec["segment"])
        if rec.get("market") and rec["market"] not in data.setdefault("markets", []):
            data["markets"].append(rec["market"])
        p["seeds"].append({
            "subject": rec.get("subject", ""),
            "hook": rec.get("hook", ""),
            "cta": rec.get("cta", ""),
            "segment": rec.get("segment", ""),
            "market": rec.get("market", ""),
            "lang": rec.get("lang", payload.get("lang", "")),
            "note": rec.get("note", ""),
            "source": "seed",
            "added_at": now_iso(),
        })
        n += 1
    save_business(root, data)
    msg = f"imported {n} seed hooks -> business '{key}'  ({business_path(root, key)})"
    if skipped:
        msg += f"  (skipped {skipped} duplicates)"
    print(msg)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="按业务分区的钩子库（租户级存储，战绩来自 lead_db.py hook-stats）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="登记一个业务分区")
    p.add_argument("--key")
    p.add_argument("--name")
    p.add_argument("--product")
    p.add_argument("--icp")
    p.add_argument("--lang")
    p.add_argument("--segment", action="append")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("add", help="录入一条钩子实例")
    p.add_argument("--business", required=True)
    p.add_argument("--pattern", required=True)
    p.add_argument("--subject")
    p.add_argument("--hook")
    p.add_argument("--cta")
    p.add_argument("--segment")
    p.add_argument("--market", help="市场/国家码 (如 DE)。不同市场的钩子不可混用")
    p.add_argument("--lang", help="语言码 (如 de)。留空则用业务默认语言")
    p.add_argument("--note")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("add-market", help="登记业务的目标市场")
    p.add_argument("--business", required=True)
    p.add_argument("--market", required=True, help="2 位国家码，如 DE")
    p.add_argument("--lang", help="语言码；缺省按国家码推断")
    p.set_defaults(func=cmd_add_market)

    p = sub.add_parser("record", help="从 hook-stats JSON 回写战绩"
                                     "（lead_db.py hook-stats 的输出）")
    p.add_argument("--business", required=True)
    p.add_argument("--stats-file", help="hook-stats JSON 文件路径；缺省读 stdin（传 - 同 stdin）")
    p.add_argument("--market", help="市场/国家码，如 DE。本批发送的市场标注")
    p.add_argument("--segment", help="客群标注（可选）")
    p.add_argument("--name", help="补充/修正业务显示名")
    p.add_argument("--icp", help="补充/修正业务 ICP")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("recommend", help="查该业务在指定市场已验证有效的钩子")
    p.add_argument("--business", required=True)
    p.add_argument("--market", help="市场/国家码 (如 DE)。缺省则看全部市场")
    p.add_argument("--lang", help="语言码；缺省按 market 推断")
    p.add_argument("--segment")
    p.add_argument("--min-sent", type=int, default=30,
                   help="视为「已验证」的最小样本量 (default 30)")
    p.set_defaults(func=cmd_recommend)

    p = sub.add_parser("list", help="列出所有业务分区")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("seed", help="批量导入种子实例 JSON")
    p.add_argument("--file", required=True)
    p.add_argument("--business")
    p.set_defaults(func=cmd_seed)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
