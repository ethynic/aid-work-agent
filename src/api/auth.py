"""
认证相关API
包括登录、注册、短信验证码等
"""

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.db.database import get_db_connection
from src.db.models import UserDB, SessionDB, send_sms_code, verify_sms_code, hash_password, verify_password, generate_captcha, verify_captcha
from src.config.settings import settings
from src.api.rate_limit import check_login_rate_limit

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _verify_qb_token(password: str) -> bool:
    """验证平台管理员 QBTOKEN（优先使用哈希比较，兼容明文比较）"""
    qb_token_hash = getattr(settings, "qb_token_hash", "")
    if qb_token_hash:
        return verify_password(password, qb_token_hash)
    # 向后兼容：如果未配置哈希，使用明文比较（不推荐）
    qb_token = getattr(settings, "qb_token", "")
    return bool(qb_token) and password == qb_token


# ============== 请求/响应模型 ==============

class PhoneLoginRequest(BaseModel):
    phone: str
    password: str


class PhoneCodeLoginRequest(BaseModel):
    phone: str
    code: str


class SendCodeRequest(BaseModel):
    phone: str


class RegisterRequest(BaseModel):
    phone: str
    password: str
    code: str


# class WechatLoginRequest(BaseModel):
#     wx_openid: str
#     wx_unionid: Optional[str] = None


class BindPhoneRequest(BaseModel):
    user_id: str
    phone: str
    code: str


class LoginRequest(BaseModel):
    """新的登录请求（手机号/用户名 + 密码 + 图形验证码）"""
    identifier: str  # 手机号或用户名
    password: str
    captcha_code: str
    captcha_id: str


class ResetPasswordRequest(BaseModel):
    """重置密码请求"""
    phone: str
    captcha_code: str  # 图形验证码
    captcha_id: str    # 图形验证码ID
    sms_code: str
    new_password: str


class SendResetCodeRequest(BaseModel):
    """发送重置密码验证码请求"""
    phone: str
    captcha_code: str
    captcha_id: str


class LoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    user: Optional[dict] = None
    message: Optional[str] = None


class UserInfo(BaseModel):
    user_id: str
    username: str
    phone: Optional[str] = None
    avatar_url: Optional[str] = None


def get_user_info_with_admin(user: dict) -> dict:
    """构建包含 is_admin 标记的完整用户信息"""
    if not user:
        return {}
    # 判断是否为管理员
    admin_phones = getattr(settings, "admin", None)
    is_admin = False
    if admin_phones:
        phone_list = getattr(admin_phones, "phones", [])
        is_admin = user.get("phone", "") in phone_list

    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "phone": user["phone"],
        "avatar_url": user.get("avatar_url"),
        "is_admin": is_admin,
    }


# ============== Token管理（数据库存储，支持多进程）==============


def generate_token(user_id: str) -> str:
    """生成简单的访问令牌（存储到数据库），并清理超出上限的旧 token"""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 检查并发 token 数并清理超出上限的旧 token
        max_tokens = getattr(settings, "max_concurrent_tokens", 2)
        cursor.execute("""
            SELECT token FROM tokens WHERE user_id = %s ORDER BY expires_at ASC
        """, (user_id,))
        existing_tokens = cursor.fetchall()

        if len(existing_tokens) >= max_tokens:
            # 删除最早的 token，保留 max_tokens - 1 个
            to_delete = len(existing_tokens) - max_tokens + 1
            for i in range(to_delete):
                old_token = existing_tokens[i]["token"]
                cursor.execute("DELETE FROM tokens WHERE token = %s", (old_token,))
                logger.info(f"Deleted old token for user {user_id} (concurrent limit: {max_tokens})")

        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
        """, (token, user_id, expires_at))
        conn.commit()

    logger.info(f"Token generated for user: {user_id}")
    return token


def verify_token(token: str) -> Optional[str]:
    """验证令牌并返回user_id"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, expires_at FROM tokens
            WHERE token = %s
        """, (token,))
        row = cursor.fetchone()

        if not row:
            return None

        # 检查是否过期，过期则主动删除
        # PostgreSQL 返回 datetime 对象，SQLite 返回字符串
        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")

        if datetime.now() > expires_at:
            cursor.execute("DELETE FROM tokens WHERE token = %s", (token,))
            conn.commit()
            return None

        return row["user_id"]


def delete_token(token: str) -> bool:
    """删除指定的 token"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tokens WHERE token = %s", (token,))
        conn.commit()
        return cursor.rowcount > 0


