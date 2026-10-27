#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""邮箱职能相关性判定 —— 全技能唯一事实源。

解决什么问题
------------
「官网上抓到了邮箱」不等于「拿到了可用的联系邮箱」。真实情况是三类邮箱
经常混在一起，而旧逻辑把它们当成同一档：

    hr@ / jobs@ / careers@      招聘邮箱   —— 与产品推销完全无关
    legal@ / finance@ / press@  其他职能部门 —— 与产品推销无关
    info@ / contact@ / office@  公开联系窗口 —— 没有负责人，只是个信箱
    purchasing@ / export@       对口的部门邮箱 —— 这才是目标

旧实现把前两类判成 `generic` 且 `confidence=high`，于是
「抓到就能发」，大量无关地址直接进了发送队列；而真正对口的
`purchasing@` 反被当成「通用邮箱」，没有优先权。

本模块把判定拆成三个正交的维度，四个脚本共用同一套口径：

    type        形态   personal / department / generic / unknown
    function    职能   purchasing / sales / hr / legal / ... （业务归属）
    relevance   相关性 relevant / generic / unknown / irrelevant

只有 `relevance == "relevant"` 的邮箱才算有效目标邮箱。
「与当前产品/服务对应的部门」由 campaign 级的 `contact_targets.json`
声明，见 `load_target_config()`。

