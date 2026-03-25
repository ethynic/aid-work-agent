"""
认证相关API
包括登录、注册、短信验证码等
"""

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from loguru import logger

from src.db.database import get_db_connection
from src.db.models import UserDB, SessionDB, send_sms_code, verify_sms_code, hash_password

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


# ============== 简单的Token管理（生产环境应使用JWT）==============

_active_tokens = {}  # token -> user_id


def generate_token(user_id: str) -> str:
    """生成简单的访问令牌"""
    token = secrets.token_urlsafe(32)
    _active_tokens[token] = {
        "user_id": user_id,
        "created_at": datetime.now()
    }
    return token


def verify_token(token: str) -> Optional[str]:
    """验证令牌并返回user_id"""
    token_data = _active_tokens.get(token)
    if token_data:
        # 检查是否过期（7天）
        if datetime.now() - token_data["created_at"] < timedelta(days=7):
            return token_data["user_id"]
        else:
            del _active_tokens[token]
    return None


def get_current_user(request: Request) -> Optional[dict]:
    """从请求中获取当前用户"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_id = verify_token(token)
        if user_id:
            return UserDB.get_by_id(user_id)
    return None


# ============== 短信验证码（Mock实现）==============

def send_sms_code(phone: str) -> bool:
    """
    发送短信验证码
    当前为Mock实现，固定验证码888888
    后续对接真实短信供应商时替换此函数
    """
    code = "888888"  # Mock固定验证码
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 标记旧验证码为已使用
        cursor.execute("UPDATE sms_codes SET used = 1 WHERE phone = ?", (phone,))
        
        # 存储新验证码（5分钟有效）
        expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO sms_codes (phone, code, expires_at)
            VALUES (?, ?, ?)
        """, (phone, code, expires_at))
        conn.commit()
    
    logger.info(f"[MOCK SMS] 验证码 {code} 已发送到 {phone}")
    # 实际生产中应调用短信供应商API
    return True


def verify_sms_code(phone: str, code: str) -> bool:
    """验证短信验证码"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM sms_codes 
            WHERE phone = ? AND code = ? AND used = 0 
            AND expires_at > ?
            ORDER BY created_at DESC LIMIT 1
        """, (phone, code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        row = cursor.fetchone()
        
        if row:
            # 标记验证码为已使用
            cursor.execute("UPDATE sms_codes SET used = 1 WHERE phone = ? AND code = ?",
                         (phone, code))
            conn.commit()
            return True
        return False


# ============== API 端点 ==============

# @router.get("/wx/qrcode")
# async def get_wx_qrcode():
    """
    获取微信登录二维码
    返回一个模拟的二维码URL和scene_str
    实际需要对接微信开放平台API
    """
    scene_str = f"wxlogin_{uuid.uuid4().hex[:16]}"
    # 这里返回模拟数据，实际应调用微信API获取真实二维码
    return {
        "qrcode_url": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=weixin://wxpay/bizpayurl?pr={scene_str}",
        "scene_str": scene_str,
        "expire_seconds": 300
    }


# @router.get("/wx/qrcode/{scene_str}/status")
# async def check_wx_qrcode_status(scene_str: str):
    """
    检查微信扫码状态
    实际需要对接微信开放平台API实现回调
    """
    # Mock实现：模拟2秒后扫码成功
    # 实际应通过WebSocket或轮询获取微信回调
    return {
        "status": "waiting",  # waiting | scanned | confirmed | expired
        "openid": None,
        "unionid": None
    }


# @router.post("/wx/login")
# async def wechat_login(request: WechatLoginRequest):
    """微信登录/绑定"""
    # 查找是否已存在该微信用户
    user = UserDB.get_by_wx_openid(request.wx_openid)
    
    if user:
        # 已存在用户，直接登录
        token = generate_token(user["user_id"])
        return LoginResponse(
            success=True,
            token=token,
            user={
                "user_id": user["user_id"],
                "username": user["username"],
                "phone": user["phone"]
            }
        )
    else:
        # 新微信用户，创建临时账号
        user = UserDB.create(wx_openid=request.wx_openid)
        if user:
            token = generate_token(user["user_id"])
            return LoginResponse(
                success=True,
                token=token,
                user={
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "phone": None
                },
                message="请绑定手机号以完���登录"
            )
        return LoginResponse(success=False, message="登录失败")


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
                    user={
                        "user_id": user["user_id"],
                        "username": user["username"],
                        "phone": user["phone"]
                    }
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
                    user={
                        "user_id": user["user_id"],
                        "username": user["username"],
                        "phone": user["phone"]
                    }
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
                user={
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "phone": user["phone"]
                },
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
                user={
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "phone": user["phone"]
                }
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
                user={
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "phone": user["phone"]
                }
            )
        else:
            # 手机号不存在，自动注册
            user = UserDB.create(phone=request.phone)
            if user:
                token = generate_token(user["user_id"])
                return LoginResponse(
                    success=True,
                    token=token,
                    user={
                        "user_id": user["user_id"],
                        "username": user["username"],
                        "phone": user["phone"]
                    }
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
            user={
                "user_id": user["user_id"],
                "username": user["username"],
                "phone": user["phone"]
            }
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
        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "phone": user["phone"],
            "avatar_url": user.get("avatar_url")
        }
    raise HTTPException(status_code=401, detail="未登录")


@router.post("/logout")
async def logout(request: Request):
    """登出"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token in _active_tokens:
            del _active_tokens[token]
    return {"success": True}