#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部 Skill 插件审批 CLI（M1 知识层）

用法（在仓库根或容器内执行）：
    # 扫描插件目录候选与审批状态（不写清单）
    python scripts/approve_skill_plugin.py --scan

    # 审批指定 skill（按 SKILL.md frontmatter name 匹配；note 必填，注明来源与用途）
    python scripts/approve_skill_plugin.py --approve my-plugin --note "来源: xxx 供应商，用途: yyy"

    # 撤销审批（runner 进程缓存随签名轮询在下一次执行生效；租户视图最迟 TTL 300s）
    python scripts/approve_skill_plugin.py --revoke my-plugin

安全说明：
- 审批即锁定内容 hash（整目录 computedHash + SKILL.md sha256）；插件目录内容
  变化后需重新 --approve，否则注册表重建时被剔除（fail-closed）。
- 清单写入采用「临时文件 + os.replace」原子 rename，避免 runner 并发读到
  半写 JSON 触发全量插件不可见闪断。
- 审批清单是平台级清单，无租户维度：审批 + 进入 allowed 白名单 = 所有租户
  立即可见（见 docs/plans/plan-external-skill-plugin-m1.md §3.2 / 风险 5）。
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from src.core import skill_plugin_gate as gate  # noqa: E402


def _find_candidate(name: str, plugins_cfg):
    """在插件目录链中按 frontmatter name 查找 skill 目录（高优先级目录优先）。"""
    for plugin_dir in reversed(gate.resolve_plugin_dirs(plugins_cfg)):
        if not plugin_dir.exists():
            continue
        for skill_dir in sorted(plugin_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            if gate._read_frontmatter_name(skill_md) == name:
                return skill_dir
    return None


def _scan_declared_state(skill_dir: Path) -> str:
    """--scan 候选行的设备执行声明摘要（解析失败 → [声明非法]，提前暴露审批阻断点）。"""
    try:
        declaration = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
    except gate.SkillDeviceDeclarationError as e:
        return f"[声明非法]（--approve 将拒绝: {e}）"
    if declaration.execution != "device":
        return "[非设备执行]" if declaration.execution else "[未声明 execution]"
    return f"[设备执行 entry={', '.join(declaration.entries) or '!! 缺 metadata.entry'} mutable={', '.join(declaration.mutable) or '无'}]"


def cmd_scan(approvals_file: Path, plugins_cfg) -> int:
    approvals = gate.read_approvals(approvals_file)
    found_any = False
    for plugin_dir in gate.resolve_plugin_dirs(plugins_cfg):
        print(f"插件目录: {plugin_dir}（{'存在' if plugin_dir.exists() else '不存在'}）")
        if not plugin_dir.exists():
            continue
        for skill_dir in sorted(plugin_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            found_any = True
            name = gate._read_frontmatter_name(skill_md)
            if not name:
                print(f"  [无效] {skill_dir.name}: SKILL.md 缺失 frontmatter name")
                continue
            entry = approvals.get(name)
            if entry is None:
                print(f"  [未审批] {name} @ {skill_dir}")
                print(f"           computedHash: {gate.compute_skill_dir_hash(skill_dir)}")
                print(f"           skillMdHash:  {gate.compute_skill_md_hash(skill_dir)}")
                print(f"           声明: {_scan_declared_state(skill_dir)}")
            else:
                match = "一致" if gate.compute_skill_dir_hash(skill_dir) == entry["computedHash"] else "!! 与审批时不符"
                print(f"  [已审批] {name} @ {skill_dir}（hash {match}）")
                if entry.get("entries") and entry.get("exec_hash"):
                    print(f"           设备执行: entries={', '.join(entry['entries'])} exec_hash={entry['exec_hash'][:16]}..."
                          f" version={entry.get('version') or '(无)'} mutable={', '.join(entry.get('mutable') or []) or '(无)'}")
                else:
                    print(f"           [设备执行未审批]（存量条目无 entries/exec_hash——device 路由技能派发侧将被拒绝，"
                          f"需重新 --approve 写入设备执行项）")
                print(f"           声明: {_scan_declared_state(skill_dir)}")
    if not found_any:
        print("（插件目录中无候选 skill）")
    print(f"审批清单: {approvals_file}（{'存在' if approvals_file.exists() else '不存在'}，"
          f"当前 {len(approvals)} 条）")
    return 0


def cmd_approve(name: str, note: str, approvals_file: Path, plugins_cfg) -> int:
    skill_dir = _find_candidate(name, plugins_cfg)
    if skill_dir is None:
        print(f"错误: 插件目录中未找到 skill '{name}'（按 SKILL.md frontmatter name 匹配）", file=sys.stderr)
        return 1
    computed = gate.compute_skill_dir_hash(skill_dir)
    md_hash = gate.compute_skill_md_hash(skill_dir)

    # M2 设备执行项（plan §3.6）：--approve 时从 SKILL.md 快照入口白名单与自学习可变路径，
    # 声明非法（逃逸/相交/超限/SKILL.md 入 mutable/入口文件缺失）→ 拒绝写入设备执行项。
    device_entry: Dict[str, Any] = {}
    try:
        declaration = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
    except gate.SkillDeviceDeclarationError as e:
        print(f"错误: skill '{name}' 的设备执行声明非法，拒绝审批（fail-closed）: {e}", file=sys.stderr)
        return 1
    if declaration.execution == "device":
        if not declaration.entries:
            print(f"错误: skill '{name}' 声明 execution=device 但未声明 metadata.entry（设备执行入口），"
                  f"拒绝审批其设备执行项。请在 SKILL.md metadata.entry 声明入口脚本相对路径"
                  f"（如 scripts/flow.py）后重新审批。", file=sys.stderr)
            return 1
        device_entry = {
            "entries": list(declaration.entries),
            "exec_hash": gate.compute_skill_exec_hash(skill_dir, list(declaration.mutable)),
            "version": declaration.version or "",
            "mutable": list(declaration.mutable),
        }

    approvals = gate.read_approvals(approvals_file)
    approvals[name] = {
        "source_dir": str(skill_dir),
        "computedHash": computed,
        "skillMdHash": md_hash,
        "note": note,
        **device_entry,
    }
    gate.write_approvals(approvals_file, approvals)
    logger.info(f"已审批插件 skill: {name} @ {skill_dir} (computedHash={computed[:12]}...)")
    print(f"已审批: {name} @ {skill_dir}")
    print(f"  computedHash: {computed}")
    print(f"  skillMdHash:  {md_hash}")
    if device_entry:
        print(f"  设备执行项（请复核）:")
        print(f"    entries:   {', '.join(device_entry['entries'])}")
        print(f"    exec_hash: {device_entry['exec_hash']}")
        print(f"    version:   {device_entry['version'] or '(未声明)'}")
        print(f"    mutable:   {', '.join(device_entry['mutable']) or '(无)'}")
    print("提示: 审批后还需将 skill 名加入 config.yaml skills.master_agent.allowed /"
          " subagent.default_allowed 才对 Agent 生效（空列表 = 允许所有）。")
    return 0


def cmd_revoke(name: str, approvals_file: Path) -> int:
    approvals = gate.read_approvals(approvals_file)
    if name not in approvals:
        print(f"错误: '{name}' 不在审批清单中", file=sys.stderr)
        return 1
    del approvals[name]
    gate.write_approvals(approvals_file, approvals)
    logger.info(f"已撤销插件 skill 审批: {name}")
    print(f"已撤销: {name}")
    print("提示: runner 进程缓存随签名轮询在下一次执行生效；租户合并视图最迟 TTL 300s 后刷新。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="外部 Skill 插件审批 CLI（M1 知识层）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", action="store_true", help="扫描插件目录候选与审批状态")
    mode.add_argument("--approve", metavar="NAME", help="审批指定 skill（按 frontmatter name）")
    mode.add_argument("--revoke", metavar="NAME", help="撤销审批")
    parser.add_argument("--note", help="审批备注（--approve 时必填：注明来源与用途）")
    parser.add_argument("--approvals", metavar="PATH",
                        help="审批清单路径（默认按 settings skills.plugins 解析）")
    args = parser.parse_args()

    from src.config.settings import settings
    plugins_cfg = settings.skills.plugins
    if not plugins_cfg.enabled:
        print("警告: skills.plugins.enabled=false，插件目录不参与注册链；"
              "审批清单仍可维护，待 enabled=true 后生效。", file=sys.stderr)

    approvals_file = Path(args.approvals) if args.approvals else gate.resolve_approvals_file(plugins_cfg)

    if args.scan:
        return cmd_scan(approvals_file, plugins_cfg)
    if args.approve:
        if not args.note:
            print("错误: --approve 需要 --note（注明来源与用途，审批从紧）", file=sys.stderr)
            return 2
        return cmd_approve(args.approve, args.note, approvals_file, plugins_cfg)
    return cmd_revoke(args.revoke, approvals_file)


if __name__ == "__main__":
    sys.exit(main())
