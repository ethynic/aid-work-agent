from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager
import src.social_media.connectors  # noqa: F401
from src.social_media.connectors.registry import connector_registry
from src.social_media.enums import PlatformCapability, PublishStatus


def sanitize_error_info(error_msg: str) -> str:
    if not error_msg:
        return error_msg
    for pattern in [r'password["\s:=]+\S+', r'appsecret["\s:=]+\S+', r'secret["\s:=]+\S+', r'token["\s:=]+\S+']:
        error_msg = re.sub(pattern, lambda m: m.group(0).split("=")[0] + "=***", error_msg, flags=re.IGNORECASE)
    return error_msg


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def json_dumps(payload: Any) -> str:
    return json.dumps(payload if payload is not None else {}, ensure_ascii=False, default=str)


def mask_credentials(credentials: dict[str, Any] | None) -> dict[str, str]:
    masked = {}
    for key, value in (credentials or {}).items():
        text = str(value)
        masked[key] = "***" if len(text) <= 6 else f"{text[:3]}***{text[-3:]}"
    return masked


def _tenant_condition(tenant_id: str | None) -> tuple[str, list[Any]]:
    return ("tenant_id = %s", [tenant_id]) if tenant_id else ("tenant_id IS NULL", [])


class SocialMediaService:
    async def create_account(self, tenant_id: str | None, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = new_id("sacct")
        credentials = payload.get("credentials") or {}
        encrypted = encryption_manager.encrypt(json_dumps(credentials)) if credentials else None
        connector = connector_registry.create(payload["platform"])
        capabilities = await connector.validate_account(payload)
        with get_db_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO social_accounts (
                    account_id, tenant_id, platform, display_name, external_account_id,
                    auth_type, credentials_encrypted, credential_key_version, status,
                    capabilities_json, last_validated_at, created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
            """, (
                account_id, tenant_id, payload["platform"], payload["display_name"],
                payload.get("external_account_id"), payload.get("auth_type", "credentials"),
                encrypted, "v1", "active", json_dumps(capabilities.to_json()), user_id,
            ))
            conn.commit()
        return {"account_id": account_id}

    def list_accounts(self, tenant_id: str | None) -> list[dict[str, Any]]:
        where, params = _tenant_condition(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT account_id, tenant_id, platform, display_name, external_account_id,
                       auth_type, status, capabilities_json, last_validated_at, created_at
                FROM social_accounts WHERE {where} AND status != 'deleted'
                ORDER BY created_at DESC
            """, params)
            return [dict(row) for row in cur.fetchall()]

    async def refresh_account_capabilities(self, tenant_id: str | None, account_id: str) -> dict[str, Any]:
        account = self.get_account(tenant_id, account_id)
        connector = connector_registry.create(account["platform"])
        capabilities = await connector.validate_account(account)
        with get_db_connection() as conn:
            conn.cursor().execute("""
                UPDATE social_accounts
                SET capabilities_json = %s, last_validated_at = NOW(), updated_at = NOW()
                WHERE account_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (json_dumps(capabilities.to_json()), account_id, tenant_id))
            conn.commit()
        return capabilities.to_json()

    def get_account(self, tenant_id: str | None, account_id: str) -> dict[str, Any]:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT account_id, tenant_id, platform, display_name, external_account_id,
                       auth_type, status, capabilities_json, last_validated_at, created_at
                FROM social_accounts
                WHERE account_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (account_id, tenant_id))
            row = cur.fetchone()
        if not row:
            raise ValueError("账号不存在或无权限")
        return dict(row)

    def delete_account(self, tenant_id: str | None, account_id: str) -> None:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE social_accounts
                SET status = 'deleted', credentials_encrypted = NULL, updated_at = NOW()
                WHERE account_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (account_id, tenant_id))
            cur.execute("""
                UPDATE social_publish_jobs
                SET status = 'cancelled', updated_at = NOW()
                WHERE account_id = %s AND tenant_id IS NOT DISTINCT FROM %s
                  AND status IN ('draft','scheduled','queued','retry_wait')
            """, (account_id, tenant_id))
            conn.commit()

    def create_plan(self, tenant_id: str | None, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id = new_id("splan")
        with get_db_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO social_content_plans (
                    plan_id, tenant_id, name, period_start, period_end, goal,
                    target_audience, status, owner_user_id, created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                plan_id, tenant_id, payload["name"], payload.get("period_start"),
                payload.get("period_end"), payload.get("goal"), payload.get("target_audience"),
                payload.get("status", "draft"), payload.get("owner_user_id", user_id), user_id,
            ))
            conn.commit()
        return {"plan_id": plan_id}

    def list_plans(self, tenant_id: str | None) -> list[dict[str, Any]]:
        where, params = _tenant_condition(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM social_content_plans WHERE {where} ORDER BY created_at DESC", params)
            return [dict(row) for row in cur.fetchall()]

    def create_plan_item(self, tenant_id: str | None, plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        item_id = new_id("sitem")
        with get_db_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO social_content_items (
                    item_id, tenant_id, plan_id, topic, objective, planned_at,
                    timezone, owner_user_id, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                item_id, tenant_id, plan_id, payload["topic"], payload.get("objective"),
                payload.get("planned_at"), payload.get("timezone", "Asia/Shanghai"),
                payload.get("owner_user_id"), payload.get("status", "draft"),
            ))
            conn.commit()
        return {"item_id": item_id}

    def list_plan_items(self, tenant_id: str | None, plan_id: str) -> list[dict[str, Any]]:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM social_content_items
                WHERE tenant_id IS NOT DISTINCT FROM %s AND plan_id = %s
                ORDER BY planned_at ASC NULLS LAST, created_at DESC
            """, (tenant_id, plan_id))
            return [dict(row) for row in cur.fetchall()]

    def create_asset(self, tenant_id: str | None, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asset_id = new_id("sasset")
        with get_db_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO social_media_assets (
                    asset_id, tenant_id, storage_file_id, asset_type, mime_type, file_size,
                    checksum, source_type, source_uri, license_type, license_owner,
                    license_expires_at, status, metadata_json, created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                asset_id, tenant_id, payload.get("storage_file_id"), payload.get("asset_type"),
                payload.get("mime_type"), payload.get("file_size"), payload.get("checksum"),
                payload.get("source_type"), payload.get("source_uri"), payload.get("license_type"),
                payload.get("license_owner"), payload.get("license_expires_at"),
                payload.get("status", "active"), json_dumps(payload.get("metadata") or {}), user_id,
            ))
            conn.commit()
        return {"asset_id": asset_id}

    def list_assets(self, tenant_id: str | None) -> list[dict[str, Any]]:
        where, params = _tenant_condition(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM social_media_assets WHERE {where} ORDER BY created_at DESC LIMIT 100", params)
            return [dict(row) for row in cur.fetchall()]

    def create_content_master(self, tenant_id: str | None, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        master_id = new_id("smaster")
        content_hash = stable_hash({
            "title": payload.get("title"),
            "brief": payload.get("brief"),
            "facts": payload.get("facts"),
            "sources": payload.get("source_refs"),
        })
        with get_db_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO social_content_masters (
                    master_id, tenant_id, item_id, title, brief, facts_json,
                    source_refs_json, brand_constraints_json, revision, content_hash,
                    status, created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)
            """, (
                master_id, tenant_id, payload.get("item_id"), payload["title"], payload.get("brief"),
                json_dumps(payload.get("facts") or []), json_dumps(payload.get("source_refs") or []),
                json_dumps(payload.get("brand_constraints") or {}), content_hash,
                payload.get("status", "draft"), user_id,
            ))
            conn.commit()
        return {"master_id": master_id, "content_hash": content_hash}

    async def create_variant(self, tenant_id: str | None, user_id: str, master_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.get_account(tenant_id, payload["account_id"])
        variant_id = new_id("svariant")
        content = payload.get("content") or {}
        content_hash = stable_hash(content)
        connector = connector_registry.create(account["platform"])
        validation = await connector.validate_variant({"content_json": content})
        with get_db_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO social_content_variants (
                    variant_id, tenant_id, master_id, account_id, platform, content_type,
                    revision, content_json, content_hash, spec_version, prompt_version,
                    status, created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s)
            """, (
                variant_id, tenant_id, master_id, account["account_id"], account["platform"],
                payload.get("content_type", "article"), json_dumps(content), content_hash,
                validation.get("spec_version"), payload.get("prompt_version", "manual"),
                "draft", user_id,
            ))
            conn.commit()
        return {"variant_id": variant_id, "content_hash": content_hash, "validation": validation}

    def get_variant(self, tenant_id: str | None, variant_id: str) -> dict[str, Any]:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM social_content_variants
                WHERE variant_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (variant_id, tenant_id))
            row = cur.fetchone()
        if not row:
            raise ValueError("内容版本不存在或无权限")
        return dict(row)

    def submit_review(self, tenant_id: str | None, variant_id: str) -> None:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE social_content_variants
                SET status = 'pending_review', updated_at = NOW()
                WHERE variant_id = %s AND tenant_id IS NOT DISTINCT FROM %s AND status IN ('draft','rejected')
            """, (variant_id, tenant_id))
            if cur.rowcount != 1:
                raise ValueError("只有草稿或驳回版本可提交审核")
            conn.commit()

    def review_variant(self, tenant_id: str | None, user_id: str, variant_id: str, decision: str, comment: str | None) -> dict[str, Any]:
        variant = self.get_variant(tenant_id, variant_id)
        if variant["status"] != "pending_review":
            raise ValueError("版本未处于待审核状态")
        review_id = new_id("sreview")
        target_status = "approved" if decision == "approved" else "rejected"
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO social_review_records (
                    review_id, tenant_id, variant_id, variant_revision, content_hash,
                    decision, comment, reviewer_user_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                review_id, tenant_id, variant_id, variant["revision"], variant["content_hash"],
                decision, comment, user_id,
            ))
            cur.execute("""
                UPDATE social_content_variants SET status = %s, updated_at = NOW()
                WHERE variant_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (target_status, variant_id, tenant_id))
            conn.commit()
        return {"review_id": review_id, "status": target_status}

    def _account_capability_values(self, account: dict[str, Any]) -> set[str]:
        raw = account.get("capabilities_json") or {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        return set(raw.get("supported") or [])

    def create_publish_job(self, tenant_id: str | None, user_id: str, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        variant = self.get_variant(tenant_id, payload["variant_id"])
        if variant["status"] != "approved":
            raise ValueError("只有已审核通过的版本可创建发布任务")
        account = self.get_account(tenant_id, variant["account_id"])
        capabilities = self._account_capability_values(account)
        publish_mode = payload.get("publish_mode", "immediate")
        if publish_mode in {"immediate", "scheduled"} and PlatformCapability.API_PUBLISH.value not in capabilities:
            raise ValueError("账号不支持 API 发布")
        if publish_mode == "assisted" and PlatformCapability.ASSISTED_PUBLISH.value not in capabilities:
            raise ValueError("账号不支持辅助发布")
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT review_id FROM social_review_records
                WHERE tenant_id IS NOT DISTINCT FROM %s AND variant_id = %s
                  AND variant_revision = %s AND content_hash = %s AND decision = 'approved'
                ORDER BY reviewed_at DESC LIMIT 1
            """, (tenant_id, variant["variant_id"], variant["revision"], variant["content_hash"]))
            if not cur.fetchone():
                raise ValueError("未找到匹配当前 revision 和 hash 的审核记录")

            job_id = new_id("sjob")
            business_key = idempotency_key or stable_hash({
                "tenant_id": tenant_id,
                "variant_id": variant["variant_id"],
                "revision": variant["revision"],
                "mode": publish_mode,
                "scheduled_at": payload.get("scheduled_at"),
            })
            status = PublishStatus.READY_FOR_MANUAL_PUBLISH.value if publish_mode == "assisted" else (
                PublishStatus.SCHEDULED.value if publish_mode == "scheduled" else PublishStatus.QUEUED.value
            )
            snapshot = {
                "variant_id": variant["variant_id"],
                "revision": variant["revision"],
                "content_hash": variant["content_hash"],
                "platform": variant["platform"],
                "account_id": variant["account_id"],
                "content": variant.get("content_json") or {},
            }
            cur.execute("""
                INSERT INTO social_publish_jobs (
                    job_id, tenant_id, account_id, variant_id, variant_revision, content_hash,
                    publish_mode, scheduled_at, timezone, status, idempotency_key,
                    publish_snapshot_json, created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
            """, (
                job_id, tenant_id, variant["account_id"], variant["variant_id"], variant["revision"],
                variant["content_hash"], publish_mode, payload.get("scheduled_at"),
                payload.get("timezone", "Asia/Shanghai"), status, business_key, json_dumps(snapshot), user_id,
            ))
            if cur.rowcount == 0:
                cur.execute("SELECT job_id, status FROM social_publish_jobs WHERE idempotency_key = %s", (business_key,))
                existing = dict(cur.fetchone())
                conn.commit()
                return {"job_id": existing["job_id"], "status": existing["status"], "idempotent": True}
            conn.commit()
        return {"job_id": job_id, "status": status, "idempotent": False}

    def list_publish_jobs(self, tenant_id: str | None) -> list[dict[str, Any]]:
        where, params = _tenant_condition(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT j.*, a.platform, a.display_name AS account_name
                FROM social_publish_jobs j
                LEFT JOIN social_accounts a ON a.account_id = j.account_id
                WHERE j.{where}
                ORDER BY j.created_at DESC
                LIMIT 100
            """, params)
            return [dict(row) for row in cur.fetchall()]

    def get_publish_job(self, tenant_id: str | None, job_id: str) -> dict[str, Any]:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM social_publish_jobs
                WHERE job_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (job_id, tenant_id))
            row = cur.fetchone()
        if not row:
            raise ValueError("发布任务不存在或无权限")
        return dict(row)

    def cancel_publish_job(self, tenant_id: str | None, job_id: str) -> dict[str, Any]:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE social_publish_jobs
                SET status = 'cancelled', updated_at = NOW()
                WHERE job_id = %s AND tenant_id IS NOT DISTINCT FROM %s
                  AND status IN ('draft','scheduled','queued','retry_wait','ready_for_manual_publish')
            """, (job_id, tenant_id))
            if cur.rowcount != 1:
                raise ValueError("发布任务不可取消或不存在")
            conn.commit()
        return {"job_id": job_id, "status": "cancelled"}

    def manual_confirm(self, tenant_id: str | None, user_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        published_id = new_id("spub")
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM social_publish_jobs
                WHERE job_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (job_id, tenant_id))
            job = cur.fetchone()
            if not job:
                raise ValueError("发布任务不存在或无权限")
            if job["status"] != PublishStatus.READY_FOR_MANUAL_PUBLISH.value:
                raise ValueError("仅辅助发布任务可人工确认")
            cur.execute("""
                UPDATE social_publish_jobs SET status = 'manually_confirmed', updated_at = NOW()
                WHERE job_id = %s AND tenant_id IS NOT DISTINCT FROM %s
            """, (job_id, tenant_id))
            cur.execute("""
                INSERT INTO social_published_contents (
                    published_id, tenant_id, job_id, account_id, variant_id, platform,
                    external_content_id, external_url, confirmation_source, published_at, confirmed_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'manual',%s,%s)
            """, (
                published_id, tenant_id, job_id, job["account_id"], job["variant_id"],
                (job.get("publish_snapshot_json") or {}).get("platform") if isinstance(job.get("publish_snapshot_json"), dict) else None,
                payload.get("external_content_id"), payload.get("external_url"),
                payload.get("published_at") or datetime.utcnow(), user_id,
            ))
            conn.commit()
        return {"published_id": published_id, "status": "manually_confirmed"}

    def analytics_overview(self, tenant_id: str | None) -> dict[str, Any]:
        where, params = _tenant_condition(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT status, COUNT(*) AS count FROM social_publish_jobs WHERE {where} GROUP BY status", params)
            by_status = {row["status"]: row["count"] for row in cur.fetchall()}
            cur.execute(f"SELECT platform, COUNT(*) AS count FROM social_accounts WHERE {where} AND status != 'deleted' GROUP BY platform", params)
            by_platform = {row["platform"]: row["count"] for row in cur.fetchall()}
        return {"publish_jobs_by_status": by_status, "accounts_by_platform": by_platform}
