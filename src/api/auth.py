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
from src.db.models import UserDB, SessionDB, send_sms_code, verify_sms_code, hash_password
from src.config.settings import settings

router = APIRouter(prefix="/api/auth", tags=["认证"])


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
    """生成简单的访问令牌（存储到数据库）"""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (?, ?, ?)
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
            WHERE token = ?
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
            cursor.execute("DELETE FROM tokens WHERE token = ?", (token,))
            conn.commit()
            return None

        return row["user_id"]


def delete_token(token: str) -> bool:
    """删除指定的 token"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tokens WHERE token = ?", (token,))
        conn.commit()
        return cursor.rowcount > 0


def cleanup_expired_tokens() -> int:
    """清理所有过期的 token，返回清理数量"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tokens WHERE expires_at < ?",
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


@router.post("/phone/send-code")
async def send_code(request: SendCodeRequest):
    """发送短信验证码"""
    # 简单验证手机号格式
    if len(request.phone) != 11 or not request.phone.isdigit():
        return {"success": False, "message": "手机号格式不正确"}
    
    if send_sms_code(request.phone):
        return {"success": True, "message": "验证码已发送", "expires_in": 300}
    return {"success": False, "message": "发送失败，请稍后重试"}


@router.post("/phone/login")
async def phone_login(request: PhoneLoginRequest):
    """手机号密码登录

    扩展功能：888888 作为 Mock 固定密码
    - 如果手机号存在账号，密码为空，输入 888888 可以登录
    - 如果手机号不存在账号，创建账号并允许登录
    - 如果手机号存在账号，但密码不为空且不是 888888，报错"手机号或密码有误"
    """
    MOCK_PASSWORD = "888888"

    user = UserDB.get_by_phone(request.phone)

    if user:
        # 用户已存在
        password_hash = user.get("password_hash")

        if not password_hash:
            # 密码为空，输入 888888 可以登录
            if request.password == MOCK_PASSWORD:
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user=get_user_info_with_admin(user)
                )
            else:
                return LoginResponse(success=False, message="手机号或密码有误")
        else:
            # 密码已设置，验证密码或 888888
            if request.password == MOCK_PASSWORD or password_hash == hash_password(request.password):
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user=get_user_info_with_admin(user)
                )
            else:
                return LoginResponse(success=False, message="手机号或密码有误")
    else:
        # 用户不存在，创建新账号
        user = UserDB.create(phone=request.phone)
        if user:
            token = generate_token(user["user_id"])
            return LoginResponse(
                success=True,
                token=token,
                user=get_user_info_with_admin(user),
                message="账号已自动创建"
            )
        return LoginResponse(success=False, message="登录失败")


@router.post("/phone/code-login")
async def phone_code_login(request: PhoneCodeLoginRequest):
    """手机号验证码登录"""
    # 如果验证码是888888，直接认为是合法验证码
    if request.code == "888888":
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
    return LoginResponse(success=False, message="验证码错误或已过期")


@router.post("/register")
async def register(request: RegisterRequest):
    """用户注册"""
    # 验证验证码
    if not verify_sms_code(request.phone, request.code):
        return {"success": False, "message": "验证码错误或已过期"}
    
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
        return {"success": False, "message": "验证码错误或已过期"}
    
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
            UPDATE users SET phone = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?
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


@router.post("/logout")
async def logout(request: Request):
    """登出"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        delete_token(token)
    return {"success": True}