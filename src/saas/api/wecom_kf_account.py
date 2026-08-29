"""微信客服账号管理 API（引流功能）

路由：/api/saas/wecom-kf/*
- POST   /accounts            创建客服账号（企微 account/add + add_contact_way + 写配置 + 二维码）
- GET    /accounts            列出租户全部客服账号（含绑定员工 / 引流人数 / 累计积分）
- PUT    /accounts/{open_kfid} 编辑（名称/头像 → account/update；其余本地字段直接改配置，支持换绑）
- DELETE /accounts/{open_kfid} 删除（先企微后本地，账号不存在视为可删）

数据模型：tenant_channel_configs.config.kf_account[] JSON 数组，每条在既有字段基础上新增：
  tenant_user_id（绑定引流员工，可换绑）/ scene（add_contact_way 场景值，不可变）/
  contact_url（客服链接，不可变）/ expire_at（到期日 YYYY-MM-DD，可空）/
  credit_limit（积分上限，0=不限）/ qr_title（二维码标题）

方案见：docs/channel/wecom_kf/kf-account-referral-plan.md
"""

import asyncio
import base64
import io
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.config.settings import settings
from src.db.models import CustomerReferralDB
from src.saas.api.tenant_auth import require_admin
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.services.channel_factory import ChannelFactory

router = APIRouter(prefix="/api/saas/wecom-kf", tags=["微信客服账号管理"])

# ============== 常量 ==============

MAX_AVATAR_BYTES = 1 * 1024 * 1024  # 头像上限 1MB
_AVATAR_SUFFIXES = (".png", ".jpg", ".jpeg")
_DEFAULT_CREDIT_LIMIT = 0  # 0 = 不限
MAX_EMPLOYEE_QR_BYTES = 1 * 1024 * 1024  # 顾问二维码上限 1MB

# 校验时直接复用 prompts.py 里的固定话术，避免两处定义漂移
from src.channels.wecom_kf.prompts import MSG_EXPIRED, MSG_CREDIT_EXHAUSTED  # noqa: E402,F401


# ============== 请求模型 ==============

class KfAccountCreate(BaseModel):
    config_id: Optional[str] = Field(None, description="目标渠道配置 config_id（编辑页当前渠道），缺省取租户第一个已验证的 wecom_kf 配置")
    name: str = Field(..., min_length=1, max_length=16, description="客服账号名称，不超过16字符")
    tenant_user_id: str = Field(..., description="绑定引流员工 user_id")
    avatar_base64: Optional[str] = Field(None, description="头像 base64（可选，缺省走租户logo/默认占位兜底）")
    subagent_type: Optional[str] = Field(None, description="绑定的子智能体类型")
    welcome_message: Optional[str] = Field(None, description="欢迎语")
    servicer_userid_list: Optional[List[str]] = Field(None, description="人工接待人员企微 userid")
    allow_agent_transfer: bool = Field(True, description="允许 Agent 主动转人工")
    expire_at: Optional[str] = Field(None, description="到期日期 YYYY-MM-DD，空=不限制")
    credit_limit: int = Field(0, ge=0, description="积分上限，0=不限")
    qr_title: Optional[str] = Field(None, description="二维码标题")
    employee_qr_base64: Optional[str] = Field(None, description="顾问二维码 base64（可选，用于留资时下发顾问微信二维码）")


class KfAccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=16, description="客服账号名称")
    avatar_base64: Optional[str] = Field(None, description="头像 base64（可选，不改则不传）")
    tenant_user_id: Optional[str] = Field(None, description="换绑引流员工（仅影响后续新扫码归因）")
    subagent_type: Optional[str] = Field(None, description="绑定的子智能体类型")
    welcome_message: Optional[str] = Field(None, description="欢迎语")
    servicer_userid_list: Optional[List[str]] = Field(None, description="人工接待人员企微 userid")
    allow_agent_transfer: Optional[bool] = Field(None, description="允许 Agent 主动转人工")
    expire_at: Optional[str] = Field(None, description="到期日期 YYYY-MM-DD，传 null 清除限制")
    credit_limit: Optional[int] = Field(None, ge=0, description="积分上限，0=不限")
    qr_title: Optional[str] = Field(None, description="二维码标题")
    employee_qr_base64: Optional[str] = Field(None, description="顾问二维码 base64（传 null 不清除；传新值替换旧图）")