用法
----
```bash
python email_rules.py --email hr@acme.com --email purchasing@acme.com
python email_rules.py --email info@acme.com --target-functions purchasing,sales
python email_rules.py --show-functions
```
"""

import argparse
import json
import re
from pathlib import Path

# --------------------------------------------------------------- 职能词表

# 本地部分 → 业务职能。覆盖英/德/法/西/意等外贸主流语种的常见写法。
FUNCTION_MAP = {
    # —— 采购 / 寻源
    "purchasing": "purchasing",
    "purchase": "purchasing",
    "purchases": "purchasing",
    "procurement": "purchasing",
    "procure": "purchasing",
    "sourcing": "purchasing",
    "supplier": "purchasing",
    "supply": "purchasing",
    "supplies": "purchasing",
    "supplychain": "purchasing",
    "buyer": "purchasing",
    "buying": "purchasing",
    "einkauf": "purchasing",
    "beschaffung": "purchasing",
    "achats": "purchasing",
    "aprovisionamiento": "purchasing",
    "approvisionnement": "purchasing",
    # —— 销售 / 商务
    "sales": "sales",
    "sale": "sales",
    "export": "sales",
    "exports": "sales",
    "import": "sales",
    "imports": "sales",
    "business": "sales",
    "businessdevelopment": "sales",
    "bd": "sales",
    "commercial": "sales",
    "vertrieb": "sales",
    "verkauf": "sales",
    "ventes": "sales",
    "ventas": "sales",
    "comercial": "sales",
    "exportacao": "sales",
    "partner": "sales",
    "partners": "sales",
    "cooperation": "sales",
    "trade": "sales",
    "overseas": "sales",
    "global": "sales",
    "inside-sales": "sales",
    "outside-sales": "sales",
    "orders": "sales",
    "order": "sales",
    # —— 工程 / 技术 / 质量
    "engineering": "engineering",
    "engineer": "engineering",
    "technical": "engineering",
    "tech": "engineering",
    "rd": "engineering",
    "research": "engineering",
    "development": "engineering",
    "quality": "engineering",
    "qa": "engineering",
    "qc": "engineering",
    "test": "engineering",
    "testing": "engineering",
    "laboratory": "engineering",
    # —— 生产 / 运营 / 物流
    "production": "production",
    "plant": "production",
    "factory": "production",
    "operations": "production",
    "ops": "production",
    "produktion": "production",
    "logistics": "logistics",
    "logistik": "logistics",
    "warehouse": "logistics",
    "shipping": "logistics",
    "fulfilment": "logistics",
    "dispatch": "logistics",
    # —— 管理层
    "ceo": "management",
    "owner": "management",
    "founder": "management",
    "president": "management",
    "director": "management",
    "leadership": "management",
    "managingdirector": "management",
    "managing-director": "management",
    "md": "management",
    "gm": "management",
    "generalmanager": "management",
    "general-manager": "management",
    "geschaeftsfuehrer": "management",
    "geschäftsführer": "management",
    "direction": "management",
    "direttore": "management",
    "directeur": "management",
    # —— 公开联系窗口（无具体负责人）
    "info": "general",
    "information": "general",
    "contact": "general",
    "hello": "general",
    "hi": "general",
    "mail": "general",
    "email": "general",
    "enquiries": "general",
    "enquiry": "general",
    "inquiry": "general",
    "inquiries": "general",
    "office": "office_admin",
    "reception": "office_admin",
    "switchboard": "office_admin",
    "admin": "office_admin",
    "administration": "office_admin",
    "secretary": "office_admin",
    "assistant": "office_admin",
    "support": "support",
    "service": "support",
    "services": "support",
    "customerservice": "support",
    "customer-service": "support",
    "kundenservice": "support",
    "helpdesk": "support",
    "hotline": "support",
    # —— 明确无关：IT / 邮件系统
    "it": "it",
    "sysadmin": "it",
    "webmaster": "it",
    "hostmaster": "it",
    "postmaster": "it",
    "abuse": "it",
    "root": "it",
    # —— 明确无关：招聘 / 人事
    "hr": "hr",
    "humanresources": "hr",
    "human-resources": "hr",
    "personnel": "hr",
    "recruiting": "recruiting",
    "recruitment": "recruiting",
    "jobs": "recruiting",
    "job": "recruiting",
    "career": "recruiting",
    "careers": "recruiting",
    "karriere": "recruiting",
    "stellenangebote": "recruiting",
    "stellenangebot": "recruiting",
    # —— 明确无关：财务
    "finance": "finance",
    "financial": "finance",
    "accounting": "finance",
    "bookkeeping": "finance",
    "billing": "finance",
    "invoice": "finance",
    "invoicing": "finance",
    "accounts": "finance",
    "payroll": "finance",
    "tax": "finance",
    "credit": "finance",
    # —— 明确无关：法务 / 合规
    "legal": "legal",
    "law": "legal",
    "compliance": "legal",
    "privacy": "legal",
    "gdpr": "legal",
    "dpo": "legal",
    "datenschutz": "legal",
    "jurist": "legal",
    # —— 明确无关：市场 / 公关
    "marketing": "marketing",
    "ads": "marketing",
    "advertising": "marketing",
    "press": "press",
    "presse": "press",
    "media": "press",
    "pr": "press",
    "news": "press",
    "newsletter": "press",
    "communications": "press",
    "social": "press",
    # —— 明确无关：自动应答
    "noreply": "noreply",
    "no-reply": "noreply",
    "donotreply": "noreply",
    "do-not-reply": "noreply",
    "notification": "noreply",
    "notifications": "noreply",
    "bounce": "noreply",
    "unsubscribe": "noreply",
    "subscribe": "noreply",
    "mailer-daemon": "noreply",
}

# 职能 → 默认档位
#   target    与产品/服务推销对口的职能（默认有效）
#   generic   公开联系窗口 —— 没有负责人，不算有效目标邮箱
#   excluded  明确与推销无关的职能（招聘/财务/法务/公关/IT/自动应答）
FUNCTION_TIERS = {
    "purchasing": "target",
    "sales": "target",
    "engineering": "target",
    "production": "target",
    "logistics": "target",
    "management": "target",
    "personal": "target",
    "general": "generic",
    "office_admin": "generic",
    "support": "generic",
    "hr": "excluded",
    "recruiting": "excluded",
    "finance": "excluded",
    "legal": "excluded",
    "marketing": "excluded",
    "press": "excluded",
    "it": "excluded",
    "noreply": "excluded",
}

# 决策层与个人邮箱天然与任何商业推介相关，不受 target_functions 限制
ALWAYS_RELEVANT = {"management", "personal"}

# 未声明 target_functions 时，默认全部 target 档职能都算对口
DEFAULT_TARGET_FUNCTIONS = None  # None = 不限制

# 相关性档位，严格度递增（取更严的一个）
RELEVANCE_ORDER = {"relevant": 0, "generic": 1, "unknown": 2, "irrelevant": 3}

RELEVANCE_LABELS = {
    "relevant": "对口职能",
    "generic": "公开窗口",
    "unknown": "职能不明",
    "irrelevant": "职能无关",
}

# --------------------------------------------------------------- 人名识别

# 常见英文名与中文姓氏（用于单 token 的人名弱信号）
FIRST_NAMES = {
    "alex", "andrew", "ben", "bill", "bob", "brian", "chris", "dan", "david",
    "diana", "eric", "frank", "george", "helen", "ian", "jack", "james",
    "jane", "jason", "jeff", "jessica", "john", "kate", "kelly", "kevin",
    "laura", "linda", "lisa", "maria", "mark", "martin", "mary", "michael",
    "mike", "nathan", "olivia", "peter", "phil", "ray", "richard", "robert",
    "ryan", "sam", "sarah", "scott", "simon", "steve", "steven", "tom",
    "tony", "victoria", "wei", "william",
}

CN_SURNAMES = {
    "zhang", "wang", "li", "chen", "liu", "yang", "zhao", "zhou", "wu", "xu",
    "sun", "ma", "zhu", "hu", "guo", "lin", "he", "gao", "luo", "zheng",
    "liang", "xie", "song", "tang", "han", "cao",
}

NAME_TOKENS = FIRST_NAMES | CN_SURNAMES

# 与 site_probe.py 旧实现保持一致的弱信号正则（保留导出以兼容外部引用）
EN_NAME_HINT = re.compile(
    r"^(first|last)?\.?(first|last)?[._]?"
    r"(alex|andrew|ben|bill|bob|brian|chris|dan|david|diana|eric|"
    r"frank|george|helen|ian|jack|james|jane|jason|jeff|jessica|john|"
    r"kate|kelly|kevin|laura|linda|lisa|maria|mark|martin|mary|michael|"
    r"mike|nathan|olivia|peter|phil|ray|richard|robert|ryan|sam|sarah|"
    r"scott|simon|steve|steven|tom|tony|victoria|wei|william|zhang|"
    r"wang|li|chen|liu|yang|zhao|zhou|wu|xu|sun|ma|zhu|hu|guo|lin|"
    r"he|gao|luo|zheng|liang|xie|song|tang|han|cao|"
    r"(?:[a-z]{2,10})(?:[._][a-z]{2,10})?)$", re.I)

CN_NAME_HINT = re.compile(
    r"^(?:[a-z]{1,10}\.(?:[a-z]{1,10})?)$", re.I)

# 邮件列表 / 归档 / 自动回复类地址
LIST_HOST_HINTS = ("lists.", "list.", "mailman.", "majordomo.", "bounce",
                   "noreply", "no-reply", "donotreply", "users.", "devel.",
                   "announce", "newsletter")


def is_list_address(email):
    """判断是否邮件列表/归档地址（不适合一对一外联）。"""
    e = (email or "").lower()
    if "@" not in e:
        return False
    local, domain = e.split("@", 1)
    if local.startswith("debian-") or local.startswith("owner-"):
        return True
    return any(h in domain for h in LIST_HOST_HINTS)


# --------------------------------------------------------------- 职能判定

def _strip_numeric_suffix(token):
    """info2 -> info, sales01 -> sales（保留原 token 以便回退）。"""
    return re.sub(r"\d{1,2}$", "", token or "")


def _tokens_of(local):
    """把本地部分切成 token：按 . _ - + 分割。"""
    return [t for t in re.split(r"[._\-+]", local or "") if t]


def classify_function(local):
    """判定本地部分对应的业务职能。

    返回 (function_id, tier, reason)。判定顺序：
      1. 整体命中职能词表
      2. 去掉数字后缀再试（info2 / sales01）
      3. 含分隔符的多段式：取其中非通用的职能 token
      4. 单段：命中人名词表 → personal；否则 unknown

    3 里的多段式优先取「非 general」的职能，例如 info.sales 判为 sales
    而不是笼统的 general。
    """
    local = (local or "").strip().lower()
    if not local:
        return "unknown", "unknown", "本地部分为空"

    # 1) 整体命中
    if local in FUNCTION_MAP:
        fn = FUNCTION_MAP[local]
        return fn, FUNCTION_TIERS.get(fn, "target"), f"本地部分 `{local}` 是职能邮箱"

    # 2) 通用词 + 数字后缀
    base = _strip_numeric_suffix(local)
    if base != local and base in FUNCTION_MAP:
        fn = FUNCTION_MAP[base]
        return fn, FUNCTION_TIERS.get(fn, "target"), \
            f"本地部分 `{local}` 是职能邮箱（{base} + 编号）"

    # 3) 多段式
    tokens = _tokens_of(local)
    if len(tokens) > 1:
        hits = [(t, FUNCTION_MAP[t]) for t in tokens if t in FUNCTION_MAP]
        if hits:
            # 优先取非 general 的职能，general 只在没有更具体的时才用
            specific = [h for h in hits if h[1] != "general"]
            chosen = (specific or hits)[0]
            fn = chosen[1]
            return fn, FUNCTION_TIERS.get(fn, "target"), \
                f"本地部分 `{local}` 含职能 token `{chosen[0]}`"
        # 两段式且都不是职能 token → 形似人名（maria.schmidt / j.doe）
        if all(t.isalpha() and 1 <= len(t) <= 20 for t in tokens):
            return "personal", "target", \
                f"本地部分 `{local}` 形似人名（弱信号，需核验）"
        return "unknown", "unknown", f"本地部分 `{local}` 形态不明"

    # 4) 单段
    token = tokens[0] if tokens else local
    if token in NAME_TOKENS:
        return "personal", "target", \
            f"本地部分 `{token}` 形似人名（弱信号，需核验）"
    if token.isdigit():
        return "unknown", "unknown", f"本地部分 `{token}` 为纯数字（多为分机/工号）"
    return "unknown", "unknown", f"本地部分 `{token}` 形态不明，无法判断职能"


# --------------------------------------------------------------- 相关性

def relevance_of(function, tier, target_functions=None,
                 exclude_functions=None):
    """由职能 + 档位推出相关性。

    target_functions 为 None 表示不限制（全部 target 档职能都算对口）。
    非 None 时是严格白名单：不在名单里的 target 职能判为 irrelevant。
    management 与 personal 恒定相关，不受白名单限制。
    """
    fn = function or "unknown"
    if fn in ALWAYS_RELEVANT:
        return "relevant", f"职能 `{fn}` 天然与商业推介相关"

    exclude = {str(x).strip().lower() for x in (exclude_functions or [])}
    if fn in exclude:
        return "irrelevant", f"职能 `{fn}` 已被 campaign 显式排除"

    if tier == "excluded":
        return "irrelevant", f"职能 `{fn}` 与本次推销无关（如招聘/财务/法务/公关）"

    if tier == "generic":
        return "generic", f"本地部分是公开联系窗口（职能 `{fn}`），无具体负责人"

    if tier == "target":
        if target_functions is None:
            return "relevant", f"职能 `{fn}` 属于默认对口范围"
        wanted = {str(x).strip().lower() for x in target_functions}
        if fn in wanted:
            return "relevant", f"职能 `{fn}` 在 campaign 声明的对口范围内"
        return "irrelevant", \
            f"职能 `{fn}` 不在 campaign 声明的对口范围 {sorted(wanted)} 内"

    return "unknown", f"本地部分形态不明，无法判断是否对口"


def assess_email(email, target_functions=None, exclude_functions=None):
    """完整判定一个邮箱。返回 dict。

    键：email / local / type / function / tier / relevance / reason /
        needs_verification
    """
    raw = (email or "").strip()
    e = raw.lower()
    result = {
        "email": raw,
        "local": "",
        "type": "unknown",
        "function": "unknown",
        "tier": "unknown",
        "relevance": "unknown",
        "reason": "",
        "needs_verification": False,
    }
    if "@" not in e:
        result["reason"] = "格式非法：缺少 @"
        return result

    local, domain = e.split("@", 1)
    result["local"] = local
    result["domain"] = domain

    if is_list_address(e):
        result["type"] = "unknown"
        result["reason"] = "邮件列表/归档地址，不适合外联"
        return result

    fn, tier, why = classify_function(local)
    rel, rel_why = relevance_of(fn, tier, target_functions, exclude_functions)
    result["function"] = fn
    result["tier"] = tier
    result["relevance"] = rel
    result["reason"] = f"{why}；{rel_why}"
    if fn == "personal":
        result["type"] = "personal"
        result["needs_verification"] = True
    elif tier == "generic":
        result["type"] = "generic"
    elif tier == "excluded":
        result["type"] = "department"
    elif tier == "target":
        result["type"] = "department"
    else:
        result["type"] = "unknown"
    return result


def is_valid_target(email, target_functions=None, exclude_functions=None):
    """是否算「有效目标邮箱」——只有相关职能的邮箱才算。"""
    return assess_email(email, target_functions, exclude_functions)["relevance"] \
        == "relevant"


def strictest(a, b):
    """取两个相关性档位中更严的一个。"""
    a = a or "unknown"
    b = b or "unknown"
    return a if RELEVANCE_ORDER.get(a, 2) >= RELEVANCE_ORDER.get(b, 2) else b


# --------------------------------------------------------------- campaign 配置

TARGET_CONFIG_NAME = "contact_targets.json"


def load_target_config(campaign_dir):
    """读取 campaign 级的对口职能声明。

    文件格式：
        {
          "product_or_service": "工业硅胶密封件",
          "target_functions": ["purchasing", "sourcing", "engineering"],
          "exclude_functions": ["press"],
          "notes": "目标客户是欧美工业件采购商"
        }

    `target_functions` 缺省为 null，表示不限制（全部 target 档职能算对口）。
    返回 dict，找不到文件时返回默认值。
    """
    default = {
        "product_or_service": "",
        "target_functions": DEFAULT_TARGET_FUNCTIONS,
        "exclude_functions": [],
        "notes": "",
        "_source": "default",
    }
    if not campaign_dir:
        return default
    path = Path(campaign_dir) / TARGET_CONFIG_NAME
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        default["notes"] = f"读取 {path.name} 失败：{exc}，使用默认配置"
        return default
    if not isinstance(data, dict):
        default["notes"] = f"{path.name} 格式非法（应为对象），使用默认配置"
        return default
    merged = dict(default)
    for key in ("product_or_service", "target_functions", "exclude_functions",
                "notes"):
        if key in data:
            merged[key] = data[key]
    if merged["target_functions"] is not None:
        merged["target_functions"] = [str(x).strip().lower()
                                      for x in merged["target_functions"]]
    merged["exclude_functions"] = [str(x).strip().lower()
                                   for x in (merged["exclude_functions"] or [])]
    merged["_source"] = str(path)
    return merged


def save_target_config(campaign_dir, product_or_service, target_functions,
                       exclude_functions=None, notes=""):
    """写出 campaign 级对口职能声明。"""
    path = Path(campaign_dir) / TARGET_CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "product_or_service": product_or_service,
        "target_functions": (None if target_functions is None
                             else [str(x).strip().lower()
                                   for x in target_functions]),
        "exclude_functions": [str(x).strip().lower()
                              for x in (exclude_functions or [])],
        "notes": notes,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


# --------------------------------------------------------------- 称呼建议

def salutation_hint(email_type, has_name):
    """按邮箱类型给出邮件称呼建议。"""
    if email_type == "personal" and has_name:
        return "可用姓名称呼（需先核验邮箱与姓名对应关系）"
    if email_type == "personal":
        return "形似人名但未核验，称呼写『您好：』"
    if email_type == "department":
        return "部门邮箱，称呼写『您好：』或『尊敬的采购部』"
    return "通用/形态不明，称呼写『您好：』，不写具体人名"


# --------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(
        description="邮箱职能与相关性判定（site_probe / send_emails 共用口径）")
    ap.add_argument("--email", action="append", default=[],
                    help="待判定邮箱，可重复传入")
    ap.add_argument("--file", help="每行一个邮箱的文件")
    ap.add_argument("--campaign", help="campaign 目录，读取 contact_targets.json")
    ap.add_argument("--target-functions",
                    help="逗号分隔的对口职能，覆盖 campaign 配置")
    ap.add_argument("--exclude-functions",
                    help="逗号分隔的排除职能，覆盖 campaign 配置")
    ap.add_argument("--show-functions", action="store_true",
                    help="打印职能词表与档位")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    if a.show_functions:
        by_tier = {}
        for local, fn in sorted(FUNCTION_MAP.items()):
            by_tier.setdefault(FUNCTION_TIERS.get(fn, "target"), []).append(
                (local, fn))
        print("=== 职能档位 ===")
        for tier in ("target", "generic", "excluded"):
            items = by_tier.get(tier, [])
            print(f"\n[{tier}] {len(items)} 项")
            for local, fn in items:
                print(f"  {local:<24} -> {fn}")
        return 0

    cfg = load_target_config(a.campaign)
    targets = cfg.get("target_functions")
    excludes = cfg.get("exclude_functions")
    if a.target_functions:
        targets = [x.strip().lower() for x in a.target_functions.split(",")
                   if x.strip()]
    if a.exclude_functions:
        excludes = [x.strip().lower() for x in a.exclude_functions.split(",")
                    if x.strip()]

    emails = list(a.email)
    if a.file:
        for line in Path(a.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                emails.append(line)
    if not emails:
        ap.print_help()
        return 1

    if cfg.get("product_or_service"):
        print(f"产品/服务      : {cfg['product_or_service']}")
    print(f"对口职能白名单 : {targets if targets else '（不限制）'}")
    print(f"显式排除职能   : {excludes or '（无）'}")
    print("=" * 68)

    results = [assess_email(e, targets, excludes) for e in emails]
    for r in results:
        mark = {"relevant": "[ OK ]", "generic": "[WARN]", "unknown": "[WARN]",
                "irrelevant": "[SKIP]"}.get(r["relevance"], "[????]")
        print(f"{mark} {r['email']}")
        print(f"        形态={r['type']}  职能={r['function']}  "
              f"相关性={r['relevance']}（{RELEVANCE_LABELS.get(r['relevance'],'')}）")
        print(f"        {r['reason']}")
        if r["relevance"] == "relevant":
            print(f"        称呼: {salutation_hint(r['type'], False)}")

    valid = sum(1 for r in results if r["relevance"] == "relevant")
    print("=" * 68)
    print(f"有效目标邮箱: {valid}/{len(results)}")
    if valid == 0:
        print("→ 该公司官网未拿到对口邮箱：不要用上述地址发送，"
              "按 contact-discovery.md 路径 2→5 继续找；"
              "全部无果才判 no_email。")

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys_exit = __import__("sys").exit
    sys_exit(main() or 0)
