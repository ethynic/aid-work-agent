#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发件人档案（sender profile）管理 —— 让技能在多次调用间复用已校验的发件域名/邮箱，避免重复体检与询问。

为什么需要
----------
域名体检（SPF/DKIM/DMARC/MX/PTR/黑名单）与合规判定是一次性结论，不会天天变。
把结果存到 `workspace/sender_profile.json`，下次调用技能时：
  - 档案存在且 domain_check / compliance 均通过 → 跳过体检、不再询问发件人信息；
  - 用户提供新的域名/邮箱/SMTP/发件人 → 覆写对应字段并重新校验；
  - 档案缺失或上次未通过 → 走完整校验流程。

用法
----
  # 查看当前档案状态（是否需要重新校验）
  python sender_profile.py status --profile workspace/sender_profile.json

  # 保存/新建档案（从一份 JSON 读入；密码只存环境变量名，不落明文）
  python sender_profile.py save --profile workspace/sender_profile.json --data profile.json
  # 或管道： cat profile.json | python sender_profile.py save --profile workspace/sender_profile.json

  # 删除档案（下次调用将重新校验）
  python sender_profile.py reset --profile workspace/sender_profile.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = "workspace/sender_profile.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_profile(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 档案读取失败：{e}", file=sys.stderr)
        return None


def status(path):
    prof = load_profile(path)
    if prof is None:
        out = {"exists": False, "recheck_required": True,
               "reason": "档案不存在，需完整校验"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    dc = prof.get("domain_check") or {}
    comp = prof.get("compliance") or {}
    sender = prof.get("sender") or {}
    smtp = prof.get("smtp") or {}
    passed = bool(dc.get("passed")) and bool(comp.get("passed"))
    has_identity = bool(sender.get("from_email")) and bool(smtp.get("smtp_host"))
    if not passed:
        reason = "上次校验未通过，需重新校验"
    elif not has_identity:
        reason = "缺少发件身份/SMTP，需补充"
    else:
        reason = "可复用，跳过体检与询问"
    out = {
        "exists": True,
        "recheck_required": (not passed) or (not has_identity),
        "domain_check_passed": bool(dc.get("passed")),
        "domain_check_at": dc.get("checked_at"),
        "compliance_passed": bool(comp.get("passed")),
        "compliance_at": comp.get("checked_at"),
        "has_sender_identity": has_identity,
        "from_email": sender.get("from_email"),
        "domain": prof.get("domain"),
        "updated_at": prof.get("updated_at"),
        "reason": reason,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def save(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 合并：若已存在则保留未提供的字段（支持只更新部分字段）
    existing = load_profile(path) or {}
    merged = {**existing, **data}
    merged["updated_at"] = now_iso()
    # 安全：绝不落盘明文密码
    smtp = merged.get("smtp") or {}
    smtp.pop("smtp_password", None)
    smtp.setdefault("smtp_password_env", "SMTP_PASSWORD")
    merged["smtp"] = smtp
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 发件人档案已保存：{p}")
    return 0


def reset(path):
    p = Path(path)
    if p.exists():
        p.unlink()
        print(f"[ok] 档案已删除：{p}（下次调用将重新校验）")
    else:
        print("[info] 档案不存在，无需删除")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="发件人档案管理（复用已校验的发件域名/邮箱）")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--profile", default=DEFAULT_PATH, help="档案路径")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[parent], help="查看档案状态（是否需要重新校验）")
    ps = sub.add_parser("save", parents=[parent], help="保存/更新档案")
    ps.add_argument("--data", help="JSON 文件路径（含档案内容）")
    sub.add_parser("reset", parents=[parent], help="删除档案")
    a = ap.parse_args()

    if a.cmd == "status":
        return status(a.profile)
    if a.cmd == "reset":
        return reset(a.profile)
    if a.cmd == "save":
        if a.data:
            data = json.loads(Path(a.data).read_text(encoding="utf-8"))
        else:
            data = json.loads(sys.stdin.read())
        return save(a.profile, data)
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