# ============== 场景反查（channel_routes 复用） ==============


def resolve_scene(tenant_id: str, scene: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """按 scene 反查租户 wecom_kf 配置中匹配的 (config_id, kf_account)。

    租户 wecom_kf 配置通常 1 条、kf_account 个位数，遍历成本可忽略。
    """
    if not scene:
        return None, None
    try:
        configs = ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf")
        for cfg in configs:
            for kf in cfg.get("config", {}).get("kf_account", []):
                if kf.get("scene") == scene:
                    return cfg["config_id"], kf
    except Exception as e:
        logger.warning(f"[wecom-kf] scene 反查失败: tenant={tenant_id}, scene={scene}, error={e}")
    return None, None


# ============== 内部工具 ==============


def _is_account_not_exists(result: Dict[str, Any]) -> bool:
    """企微删除账号返回"账号不存在"视为本地可删（幂等删除）。"""
    errmsg = (result.get("errmsg") or "").lower()
    return "不存在" in errmsg or "not exist" in errmsg


def _raise_servicer_op_error(result: Dict[str, Any], action: str) -> None:
    """servicer add/del 返回 result_list 含逐 userid 结果，任一失败即抛 400。"""
    if result.get("errcode", 0) != 0:
        raise HTTPException(status_code=400, detail=f"企微{action}失败: {result.get('errmsg')}")
    failures = [
        item for item in result.get("result_list", []) if item.get("errcode", 0) != 0
    ]
    if failures:
        detail = "；".join(
            f"{item.get('userid') or item.get('department_id', '?')}: {item.get('errmsg')}"
            for item in failures
        )
        raise HTTPException(status_code=400, detail=f"企微{action}失败: {detail}")


async def _servicer_add_batched(adapter, open_kfid: str, userid_list: List[str]) -> None:
    """分批调用企微添加接待人员（单次上限 100 个），任一失败抛 400。"""
    batch_size = 100
    for i in range(0, len(userid_list), batch_size):
        chunk = userid_list[i : i + batch_size]
        result = await adapter.api_client.servicer_add(open_kfid, chunk)
        _raise_servicer_op_error(result, "添加接待人员")


async def _servicer_del_batched(adapter, open_kfid: str, userid_list: List[str]) -> None:
    """分批调用企微删除接待人员（单次上限 100 个），任一失败抛 400。"""
    batch_size = 100
    for i in range(0, len(userid_list), batch_size):
        chunk = userid_list[i : i + batch_size]
        result = await adapter.api_client.servicer_del(open_kfid, chunk)
        _raise_servicer_op_error(result, "删除接待人员")


async def _sync_kf_servicers(adapter, open_kfid: str, target_userid_list: List[str]) -> None:
    """将客服账号接待人员同步为目标列表，与企微保持一致；任一企微报错抛 400。

    1. 校验每个目标 userid 在企业微信通讯录存在（user/get），否则抛 400（拦 60111）
    2. 对比企微当前接待人员，新增未配置的（servicer/add）、删除多余的（servicer/del）
    调用方在本地配置写入前执行，同步失败则整个保存失败，保证本地与企微一致。
    """
    # 用 set 去重；后续用企微返回的 canonical userid 构建目标集合，
    # 避免用户输入大小写与企微存储大小写不一致时 add/del 反复
    target_set = set(target_userid_list)
    canonical_set: set = set()

    for userid in sorted(target_set):
        user_result = await adapter.api_client.get_user(userid)
        if user_result.get("errcode", 0) != 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"人工接待人员 {userid} 在企业微信通讯录中不存在"
                    f"（{user_result.get('errmsg')}），请核对企微 userid"
                ),
            )
        # user/get 返回 canonical userid；响应无该字段时回退原始输入
        canonical_set.add(user_result.get("userid") or userid)

    list_result = await adapter.api_client.servicer_list(open_kfid)
    if list_result.get("errcode", 0) != 0:
        raise HTTPException(
            status_code=400, detail=f"获取企微接待人员列表失败: {list_result.get('errmsg')}"
        )
    current_set = {
        item.get("userid")
        for item in list_result.get("servicer_list", [])
        if item.get("userid")
    }

    to_add = canonical_set - current_set
    if to_add:
        await _servicer_add_batched(adapter, open_kfid, sorted(to_add))

    to_del = current_set - canonical_set
    if to_del:
        await _servicer_del_batched(adapter, open_kfid, sorted(to_del))