def cleanup_expired_tokens() -> int:
    """清理所有过期的 token，返回清理数量"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tokens WHERE expires_at < %s",
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()
        count = cursor.rowcount
        if count > 0:
            logger.info(f"Cleaned up {count} expired tokens")
        return count


def get_current_user(request: Request) -> Optional[dict]:
    """从请求中获取当前用户"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_id = verify_token(token)
        if user_id:
            return UserDB.get_by_id(user_id)
    return None


# ============== API 端点 ==============


@router.get("/captcha")
async def get_captcha():
    """获取图形验证码（返回 SVG 图片，不返回明文验证码）"""
    captcha = generate_captcha()
    logger.info(f'后端日志：生成验证码, captcha_id={captcha["captcha_id"]}')
    return {
        "success": True,
        "captcha_id": captcha["captcha_id"],
        "svg_base64": captcha["svg_base64"]
    }


@router.post("/captcha/validate")
async def validate_captcha(captcha_id: str, code: str):
    """验证图形验证码（用于重置密码前校验）"""
    if verify_captcha(captcha_id, code):
        return {"success": True, "message": "验证码正确"}
    return {"success": False, "message": "验证码错误或已过期，过期时间5分钟"}


@router.post("/phone/send-code")
async def send_code(request: SendCodeRequest):
    """发送短信验证码"""
    # 简单验证手机号格式
    if len(request.phone) != 11 or not request.phone.isdigit():
        return {"success": False, "message": "手机号格式不正确"}
    
    if send_sms_code(request.phone):
        return {"success": True, "message": "验证码已发送", "expires_in": 300}
    return {"success": False, "message": "发送失败，请稍后重试"}


