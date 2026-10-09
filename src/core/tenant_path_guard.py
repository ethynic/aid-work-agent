"""LLM 命令路径安全防护（跨租户存储 + 平台源码目录）

多租户 SaaS 场景下，LLM 可调用的文件工具（cp、skill_execute 等）不得触碰
其他租户的存储目录。真实案例（2026-09-11）：售前会话中 LLM 通过
`skill_execute` 执行 `find /app -iname '*爱定义*'`，枚举到了其他租户
storage/tenants/ 下的报价单文件路径。

真实案例（2026-10-09）：微信客服会话中 LLM 连续 15 轮用 skill_execute 执行
`grep -r ... /app/src`、`sed -n ... src/tools/http_api.py`、`cat src/...` 等
命令翻找平台源码定位文件下载逻辑，撞满迭代上限。check_text_for_platform_source_paths
拦截对平台源码目录（src/）的引用；src/skills/ 是技能指南让 LLM 执行的脚本
路径，放行。

本模块提供统一的路径归属判定与输出脱敏：
- find_foreign_tenant_owner(): 判断路径是否落在其他租户的 tenants 目录内
- check_text_for_foreign_tenant_paths(): 检查命令/文本中的跨租户路径引用
- redact_foreign_tenant_paths(): 对命令输出中泄露的其他租户路径整行脱敏

匹配不依赖固定存储根（/app/storage、/app/source_storage 等任意挂载点
下的 */tenants/{tid}/ 均可识别），owner 段统一经 normalize_tenant_id
归一化（剥离 tenant_ 前缀）后比较。
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

# tenants 目录下的一段被识别为租户目录名（含 tenant_ 前缀或归一化后的短 id）
_TENANT_DIR_RE = re.compile(
    r"(?:^|[\\/])tenants[\\/]+([A-Za-z0-9_.\-]+)(?:[\\/]|[\r\n]|$)"
)

# 裸 `tenants` 词：后面不是「分隔符 + 租户目录名」的形式。
# 用于拦截绕过完整路径写法的命令，如：
#   cd /app/storage/tenants && cd tenant_bbb && cat ...
#   tar -C /app/storage tenants/... 前先 ls storage/tenants（枚举全部租户）
# 正常引用本租户文件总是 `tenants/{owner}/...` 完整形式。
_BARE_TENANTS_RE = re.compile(
    r"(?<![A-Za-z0-9_])tenants(?![\\/]+[A-Za-z0-9_.\-])"
)

# 脱敏后替换的整行文本
_REDACTED_LINE = "[安全防护] 本行包含其他租户存储路径，已脱敏"


def _iter_tenant_owners(path_str: str) -> List[str]:
    """提取路径文本中所有 tenants/{owner} 段的 owner 目录名"""
    return _TENANT_DIR_RE.findall(path_str)


def find_foreign_tenant_owner(path: Path, current_tenant_id: Optional[str]) -> Optional[str]:
    """判断解析后的路径是否落在其他租户的租户目录内。

    返回违规 owner 的归一化租户 ID（用于日志），无违规返回 None。

    规则：
    - 路径中任意位置的 `tenants/{owner}/` 段，owner 归一化后必须等于
      当前租户（含 `tenant_` 前缀等价，如 tenant_abc 与 abc 视为同一租户）
    - current_tenant_id 为空（无工具执行上下文）时，任何 tenants 目录
      一律视为越界（后台脚本如需跨租户搬运文件应使用独立运维入口，
      不应经过面向 LLM 的工具）
    """
    from src.core.storage import normalize_tenant_id

    parts = path.parts
    owners = []
    for i, part in enumerate(parts):
        if part == "tenants" and i + 1 < len(parts):
            owners.append(parts[i + 1])
    if not owners:
        return None

    if not current_tenant_id:
        return owners[0]

    current = normalize_tenant_id(current_tenant_id)
    for owner in owners:
        if normalize_tenant_id(owner) != current:
            return owner
    return None


def extract_knowledge_refs(path: Path) -> Optional[List[Tuple[str, str]]]:
    """提取路径中所有 `tenants/{owner}/knowledge/{source_type}/` 段对。

    用于 cp 工具的共享知识库放行判定（2026-10-10）：共享知识库授权了
    来源租户的文档检索与读取，cp 复制交付应遵循同一授权边界。

    返回值语义（fail-closed）：
    - 无 tenants 段 → []（非租户路径，与本函数无关）
    - 任一 tenants/{owner} 段后不是 `knowledge/{source_type}/<文件>` 结构
      （如 conversation/ 附件、knowledge/ 后直接是文件名或到末尾）→ None
      （无法确认是共享知识库文件，调用方必须拒绝）
    - 否则返回所有 (owner, source_type) 对（含本租户自身的段，由调用方比对）
    """
    parts = path.parts
    n = len(parts)
    refs: List[Tuple[str, str]] = []
    i = 0
    while i < n:
        if parts[i] == "tenants" and i + 1 < n:
            owner = parts[i + 1]
            # source_type 段之后必须还有文件段：knowledge/{st} 末段是文件名的
            # 旧版路径（无分类子目录）无法确认归属授权，按解析失败处理
            if i + 4 < n and parts[i + 2] == "knowledge":
                refs.append((owner, parts[i + 3]))
                i += 4
                continue
            return None
        i += 1
    return refs


def extract_flat_knowledge_ref(path: Path) -> Optional[Tuple[str, str]]:
    """识别旧版平铺知识库路径 `tenants/{owner}/knowledge/{filename}`。

    旧版文档（分类子目录引入前）磁盘与 documents.file_path 登记均为
    `knowledge/` 下直接放文件，路径无 source_type 段，extract_knowledge_refs
    解析失败。cp 共享放行判定用本函数识别该形态后，由调用方回查
    documents 表确定 source_type（路径本身不含授权信息，必须查库）。

    返回值语义（fail-closed）：
    - 无 tenants 段 → None
    - 恰好一个 tenants/{owner} 段且其后为 `knowledge/{filename}`（filename
      为末段）→ (owner, filename)
    - 其余结构（多个 tenants 段、新版四段路径、conversation 等其他 scene、
      knowledge/ 下还有更深层级）→ None
    """
    parts = path.parts
    n = len(parts)
    idx: Optional[int] = None
    for i, part in enumerate(parts):
        if part == "tenants":
            if idx is not None:
                return None
            idx = i
    if idx is None or idx + 3 != n - 1:
        return None
    if parts[idx + 2] != "knowledge":
        return None
    return parts[idx + 1], parts[idx + 3]


def check_text_for_foreign_tenant_paths(
    text: str, current_tenant_id: Optional[str]
) -> Tuple[bool, Optional[str]]:
    """检查命令文本是否引用了其他租户的存储路径。

    返回 (是否违规, 违规 owner)。用于 skill_execute 执行前的命令预检，
    拦截形如 `ls /app/source_storage/tenants/{其他租户}/...` 的命令。
    current_tenant_id 为空时，任何 tenants 路径引用均视为违规
    （无法确认归属，一律拒绝）。

    同时拦截「裸 tenants 词」写法（后跟 shell 续接符再进租户目录，
    如 `cd /app/storage/tenants && cd {其他租户}`），此类命令无法从
    文本确认目标租户，统一拒绝；返回 owner 为 "bare_tenants"。
    """
    from src.core.storage import normalize_tenant_id

    if _BARE_TENANTS_RE.search(text):
        # 裸 tenants 词独立于完整路径判定：命令同时含本租户完整路径
        # 和裸 tenants（如 `ls storage/tenants/aaa/; ls storage/tenants`）
        # 时仍要拦截枚举行为
        return True, "bare_tenants"

    owners = _iter_tenant_owners(text)
    if not owners:
        return False, None

    if not current_tenant_id:
        return True, owners[0]

    current = normalize_tenant_id(current_tenant_id)
    for owner in owners:
        if normalize_tenant_id(owner) != current:
            return True, owner
    return False, None


def is_source_storage_reference(text: str) -> bool:
    """命令文本是否引用了 source_storage（生产存储在测试容器的挂载点）。

    source_storage 仅租户迁移运维脚本使用，skill 命令中出现即为探测行为。
    """
    return "source_storage" in text


# 独立的 `src` 词（/app/src、src/tools、cd src 等形态）；
# source/srcs 等含 src 前缀的单词不命中。
_PLATFORM_SRC_RE = re.compile(r"(?<![A-Za-z0-9_])src(?![A-Za-z0-9_])")


def check_text_for_platform_source_paths(text: str) -> Tuple[bool, Optional[str]]:
    """检查命令文本是否引用了平台源码目录（src/，src/skills/ 除外）。

    返回 (是否违规, 命中位置附近的文本片段)。用于 skill_execute 执行前的
    命令预检，拦截 LLM 用 grep/sed/cat/find 等命令翻找平台源码的探测行为；
    查找业务文件应走 knowledge_file_search / knowledge_base_search / cp。

    src/skills/ 是技能指南中让 LLM 直接执行的脚本路径（如
    `python src/skills/contract-approval-1.0.0/scripts/xxx.py`），放行。
    """
    for match in _PLATFORM_SRC_RE.finditer(text):
        rest = text[match.end():]
        if re.match(r"/skills(?:/|[\r\n]|$)", rest):
            continue
        return True, text[max(0, match.start() - 20):match.end() + 40].strip()
    return False, None


def redact_foreign_tenant_paths(
    text: str, current_tenant_id: Optional[str]
) -> Tuple[str, List[str]]:
    """对文本中泄露的其他租户存储路径按行脱敏。

    覆盖命令预检无法命中的场景（如 `find /app -iname '*xx*'` 的输出中
    出现其他租户文件路径）。命中行整行替换为脱敏标记，返回
    (脱敏后文本, 违规 owner 列表)。
    """
    from src.core.storage import normalize_tenant_id

    if not text:
        return text, []

    current = normalize_tenant_id(current_tenant_id) if current_tenant_id else None
    violations: List[str] = []
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        owners = _iter_tenant_owners(line)
        foreign = None
        for owner in owners:
            if current is None or normalize_tenant_id(owner) != current:
                foreign = owner
                break
        if foreign:
            lines[idx] = _REDACTED_LINE
            if foreign not in violations:
                violations.append(foreign)
    if violations:
        logger.warning(
            f"[安全防护] 工具输出包含其他租户存储路径，已脱敏 {len(violations)} 处: "
            f"current_tenant={current or '无'}, foreign_tenants={violations}"
        )
    return "\n".join(lines), violations