def _build_qr_data_url(contact_url: str) -> str:
    """用 qrcode 库生成二维码 PNG → base64 data URL（无公网文件端点，避免鉴权漏洞）。"""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(contact_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"[wecom-kf] 二维码生成失败: {e}")
        return ""


async def _decode_avatar(avatar_base64: Optional[str]) -> Optional[bytes]:
    """解码头像 base64，校验大小与图片类型。非法返回 None。"""
    if not avatar_base64:
        return None
    try:
        data = base64.b64decode(avatar_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"头像 base64 解码失败: {e}")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail=f"头像大小超过 {MAX_AVATAR_BYTES // 1024 // 1024}MB 限制")
    # 校验图片 magic bytes（PNG/JPEG）
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    if not (is_png or is_jpeg):
        raise HTTPException(status_code=400, detail="头像仅支持 PNG/JPG 格式")
    return data


def _decode_employee_qr(employee_qr_base64: Optional[str]) -> Optional[bytes]:
    """解码顾问二维码 base64，校验大小与图片类型。非法返回 None。"""
    if not employee_qr_base64:
        return None
    try:
        data = base64.b64decode(employee_qr_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"顾问二维码 base64 解码失败: {e}")
    if len(data) > MAX_EMPLOYEE_QR_BYTES:
        raise HTTPException(status_code=400, detail=f"顾问二维码图片超过 {MAX_EMPLOYEE_QR_BYTES // 1024 // 1024}MB 限制")
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    if not (is_png or is_jpeg):
        raise HTTPException(status_code=400, detail="顾问二维码仅支持 PNG/JPG 格式")
    return data


async def _register_employee_qr(tenant_id: str, qr_bytes: bytes, user_id: str) -> str:
    """将顾问二维码 bytes 注册到 ImageRegistry，返回 file_id。

    注册参数（设计 §4.2.3）：source="user_upload", usage="attachment",
    ttl_seconds=PERMANENT_TTL -- 不设 TTL、不被 cleanup 清理，语义准确。
    """
    suffix = ".png" if qr_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    tmp_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "storage", "uploads", "wecom_kf")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.abspath(os.path.join(tmp_dir, f"_kf_qr_{uuid.uuid4().hex[:8]}{suffix}"))
    try:
        with open(tmp_path, "wb") as f:
            f.write(qr_bytes)
        # PERMANENT_TTL 是 ImageRegistry 类属性，不是模块级导出，必须从类上取
        from src.core.image_asset import ImageRegistry, get_image_registry

        ref = await get_image_registry().register(
            source_path=tmp_path,
            tenant_id=tenant_id,
            user_id=user_id,
            display_name="顾问二维码",
            source="user_upload",
            usage="attachment",
            ttl_seconds=ImageRegistry.PERMANENT_TTL,
        )
        return ref.file_id
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def _cleanup_employee_qr(file_id: Optional[str]) -> None:
    """删除顾问二维码图片资产（磁盘文件 + Redis 元数据）。失败仅告警不阻断主流程。"""
    if not file_id:
        return
    try:
        # 注意：从模块导入的是单例实例，而不是 src.core 包的 __getattr__ 惰性导出
        from src.core.redis_client import redis_client
        from src.core.image_asset import get_image_registry

        registry = get_image_registry()
        ref = await registry.get_ref_by_file_id(file_id)
        if ref:
            path = await registry.resolve_local_path(ref)
            path.unlink(missing_ok=True)
        redis_client.delete(redis_client.make_key("uploaded_file", file_id))
        logger.info(f"[wecom-kf] 顾问二维码已清理: file_id={file_id}")
    except Exception as e:
        logger.warning(f"[wecom-kf] 顾问二维码清理失败 file_id={file_id}: {e}")


