"""WeixinFixedContentAdapter：weixin.fixed_content.v1 场景适配器（R41/R43）

执行链契约（对接 P1-A 底座，适配器方法全部经 TrustedAdapterRegistry 受信调用）：
- compile_operations：已发布 revision 的 text/link/image blocks → weixin_message_send_v2
  v2 操作描述分量（R15 字段集由底座 executor 组装）；payload_ref 指向冻结内容块
  （image 块 payload 字节为受控资产引用 asset:<id>，P4-A）。
- authorize_operation：属主/绑定/epoch 校验 + 微信发送配额 scopes（quota_map）。
- validate_evidence（R43）：结构绑定校验（weixin-evidence:<request_id>:<seq>）+
  可插拔真实证据校验器接口；真实校验器随 P0 真机交付前为空实现，evidence_real_mode
  配置开启时 fail-closed（无校验器一律 False）。结构性校验不是真实证据验证。
- serve_payload：content_blocks 冻结字节 + hash 自检（不一致 fail-closed 抛异常）。
"""

import base64
import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional, Protocol

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation.adapters import (
    AdapterContext,
    AuthorizeDecision,
    CompiledOperation,
    EvidenceContext,
    RevisionValidation,
    RunBusinessResult,
    TargetResolution,
)
from src.weixin_marketing import content, quota_map
from src.weixin_marketing.config import WeixinMarketingConfig, get_weixin_marketing_config
from src.weixin_marketing.constants import (
    ACCOUNT_BINDING_STATUS_ACTIVE,
    AUTOMATION_STATUS_ACTIVE,
    GROUP_BINDING_STATE_COMPLETE,
    OPERATION_MESSAGE_SEND,
    PROVIDER_KEY,
    REVISION_STATUS_PUBLISHED,
    SCENARIO_KEY,
    TEST_TASK_REF_SUFFIX,
)
from src.weixin_marketing.models import parse_trigger
from src.weixin_marketing.triggers import compile_trigger_specs, validate_blocks

_EVIDENCE_REF_RE = re.compile(r"^weixin-evidence:[^:\s]+:\d+$")


class RealEvidenceVerifier(Protocol):
    """可插拔真实证据校验器（R43）：随 P0 真机交付前的接口占位。

    真实实现须核验发送后证据（截图/会话快照等）确实存在且归属本次 request_id；
    返回 False 表示证据不成立（delivery 收敛 unknown）。结构性通过不等于真实验证。
    """

    def verify(self, ctx: EvidenceContext) -> bool: ...


