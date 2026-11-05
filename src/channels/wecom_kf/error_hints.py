"""企微（微信客服）已知错误码简释映射

记录错误日志时为已知错误码追加简要解释，便于生产排查直接定位——
部分错误码的真实含义从 errmsg 看不出来（如 95003 的 errmsg 只有
「send msg quota limit」，真实机制是「未验证企业累计接待客户数上限」）。

解释内容与 docs/incidents/ 复盘文档保持同步更新：新踩坑的错误码
定案后在此登记，已有解释被后续复盘修正时同步修改。
"""
from typing import Any, Dict

# 实证出处见各条目指向的复盘文档；新条目须先有复盘/排查定案再登记
WECHAT_KF_ERROR_HINTS: Dict[int, str] = {
    95003: (
        "发送客服消息可接待客户咨询数限制：未验证企业累计仅可接待约 100 个客户，"
        "撞线后新客户咨询回复全失败、老客户不受影响；根解=企微管理后台完成企业验证"
        "（实证见 docs/incidents/wecom-kf-send-95003-unverified-quota-20261105.md）"
    ),
    95000: (
        "客服账号被平台标记异常或接口参数形态问题：常见为客服账号不在「通过API管理微信客服」"
        "模式（未认证企业易被标记）；另 sync_msg 不带 open_kfid 过滤也报 95000，勿误判账号失效"
        "（参考 docs/incidents/wecom-kf-customer-send-failure-20261025.md）"
    ),
    95007: "请求体含微信未声明的多余字段（如手工模拟时多传 token），检查请求构造是否与生产一致",
    40058: (
        "请求参数缺失/形态错误：注意 servicer/list 等部分接口是 GET（query 传参），"
        "用 POST + JSON body 会报 missing field"
    ),
    45009: (
        "接口调用超过限频（api/min）：并发回调拉取或集中发送易触发，调用方应有指数退避重试"
        "（参考 docs/incidents/wecom-kf-sync-msg-45009-incident.md）"
    ),
}


def append_known_error_hint(errcode: Any, errmsg: Any) -> str:
    """已知错误码时在 errmsg 后追加简要解释；未知错误码原样返回（无 errmsg 时返回空串）"""
    try:
        code = int(errcode)
    except (TypeError, ValueError):
        return str(errmsg or "")
    base = str(errmsg or "")
    hint = WECHAT_KF_ERROR_HINTS.get(code)
    if not hint:
        return base
    if not base:
        return f"已知错误[{code}]: {hint}"
    return f"{base} | 已知错误[{code}]: {hint}"