async def _register_avatar(tenant_id: str, avatar_bytes: bytes, user_id: str) -> str:
    """将客服头像 bytes 注册到 ImageRegistry，返回 file_id（本地持久化，供编辑弹框回显）。

    与顾问二维码同策略：source="user_upload", usage="attachment", 永久 TTL，
    账号删除时由 _cleanup_avatar 主动清理。
    """
    suffix = ".png" if avatar_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_abs_path

    ensure_tenant_storage_dir(tenant_id, "temp")
    tmp_path = get_tenant_storage_abs_path(tenant_id, "temp", f"_kf_avatar_{uuid.uuid4().hex[:8]}{suffix}")
    try:
        with open(tmp_path, "wb") as f:
            f.write(avatar_bytes)
        # PERMANENT_TTL 是 ImageRegistry 类属性，不是模块级导出，必须从类上取
        from src.core.image_asset import ImageRegistry, get_image_registry

        ref = await get_image_registry().register(
            source_path=tmp_path,
            tenant_id=tenant_id,
            user_id=user_id,
            display_name="客服头像",
            source="user_upload",
            usage="attachment",
            ttl_seconds=ImageRegistry.PERMANENT_TTL,
        )
        return ref.file_id
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def _cleanup_avatar(file_id: Optional[str]) -> None:
    """删除客服头像图片资产（磁盘文件 + Redis 元数据）。失败仅告警不阻断主流程。"""
    if not file_id:
        return
    try:
        from src.core.redis_client import redis_client
        from src.core.image_asset import get_image_registry

        registry = get_image_registry()
        ref = await registry.get_ref_by_file_id(file_id)
        if ref:
            path = await registry.resolve_local_path(ref)
            path.unlink(missing_ok=True)
        redis_client.delete(redis_client.make_key("uploaded_file", file_id))
        logger.info(f"[wecom-kf] 客服头像已清理: file_id={file_id}")
    except Exception as e:
        logger.warning(f"[wecom-kf] 客服头像清理失败 file_id={file_id}: {e}")


async def _upload_avatar_bytes(api_client, avatar_bytes: bytes) -> str:
    """将头像 bytes 写入临时文件并上传企微，返回 media_id。"""
    suffix = ".png" if avatar_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    tmp_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "storage", "uploads", "wecom_kf")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.abspath(os.path.join(tmp_dir, f"_kf_avatar_{uuid.uuid4().hex[:8]}{suffix}"))
    try:
        with open(tmp_path, "wb") as f:
            f.write(avatar_bytes)
        result = await api_client.upload_media(tmp_path, "image")
        return result.get("media_id", "")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def _scale_image_to_max_edge(path: str, max_edge: int = 640) -> Optional[bytes]:
    """缩放图片使最大边不超过 max_edge，返回 PNG bytes（Pillow，to_thread 防阻塞）。"""
    def _do():
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            max_side = max(w, h)
            if max_side > max_edge:
                scale = max_edge / max_side
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        logger.warning(f"[wecom-kf] 租户 logo 缩放失败: {e}")
        return None


async def _resolve_tenant_logo_bytes(tenant_id: str) -> Optional[bytes]:
    """读取租户 logo（tenants.logo_file_id → Redis uploaded_file 取磁盘路径 → 缩放 640px）。"""
    try:
        from src.saas.db.tenant_db import TenantDB
        tenant = TenantDB.get_by_id(tenant_id)
        logo_file_id = (tenant or {}).get("logo_file_id")
        if not logo_file_id:
            return None
        from src.core.storage import resolve_path_via_redis
        logo_path = resolve_path_via_redis(logo_file_id)
        if not logo_path:
            return None
        return await _scale_image_to_max_edge(logo_path)
    except Exception as e:
        logger.warning(f"[wecom-kf] 租户 logo 读取失败: tenant={tenant_id}, error={e}")
        return None


async def _resolve_avatar_media_id(adapter, tenant_id: str, avatar_bytes: Optional[bytes]) -> str:
    """头像 media_id 兜底链：管理员上传 > 租户 logo > 默认占位 PNG。"""
    api_client = adapter.api_client
    if avatar_bytes:
        media_id = await _upload_avatar_bytes(api_client, avatar_bytes)
        if media_id:
            return media_id
        logger.warning("[wecom-kf] 管理员上传头像失败，尝试租户 logo 兜底")
    logo_bytes = await _resolve_tenant_logo_bytes(tenant_id)
    if logo_bytes:
        media_id = await _upload_avatar_bytes(api_client, logo_bytes)
        if media_id:
            return media_id
        logger.warning("[wecom-kf] 租户 logo 兜底上传失败，使用默认占位")
    # 默认占位：复用 adapter 默认缩略图（带缓存）
    return await adapter._get_default_thumb_media_id()