class WeixinFixedContentAdapter:
    """微信固定内容场景适配器（P2：文字/网址；图片 operation 留 P4）"""

    scenario_key = SCENARIO_KEY

    def __init__(
        self,
        *,
        config: Optional[WeixinMarketingConfig] = None,
        evidence_verifier: Optional[RealEvidenceVerifier] = None,
    ):
        # config 参数仅测试注入；生产路径（registration）不注入。注入态下各读数
        # （quota/门控等）来自注入值；生产态：授权门控经 get_hot_gate_config()
        # 每次 mtime 热读 yaml（免重启），其余配置读数来自进程内 settings 快照
        # （改 yaml 需重启生效）
        self._config = config
        self._evidence_verifier = evidence_verifier

    @property
    def config(self) -> WeixinMarketingConfig:
        return self._config or get_weixin_marketing_config()

    # ==================== 表访问 ====================

    @staticmethod
    def _to_uuid_text(value) -> Optional[str]:
        """参数侧 UUID 校验/规整（P2-5）：非法形态返回 None（按不存在处理，fail-closed）。

        psycopg2 未 register_uuid，不能直接适配 UUID 对象——此处校验并规整为
        canonical 字符串，SQL 侧 `id = %s` 以无类型字面量比较（走索引，无列侧 ::text 转换）。
        """
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            return None

    @classmethod
    def _load_automation_row(cls, tenant_id: str, task_ref: str) -> Optional[Dict[str, Any]]:
        key = cls._to_uuid_text(task_ref)
        if key is None:
            return None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, tenant_id, user_id, status, active_revision_id, version
                FROM bs_weixin_marketing_automations
                WHERE tenant_id = %s AND id = %s
                """,
                (tenant_id, key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @classmethod
    def _load_revision_row(cls, tenant_id: str, revision_ref: str) -> Optional[Dict[str, Any]]:
        key = cls._to_uuid_text(revision_ref)
        if key is None:
            return None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, tenant_id, automation_id, status, executor_type, trigger_json,
                       policy_json, group_binding_id, content_hash
                FROM bs_weixin_marketing_revisions
                WHERE tenant_id = %s AND id = %s
                """,
                (tenant_id, key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @classmethod
    def _load_revision_blocks(cls, tenant_id: str, revision_ref: str) -> List[Dict[str, Any]]:
        key = cls._to_uuid_text(revision_ref)
        if key is None:
            return []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT position, kind, text_content, url, asset_id, payload_hash
                FROM bs_weixin_marketing_content_blocks
                WHERE tenant_id = %s AND revision_id = %s
                ORDER BY position
                """,
                (tenant_id, key),
            )
            return [dict(r) for r in cursor.fetchall()]

    @classmethod
    def _load_group_binding(cls, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:
        key = cls._to_uuid_text(binding_id)
        if key is None:
            return None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, tenant_id, user_id, device_id, account_binding_id, label,
                       identity_evidence_ref, identity_version, state
                FROM bs_weixin_marketing_group_bindings
                WHERE tenant_id = %s AND id = %s
                """,
                (tenant_id, key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @classmethod
    def _load_account_binding(cls, tenant_id: str, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        key = cls._to_uuid_text(binding_id) if binding_id else None
        if key is None:
            return None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, tenant_id, user_id, device_id, account_anchor_ref, session_epoch, status
                FROM bs_weixin_marketing_account_bindings
                WHERE tenant_id = %s AND id = %s
                """,
                (tenant_id, key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ==================== 场景适配器协议 ====================

    def validate_revision(
        self, ctx: AdapterContext, revision_config: Dict[str, Any]
    ) -> RevisionValidation:
        """发布事务内校验：trigger 编译 + 块门禁 + 绑定可达；产出冻结 schedule specs"""
        config = self.config
        trigger_raw = revision_config.get("trigger") or {}
        blocks = list(revision_config.get("blocks") or [])
        group_binding_id = revision_config.get("group_binding_id")
        try:
            trigger = parse_trigger(dict(trigger_raw)) if trigger_raw else None
            if trigger is None:
                return RevisionValidation(ok=False, reason="缺少触发配置")
            specs = compile_trigger_specs(trigger, config=config)
            validate_blocks(blocks, config)
        except ValueError as e:
            return RevisionValidation(ok=False, reason=str(e))
        if not group_binding_id:
            return RevisionValidation(ok=False, reason="缺少目标群绑定")
        binding = self._load_group_binding(ctx.tenant_id, str(group_binding_id))
        if binding is None:
            return RevisionValidation(ok=False, reason="目标群绑定不存在")
        if binding.get("state") != GROUP_BINDING_STATE_COMPLETE:
            return RevisionValidation(ok=False, reason="目标群绑定未完成核验")
        return RevisionValidation(ok=True, schedule_specs=specs)

    def resolve_target(self, ctx: AdapterContext, target_ref: str) -> TargetResolution:
        """group_bindings 行引用 → 短期 handle + 身份版本（P2 为确定性派生；
        真实 probe/短期 handle 签发随 P0/P3 交付，届时替换派生实现）"""
        binding = self._load_group_binding(ctx.tenant_id, str(target_ref))
        if binding is None:
            return TargetResolution(ok=False, reason="group_binding_not_found")
        if binding.get("tenant_id") != ctx.tenant_id:
            return TargetResolution(ok=False, reason="group_binding_cross_tenant")
        if binding.get("state") != GROUP_BINDING_STATE_COMPLETE:
            return TargetResolution(ok=False, reason=f"group_binding_state:{binding.get('state')}")
        account = self._load_account_binding(ctx.tenant_id, binding.get("account_binding_id"))
        if account is not None and account.get("status") != ACCOUNT_BINDING_STATUS_ACTIVE:
            return TargetResolution(ok=False, reason="account_binding_disabled")
        identity_version = binding.get("identity_version") or "0"
        session_epoch = (account or {}).get("session_epoch") or 0
        handle = base64.urlsafe_b64encode(
            hashlib.sha256(
                f"{ctx.tenant_id}|{target_ref}|{identity_version}".encode("utf-8")
            ).digest()
        ).rstrip(b"=").decode("ascii")[:32]
        target_version = f"iv-{identity_version}-se-{session_epoch}"
        return TargetResolution(ok=True, target_handle=handle, target_version=target_version)

    def authorize_operation(
        self,
        ctx: AdapterContext,
        *,
        operation: str,
        target_ref: Optional[str],
        target_version: Optional[str],
        payload_hash: Optional[str],
        authorization_revision: Optional[str],
        authorization_epoch: Optional[int],
        invocation: Optional[Dict[str, Any]] = None,
        cursor: Optional[Any] = None,
    ) -> AuthorizeDecision:
        """许可事务内场景授权：操作名/任务状态/active revision/属主/绑定链 + 配额 scopes

        P3-A1：task_ref 带 TEST_TASK_REF_SUFFIX 的试发 run 复用同一校验链（任务须
        active、revision 须为当前发布版），但配额切换为 wxm:test:* 独立桶——试发
        不挤占生产发送额度。生产 task_ref 为纯 UUID，不会命中该分支。

        P5 增量复核必修①：入口实时总门控**真热读**——生产注册路径（无配置注入）
        经 get_hot_gate_config() 每调用检查 yaml 文件 mtime（变则重解析，未变走
        缓存），yaml 翻 enabled=false / 移出 tenant_allowlist 后 API 进程内下一次
        授权调用即被拒（ADAPTER_DENIED 403，许可零签发），无需重启；文件不可达/
        损坏 fail-closed 拒绝。测试注入配置时门控跟随注入值（文件级传播由
        TestHotGatePropagation 实证）。已签发许可与在途回执接纳不受影响（手册 §4）。

        cursor（B1.2 协议形参）：本场景授权链全部为自开连接的普通读，保持现状
        不切换游标（不接许可事务写可见性依赖）；形参仅满足协议签名。
        """
        if self._config is not None:
            gate_enabled = self._config.enabled
            gate_allowlist = self._config.tenant_allowlist
        else:
            from src.weixin_marketing.config import get_hot_gate_config

            hot = get_hot_gate_config()
            gate_enabled, gate_allowlist = hot.enabled, hot.tenant_allowlist
        if not gate_enabled:
            return AuthorizeDecision(allowed=False, reason="weixin_marketing_disabled")
        if gate_allowlist and ctx.tenant_id not in gate_allowlist:
            return AuthorizeDecision(allowed=False, reason="tenant_not_allowed")
        if operation != OPERATION_MESSAGE_SEND:
            return AuthorizeDecision(allowed=False, reason=f"unsupported_operation:{operation}")
        raw_task_ref = str(ctx.task_ref or "")
        is_test_task = raw_task_ref.endswith(TEST_TASK_REF_SUFFIX)
        automation_ref = raw_task_ref[: -len(TEST_TASK_REF_SUFFIX)] if is_test_task else raw_task_ref
        automation = self._load_automation_row(ctx.tenant_id, automation_ref)
        if automation is None:
            return AuthorizeDecision(allowed=False, reason="automation_missing")
        if automation.get("status") != AUTOMATION_STATUS_ACTIVE:
            return AuthorizeDecision(allowed=False, reason=f"automation_status:{automation.get('status')}")
        if authorization_revision and str(automation.get("active_revision_id")) != str(authorization_revision):
            return AuthorizeDecision(allowed=False, reason="revision_not_active")
        # 底座许可事务已锁定 task subject 校验 status/epoch（含传参 epoch），
        # 此处复验属主：场景执行链的 user 必须是任务属主（共享 ACL 留 P3 扩展）
        if ctx.user_id and automation.get("user_id") != ctx.user_id:
            return AuthorizeDecision(allowed=False, reason="not_owner")
        revision = self._load_revision_row(ctx.tenant_id, str(authorization_revision or automation.get("active_revision_id")))
        if revision is None or revision.get("status") != REVISION_STATUS_PUBLISHED:
            return AuthorizeDecision(allowed=False, reason="revision_not_published")
        binding = self._load_group_binding(ctx.tenant_id, str(target_ref or ""))
        if binding is None:
            return AuthorizeDecision(allowed=False, reason="group_binding_not_found")
        if binding.get("state") != GROUP_BINDING_STATE_COMPLETE:
            return AuthorizeDecision(allowed=False, reason=f"group_binding_state:{binding.get('state')}")
        account = self._load_account_binding(ctx.tenant_id, binding.get("account_binding_id"))
        if account is not None and account.get("status") != ACCOUNT_BINDING_STATUS_ACTIVE:
            return AuthorizeDecision(allowed=False, reason="account_binding_disabled")
        # payload_hash 必须命中该 revision 某一块的冻结 hash（防替换正文）
        if payload_hash:
            blocks = self._load_revision_blocks(ctx.tenant_id, str(revision["id"]))
            if not any(b.get("payload_hash") == payload_hash for b in blocks):
                return AuthorizeDecision(allowed=False, reason="payload_hash_not_in_revision")
        if is_test_task:
            scopes = quota_map.build_test_quota_scopes(
                tenant_id=ctx.tenant_id,
                scenario_key=self.scenario_key,
                task_ref=automation_ref,
                group_binding_id=str(target_ref),
                account_binding_id=str(binding.get("account_binding_id")) if binding.get("account_binding_id") else None,
                config=self.config,
            )
        else:
            scopes = quota_map.build_quota_scopes(
                tenant_id=ctx.tenant_id,
                scenario_key=self.scenario_key,
                task_ref=ctx.task_ref,
                group_binding_id=str(target_ref),
                account_binding_id=str(binding.get("account_binding_id")) if binding.get("account_binding_id") else None,
                config=self.config,
            )
        return AuthorizeDecision(allowed=True, quota_scopes=scopes)

    def compile_operations(
        self, ctx: AdapterContext, revision_config: Dict[str, Any]
    ) -> List[CompiledOperation]:
        """冻结 revision → 有序 v2 操作描述分量（P4-A：text/link/image 混排顺序编译）。

        image 块同样编译为 weixin_message_send_v2 操作：payload 字节为受控资产引用
        ``asset:<asset_id>``（R57），真实图片字节由 Runtime 经素材下载端点按 invocation
        scope 获取——真实发送驱动属 P0 真机门禁，P4 不实现、不宣称真机图片通过。
        """
        blocks = list(revision_config.get("blocks") or [])
        target_ref = str(revision_config.get("group_binding_id") or "")
        operations: List[CompiledOperation] = []
        for block in sorted(blocks, key=lambda b: b.get("position", 0)):
            position = int(block["position"])
            payload_ref = content.build_payload_ref(ctx.revision_ref, position)
            payload_hash = block.get("payload_hash") or content.payload_hash_of(block)
            operations.append(
                CompiledOperation(
                    position=position,
                    operation=OPERATION_MESSAGE_SEND,
                    provider_key=PROVIDER_KEY,
                    target_ref=target_ref,
                    target_handle=None,  # 执行期 resolve_target 现签
                    target_version=None,
                    payload_ref=payload_ref,
                    payload_hash=payload_hash,
                )
            )
        return operations

    def aggregate_result(
        self, ctx: AdapterContext, delivery_results: List[Dict[str, Any]]
    ) -> RunBusinessResult:
        """整轮业务判定（机器 effect 分别存储，人工判定不覆盖机器证据）"""
        succeeded = sum(
            1 for d in delivery_results if d.get("effect") == "applied" and d.get("phase") == "verified"
        )
        unknown = sum(
            1
            for d in delivery_results
            if d.get("effect") == "unknown" or d.get("phase") == "unknown" or d.get("state") == "unknown"
        )
        skipped = sum(1 for d in delivery_results if d.get("state") == "skipped")
        total = len(delivery_results)
        if unknown:
            verdict = "needs_manual_review"
        elif succeeded == total:
            verdict = "all_delivered"
        elif succeeded > 0:
            verdict = "partially_delivered"
        else:
            verdict = "none_delivered"
        summary = (
            f"total={total} succeeded={succeeded} unknown={unknown} skipped={skipped}"
        )
        return RunBusinessResult(verdict=verdict, summary=summary)

    def validate_evidence(self, ctx: EvidenceContext) -> bool:
        """R43：结构绑定校验 + 可插拔真实校验器。

        结构层：evidence_ref 必须形如 weixin-evidence:<request_id>:<seq> 且中段精确
        等于本次 request_id。真实层：配置了校验器则必须通过；evidence_real_mode=true
        且无校验器时 fail-closed（一律 False）——结构性通过不宣称真实证据验证。
        """
        evidence_ref = ctx.evidence_ref or ""
        if not _EVIDENCE_REF_RE.match(evidence_ref):
            return False
        namespace, request_component, _seq = evidence_ref.split(":", 2)
        if namespace != "weixin-evidence":
            return False
        if request_component != ctx.request_id:
            return False
        if ctx.scenario_key and ctx.scenario_key != self.scenario_key:
            return False
        verifier = self._evidence_verifier
        if verifier is not None:
            try:
                return bool(verifier.verify(ctx))
            except Exception as e:  # noqa: BLE001 校验器异常按不成立处理（fail-closed）
                logger.opt(exception=True).warning(
                    f"后端日志：weixin 真实证据校验器异常 tenant={ctx.tenant_id}: {e}"
                )
                return False
        if self.config.evidence_real_mode:
            # 真实模式无校验器：fail-closed（R43）
            return False
        return True

    def serve_payload(self, ctx: AdapterContext, payload_ref: str) -> bytes:
        """冻结字节：content_blocks 按 (revision, position) 取行，hash 自检不一致抛错"""
        revision_ref, position = content.parse_payload_ref(payload_ref)
        if ctx.revision_ref and revision_ref != ctx.revision_ref:
            raise content.ContentError("payload_ref 与当前 revision 不一致")
        blocks = self._load_revision_blocks(ctx.tenant_id, revision_ref)
        block = next((b for b in blocks if b["position"] == position), None)
        if block is None:
            raise content.ContentError(f"内容块不存在: revision={revision_ref} position={position}")
        revision = self._load_revision_row(ctx.tenant_id, revision_ref)
        if revision is None or revision.get("status") not in ("published", "superseded"):
            # 已发布/被替换的冻结 revision 可继续服务在途/重试载荷；draft 不可
            raise content.ContentError(f"revision 不可用: {revision_ref}")
        data = content.block_payload_bytes(block)
        actual = hashlib.sha256(data).hexdigest()
        if block.get("payload_hash") and actual != block["payload_hash"]:
            raise content.ContentError(
                f"payload 字节与冻结 hash 不一致: revision={revision_ref} position={position}"
            )
        return data
