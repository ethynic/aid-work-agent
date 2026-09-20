"""boss.chat_reply.v1 任务发布契约模型（B2，设计 §5.4 完整冻结）。

与微信 TaskSpecPayload 的差异（设计 §5.4 冻结）：
- 无 opening_text 字段（开场白禁用，extra=forbid 显式拒绝）；
- completion_rule 仅 rounds（复用通用 RoundsRule，禁止 peer_confirmed/judged）；
- scripts 1..10 条话术版本引用（script_version_id+content_hash+frozen_template+
  slot_schema 白名单），总模板 ≤10000 字符；
- slot_evidence_sources 键集合 == 全部 scripts 的 slot_schema 键集合（并集精确相等）；
- slot 占位符语法 {slot_name} 白名单（发布校验拒绝模板中的未知占位符）；
- resume_field 槽位 field 必须在热读白名单内（key_info.* 子集）。

reply_policy/limits/work_window/completion_rule 逐字复用通用层冻结模型
（session_tasks.models 的 ReplyPolicy/TaskLimits/WorkWindow/RoundsRule——设计
§5.4 按类型名冻结，不另立弱化形态）。

spec_validator 经描述器注册（替换 B1.3 占位），create/PATCH/publish 三处共用；
返回 plain dict（service._spec_to_plain 对 BaseModel 走 model_dump(mode="json")）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from src.session_tasks.models import ReplyPolicy, RoundsRule, TaskLimits, WorkWindow

from .constants import (
    PLACEHOLDER_RE,
    SCRIPT_TEMPLATE_MAX_CHARS,
    SCRIPTS_MAX_COUNT,
    SCRIPTS_TOTAL_TEMPLATE_MAX_CHARS,
)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTENT_HASH_MAX = 128


def normalize_template_bytes(template: str) -> bytes:
    """模板规范化字节（content_hash 口径，设计 §5.3）：NFC + \r\n→\n + 去 BOM。"""
    import unicodedata

    text = unicodedata.normalize("NFC", str(template)).replace("\r\n", "\n").lstrip("﻿")
    return text.encode("utf-8")


def template_content_hash(template: str) -> str:
    """content_hash = 模板规范化字节 sha256（§5.3 冻结口径）。"""
    import hashlib

    return hashlib.sha256(normalize_template_bytes(template)).hexdigest()


class BossSlotField(BaseModel):
    """槽位描述（设计 §5.4 slot_schema 值形态）：required 缺省 true（§5.4 三级
    分流对"必需 slot 缺失"才转 handoff）。"""

    model_config = {"extra": "forbid"}

    required: bool = True
    description: str = Field(default="", max_length=200)


class BossScriptRef(BaseModel):
    """话术版本引用（设计 §5.4 冻结）：发布冻结副本（script_version_id+content_hash
    +frozen_template+slot_schema），纵深防御见 §5.3。"""

    model_config = {"extra": "forbid"}

    script_version_id: UUID
    content_hash: str = Field(min_length=1, max_length=_CONTENT_HASH_MAX)
    frozen_template: str = Field(min_length=1, max_length=SCRIPT_TEMPLATE_MAX_CHARS)
    slot_schema: Dict[str, BossSlotField] = Field(default_factory=dict, max_length=20)

    @field_validator("slot_schema")
    @classmethod
    def _slot_keys(cls, v: Dict[str, BossSlotField]) -> Dict[str, BossSlotField]:
        for key in v:
            if not _KEY_RE.fullmatch(key):
                raise ValueError(f"槽位名必须匹配 ^[a-z][a-z0-9_]{{0,63}}$: {key}")
        return v

    @model_validator(mode="after")
    def _template_placeholders(self) -> "BossScriptRef":
        """发布校验（设计 §5.4）：模板中的未知占位符一律拒绝——运行时不应出现
        未知占位符（出现即渲染不变量破坏）。"""
        for name in re.findall(PLACEHOLDER_RE, self.frozen_template):
            if name not in self.slot_schema:
                raise ValueError(f"模板含未知占位符 {{{name}}}（不在 slot_schema 白名单内）")
        return self

    @model_validator(mode="after")
    def _content_hash_consistency(self) -> "BossScriptRef":
        """发布校验（CR 补强，设计 §5.3 核验③）：content_hash 必须等于 frozen_template
        规范化哈希——spec_validator 无租户上下文，版本存在性核验（①②）需 §4.3 分派器
        加租户参数（B3 决断）；但本项纯本地即可闭环，缺失时坏 spec 会带病发布并在
        运行期以 render_invariant_violation 转人工（first-line defense 后移）。"""
        if template_content_hash(self.frozen_template) != self.content_hash:
            raise ValueError("content_hash 与 frozen_template 规范化哈希不一致（spec 冻结副本损坏）")
        return self


class BossSlotSource(BaseModel):
    """槽位取值来源（设计 §5.4 冻结）：peer_message（模型从对方消息提取）或
    resume_field（服务端从绑定 resume_id 简历库 key_info 白名单字段取值）。"""

    model_config = {"extra": "forbid"}

    source: str  # peer_message | resume_field
    field: Optional[str] = None

    @field_validator("source")
    @classmethod
    def _source_enum(cls, v: str) -> str:
        if v not in ("peer_message", "resume_field"):
            raise ValueError("source 必须是 peer_message/resume_field")
        return v

    @model_validator(mode="after")
    def _field_shape(self) -> "BossSlotSource":
        if self.source == "resume_field":
            if not self.field or not _KEY_RE.fullmatch(self.field):
                raise ValueError("resume_field 槽位必须携带合法 field（key_info 字段名）")
        elif self.field is not None:
            raise ValueError("peer_message 槽位不得携带 field")
        return self


class BossTaskSpecPayload(BaseModel):
    """BOSS 任务发布单（设计 §5.4 完整冻结；开场白不存在）。"""

    model_config = {"extra": "forbid"}

    goal: str = Field(min_length=1, max_length=2000)
    completion_rule: RoundsRule  # 仅 rounds（设计 §5.4；peer_confirmed/judged 拒绝）
    reply_policy: ReplyPolicy
    limits: TaskLimits
    work_window: Optional[WorkWindow] = None
    scripts: List[BossScriptRef] = Field(min_length=1, max_length=SCRIPTS_MAX_COUNT)
    slot_evidence_sources: Dict[str, BossSlotSource] = Field(max_length=40)

    @model_validator(mode="after")
    def _cross_checks(self) -> "BossTaskSpecPayload":
        # 1) 总模板 ≤10000 字符（设计 §5.4 冻结）
        total = sum(len(s.frozen_template) for s in self.scripts)
        if total > SCRIPTS_TOTAL_TEMPLATE_MAX_CHARS:
            raise ValueError(f"话术模板总长 {total} 超过 {SCRIPTS_TOTAL_TEMPLATE_MAX_CHARS} 上限")
        # 2) script_version_id 不重复（同版本重复引用无意义且破坏幂等假设）
        ids = [str(s.script_version_id) for s in self.scripts]
        if len(set(ids)) != len(ids):
            raise ValueError("scripts 中 script_version_id 重复")
        # 3) slot_evidence_sources 键集合 == 全部 scripts slot_schema 键并集（精确相等）
        schema_keys: set = set()
        for s in self.scripts:
            schema_keys |= set(s.slot_schema.keys())
        if set(self.slot_evidence_sources.keys()) != schema_keys:
            raise ValueError("slot_evidence_sources 键集合必须与 slot_schema 键并集精确相等")
        # 4) resume_field 白名单（热读；key_info.* 子集，设计 §5.4）
        from .config import resume_field_whitelist

        allowed = set(resume_field_whitelist())
        for key, source in self.slot_evidence_sources.items():
            if source.source == "resume_field" and source.field not in allowed:
                raise ValueError(f"resume_field 槽位 {key} 的字段 {source.field} 不在白名单内")
        # 5) rounds 预算（继承通用发布校验语义，无开场白）：rounds_target ≤ max_replies
        if self.completion_rule.rounds_target > self.limits.max_replies:
            raise ValueError(
                f"rounds_target({self.completion_rule.rounds_target}) 不得超过 max_replies({self.limits.max_replies})"
            )
        return self


def validate_boss_task_spec(payload: dict) -> Dict[str, Any]:
    """描述器 spec_validator 入口（B1.3 占位替换）：非法抛 pydantic ValidationError
    （service 层按 SpecValidationError 转 400 field_errors），合法返回 plain dict。"""
    spec = BossTaskSpecPayload.model_validate(payload)
    return spec.model_dump(mode="json")