def _generate_scene(tenant_id: str) -> str:
    """生成唯一 scene：kf_ + uuid4().hex[:16]（21 字符，满足 [0-9a-zA-Z_-] 与 ≤32 字节）。"""
    existing = set()
    for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
        for kf in cfg.get("config", {}).get("kf_account", []):
            if kf.get("scene"):
                existing.add(kf["scene"])
    for _ in range(5):
        scene = "kf_" + uuid.uuid4().hex[:16]
        if scene not in existing:
            return scene
    return "kf_" + uuid.uuid4().hex[:16]


def _find_kf_entry(tenant_id: str, open_kfid: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """查找 open_kfid 对应的 (config_id, config_dict, kf_account)。"""
    for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
        config_dict = cfg.get("config", {})
        kf_accounts = config_dict.get("kf_account", [])
        for kf in kf_accounts:
            if kf.get("open_kfid") == open_kfid:
                return cfg["config_id"], config_dict, kf
    return None, None, None


def _to_account_view(kf: Dict[str, Any], tenant_id: str, config_id: str) -> Dict[str, Any]:
    """将 kf_account 条目转为 API 视图（含归属渠道 config_id、引流人数、累计积分）。"""
    open_kfid = kf.get("open_kfid", "")
    return {
        "config_id": config_id,
        "open_kfid": open_kfid,
        "name": kf.get("name", ""),
        "subagent_type": kf.get("subagent_type", ""),
        "tenant_user_id": kf.get("tenant_user_id"),
        "scene": kf.get("scene", ""),
        "contact_url": kf.get("contact_url", ""),
        "expire_at": kf.get("expire_at"),
        "credit_limit": kf.get("credit_limit", _DEFAULT_CREDIT_LIMIT),
        "qr_title": kf.get("qr_title", ""),
        "welcome_message": kf.get("welcome_message", ""),
        "servicer_userid_list": kf.get("servicer_userid_list", []),
        "allow_agent_transfer": kf.get("allow_agent_transfer", True),
        "qr_data_url": _build_qr_data_url(kf.get("contact_url", "")),
        "avatar_file_id": kf.get("avatar_file_id"),
        "avatar_download_url": (
            f"/api/files/{kf['avatar_file_id']}/download"
            if kf.get("avatar_file_id")
            else ""
        ),
        "employee_qr_file_id": kf.get("employee_qr_file_id"),
        "employee_qr_download_url": (
            f"/api/files/{kf['employee_qr_file_id']}/download"
            if kf.get("employee_qr_file_id")
            else ""
        ),
        "referral_count": CustomerReferralDB.count_by_open_kfid(tenant_id, open_kfid),
        "credit_used": CustomerReferralDB.sum_kf_account_credit(tenant_id, open_kfid),
    }


async def _get_wecom_kf_config(tenant_id: str) -> Dict[str, Any]:
    """获取租户 wecom_kf 配置（优先已验证），未配置时抛 400。"""
    configs = ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf")
    if not configs:
        raise HTTPException(status_code=400, detail="请先配置并保存微信客服渠道")
    return next((c for c in configs if c.get("verified")), configs[0])


# ============== API 端点 ==============


@router.post("/accounts")
async def create_kf_account(request: Request, body: KfAccountCreate):
    """创建客服账号：企微 account/add → 生成 scene → add_contact_way → 写配置 → 返回二维码。"""
    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 优先写入前端当前编辑的渠道配置（支持同租户多条 wecom_kf 配置），
    # 缺省 config_id 时回退到租户第一个已验证的配置（兼容历史单渠道场景）
    if body.config_id:
        cfg = next(
            (
                c
                for c in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf")
                if c.get("config_id") == body.config_id
            ),
            None,
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="微信客服渠道配置不存在")
    else:
        cfg = await _get_wecom_kf_config(tenant_id)
    config_id = cfg["config_id"]
    config_dict = cfg.get("config", {})

    adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
    if adapter is None:
        raise HTTPException(status_code=500, detail="微信客服渠道适配器创建失败")

    # 1. 上传头像（兜底链：上传 > 租户 logo > 默认占位）
    avatar_bytes = await _decode_avatar(body.avatar_base64)
    media_id = await _resolve_avatar_media_id(adapter, tenant_id, avatar_bytes)
    if not media_id:
        raise HTTPException(status_code=400, detail="客服头像上传失败，请检查企微临时素材接口")

    # 2. 企微创建账号
    add_result = await adapter.api_client.account_add(body.name, media_id)
    if add_result.get("errcode", 0) != 0:
        raise HTTPException(status_code=400, detail=f"企微创建客服账号失败: {add_result.get('errmsg')}")
    open_kfid = add_result.get("open_kfid", "")
    if not open_kfid:
        raise HTTPException(status_code=400, detail="企微返回的 open_kfid 为空")

    # 3. 生成 scene + 获取客服链接
    scene = _generate_scene(tenant_id)
    way_result = await adapter.api_client.add_contact_way(open_kfid, scene)
    contact_url = way_result.get("url", "")
    if way_result.get("errcode", 0) != 0 or not contact_url:
        # 回滚刚创建的账号，避免残留孤儿账号
        try:
            await adapter.api_client.account_del(open_kfid)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"获取客服链接失败: {way_result.get('errmsg')}")

    # 3.5 同步接待人员（创建时指定的人工接待人员）；企微报错则回滚账号避免残留孤儿账号
    if body.servicer_userid_list:
        try:
            await _sync_kf_servicers(adapter, open_kfid, body.servicer_userid_list)
        except HTTPException:
            try:
                await adapter.api_client.account_del(open_kfid)
            except Exception:
                pass
            raise

    # 4. 写配置
    new_kf: Dict[str, Any] = {
        "name": body.name,
        "open_kfid": open_kfid,
        "subagent_type": body.subagent_type or "",
        "allow_agent_transfer": body.allow_agent_transfer,
        "tenant_user_id": body.tenant_user_id,
        "scene": scene,
        "contact_url": contact_url,
        "expire_at": body.expire_at,
        "credit_limit": body.credit_limit,
        "qr_title": body.qr_title or "",
    }
    if body.welcome_message:
        new_kf["welcome_message"] = body.welcome_message
    if body.servicer_userid_list:
        new_kf["servicer_userid_list"] = body.servicer_userid_list
    # 头像本地持久化：仅管理员自定义上传时注册（走租户 logo / 默认占位兜底时不落本地）
    if avatar_bytes:
        new_kf["avatar_file_id"] = await _register_avatar(
            tenant_id, avatar_bytes, admin.get("user_id", "")
        )
    if body.employee_qr_base64:
        qr_bytes = _decode_employee_qr(body.employee_qr_base64)
        if qr_bytes:
            new_kf["employee_qr_file_id"] = await _register_employee_qr(
                tenant_id, qr_bytes, admin.get("user_id", "")
            )

    kf_accounts = config_dict.get("kf_account", [])
    kf_accounts = [k for k in kf_accounts if k.get("open_kfid")] + [new_kf]
    config_dict["kf_account"] = kf_accounts
    ChannelConfigDB.update(config_id, config_dict)

    # 5. 失效 adapter 缓存，使新账号立即生效
    await ChannelFactory.invalidate_adapter(tenant_id, "wecom_kf", config_id, close=True)

    qr_data_url = _build_qr_data_url(contact_url)
    logger.info(f"[wecom-kf] 客服账号创建成功: open_kfid={open_kfid}, scene={scene}, tenant={tenant_id}")
    return {
        "success": True,
        "open_kfid": open_kfid,
        "name": body.name,
        "contact_url": contact_url,
        "qr_data_url": qr_data_url,
        "scene": scene,
        "tenant_user_id": body.tenant_user_id,
        "expire_at": body.expire_at,
        "credit_limit": body.credit_limit,
        "qr_title": body.qr_title or "",
    }