@router.post("/login")
async def login(request: Request, body: LoginRequest):
    """新的登录接口：手机号/用户名 + 密码 + 图形验证码

    支持两种模式：
    - 演示模式（DEMO_ENABLED=true）：任意手机号 + 888888 密码登录，自动注册用户
    - SaaS 模式：需要 users 表中有注册用户，且密码正确

    平台管理员判断条件：
    - 手机号在 config.yaml 的 admin.phones 数组中
    - 密码等于 .env 中的 QBTOKEN

    平台管理员特殊逻辑：
    - 如果用户不存在但满足平台管理员条件，自动在 users 表创建该用户（role='platform_admin'）
    - 这样方便管理员的数据初始化
    """
    from src.config.settings import settings

    # 登录速率限制（IP 维度）
    client_ip = request.client.host if request.client else "unknown"
    allowed, msg = check_login_rate_limit(client_ip)
    if not allowed:
        return LoginResponse(success=False, message=msg)

    # 校验图形验证码
    if not verify_captcha(body.captcha_id, body.captcha_code):
        return LoginResponse(success=False, message="图形验证码错误或已过期，过期时间5分钟")

    # 检查演示模式配置
    demo_enabled = getattr(settings, "demo", None) and getattr(settings.demo, "enabled", False)
    mock_password = getattr(settings, "demo", None) and getattr(settings.demo, "mock_password", "888888")

    # 根据 identifier 判断是手机号还是用户名
    identifier = body.identifier.strip()

    # 尝试通过手机号或用户名查找用户
    user = None
    is_phone = False

    # 判断是否为手机号格式
    if identifier.isdigit() and len(identifier) == 11:
        user = UserDB.get_by_phone(identifier)
        is_phone = True
    else:
        # 按用户名查找
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE username = {placeholder}", (identifier,))
            row = cursor.fetchone()
            if row:
                user = dict(row)

    # 演示模式：任意手机号 + mock_password 即可登录（自动注册）
    if demo_enabled and is_phone and body.password == mock_password:
        if not user:
            # 自动创建用户
            user = UserDB.create(phone=identifier)
            logger.info(f"演示模式自动创建用户: {identifier}")
        if user:
            token = generate_token(user["user_id"])
            return LoginResponse(
                success=True,
                token=token,
                user=get_user_info_with_admin(user)
            )

    # 平台管理员检查：手机号在admin.phones中 且 密码等于QBTOKEN
    admin_phones = getattr(settings, "admin", None)
    is_platform_admin = (
        is_phone
        and admin_phones
        and identifier in getattr(admin_phones, "phones", [])
        and _verify_qb_token(body.password)
    )

    if is_platform_admin and not user:
        # 平台管理员但用户不存在，自动创建用户（role='platform_admin'）
        logger.info(f"后端日志：平台管理员用户不存在，自动创建，phone={identifier}")
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 生成唯一user_id
            user_id = str(uuid.uuid4())
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(f"""
                INSERT INTO users (user_id, username, phone, role, tenant_id, created_at, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
            """, (user_id, identifier, identifier, "platform_admin", None, now, now))
            conn.commit()

            # 查询刚创建的用户
            cursor.execute(f"SELECT * FROM users WHERE user_id = {placeholder}", (user_id,))
            user = dict(cursor.fetchone())

    if not user:
        return LoginResponse(success=False, message="用户不存在")

    # 校验密码
    password_hash = user.get("password_hash")

    # 检查是否为平台管理员
    is_admin = False
    if is_phone:
        admin_phones = getattr(settings, "admin", None)
        if admin_phones:
            phone_list = getattr(admin_phones, "phones", [])
            # 平台管理员：手机号在admin.phones中 且 密码等于QBTOKEN
            if user.get("phone", "") in phone_list:
                if _verify_qb_token(body.password):
                    is_admin = True

    if is_admin:
        # 平台管理员直接登录，确保 role 为 platform_admin
        if user.get("role") != "platform_admin":
            UserDB.update(user["user_id"], role="platform_admin")
            user = UserDB.get_by_id(user["user_id"])
        token = generate_token(user["user_id"])
        return LoginResponse(
            success=True,
            token=token,
            user={
                "user_id": user["user_id"],
                "username": user["username"],
                "phone": user["phone"],
                "avatar_url": user.get("avatar_url"),
                "is_admin": True,
            }
        )

    # 普通用户密码校验
    if not password_hash:
        # 密码未设置，不允许登录（除非是平台管理员）
        return LoginResponse(success=False, message="密码未设置，请使用忘记密码功能重置")

    if password_hash != hash_password(body.password):
        return LoginResponse(success=False, message="手机号或密码有误")

    # 登录成功
    token = generate_token(user["user_id"])
    return LoginResponse(
        success=True,
        token=token,
        user=get_user_info_with_admin(user)
    )


@router.post("/phone/login")
async def phone_login(request: Request, body: PhoneLoginRequest):
    """手机号密码登录

    演示模式（DEMO_ENABLED=true）：888888 作为 Mock 固定密码
    - 如果手机号存在账号，密码为空，输入 888888 可以登录
    - 如果手机号不存在账号，创建账号并允许登录
    - 如果手机号存在账号，但密码不为空且不是 888888，验证真实密码

    非演示模式：仅验证真实密码
    """
    from src.config.settings import settings

    # 登录速率限制（手机号 + IP 双维度）
    client_ip = request.client.host if request.client else "unknown"
    allowed, msg = check_login_rate_limit(body.phone)
    if not allowed:
        return LoginResponse(success=False, message=msg)
    allowed, msg = check_login_rate_limit(client_ip)
    if not allowed:
        return LoginResponse(success=False, message=msg)

    demo_enabled = getattr(settings, "demo", None) and getattr(settings.demo, "enabled", False)
    mock_password = getattr(settings, "demo", None) and getattr(settings.demo, "mock_password", "888888")

    user = UserDB.get_by_phone(body.phone)

    if user:
        # 用户已存在
        password_hash = user.get("password_hash")

        if not password_hash:
            # 密码未设置，仅演示模式下允许 mock_password 登录
            if demo_enabled and body.password == mock_password:
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user=get_user_info_with_admin(user)
                )
            else:
                return LoginResponse(success=False, message="密码未设置，请使用忘记密码功能重置")
        else:
            # 密码已设置：演示模式下 mock_password 或真实密码均可登录
            if (demo_enabled and body.password == mock_password) or password_hash == hash_password(body.password):
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user=get_user_info_with_admin(user)
                )
            else:
                return LoginResponse(success=False, message="手机号或密码有误")
    else:
        # 用户不存在，仅演示模式下自动创建账号
        if demo_enabled and body.password == mock_password:
            user = UserDB.create(phone=body.phone)
            if user:
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user=get_user_info_with_admin(user),
                    message="账号已自动创建"
                )
            return LoginResponse(success=False, message="登录失败")
        return LoginResponse(success=False, message="用户不存在")


