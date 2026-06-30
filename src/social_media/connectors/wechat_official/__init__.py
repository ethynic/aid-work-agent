from src.social_media.connectors.registry import connector_registry
from src.social_media.connectors.wechat_official.connector import WeChatOfficialConnector

connector_registry.register("wechat_official", WeChatOfficialConnector)