@router.get("/accounts")
async def list_kf_accounts(request: Request):
    """列出当前租户全部客服账号（含绑定员工 / 引流人数 / 累计积分）。"""
    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    accounts = []
    for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
        for kf in cfg.get("config", {}).get("kf_account", []):
            if not kf.get("open_kfid"):
                continue
            accounts.append(_to_account_view(kf, tenant_id, cfg["config_id"]))
    return {"success": True, "accounts": accounts}


@router.put("/accounts/{open_kfid}")
async def update_kf_account(request: Request, open_kfid: str, body: KfAccountUpdate):
    """编辑客服账号：名称/头像 → 企微 account/update；本地字段直接改配置（支持换绑）。

    scene / contact_url / open_kfid 不可变（已发放二维码持续有效）。
    """
    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    config_id, config_dict, kf = _find_kf_entry(tenant_id, open_kfid)
    if not config_id or kf is None:
        raise HTTPException(status_code=404, detail="客服账号不存在")

    adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
    if adapter is None:
        raise HTTPException(status_code=500, detail="微信客服渠道适配器创建失败")

    # 名称/头像变更 → 企微 account/update（未提供的字段不传）
    if body.name is not None and body.name != kf.get("name"):
        name_result = await adapter.api_client.account_update(open_kfid, name=body.name)
        if name_result.get("errcode", 0) != 0:
            raise HTTPException(status_code=400, detail=f"企微更新客服名称失败: {name_result.get('errmsg')}")
        kf["name"] = body.name

    if body.avatar_base64 is not None and body.avatar_base64:
        avatar_bytes = await _decode_avatar(body.avatar_base64)
        media_id = await _resolve_avatar_media_id(adapter, tenant_id, avatar_bytes)
        if not media_id:
            raise HTTPException(status_code=400, detail="客服头像上传失败")
        media_result = await adapter.api_client.account_update(open_kfid, media_id=media_id)
        if media_result.get("errcode", 0) != 0:
            raise HTTPException(status_code=400, detail=f"企微更新客服头像失败: {media_result.get('errmsg')}")
        # 头像本地持久化：注册新图 + 清理旧图，保证编辑弹框可回显
        new_avatar_id = await _register_avatar(tenant_id, avatar_bytes, admin.get("user_id", ""))
        old_avatar_id = kf.get("avatar_file_id")
        if old_avatar_id and old_avatar_id != new_avatar_id:
            await _cleanup_avatar(old_avatar_id)
        kf["avatar_file_id"] = new_avatar_id

    # 本地字段（换绑 tenant_user_id 仅影响后续新扫码归因）
    if body.tenant_user_id is not None:
        kf["tenant_user_id"] = body.tenant_user_id
    if body.subagent_type is not None:
        kf["subagent_type"] = body.subagent_type
    if body.welcome_message is not None:
        if body.welcome_message:
            kf["welcome_message"] = body.welcome_message
        else:
            kf.pop("welcome_message", None)
    if body.servicer_userid_list is not None:
        # 同步企微接待人员（校验 userid 存在 + add 新增 + del 删除），
        # 企微报错则抛 400 使保存失败，不写本地配置，保证与企微一致
        await _sync_kf_servicers(adapter, open_kfid, body.servicer_userid_list)
        if body.servicer_userid_list:
            kf["servicer_userid_list"] = body.servicer_userid_list
        else:
            kf.pop("servicer_userid_list", None)
    if body.allow_agent_transfer is not None:
        kf["allow_agent_transfer"] = body.allow_agent_transfer
    # 用 model_fields_set 区分"未传"与"显式传 null"：传 null 时 body.expire_at 为 None，
    # 若用 `is not None` 判断会跳过赋值，导致无法清除到期日期
    if "expire_at" in body.model_fields_set:
        kf["expire_at"] = body.expire_at or None
    if body.credit_limit is not None:
        kf["credit_limit"] = body.credit_limit
    if body.qr_title is not None:
        kf["qr_title"] = body.qr_title

    # 顾问二维码：传新图替换旧图（显式清理旧 file_id，见设计 §4.2.3）
    if body.employee_qr_base64:
        qr_bytes = _decode_employee_qr(body.employee_qr_base64)
        if qr_bytes:
            new_file_id = await _register_employee_qr(
                tenant_id, qr_bytes, admin.get("user_id", "")
            )
            old_file_id = kf.get("employee_qr_file_id")
            if old_file_id and old_file_id != new_file_id:
                await _cleanup_employee_qr(old_file_id)
            kf["employee_qr_file_id"] = new_file_id

    ChannelConfigDB.update(config_id, config_dict)
    await ChannelFactory.invalidate_adapter(tenant_id, "wecom_kf", config_id, close=True)

    logger.info(f"[wecom-kf] 客服账号已更新: open_kfid={open_kfid}, tenant={tenant_id}")
    return {"success": True, "account": _to_account_view(kf, tenant_id, config_id)}


