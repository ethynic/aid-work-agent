"""
租户渠道配置 CRUD 操作
"""

import json
import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


class ChannelConfigDB:
    """租户渠道配置数据库访问类"""

    @staticmethod
    def create(
        tenant_id: str,
        channel_type: str,
        config: dict,
    ) -> Optional[Dict[str, Any]]:
        """创建渠道配置"""
        config_id = f"chan_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, config)
                    VALUES (%s, %s, %s, %s)
                """, (
                    config_id, tenant_id, channel_type,
                    json.dumps(config, ensure_ascii=False),
                ))
                conn.commit()
                logger.info(f"Channel config created: {config_id} ({channel_type})")
                return ChannelConfigDB.get_by_id(config_id)
            except Exception as e:
                logger.error(f"Failed to create channel config: {e}")
                return None

    @staticmethod
    def get_by_id(config_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取渠道配置"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tenant_channel_configs WHERE config_id = %s", (config_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                return d
            return None

    @staticmethod
    def list_by_tenant(tenant_id: str, channel_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出租户的渠道配置"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if channel_type:
                cursor.execute(
                    "SELECT * FROM tenant_channel_configs WHERE tenant_id = %s AND channel_type = %s",
                    (tenant_id, channel_type),
                )
            else:
                cursor.execute(
                    "SELECT * FROM tenant_channel_configs WHERE tenant_id = %s",
                    (tenant_id,),
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["config"] = json.loads(d["config"]) if d.get("config") else {}
                results.append(d)
            return results

    @staticmethod
    def update(config_id: str, config: dict) -> bool:
        """更新渠道配置"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tenant_channel_configs
                SET config = %s, updated_at = CURRENT_TIMESTAMP
                WHERE config_id = %s
            """, (json.dumps(config, ensure_ascii=False), config_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def set_verified(config_id: str, verified: bool = True) -> bool:
        """设置验证状态"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tenant_channel_configs
                SET verified = %s, updated_at = CURRENT_TIMESTAMP
                WHERE config_id = %s
            """, (1 if verified else 0, config_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(config_id: str) -> bool:
        """删除渠道配置"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenant_channel_configs WHERE config_id = %s", (config_id,))
            conn.commit()
            return cursor.rowcount > 0
