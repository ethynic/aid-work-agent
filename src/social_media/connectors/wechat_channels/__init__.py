from src.social_media.connectors.registry import connector_registry
from src.social_media.connectors.wechat_channels.connector import WeChatChannelsConnector

connector_registry.register("wechat_channels", WeChatChannelsConnector)