@router.post("/accounts/{open_kfid}/contact-way")
async def ensure_kf_contact_way(request: Request, open_kfid: str):
    """生成/补齐客服账号的联系方式（scene + 链接 + 二维码）。

    历史账号（引流归因功能之前创建）可能缺失 contact_url / scene，
    已存在则直接返回现有（幂等），缺失时重新调用 add_contact_way 生成新链接。
    """
    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    config_id, config_dict, kf = _find_kf_entry(tenant_id, open_kfid)
    if not config_id or kf is None:
        raise HTTPException(status_code=404, detail="客服账号不存在")

    existing_url = kf.get("contact_url", "")
    existing_scene = kf.get("scene", "")
    if existing_url and existing_scene:
        return {
            "success": True,
            "open_kfid": open_kfid,
            "scene": existing_scene,
            "contact_url": existing_url,
            "qr_data_url": _build_qr_data_url(existing_url),
            "qr_title": kf.get("qr_title", ""),
            "reused": True,
        }

    adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
    if adapter is None:
        raise HTTPException(status_code=500, detail="微信客服渠道适配器创建失败")

    scene = _generate_scene(tenant_id)
    way_result = await adapter.api_client.add_contact_way(open_kfid, scene)
    contact_url = way_result.get("url", "")
    if way_result.get("errcode", 0) != 0 or not contact_url:
        raise HTTPException(status_code=400, detail=f"获取客服链接失败: {way_result.get('errmsg')}")

    kf["scene"] = scene
    kf["contact_url"] = contact_url
    ChannelConfigDB.update(config_id, config_dict)
    await ChannelFactory.invalidate_adapter(tenant_id, "wecom_kf", config_id, close=True)

    logger.info(f"[wecom-kf] 客服账号补齐链接: open_kfid={open_kfid}, scene={scene}, tenant={tenant_id}")
    return {
        "success": True,
        "open_kfid": open_kfid,
        "scene": scene,
        "contact_url": contact_url,
        "qr_data_url": _build_qr_data_url(contact_url),
        "qr_title": kf.get("qr_title", ""),
        "reused": False,
    }


