#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发件人档案（sender profile）管理 —— 跨调用复用发件身份与 SMTP 配置，避免重复询问。

设计边界（v3.3.6 起）
--------------------
**域名体检已从主流程剥离为独立方法**，只有用户明确下指令时才调用
`scripts/domain_check.py`。因此本模块：
  - 判定档案「是否可复用」只看：档案存在 + 合规判定通过 + 有发件身份/SMTP；
    **`domain_check.passed` 不参与判定，也不阻塞发送**；
  - `domain_check` 字段降级为**可选的存档记录**，仅供人回看，可由
    `record-domain-check` 子命令按需回写；
  - 合规判定（compliance）仍属主流程，保留强制。

⚠ 档案含真实发件身份，**必须放在技能目录之外**。技能目录可能被打包、分享或
同步到其他机器，凭据不应随之流动。（本系统版默认路径为租户级
`lead-gen/sender_profile.json`，与 send_emails.py 读取位置同源。）

用法
----
  # 查看当前档案状态（是否需要重新收集发件配置）
  python sender_profile.py status

  # 保存/新建档案（从一份 JSON 读入；密码只存环境变量名，不落明文）
  python sender_profile.py save --data profile.json
  # 或管道： cat profile.json | python sender_profile.py save

  # 【独立方法·按需】把 domain_check.py --json 的结果存档（不影响发送流程）
  python scripts/domain_check.py outreach.example.com --json > dc.json
  python sender_profile.py record-domain-check --data dc.json

  # 删除档案（下次调用将重新收集发件配置）
  python sender_profile.py reset
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def default_profile_path():
    """默认档案路径（本系统版）：租户级 lead-gen/sender_profile.json。

    skill_execute 的 CWD 是 /tmp/skill_executor 临时目录，相对路径会写错位置，
    与发送侧读不到同一份档案（2026-11-06 trace 事故同类根因），必须绝对路径。
    可由 B2B_PROFILE 环境变量覆盖（调试用）。
    """
    env = os.environ.get("B2B_PROFILE")
    if env:
        return env
    tenant_id = (os.environ.get("AID_TENANT_ID")
                 or os.environ.get("CURRENT_TENANT_ID") or "").strip()
    if not tenant_id:
        return str(Path.home() / ".workbuddy" / "b2b_profiles" / "sender_profile.json")
    try:
        from src.core.storage import configured_storage_root, normalize_tenant_id
        return str(configured_storage_root() / "tenants"
                   / normalize_tenant_id(tenant_id) / "lead-gen" / "sender_profile.json")
    except Exception:
        return str(Path.home() / ".workbuddy" / "b2b_profiles" / "sender_profile.json")


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
    """档案是否可复用。域名体检结果**不参与判定**（独立方法，非主流程）。"""
    prof = load_profile(path)
    if prof is None:
        out = {"exists": False, "recheck_required": True,
               "reason": "档案不存在，需收集发件身份与 SMTP 配置"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    dc = prof.get("domain_check") or {}
    comp = prof.get("compliance") or {}
    sender = prof.get("sender") or {}
    smtp = prof.get("smtp") or {}
    compliance_ok = bool(comp.get("passed"))
    has_identity = bool(sender.get("from_email")) and bool(smtp.get("smtp_host"))
    if not compliance_ok:
        reason = "合规判定未通过或尚未判定，需重新确认"
    elif not has_identity:
        reason = "缺少发件身份/SMTP，需补充"
    else:
        reason = "可复用，跳过询问（域名体检为独立方法，不影响复用）"
    out = {
        "exists": True,
        "recheck_required": (not compliance_ok) or (not has_identity),
        "compliance_passed": compliance_ok,
        "compliance_at": comp.get("checked_at"),
        "has_sender_identity": has_identity,
        "from_email": sender.get("from_email"),
        "domain": prof.get("domain"),
        # 以下仅为存档信息，不参与 recheck_required 计算
        "domain_check_recorded": bool(dc.get("checked_at")),
        "domain_check_passed": dc.get("passed"),
        "domain_check_at": dc.get("checked_at"),
        "domain_check_note": "可选存档；如需体检，人工调用 scripts/domain_check.py",
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


def record_domain_check(path, report):
    """【独立方法·按需】把 domain_check.py --json 报告存入档案，不影响发送流程。

    `passed` 的口径与 domain_check.py 的退出码一致：有失败项、或全部项
    都因 DNS 查询失败而「未知」，都不能算达标。
    """
    if not isinstance(report, dict) or not isinstance(report.get("summary"), dict):
        print("[错误] 不是有效的 domain_check.py --json 报告（缺 summary）",
              file=sys.stderr)
        return 3
    s = report["summary"]
    n_fail = int(s.get("fail") or 0)
    n_unknown = int(s.get("unknown") or 0)
    n_pass = int(s.get("pass") or 0)
    passed = n_fail == 0 and not (n_pass == 0 and n_unknown > 0)
    record = {
        "passed": passed,
        "checked_at": now_iso(),
        "domain": report.get("domain"),
        "smtp_ip": report.get("smtp_ip"),
        "summary": s,
        "items": report.get("items") or [],
        "notes": "独立方法存档，仅供回看；不参与档案复用判定、不阻塞发送",
    }
    if not Path(path).exists():
        print("[warn] 发件人档案尚不存在，本次仅写入体检存档；"
              "发送时仍需补齐发件身份与 SMTP", file=sys.stderr)
    rc = save(path, {"domain_check": record})
    verdict = "达标" if passed else "未达标 / 无法判定"
    print(f"[domain-check] {report.get('domain')}: {verdict} "
          f"(fail={n_fail}, warn={s.get('warn') or 0}, unknown={n_unknown}, pass={n_pass})")
    return rc


def reset(path):
    p = Path(path)
    if p.exists():
        p.unlink()
        print(f"[ok] 档案已删除：{p}（下次调用将重新收集发件配置）")
    else:
        print("[info] 档案不存在，无需删除")
    return 0


def read_json_arg(path):
    """--data 给文件路径，否则从 stdin 读 JSON。"""
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return json.loads(sys.stdin.read())


def main():
    ap = argparse.ArgumentParser(
        description="发件人档案管理（复用发件身份/SMTP；域名体检为独立方法）")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--profile", default=default_profile_path(),
                        help="档案路径（缺省自动解析为租户 lead-gen/sender_profile.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[parent],
                   help="查看档案状态（是否需要重新收集发件配置）")
    ps = sub.add_parser("save", parents=[parent], help="保存/更新档案")
    ps.add_argument("--data", help="JSON 文件路径（含档案内容）")
    pd = sub.add_parser("record-domain-check", parents=[parent],
                        help="【独立方法·按需】把 domain_check.py --json 报告存档")
    pd.add_argument("--data", help="domain_check.py --json 的输出文件；不传则从 stdin 读")
    sub.add_parser("reset", parents=[parent], help="删除档案")
    a = ap.parse_args()

    if a.cmd == "status":
        return status(a.profile)
    if a.cmd == "reset":
        return reset(a.profile)
    if a.cmd == "save":
        return save(a.profile, read_json_arg(a.data))
    if a.cmd == "record-domain-check":
        return record_domain_check(a.profile, read_json_arg(a.data))
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