@router.post("/phone/code-login")
async def phone_code_login(request: PhoneCodeLoginRequest):
    """手机号验证码登录

    支持两种模式：
    - 演示模式（DEMO_ENABLED=true）：任意手机号 + 888888 验证码登录
    - SaaS 模式：正常的短信验证码登录
    """
    from src.config.settings import settings

    # 检查演示模式
    demo_enabled = getattr(settings, "demo", None) and getattr(settings.demo, "enabled", False)
    mock_password = getattr(settings, "demo", None) and getattr(settings.demo, "mock_password", "888888")

    # 如果验证码是888888，根据 DEMO_ENABLED 决定是否允许登录
    if request.code == mock_password:
        if not demo_enabled:
            return LoginResponse(success=False, message="演示模式已关闭")

        user = UserDB.get_by_phone(request.phone)
        if not user:
            # 手机号不存在，自动注册
            user = UserDB.create(phone=request.phone)
        if user:
            token = generate_token(user["user_id"])
            return LoginResponse(
                success=True,
                token=token,
                user=get_user_info_with_admin(user)
            )
        return LoginResponse(success=False, message="登录失败")

    # 正常验证码校验流程
    if verify_sms_code(request.phone, request.code):
        user = UserDB.get_by_phone(request.phone)
        if user:
            token = generate_token(user["user_id"])
            return LoginResponse(
                success=True,
                token=token,
                user=get_user_info_with_admin(user)
            )
        else:
            # 手机号不存在，自动注册
            user = UserDB.create(phone=request.phone)
            if user:
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user=get_user_info_with_admin(user)
            )
    return LoginResponse(success=False, message="验证码错误或已过期，过期时间5分钟")


@router.post("/register")
async def register(request: RegisterRequest):
    """用户注册"""
    # 验证验证码
    if not verify_sms_code(request.phone, request.code):
        return {"success": False, "message": "验证码错误或已过期，过期时间5分钟"}
    
    # 检查手机号是否已注册
    if UserDB.get_by_phone(request.phone):
        return {"success": False, "message": "手机号已注册"}

    # 创建用户
    user = UserDB.create(phone=request.phone, password=request.password)
    if user:
        token = generate_token(user["user_id"])
        return LoginResponse(
            success=True,
            token=token,
            user=get_user_info_with_admin(user)
        )
    return {"success": False, "message": "注册失败"}


