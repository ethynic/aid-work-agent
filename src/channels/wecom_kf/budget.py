#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信客服 owner 级回复预算（Phase 3，设计 §9.3 降级规则 1–5）

微信客服平台限制：单次咨询最多 5 次回复。verbose 与 final 必须共享同一
owner 级预算（WeComKfReplyBudget(total=5)），保证「提示是可丢的，最终正文
不可被提示挤掉」：

1. verbose 发送前必须为 final 预留至少 1 次平台回复；成功扣 1（5→4），
   失败/抑制不扣减；
2. final 正文优先且至少保留 1 次发送（预留由 verbose 侧校验保证）；
3. 正文超长/含表格优先合成长图（1 次），失败则单条截断纯文本，不得无预算
   地自动分段；
4. 剩余额度按序发图片/文件，超预算资产不发送并记录 suppressed_reply_budget；
5. 无法保证 final 至少一次正文投递时不发 verbose（adapter send_status_message
   侧实现）。

并发模型：事件循环单线程内调用，且 can_reserve/consume 均为不含 await 的同步
方法，天然原子。预算对象由路由层按 owner（每条入站消息）创建，经
make_send_response / make_send_verbose 注入 UnifiedResponse.content 私有键
``_kf_reply_budget`` 传递——adapter 实例按 (tenant, channel, config) 缓存复用，
预算绝不能挂在 adapter 实例上。
"""

from typing import Any, Dict, List

from loguru import logger


class WeComKfReplyBudget:
    """owner 生命周期共享的平台回复预算（默认 total=5）。

    语义：
    - ``can_reserve(n)``：校验剩余额度是否满足「本次发送 + 预留」需求，不扣减；
    - ``consume(n)``：发送成功后实际扣减（失败/抑制不扣，调用方先发送后扣）；
    - ``record_suppressed``：超预算资产可观测记录（日志 + suppressed_assets 列表）。
    """

    def __init__(self, total: int = 5):
        if int(total) < 1:
            raise ValueError(f"reply budget total 必须 >= 1，实际: {total}")
        self.total = int(total)
        self._remaining = int(total)
        self.suppressed_assets: List[Dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        return self._remaining

    def can_reserve(self, count: int = 1) -> bool:
        """原子校验：剩余额度是否 >= count（不扣减）。"""
        return self._remaining >= max(0, int(count))

    def consume(self, count: int = 1) -> bool:
        """扣减 count 次额度（同步无 await，事件循环内原子）；不足时返回 False 不扣。"""
        count = max(0, int(count))
        if self._remaining >= count:
            self._remaining -= count
            return True
        return False

    def record_suppressed(
        self,
        kind: str,
        file_id: str = "",
        file_name: str = "",
        reason: str = "suppressed_reply_budget",
    ) -> None:
        """记录因预算耗尽被抑制的资产（可观测：日志 + 列表，设计 §9.3 规则 4）。"""
        entry = {
            "kind": kind,
            "file_id": file_id,
            "file_name": file_name,
            "reason": reason,
        }
        self.suppressed_assets.append(entry)
        logger.warning(
            f"[wecom_kf] {reason}: kind={kind}, file_id={file_id or '-'}, "
            f"file_name={file_name or '-'}, remaining={self._remaining}/{self.total}"
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"WeComKfReplyBudget(total={self.total}, remaining={self._remaining})"
