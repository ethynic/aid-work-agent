"""企业微信群机器人消息发送（招聘面试邀约通知 Phase 1）

设计：docs/design/recruiting/recruiting-interview-notify-design.md §4

职责（只管「发」，不管「要不要发」——编排与留痕在 recruiting_notify_service）：
- send_markdown：POST 机器人 webhook，msgtype=markdown；单条 content 超 2040 字节
  （企微上限 4096 字节，UTF-8 中文约 3 字节/字，保守取 2040 防边界截断）时
  按行边界切成多条顺序发送
- send_text：msgtype=text，at_mobiles 手机号放 mentioned_mobile_list 实现群内 @
  （企微 markdown 不支持 @，@只能走 text；手机号 @ 必须用 mentioned_mobile_list，
  mentioned_list 只接受 userid）

可靠性约定：
- errcode != 0 或 HTTP 非 200 视为失败，记 err；每次请求失败重试 1 次
- 任何异常（网络断/超时/响应非 JSON）都不外抛，返回 (False, 错误文案)
- 两个函数均为纯发送：不读配置、不写库，便于上层 monkeypatch 测试
"""
import httpx
from loguru import logger

# 企微机器人单条消息字节上限（保守值：官方 markdown 上限 4096 字节，
# 为 UTF-8 与转义余量取 2040，与设计 §4「>2040 截断续发」一致）
_MAX_CONTENT_BYTES = 2040
_HTTP_TIMEOUT_SECONDS = 10
_RETRY_COUNT = 1  # 失败后重试 1 次（共 2 次尝试）


def _split_markdown(content: str, limit: int = _MAX_CONTENT_BYTES) -> list:
    """超长 markdown 按行边界切块（每块 UTF-8 字节数 ≤ limit）。

    单行本身超限时硬切该行（保尾不保头会导致语义断裂，按头部顺序截）。
    空内容返回空列表（由调用方决定是否发送）。
    """
    if not content:
        return []
    if len(content.encode("utf-8")) <= limit:
        return [content]

    chunks = []
    current = ""
    for line in content.split("\n"):
        # 候选行（含换行符）放不进当前块 → 先封块
        candidate = f"{current}\n{line}" if current else line
        if len(candidate.encode("utf-8")) > limit:
            if current:
                chunks.append(current)
                current = ""
            # 单行超限：硬截成 limit 内的段（按字符累积到字节边界）
            while len(line.encode("utf-8")) > limit:
                piece = ""
                for ch in line:
                    if len((piece + ch).encode("utf-8")) > limit:
                        break
                    piece += ch
                chunks.append(piece)
                line = line[len(piece):]
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _post_once(client: httpx.AsyncClient, url: str, payload: dict) -> tuple:
    """单次 POST 企微 webhook 并校验 errcode。

    返回 (ok, err)：ok=False 时 err 为用户可读错误文案（不含 webhook 密钥）。
    """
    resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        return False, f"企微接口返回 HTTP {resp.status_code}"
    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001 响应体非 JSON（网关劫持等）
        return False, f"企微接口响应解析失败: {type(e).__name__}"
    if data.get("errcode") != 0:
        return False, f"企微接口错误 errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
    return True, None


async def _post_with_retry(url: str, payload: dict) -> tuple:
    """带 1 次重试的 POST：网络异常 / HTTP 非 200 / errcode!=0 都触发重试。"""
    import asyncio

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        last_err = "未知错误"
        for attempt in range(1 + _RETRY_COUNT):
            try:
                ok, err = await _post_once(client, url, payload)
                if ok:
                    return True, None
                last_err = err
                logger.warning(f"企微机器人发送失败（第 {attempt} 次）: {err}")
            except Exception as e:  # noqa: BLE001 网络异常等，重试后仍失败则返回错误
                last_err = f"网络异常: {type(e).__name__}"
                logger.warning(f"企微机器人发送异常（第 {attempt} 次）: {type(e).__name__}: {e}")
            if attempt < _RETRY_COUNT:
                await asyncio.sleep(1)
        return False, last_err


async def send_markdown(url: str, content: str) -> tuple:
    """发 markdown 消息到企微群机器人。

    content 超 2040 字节时按行边界分多条发送，任一条失败即整体失败。
    返回 (ok, err)：err 为 None 或用户可读错误文案；绝不外抛异常。
    """
    if not url or not content:
        return False, "webhook 或消息内容为空"

    chunks = _split_markdown(content)
    if not chunks:  # 纯空白内容切块后为空：视为无效，不发假成功
        return False, "消息内容为空"
    for chunk in chunks:
        ok, err = await _post_with_retry(url, {"msgtype": "markdown", "markdown": {"content": chunk}})
        if not ok:
            return False, err
    return True, None


async def send_text(url: str, content: str, at_mobiles=None) -> tuple:
    """发 text 消息到企微群机器人（用于 @ 手机号，markdown 不支持 @）。

    at_mobiles 非空时放 text.mentioned_mobile_list 实现群内 @（企微按手机号匹配
    群成员；mentioned_list 只接受 userid，手机号放那里不会 @）。
    返回 (ok, err)，绝不外抛异常。
    """
    if not url or not content:
        return False, "webhook 或消息内容为空"

    payload = {
        "msgtype": "text",
        "text": {"content": content, "mentioned_mobile_list": list(at_mobiles or [])},
    }
    return await _post_with_retry(url, payload)