@router.post("/bind-phone")
async def bind_phone(request: BindPhoneRequest):
    """绑定手机号（用于微信用户绑定手机）"""
    if not verify_sms_code(request.phone, request.code):
        return {"success": False, "message": "验证码错误或已过期，过期时间5分钟"}
    
    # 检查手机号是否已被占用
    existing = UserDB.get_by_phone(request.phone)
    if existing and existing["user_id"] != request.user_id:
        return {"success": False, "message": "手机号已被其他用户绑定"}

    user = UserDB.get_by_id(request.user_id)
    if not user:
        return {"success": False, "message": "用户不存在"}

    # 更新用户手机号
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET phone = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s
        """, (request.phone, request.user_id))
        conn.commit()

    return {"success": True, "message": "手机号绑定成功"}


@router.get("/me")
async def get_current_user_info(request: Request):
    """获取当前用户信息"""
    user = get_current_user(request)
    if user:
        # 判断是否为管理员
        admin_phones = getattr(settings, "admin", None)
        is_admin = False
        if admin_phones:
            phone_list = getattr(admin_phones, "phones", [])
            is_admin = user.get("phone", "") in phone_list

        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "phone": user["phone"],
            "avatar_url": user.get("avatar_url"),
            "is_admin": is_admin,
        }
    raise HTTPException(status_code=401, detail="未登录")


class UpdateProfileRequest(BaseModel):
    """更新用户资料请求"""
    username: Optional[str] = Field(None, description="显示名称")
    avatar_url: Optional[str] = Field(None, description="头像 URL")


@router.patch("/profile")
async def update_profile(
    request: Request,
    body: UpdateProfileRequest,
):
    """更新当前用户资料（显示名称、头像）"""
    current_user = get_current_user(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    user_id = current_user["user_id"]
    updates = body.dict(exclude_unset=True)

    if not updates:
        return {"success": False, "error": "没有需要更新的字段"}

    success = UserDB.update_info(user_id, **updates)
    if success:
        # 返回更新后的用户信息
        updated_user = UserDB.get_by_id(user_id)
        return {
            "success": True,
            "message": "资料更新成功",
            "user": get_user_info_with_admin(updated_user),
        }
    return {"success": False, "error": "更新失败"}


# ============== 忘记密码 ==============

import re


@router.post("/reset-password/send-code")
async def send_reset_password_code(request: SendResetCodeRequest):
    """发送重置密码短信验证码（需先通过图形验证码）"""
    # 校验图形验证码
    if not verify_captcha(request.captcha_id, request.captcha_code):
        return {"success": False, "message": "图形验证码错误或已过期，过期时间5分钟"}

    # 校验手机号格式
    if len(request.phone) != 11 or not request.phone.isdigit():
        return {"success": False, "message": "手机号格式不正确"}

    # 检查用户是否存在
    user = UserDB.get_by_phone(request.phone)
    if not user:
        return {"success": False, "message": "该手机号未注册"}

    # 发送短信验证码
    if send_sms_code(request.phone):
        return {"success": True, "message": "验证码已发送", "expires_in": 300}
    return {"success": False, "message": "发送失败，请稍后重试"}


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """重置密码"""
    # 校验图形验证码
    if not verify_captcha(request.captcha_id, request.captcha_code):
        return {"success": False, "message": "图形验证码错误或已过期，过期时间5分钟"}

    # 校验手机号格式
    if len(request.phone) != 11 or not request.phone.isdigit():
        return {"success": False, "message": "手机号格式不正确"}

    # 校验短信验证码
    if not verify_sms_code(request.phone, request.sms_code):
        return {"success": False, "message": "短信验证码错误或已过期，过期时间5分钟"}

    # 校验新密码是否符合规则
    password_rule = getattr(settings, "password_rule", r"^(?=.*[A-Za-z])(?=.*\d).{8,50}$")
    password_msg = getattr(settings, "password_msg", "长度8-50位，必须有字母+数字")

    if not re.match(password_rule, request.new_password):
        return {"success": False, "message": f"密码不符合规则：{password_msg}"}

    # 更新用户密码
    user = UserDB.get_by_phone(request.phone)
    if not user:
        return {"success": False, "message": "用户不存在"}

    # 更新密码
    new_password_hash = hash_password(request.new_password)
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE users SET password_hash = {placeholder}, updated_at = CURRENT_TIMESTAMP WHERE user_id = {placeholder}",
                      (new_password_hash, user["user_id"]))
        conn.commit()

    return {"success": True, "message": "密码重置成功"}


@router.post("/logout")
async def logout(request: Request):
    """登出"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        delete_token(token)
    return {"success": True}