@router.delete("/accounts/{open_kfid}")
async def delete_kf_account(request: Request, open_kfid: str):
    """删除客服账号：先企微 account/del，成功（或账号不存在）才删本地；失败回显 errmsg。"""
    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    config_id, config_dict, kf = _find_kf_entry(tenant_id, open_kfid)
    if not config_id or kf is None:
        raise HTTPException(status_code=404, detail="客服账号不存在")

    adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
    if adapter is None:
        raise HTTPException(status_code=500, detail="微信客服渠道适配器创建失败")

    del_result = await adapter.api_client.account_del(open_kfid)
    if del_result.get("errcode", 0) != 0 and not _is_account_not_exists(del_result):
        raise HTTPException(status_code=400, detail=f"企微删除客服账号失败: {del_result.get('errmsg')}")

    # 清理顾问二维码 + 头像图片资产
    await _cleanup_employee_qr(kf.get("employee_qr_file_id"))
    await _cleanup_avatar(kf.get("avatar_file_id"))

    # 从配置移除
    kf_accounts = [k for k in config_dict.get("kf_account", []) if k.get("open_kfid") != open_kfid]
    config_dict["kf_account"] = kf_accounts
    ChannelConfigDB.update(config_id, config_dict)
    await ChannelFactory.invalidate_adapter(tenant_id, "wecom_kf", config_id, close=True)

    logger.info(f"[wecom-kf] 客服账号已删除: open_kfid={open_kfid}, tenant={tenant_id}")
    return {"success": True, "open_kfid": open_kfid}
