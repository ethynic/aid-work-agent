"""渠道用户信息工具包：透出渠道侧客户信息给 Agent（channel 无关，供各渠道复用）。"""

from .channel_user_info import GetChannelUserInfoTool

__all__ = ["GetChannelUserInfoTool"]
