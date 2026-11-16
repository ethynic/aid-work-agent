"""租户积分余额硬阻断公共函数（web_c 渠道与 B 端对话共用，设计 §4.4）。

原实现位于 src/main.py `_check_tenant_credit_blocked`（仅 B 端路径使用）；
web_c C 端入口需要同一口径，故抽出为公共函数，main.py 保留同名薄委托。
"""

from typing import Optional

from fastapi.responses import JSONResponse
from loguru import logger

from src.config import settings


def check_tenant_credit_blocked(tenant_id: Optional[str]) -> Optional[JSONResponse]:
    """租户积分余额硬阻断检查（#37 Phase 4）。

    - 私有云部署：放行（客户自购 key，无积分概念，余额恒 0 不得阻断）
    - tenant_id 为空（platform_admin 全局视图/后台调用）：放行
    - 租户余额 > 0：放行
    - 租户余额 ≤ 0：返回 403 JSONResponse，阻断对话

    返回 None 表示放行，返回 JSONResponse 表示阻断（直接 return 给调用方）。
    """
    if settings.deployment.private_cloud:
        return None
    if not tenant_id:
        return None
    try:
        from src.saas.db.tenant_db import TenantDB
        tenant = TenantDB.get_by_id(tenant_id)
        if not tenant:
            # 租户不存在：交给后续流程处理（最终会 404），此处放行
            return None
        credit_balance = float(tenant.get("credit_balance") or 0)
        if credit_balance <= 0:
            logger.warning(f"租户 {tenant_id} 积分余额耗尽（balance={credit_balance}），阻断对话")
            return JSONResponse({
                "success": False,
                "error": "积分余额已耗尽",
                "details": "积分余额已耗尽，无法继续对话，请联系管理员充值",
                "code": "NO_CREDIT",
            }, status_code=403)
    except Exception as e:
        logger.error(f"租户积分余额检查失败 tenant_id={tenant_id}: {e}")
        # 检查异常时不阻断，避免误伤正常用户
        return None
    return None
