"""论坛帖 → 产品 型号关联（joiner，纯函数模块）。

设计 §7（v1.1 定版规则）：
- 每产品的「型号段」= procode 非空时优先取 procode，否则 name 前缀
  ``^[A-Z]{2,5}\\d{5,7}[A-Za-z]?`` 提取（实测 386/674 的 name 前缀含可提取型号；
  数字段 5~7 位——样例 "TPG80357" 为 5 位）；
- 完整型号 token ``[A-Za-z]{2,5}\\d{5,7}[A-Za-z]?``（findall，大小写不敏感）
  与目录型号段精确匹配 → 挂；
- 截短编号 token ``(?<![0-9A-Za-z])\\d{5,6}(?![0-9])`` 对目录型号段**数字部分**
  后缀反查；唯一命中才挂；多命中记 match_evidence 疑似（存原因与候选列表）不挂
  ——``157004`` 被 14 个系列共用，误挂比漏挂伤害大；无命中忽略；
- 无 token 的帖子不关联不入库（v1）；一帖可挂多产品。

五组实测样例（设计 §7 表）必须进单测：TPJ157042 / tezj157004p（大小写）/
80357（后缀唯一）/ 26048,26097（无命中）/ 157004（14 命中疑似）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# name 前缀型号段（锚定开头；接口名称如 "TFZJ1890014欧典米灰"；数字段 5~7 位——
# 设计 §7 样例 3 "TPG80357芭菲米白" 为 5 位，{6,7} 提取不到，实测口径放宽为 {5,7}）
MODEL_FROM_NAME_PATTERN = re.compile(r"^[A-Z]{2,5}\d{5,7}[A-Za-z]?")
# 帖子 content 完整型号 token（findall，大小写不敏感；与型号段同口径 5~7 位）
FULL_TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,5}\d{5,7}[A-Za-z]?")
# 帖子 content 截短编号 token（前后不能紧邻字母/前导数字；后视仅挡数字——
# "157004p" 的数字段按截短处理走疑似，"tezj157004p" 的数字段被前视字母挡住不重复提取）
TRUNCATED_TOKEN_PATTERN = re.compile(r"(?<![0-9A-Za-z])\d{5,6}(?![0-9])")
# 型号段内的数字部分（后缀反查键）
_DIGITS_PATTERN = re.compile(r"\d+")


@dataclass
class MatchResult:
    """一帖的关联结果：linked 供挂靠，evidence 供审计重算。"""

    linked_native_ids: List[str] = field(default_factory=list)
    evidence: List[Dict] = field(default_factory=list)


def extract_model_from_name(name: Optional[str]) -> Optional[str]:
    """name 前缀提取型号段（如 ``TFZJ1890014欧典米灰`` → ``TFZJ1890014``）。"""
    if not name:
        return None
    matched = MODEL_FROM_NAME_PATTERN.match(name.strip())
    return matched.group(0) if matched else None


def catalog_model(item: Dict) -> Optional[str]:
    """目录产品的型号段：procode 非空优先，否则 name 前缀提取。"""
    procode = str(item.get("procode") or "").strip()
    if procode:
        return procode
    return extract_model_from_name(item.get("name"))


def build_model_index(catalog: List[Dict]) -> Dict[str, str]:
    """目录 → {型号段小写: native_id}（精确匹配键；同型号取首个，重复型号少见）。

    catalog 条目需含 name/procode/native_id（products 缓存行或 fetch item 均可）。
    """
    index: Dict[str, str] = {}
    for item in catalog:
        model = catalog_model(item)
        native_id = str(item.get("native_id") or item.get("id") or "").strip()
        if model and native_id and model.lower() not in index:
            index[model.lower()] = native_id
    return index


def extract_full_tokens(content: Optional[str]) -> List[str]:
    """content 中提取完整型号 token（原样大小写，匹配时统一小写）。"""
    if not content:
        return []
    return FULL_TOKEN_PATTERN.findall(content)


def extract_truncated_tokens(content: Optional[str]) -> List[str]:
    """content 中提取截短编号 token（纯数字 5~6 位）。"""
    if not content:
        return []
    return TRUNCATED_TOKEN_PATTERN.findall(content)


def match_post(content: Optional[str], model_index: Dict[str, str]) -> MatchResult:
    """按设计 §7 规则把一帖关联到产品目录。

    model_index: build_model_index 产物（{型号段小写: native_id}）。
    """
    result = MatchResult()
    linked: set = set()
    exact_digits: set = set()
    for token in extract_full_tokens(content):
        native_id = model_index.get(token.lower())
        if native_id:
            linked.add(native_id)
            result.evidence.append(
                {"token": token, "status": "exact", "native_id": native_id}
            )
            if m := _DIGITS_PATTERN.search(token):
                exact_digits.add(m.group(0))
    for token in extract_truncated_tokens(content):
        # 已被完整 token 精确命中的数字段不再走后缀反查（避免同段噪声疑似）
        if token in exact_digits:
            continue
        candidates = [
            native_id
            for model, native_id in model_index.items()
            if (m := _DIGITS_PATTERN.search(model)) and m.group(0).endswith(token)
        ]
        unique_candidates = sorted(set(candidates))
        if len(unique_candidates) == 1:
            linked.add(unique_candidates[0])
            result.evidence.append(
                {
                    "token": token,
                    "status": "suffix_unique",
                    "native_id": unique_candidates[0],
                }
            )
        elif len(unique_candidates) > 1:
            result.evidence.append(
                {
                    "token": token,
                    "status": "ambiguous",
                    "candidates": unique_candidates,
                }
            )
    result.linked_native_ids = sorted(linked)
    return result
