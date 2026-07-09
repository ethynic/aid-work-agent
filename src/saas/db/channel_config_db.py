"""租户渠道配置 CRUD 操作

wecom_personal_rpa 类型配置的特殊处理（第一期 MVP）：
- 写入前对敏感字段（archive_secret / private_key / token / encoding_aes_key / client_secret）
  做 Fernet 加密（复用 src.channels.wecom_personal_rpa.archive.credential_codec）
- 强制 listen_mode='server'（前端禁用 client，后端兜底防 API 绕过）
- 同 tenant 单例：不允许同租户创建两份 wecom_personal_rpa 配置
- API 响应给前端时返回掩码（不会泄漏明文凭证）

调用方约定：
- 服务端内部需要明文凭证时（fetcher / poller / callback_handler / verify 等），调
  decrypt_config_field 或直接用 credential_codec.decrypt_sensitive_fields
- API 响应给前端时调 mask_config_field 或直接用 credential_codec.mask_sensitive_fields
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from psycopg2 import IntegrityError
except ImportError:  # psycopg2 未安装（开发/测试场景）
    IntegrityError = None  # type: ignore[assignment,misc]

from src.channels.wecom_personal_rpa.archive import credential_codec
from src.db.database import get_db_connection

# 需要特殊处理的渠道类型
_RPA_CHANNEL_TYPE = "wecom_personal_rpa"


class ChannelConfigDB:
    """租户渠道配置数据库访问类"""

    @staticmethod
    def create(
        tenant_id: str,
        channel_type: str,
        config: dict,
        subagent_type: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建渠道配置。

        wecom_personal_rpa 类型特殊处理：
        - 单例检查：同 tenant 已存在则返回 None（调用方应转 400 错误）
        - 加密敏感字段 + 强制 listen_mode='server'

        **client 模式注册例外**：admin.register_client 用 config={"client_id":...}
        走 client 模式注册路径（不含 server 凭证，不强制 server，不加密）。
        第一期 client 模式前端禁用，但 register_client 仍保留兼容已有客户端。

        name 字段：用户自定义渠道名称，用于区分同租户多个同类渠道；可为空（兼容 register_client）。
        """
        config_id = f"chan_{uuid.uuid4().hex[:12]}"
        config_to_write = dict(config or {})

        if channel_type == _RPA_CHANNEL_TYPE:
            is_client_register = "client_id" in config_to_write and "archive_secret" not in config_to_write
            if is_client_register:
                # 旧 client 注册路径（admin.register_client）：仅记 client_id，
                # 不强制 server、不加密（无敏感字段），仍走单例检查
                config_to_write.setdefault("listen_mode", "client")
            else:
                # server 模式（用户从渠道配置页录入 5 个凭证）：加密 + 强制 server
                # 单例检查
                if ChannelConfigDB._rpa_config_exists(tenant_id):
                    logger.warning(
                        f"RPA 渠道配置创建被拒绝（同租户已存在）：tenant_id={tenant_id}"
                    )
                    return None
                config_to_write = credential_codec.encrypt_sensitive_fields(config_to_write)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, name, config, subagent_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """,
                    (
                        config_id,
                        tenant_id,
                        channel_type,
                        name,
                        json.dumps(config_to_write, ensure_ascii=False),
                        subagent_type,
                    ),
                )
                conn.commit()
                logger.info(f"Channel config created: {config_id} ({channel_type}, name={name})")
                return ChannelConfigDB.get_by_id(config_id)
            except Exception as e:
                # IntegrityError 为部分唯一索引拦截（如 wecom_personal_rpa 同租户单例
                # 约束的并发竞争），向上抛出由 API 层给出友好提示（区分 400 与 500）
                if IntegrityError is not None and isinstance(e, IntegrityError):
                    logger.warning(
                        f"Channel config create 触发唯一约束冲突 "
                        f"(tenant={tenant_id}, type={channel_type}): {e}"
                    )
                    raise
                logger.error(f"Failed to create channel config: {e}")
                return None

    @staticmethod
    def get_by_id(config_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取渠道配置。

        wecom_personal_rpa 类型返回的 config 中的敏感字段为**掩码**（用于 API 响应）。
        服务端内部需要明文时请用 ``get_by_id_decrypted``。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tenant_channel_configs WHERE config_id = %s",
                (config_id,),
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                if d.get("channel_type") == _RPA_CHANNEL_TYPE:
                    d["config"] = credential_codec.mask_sensitive_fields(d["config"])
                return d
            return None

    @staticmethod
    def get_by_id_decrypted(config_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取渠道配置（敏感字段返回**明文**，仅供服务端内部使用）。

        用于 fetcher / poller / callback_handler / verify 等需要真实凭证的场景。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tenant_channel_configs WHERE config_id = %s",
                (config_id,),
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                if d.get("channel_type") == _RPA_CHANNEL_TYPE:
                    d["config"] = credential_codec.decrypt_sensitive_fields(d["config"])
                return d
            return None

    @staticmethod
    def get_by_tenant_and_id(
        tenant_id: str, config_id: str
    ) -> Optional[Dict[str, Any]]:
        """按 tenant_id + config_id 双重过滤查询（回调路由层用，含明文凭证）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tenant_channel_configs WHERE tenant_id = %s AND config_id = %s",
                (tenant_id, config_id),
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                if d.get("channel_type") == _RPA_CHANNEL_TYPE:
                    d["config"] = credential_codec.decrypt_sensitive_fields(d["config"])
                return d
            return None

    @staticmethod
    def list_by_tenant(
        tenant_id: str, channel_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """列出租户的渠道配置（敏感字段返回掩码）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if channel_type:
                cursor.execute(
                    "SELECT * FROM tenant_channel_configs WHERE tenant_id = %s AND channel_type = %s ORDER BY id DESC",
                    (tenant_id, channel_type),
                )
            else:
                cursor.execute(
                    "SELECT * FROM tenant_channel_configs WHERE tenant_id = %s ORDER BY id DESC",
                    (tenant_id,),
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                if d.get("channel_type") == _RPA_CHANNEL_TYPE:
                    d["config"] = credential_codec.mask_sensitive_fields(d["config"])
                results.append(d)
            return results

    @staticmethod
    def list_by_channel_type(
        channel_type: str, verified_only: bool = False
    ) -> List[Dict[str, Any]]:
        """按 channel_type 跨租户查询（poller 兜底轮询用，含明文凭证）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if verified_only:
                cursor.execute(
                    "SELECT * FROM tenant_channel_configs WHERE channel_type = %s AND verified = 1",
                    (channel_type,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM tenant_channel_configs WHERE channel_type = %s",
                    (channel_type,),
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                # wecom_personal_rpa 类型：返回明文凭证（poller / fetcher 兜底用）
                if d.get("channel_type") == _RPA_CHANNEL_TYPE:
                    d["config"] = credential_codec.decrypt_sensitive_fields(d["config"])
                results.append(d)
            return results

    @staticmethod
    def update(
        config_id: str, config: dict, subagent_type: Optional[str] = None, name: Optional[str] = None
    ) -> bool:
        """更新渠道配置。

        wecom_personal_rpa 类型：
        - 加密敏感字段（空值时保留原 DB 值，避免被覆盖为空）
        - 强制 listen_mode='server'
        - last_seq / last_callback_at / last_fetch_at / last_error_* 等运行时字段
          若调用方未提供，从 DB 读原值保留（防止前端更新凭证时把这些状态字段重置）

        name 字段：传 None 时不动；传空串则清空；传非空串则更新。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 先取现有配置（用于判断类型 + 保留运行时字段）
            cursor.execute(
                "SELECT channel_type, config FROM tenant_channel_configs WHERE config_id = %s",
                (config_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False

            existing_channel_type = row["channel_type"] if isinstance(row, dict) else dict(row)["channel_type"]
            existing_config_raw = row["config"] if isinstance(row, dict) else dict(row)["config"]
            existing_config = (
                json.loads(existing_config_raw) if existing_config_raw else {}
            )

            new_config = dict(config or {})

            if existing_channel_type == _RPA_CHANNEL_TYPE:
                # 保留运行时字段（前端不传时用旧值，避免被重置）
                _RUNTIME_FIELDS = (
                    "last_seq",
                    "last_callback_at",
                    "last_fetch_at",
                    "last_error_at",
                    "last_error_msg",
                    "poll_interval_seconds",
                    "batch_limit",
                )
                for k in _RUNTIME_FIELDS:
                    if k not in new_config and k in existing_config:
                        new_config[k] = existing_config[k]

                # 敏感字段：前端传空串/None/掩码（***开头）时保留 DB 原值
                # - 空串/None：前端未改动该字段，不传值
                # - ***xxxx 掩码：前端从 get_by_id 拿到掩码后原样回传，未被改动
                # 这两种情况都不应覆盖 DB 中已有的密文，避免凭证被破坏
                for k in credential_codec.SENSITIVE_KEYS:
                    incoming = new_config.get(k)
                    is_blank = not incoming or not isinstance(incoming, str)
                    is_mask = isinstance(incoming, str) and incoming.startswith("***")
                    if (is_blank or is_mask) and existing_config.get(k):
                        # 旧值是密文，直接搬运；加密函数对密文会跳过
                        new_config[k] = existing_config[k]

                # 加密 + 强制 server
                new_config = credential_codec.encrypt_sensitive_fields(new_config)

            # name 处理：None 表示不改；其他值（含空串）按传入值更新
            if name is None:
                cursor.execute(
                    """
                    UPDATE tenant_channel_configs
                    SET config = %s, subagent_type = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE config_id = %s
                """,
                    (json.dumps(new_config, ensure_ascii=False), subagent_type, config_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE tenant_channel_configs
                    SET config = %s, subagent_type = %s, name = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE config_id = %s
                """,
                    (json.dumps(new_config, ensure_ascii=False), subagent_type, name, config_id),
                )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def set_verified(config_id: str, verified: bool = True) -> bool:
        """设置验证状态。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tenant_channel_configs
                SET verified = %s, updated_at = CURRENT_TIMESTAMP
                WHERE config_id = %s
            """,
                (1 if verified else 0, config_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(config_id: str) -> bool:
        """删除渠道配置。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tenant_channel_configs WHERE config_id = %s", (config_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_config_field(
        config_id: str, field_name: str, field_value: Any
    ) -> bool:
        """更新 config JSON 中的单个字段（如 last_seq / last_callback_at）。

        用于 fetcher 推进 seq、callback_handler 更新回调时间等增量更新场景。
        对 wecom_personal_rpa 类型直接读-改-写（避免影响其他字段）。

        安全约束：禁止通过此函数写入敏感字段（archive_secret / private_key / token /
        encoding_aes_key / client_secret）以及 listen_mode 字段。这些字段必须走
        create/update 主路径，经过加密和 server 模式强制（防止 API 绕过）。
        """
        # 安全护栏：防止绕过主路径写入敏感字段或 listen_mode
        _PROTECTED_FIELDS = set(credential_codec.SENSITIVE_KEYS) | {"listen_mode"}
        if field_name in _PROTECTED_FIELDS:
            logger.warning(
                f"update_config_field 拒绝写入受保护字段 '{field_name}' "
                f"(config_id={config_id})；请走 ChannelConfigDB.update"
            )
            return False

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT config FROM tenant_channel_configs WHERE config_id = %s",
                (config_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            raw = row["config"] if isinstance(row, dict) else dict(row)["config"]
            cfg = json.loads(raw) if raw else {}
            cfg[field_name] = field_value
            cursor.execute(
                "UPDATE tenant_channel_configs SET config = %s, updated_at = CURRENT_TIMESTAMP WHERE config_id = %s",
                (json.dumps(cfg, ensure_ascii=False), config_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _rpa_config_exists(tenant_id: str) -> bool:
        """检查租户是否已有 wecom_personal_rpa 类型配置（单例约束）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM tenant_channel_configs WHERE tenant_id = %s AND channel_type = %s LIMIT 1",
                (tenant_id, _RPA_CHANNEL_TYPE),
            )
            return cursor.fetchone() is not None
