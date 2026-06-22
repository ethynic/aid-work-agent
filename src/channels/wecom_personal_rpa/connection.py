"""企业微信个人账号 RPA 客户端 WebSocket 连接注册表

管理在线客户端的 WebSocket 连接，供 action_client 在线投递 ActionEnvelope。
离线客户端由 db.enqueue_action 写入 outbox，由客户端拉取执行。

签名契约：docs/system/wecom-personal-rpa-protocol.md §B.6（一经锁定不得变更）。

设计说明
--------
注册表为进程内内存（``dict[client_id, ws]``）。

**多 Gunicorn worker 隔离限制（首版已知，符合 backend_dev.md 多 worker 规范）**：
Gunicorn 启动多 worker 时每个进程拥有独立内存空间，本注册表仅在当前 worker
内可见。若客户端 WebSocket 连接到 worker A，而 agent 回复在 worker B 触发，
worker B 会看到客户端离线并把 action 写入 outbox，客户端需要通过 outbox
拉取才能收到——这在首版是可以接受的降级路径（action_client 优先尝试注册表，
失败则落库）。

后续如需真正的跨 worker 在线推送，需引入 Redis pub/sub 广播：
每个 worker subscribe 自己的 channel，注册表收到 action 时 publish 到目标
client_id 所在 worker。本次不实现，留 TODO。
"""

import asyncio
import json
from typing import Any, Dict, List

from loguru import logger


class ClientConnectionRegistry:
    """模块级单例，管理在线客户端 WebSocket 连接。

    所有方法对内部 dict 的读写都通过 ``asyncio.Lock`` 串行化，避免并发
    register/unregister/send 之间的竞态。``is_online`` / ``list_online``
    为纯读操作，由于 asyncio 单线程事件循环下 dict 读写本身原子，亦走锁
    以保证语义一致并屏蔽未来实现变更带来的隐患。
    """

    def __init__(self) -> None:
        # client_id -> WebSocket 实例（Starlette/FastAPI WebSocket 或等价 duck-type 对象，
        # 需实现 async send_text(self, data: str) -> None）
        self._connections: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def register(self, client_id: str, ws: Any) -> None:
        """注册（或覆盖）客户端连接。

        若同一 client_id 已有旧连接，覆盖之并记录 warning——客户端重连或
        多实例误用同一 client_id 时会发生，旧连接在覆盖后由调用方负责关闭。
        """
        async with self._lock:
            existing = self._connections.get(client_id)
            if existing is not None and existing is not ws:
                logger.warning(
                    f"RPA 连接注册表：client_id={client_id} 已存在连接，覆盖旧连接"
                )
            self._connections[client_id] = ws
            logger.info(f"RPA 连接注册表：客户端上线 client_id={client_id}")

    async def unregister(self, client_id: str) -> None:
        """移除客户端连接。不存在的 client_id 静默忽略（幂等）。"""
        async with self._lock:
            removed = self._connections.pop(client_id, None)
            if removed is not None:
                logger.info(f"RPA 连接注册表：客户端下线 client_id={client_id}")

    def is_online(self, client_id: str) -> bool:
        """客户端是否在线（当前 worker 内可见）。"""
        return client_id in self._connections

    async def send(self, client_id: str, payload: Dict[str, Any]) -> bool:
        """向在线客户端推送 payload。

        - 在线且发送成功：返回 True。
        - 离线：返回 False（调用方据此写 outbox，见 action_client.deliver_actions）。
        - 发送过程中任何异常（连接已关闭 / 对端 reset / 序列化失败）：
          视为该连接已失效，unregister 后返回 False，避免悬挂连接。
        """
        # 先在锁外读取 ws 引用（dict.get 在单线程事件循环下原子），
        # 避免在持有锁期间 await（会阻塞所有其它 register/unregister/send）。
        ws = self._connections.get(client_id)
        if ws is None:
            return False

        try:
            # ensure_ascii=False 保持中文/emoji 原样输出，与协议 schemas 的
            # JSON 字段名一一对齐（见 protocol.md §C.3）。
            text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.error(
                f"RPA 连接注册表：序列化 payload 失败 client_id={client_id}: {e}"
            )
            await self.unregister(client_id)
            return False

        try:
            await ws.send_text(text)
            return True
        except Exception as e:
            # 连接已关闭 / 对端异常 / 超时等——统一视为失效。
            logger.warning(
                f"RPA 连接注册表：发送失败，移除连接 client_id={client_id}: {e}"
            )
            await self.unregister(client_id)
            return False

    def list_online(self, client_ids: List[str]) -> List[str]:
        """从给定 client_ids 中筛出当前在线的子集（保持入参顺序）。"""
        return [cid for cid in client_ids if cid in self._connections]


# 模块级单例。下游（action_client / router / WebSocket 处理函数）统一 import 此实例。
client_connection_registry = ClientConnectionRegistry()
