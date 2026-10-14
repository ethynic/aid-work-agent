"""
对话用户手机号获取/验证绑定脚本（form-filling-api 技能）。

两种模式：
1. 无参：查询当前对话用户已绑定的手机号（users.phone，此前短信验证通过落库）。
2. verify（stdin JSON {"mobile", "code"}）：校验短信验证码，通过后把手机号写回
   users.phone 完成绑定（绑定后走无参模式，后续对话不再需要验证）。

写操作准入 fail-closed：无法确定手机号或验证未通过时，智能体应拒绝一切写操作。

用法:
  python scripts/get_user_phone.py
  echo '{"mobile": "13800000000", "code": "123456"}' | python scripts/get_user_phone.py verify
输出: stdout JSON
  无参:   {"success": true, "bound": true/false, "phone": str}
  verify: {"success": true, "bound": true, "phone": str} 或
          {"success": false, "reason": "invalid|no_user|error", "error": str}
"""

import json
import sys
import os
import re

_MOBILE_PATTERN = re.compile(r"^1\d{10}$")


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False))


def _mask_mobile(mobile: str) -> str:
    """手机号日志脱敏：138****0000"""
    if mobile and len(mobile) >= 7:
        return f"{mobile[:3]}****{mobile[-4:]}"
    return "***"


def _read_input():
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        raw = ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _get_user_id():
    return (os.environ.get("AID_USER_ID") or "").strip()


def _load_user_models():
    from src.db.models import UserDB
    return UserDB


def query_bound_phone():
    """无参模式：返回当前用户已绑定的手机号"""
    user_id = _get_user_id()
    if not user_id:
        _emit({"success": False, "error": "无法获取当前用户身份",
               "bound": False,
               "hint": "无用户上下文（后台调试场景），请按短信验证流程确认身份"})
        return

    try:
        UserDB = _load_user_models()
        user = UserDB.get_by_id(user_id)
    except Exception as e:
        _emit({"success": False, "error": f"查询用户信息失败: {e}", "bound": False})
        return

    phone = (user or {}).get("phone") if isinstance(user, dict) else None
    if phone:
        _emit({"success": True, "bound": True, "phone": phone})
    else:
        _emit({
            "success": True,
            "bound": False,
            "hint": "当前用户尚未绑定手机号。请请用户提供 11 位手机号，"
                    "调用 sms-verification 技能发送验证码，用户回复验证码后执行 "
                    "verify 模式完成验证与绑定。",
        })


def verify_and_bind():
    """verify 模式：校验验证码并绑定手机号到 users.phone"""
    data = _read_input()
    mobile = (data.get("mobile") or "").strip()
    code = (data.get("code") or "").strip()

    if not _MOBILE_PATTERN.match(mobile):
        _emit({"success": False, "reason": "invalid_mobile",
               "error": "手机号格式错误，必须是 11 位数字"})
        return
    if not code:
        _emit({"success": False, "reason": "missing_code",
               "error": "缺少验证码"})
        return

    user_id = _get_user_id()
    if not user_id:
        _emit({"success": False, "reason": "no_user",
               "error": "无法获取当前用户身份，无法绑定"})
        return

    try:
        from src.db.models import verify_sms_code
        ok = verify_sms_code(mobile, code)
    except Exception as e:
        _emit({"success": False, "reason": "error",
               "error": f"验证码校验异常: {e}"})
        return

    if not ok:
        _emit({"success": False, "reason": "invalid",
               "error": "验证码错误或已过期，可重新发送验证码后再试"})
        return

    try:
        UserDB = _load_user_models()
        updated = UserDB.update_info(user_id, phone=mobile)
    except Exception as e:
        _emit({"success": False, "reason": "error",
               "error": f"手机号绑定失败: {e}"})
        return

    if not updated:
        _emit({"success": False, "reason": "error",
               "error": "手机号绑定失败（用户记录未更新）"})
        return

    from loguru import logger
    logger.info(f"[自动填表] 用户手机号验证通过并绑定 user_id={user_id}, "
                f"phone={_mask_mobile(mobile)}")
    _emit({"success": True, "bound": True, "phone": mobile,
           "message": "手机号验证通过并完成绑定，后续对话无需再次验证"})


def main():
    mode = "query"
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "verify":
        mode = "verify"

    if mode == "verify":
        verify_and_bind()
    else:
        query_bound_phone()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 兜底：任何未预期异常都以 JSON 失败返回，不抛栈
        _emit({"success": False, "reason": "error", "error": f"脚本异常: {e}"})
