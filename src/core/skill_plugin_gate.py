#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部 Skill 插件审批门（M1 知识层）

设计文档：docs/plans/plan-external-skill-plugin-m1.md §3.1/§3.2/§3.3。

目录链（低 → 高优先级）：
    src/skills（内置，不经过本 gate）→ 仓库根 skills/（第一方插件位）
    → storage/skills/plugins/（运维动态安装位）

安全模型：
1. 插件目录中的 skill 默认不可见，必须进入审批清单
   （storage/skills/plugin-approvals.json，含内容 hash 锁定）；
2. 注册表重建时（Layer 1）校验整目录 computedHash，不符剔除；
3. 读手册时（Layer 2，SkillRegistry.get_content）校验 SKILL.md sha256，
   由 registry._plugin_hashes + 目录身份判定承接（skill_registry.py）；
4. 插件 skill 的执行由 skill_executor 入口全量拦截（M1 只读不可执行），
   与本模块解耦——审批通过不代表可执行。

本模块是审批门的唯一可信来源：runner 进程缓存（resource_cache）与
租户合并链（tenant_skill_cache）都必须经由 build_base_registry_sources
取得插件 loader，禁止各自扫描插件目录绕过审批。

审批清单是平台级清单，无租户维度：一次审批 + 该 skill 名进入
allowed 白名单 = 所有租户立即可见（租户级审批/上架属 M4），运维边界
声明见仓库根 skills/README.md。
"""

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml
from loguru import logger

from src.core.skill_loader import SkillLoader
from src.core.storage import configured_storage_root

# 目录 hash 排除项：缓存产物 / 截图产物 / 日志 / 系统文件
_EXCLUDED_DIR_NAMES = {"__pycache__", "shots", "log"}
_EXCLUDED_FILE_SUFFIXES = (".pyc",)
_EXCLUDED_FILE_NAMES = {".DS_Store"}

# 设备执行声明的 mutable 路径数上限（M2，plan §3.6：防 exec_hash 排除面无节制扩大）
_MAX_DECLARED_MUTABLE_PATHS = 8

# 审批清单格式版本（与仓库根 skills-lock.json 的 computedHash 思路对齐）
_APPROVALS_VERSION = 1


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _plugins_cfg():
    """读取 skills.plugins 配置（延迟导入避免启动期依赖）。"""
    from src.config.settings import settings
    return settings.skills.plugins


def resolve_plugin_dirs(plugins_cfg=None) -> List[Path]:
    """解析插件目录链（低 → 高优先级）：仓库根 skills/ → storage skills/plugins/。"""
    cfg = plugins_cfg if plugins_cfg is not None else _plugins_cfg()
    repo_dir = Path(cfg.repo_dir)
    if not repo_dir.is_absolute():
        repo_dir = _repo_root() / repo_dir
    storage_dir = configured_storage_root() / cfg.storage_subdir
    return [repo_dir, storage_dir]


def resolve_approvals_file(plugins_cfg=None) -> Path:
    """解析审批清单路径（相对 storage 根，兼容 AGENT_RUNNER_STORAGE_ROOT）。"""
    cfg = plugins_cfg if plugins_cfg is not None else _plugins_cfg()
    return configured_storage_root() / cfg.approvals_subpath


# ---------------------------------------------------------------------------
# 审批清单读写
# ---------------------------------------------------------------------------

def read_approvals(approvals_file: Path) -> Dict[str, dict]:
    """读取审批清单，返回 {skill_name: {"computedHash": str, "skillMdHash": str, ...}}。

    fail-closed：文件缺失 / JSON 损坏 / 结构非法 → 返回 {}（全部插件不可见）。
    """
    approvals_file = Path(approvals_file)
    if not approvals_file.exists():
        return {}
    try:
        data = json.loads(approvals_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[SkillPluginGate] 审批清单不可读，全部插件不可见（fail-closed）: "
                       f"{approvals_file}: {e}")
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        logger.warning(f"[SkillPluginGate] 审批清单结构非法（期望 version/skills 包装），"
                       f"全部插件不可见: {approvals_file}")
        return {}
    result: Dict[str, dict] = {}
    for name, entry in data["skills"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("computedHash"), str) \
                or not isinstance(entry.get("skillMdHash"), str):
            logger.warning(f"[SkillPluginGate] 审批清单条目非法，忽略: {name}")
            continue
        result[str(name)] = entry
    return result


def write_approvals(approvals_file: Path, approvals: Dict[str, dict]) -> None:
    """原子写审批清单（临时文件 + os.replace）。

    清单参与 runner 签名轮询且被并发读：半写 JSON 会触发
    「损坏 → {} → 全部插件不可见」的 fail-closed 闪断，原子写消除该窗口。
    写方为审批 CLI（scripts/approve_skill_plugin.py）。
    """
    approvals_file = Path(approvals_file)
    approvals_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _APPROVALS_VERSION, "skills": approvals}
    tmp = approvals_file.with_name(
        f".{approvals_file.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, approvals_file)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 内容 hash
# ---------------------------------------------------------------------------

def _iter_hashable_files(root: Path):
    """按相对路径排序遍历参与目录 hash 的文件（顺序与文件系统遍历顺序无关）。"""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if rel.name in _EXCLUDED_FILE_NAMES or rel.name.endswith(_EXCLUDED_FILE_SUFFIXES):
            continue
        yield rel, path


def compute_skill_dir_hash(skill_dir: Path) -> str:
    """整目录 sha256：相对路径（排序后）+ 每文件 sha256 聚合。"""
    skill_dir = Path(skill_dir)
    digest = hashlib.sha256()
    for rel, path in _iter_hashable_files(skill_dir):
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_hash.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def compute_skill_md_hash(skill_dir: Path) -> str:
    """SKILL.md 文件 sha256（Layer 2 内容门使用）。"""
    skill_md = Path(skill_dir) / "SKILL.md"
    return hashlib.sha256(skill_md.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 设备执行入口声明（M2，plan-external-skill-plugin-m2.md §3.6）
# ---------------------------------------------------------------------------

class SkillDeviceDeclarationError(ValueError):
    """SKILL.md metadata.entry / metadata.mutable 设备执行声明非法。

    审批 CLI（--approve）捕获后拒绝写入设备执行项（fail-closed：声明非法
    的插件不进入设备执行白名单，知识层审批不受影响）。
    """


def _normalize_declared_rel_path(value: str, *, field: str) -> str:
    """归一化声明的相对路径：strip、反斜杠 → 正斜杠、去重复 ./ 前缀。

    非法（绝对路径、含 ..、归一化后为空）即抛 SkillDeviceDeclarationError。
    词法判定不依赖文件系统 resolve（不受 symlink 影响），与设备端 TS 镜像
    （skillRunner.ts）保持同一归一化形态——payload 的 entry/审批 entries
    双方均为正斜杠无 ./ 前缀形态。
    """
    text = str(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text:
        raise SkillDeviceDeclarationError(f"metadata.{field} 存在空路径项")
    if text.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", text):
        raise SkillDeviceDeclarationError(
            f"metadata.{field} 存在绝对路径项: {value!r}（仅允许 skill 目录内相对路径）")
    if any(part == ".." for part in text.split("/")):
        raise SkillDeviceDeclarationError(
            f"metadata.{field} 存在逃逸 skill 目录的路径项: {value!r}（禁止 .. 段）")
    if any(part == "" for part in text.split("/")) or text.endswith("/"):
        raise SkillDeviceDeclarationError(f"metadata.{field} 存在非法路径项: {value!r}")
    return text


def _normalize_declared_path_list(value: Any, *, field: str) -> List[str]:
    """metadata.entry / metadata.mutable 的声明形态归一化：str → [str]，list[str] 原样归一。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise SkillDeviceDeclarationError(
            f"metadata.{field} 必须为非空字符串或字符串列表（当前: {type(value).__name__}）")
    normalized: List[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SkillDeviceDeclarationError(
                f"metadata.{field} 列表项必须为字符串（当前: {type(item).__name__}）")
        normalized.append(_normalize_declared_rel_path(item, field=field))
    return normalized


@dataclass(frozen=True)
class SkillDeviceDeclaration:
    """SKILL.md frontmatter 的设备执行声明解析结果（M2，计划 §3.6）。

    execution：metadata.execution（"server" | "device"），缺省 None（按 server）；
    entries：metadata.entry 归一化后的入口白名单（device 执行时下发 payload 的对账来源）；
    mutable：metadata.mutable 归一化后的自学习可变路径集（exec_hash 计算排除项）；
    version：frontmatter version（审批清单 version 字段快照来源，仅日志/展示）。
    """
    execution: Optional[str]
    entries: Tuple[str, ...] = ()
    mutable: Tuple[str, ...] = ()
    version: Optional[str] = None


def read_declared_entries_and_mutable(skill_md_path: Path) -> SkillDeviceDeclaration:
    """解析 SKILL.md frontmatter 的设备执行声明（metadata.execution/entry/mutable + version）。

    校验规则（plan §3.6，fail-closed——非法即抛 SkillDeviceDeclarationError）：
    - entry/mutable 每项为 skill 目录内相对路径（拒绝对对路径/../空段），归一化正斜杠；
    - mutable 不得含 SKILL.md（SKILL.md 恒参与 exec_hash 锁定——入口脚本与手册不可免检）；
    - mutable ≤ 8 条；
    - entries ∩ mutable = ∅（安全闭环：入口脚本是被执行代码，必须受 exec_hash 锁定，
      否则仅替换 entry 脚本内容的篡改分发目录可通过设备 exec_hash 对账执行任意代码）；
    - entry 项须在 skill 目录内实际存在（被执行代码，审批时快照确认）。

    注：execution 非 device 时 entry/mutable 声明仍解析校验（提前暴露非法声明），
    但审批 CLI 仅对 device 技能写入设备执行项。
    """
    skill_md_path = Path(skill_md_path)
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillDeviceDeclarationError(f"SKILL.md 不可读: {skill_md_path}: {e}") from e
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillDeviceDeclarationError(f"SKILL.md 缺失 frontmatter: {skill_md_path}")
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        raise SkillDeviceDeclarationError(f"SKILL.md frontmatter 解析失败: {skill_md_path}: {e}") from e
    if not isinstance(frontmatter, dict):
        raise SkillDeviceDeclarationError(f"SKILL.md frontmatter 结构非法: {skill_md_path}")

    metadata = frontmatter.get("metadata")
    if metadata is None:
        metadata = {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError as e:
            raise SkillDeviceDeclarationError(f"metadata 字符串形态解析失败: {e}") from e
    if not isinstance(metadata, dict):
        raise SkillDeviceDeclarationError("metadata 必须为映射（字典）形态")

    execution = metadata.get("execution")
    if execution is not None and execution not in ("server", "device"):
        raise SkillDeviceDeclarationError(
            f"metadata.execution 非法值: {execution!r}（仅允许 server/device）")

    entries: Tuple[str, ...] = ()
    if metadata.get("entry") is not None:
        entries = tuple(_normalize_declared_path_list(metadata.get("entry"), field="entry"))
        skill_dir = skill_md_path.parent
        for entry in entries:
            if not (skill_dir / entry).is_file():
                raise SkillDeviceDeclarationError(
                    f"metadata.entry 声明的入口文件不存在: {entry}（skill_dir={skill_dir}）")

    mutable: Tuple[str, ...] = ()
    if metadata.get("mutable") is not None:
        mutable = tuple(_normalize_declared_path_list(metadata.get("mutable"), field="mutable"))
    if len(mutable) > _MAX_DECLARED_MUTABLE_PATHS:
        raise SkillDeviceDeclarationError(
            f"metadata.mutable 超过上限 {_MAX_DECLARED_MUTABLE_PATHS} 条（当前 {len(mutable)} 条）")
    if "SKILL.md" in mutable:
        raise SkillDeviceDeclarationError("metadata.mutable 不得声明 SKILL.md（SKILL.md 恒参与 exec_hash 锁定）")
    intersection = [p for p in mutable if p in entries]
    if intersection:
        raise SkillDeviceDeclarationError(
            f"metadata.mutable 与 metadata.entry 相交（入口脚本是被执行代码，必须受 exec_hash 锁定）: "
            f"{'、'.join(intersection)}")

    version = frontmatter.get("version")
    if version is not None and not isinstance(version, str):
        version = str(version)
    return SkillDeviceDeclaration(execution=execution, entries=entries, mutable=mutable,
                                  version=version)


def compute_skill_exec_hash(skill_dir: Path, mutable_paths: Sequence[str]) -> str:
    """设备对账 hash（M2，plan §3.6）：与 compute_skill_dir_hash 同算法/同内置排除集，
    额外排除 metadata.mutable 声明的自学习可变路径。

    解决样本 skill 运行时写回 references/ui-cache.json 导致 hash 永久漂移的问题；
    computedHash（知识层完整性）语义与排除集一字不动——二者并行锁定不同关注面。
    mutable_paths 为归一化后（正斜杠、相对 skill_dir）的路径集；非法路径形态不会
    命中任何实际遍历到的相对路径（词法不匹配），不额外报错。
    设备端 TS 镜像同一算法（排序 posix 相对路径 + 每文件 sha256 + \\x00 分隔聚合）。
    """
    skill_dir = Path(skill_dir)
    excluded = {_normalize_declared_rel_path(p, field="mutable") for p in mutable_paths}
    digest = hashlib.sha256()
    for rel, path in _iter_hashable_files(skill_dir):
        if rel.as_posix() in excluded:
            continue
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_hash.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 插件目录扫描（轻量解析 frontmatter，无代码执行）
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _read_frontmatter_name(skill_md: Path) -> Optional[str]:
    """轻量解析 SKILL.md frontmatter 的 name 字段（不构造 Skill、不执行任何代码）。"""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if isinstance(frontmatter, dict) and isinstance(frontmatter.get("name"), str):
        return frontmatter["name"]
    return None


@dataclass
class PluginScan:
    """单个插件目录的扫描结果。

    include: 通过审批门的 skill name → (computedHash, skillMdHash)（取审批清单值，
    Layer 2 读手册时按 skillMdHash 复核磁盘内容）；
    rejected: (skill name 或目录名, 原因)，均已 logger.warning。
    """
    include: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    rejected: List[Tuple[str, str]] = field(default_factory=list)


def scan_plugin_dir(
    plugin_dir: Path,
    approvals: Dict[str, dict],
    builtin_names: Set[str],
) -> PluginScan:
    """扫描插件目录一级子目录，逐个判定：① 已审批 ② 目录 hash 与清单一致 ③ 不与内置同名。

    判定全部通过才进入 include 集合；未通过剔除并 warning（注明原因）。
    """
    plugin_dir = Path(plugin_dir)
    scan = PluginScan()
    if not plugin_dir.exists():
        return scan
    for skill_dir in sorted(plugin_dir.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        name = _read_frontmatter_name(skill_md)
        if not name:
            scan.rejected.append((skill_dir.name, "SKILL.md 缺失 frontmatter name，无法解析"))
            logger.warning(f"[SkillPluginGate] 剔除插件 skill（SKILL.md 缺失 frontmatter name）: {skill_dir}")
            continue
        entry = approvals.get(name)
        if entry is None:
            scan.rejected.append((name, "未进入审批清单"))
            logger.warning(f"[SkillPluginGate] 剔除插件 skill（未审批）: {name} @ {skill_dir}")
            continue
        dir_hash = compute_skill_dir_hash(skill_dir)
        if dir_hash != entry["computedHash"]:
            scan.rejected.append((name, "目录 hash 与审批时不符"))
            logger.warning(f"[SkillPluginGate] 剔除插件 skill（目录 hash 与审批时不符）: "
                           f"{name} @ {skill_dir}")
            continue
        if name in builtin_names:
            scan.rejected.append((name, "与内置 skill 同名"))
            logger.warning(f"[SkillPluginGate] 拒绝插件 skill 与内置同名（内置优先）: "
                           f"{name} @ {skill_dir}")
            continue
        scan.include[name] = (entry["computedHash"], entry["skillMdHash"])
    return scan


# ---------------------------------------------------------------------------
# 目录链构建（审批门唯一出口）
# ---------------------------------------------------------------------------

@dataclass
class BaseSources:
    """内置 + 已审批插件的加载结果（供 load_from_sources / 租户合并链消费）。

    builtin_loader: 内置 SkillLoader（run_init 默认 True，现状行为，init DDL 照旧）；
    plugin_loaders: 插件 loader 低 → 高有序列表（include 过滤 + run_init=False，
        构造即不执行插件 init_script——纵深防御，主执行通道拦截在 skill_executor）；
    plugin_hashes: skill name → (computedHash, skillMdHash)，仅含通过审批门的插件；
    warnings: 构建过程的告警摘要。
    """
    builtin_loader: SkillLoader
    plugin_loaders: List[SkillLoader]
    plugin_hashes: Dict[str, Tuple[str, str]]
    warnings: List[str] = field(default_factory=list)


def build_base_registry_sources(builtin_dir: Path, plugins_cfg=None) -> BaseSources:
    """构建「内置 + 已审批插件」目录链（审批门唯一出口）。

    - 内置目录：SkillLoader(builtin_dir)，init 照旧（现状行为）；
    - 各插件目录：scan → SkillLoader(include=通过集, run_init=False)；
    - 插件 vs 内置同名：gate 拒绝（内置优先）；插件 vs 插件同名：高优先级目录
      （storage）覆盖低优先级（仓库根 skills/），与 load_from_directories 覆盖方向一致。
    """
    cfg = plugins_cfg if plugins_cfg is not None else _plugins_cfg()
    builtin_dir = Path(builtin_dir)
    warnings: List[str] = []

    builtin_loader = SkillLoader(builtin_dir)
    builtin_names = set(builtin_loader.skills)

    plugin_loaders: List[SkillLoader] = []
    plugin_hashes: Dict[str, Tuple[str, str]] = {}

    if not cfg.enabled:
        return BaseSources(builtin_loader=builtin_loader, plugin_loaders=[],
                           plugin_hashes={}, warnings=warnings)

    approvals = read_approvals(resolve_approvals_file(cfg))
    seen_plugin_names: Dict[str, Path] = {}
    for plugin_dir in resolve_plugin_dirs(cfg):
        try:
            scan = scan_plugin_dir(plugin_dir, approvals, builtin_names)
        except Exception as e:
            warnings.append(f"插件目录扫描失败，整目录跳过: {plugin_dir}: {e}")
            logger.warning(f"[SkillPluginGate] {warnings[-1]}")
            continue
        for name, reason in scan.rejected:
            warnings.append(f"剔除插件 skill {name}: {reason}")
        if not scan.include:
            continue
        loader = SkillLoader(plugin_dir, include=set(scan.include.keys()), run_init=False)
        plugin_loaders.append(loader)
        for name, hashes in scan.include.items():
            if name in seen_plugin_names:
                logger.warning(f"[SkillPluginGate] 插件间同名，高优先级目录覆盖: "
                               f"{name}: {seen_plugin_names[name]} -> {plugin_dir}")
            seen_plugin_names[name] = plugin_dir
            plugin_hashes[name] = hashes

    return BaseSources(builtin_loader=builtin_loader, plugin_loaders=plugin_loaders,
                       plugin_hashes=plugin_hashes, warnings=warnings)


# ---------------------------------------------------------------------------
# 进程缓存签名（resource_cache 签名刷新用）
# ---------------------------------------------------------------------------

def plugin_signature(plugins_cfg=None) -> tuple:
    """轻量缓存签名：审批清单 (mtime, size) + 各插件目录一级子目录 (name, mtime) 有序元组。

    - enabled=False → 恒 ()（缓存退化为现状永久缓存）；
    - enabled=True 但目录/清单不存在 → 各段为固定 "missing" 标记，签名恒定，
      行为等同永久缓存，不产生重复重建。

    深层文件修改不改一级子目录 mtime——签名可能不变、缓存不重建；该窗口由
    Layer 2 内容门（SKILL.md hash 复核）收敛，见计划 §3.3。
    """
    cfg = plugins_cfg if plugins_cfg is not None else _plugins_cfg()
    if not cfg.enabled:
        return ()
    parts = []
    approvals_file = resolve_approvals_file(cfg)
    try:
        st = approvals_file.stat()
        parts.append(("approvals", st.st_mtime_ns, st.st_size))
    except OSError:
        parts.append(("approvals", "missing"))
    for plugin_dir in resolve_plugin_dirs(cfg):
        try:
            entries = tuple(sorted(
                (child.name, child.stat().st_mtime_ns)
                for child in plugin_dir.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ))
            parts.append((str(plugin_dir), entries))
        except OSError:
            parts.append((str(plugin_dir), "missing"))
    return tuple(parts)
