from collections.abc import Callable

from src.social_media.connectors.base import SocialPlatformConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], SocialPlatformConnector]] = {}

    def register(self, platform: str, factory: Callable[[], SocialPlatformConnector]) -> None:
        self._factories[platform] = factory

    def create(self, platform: str) -> SocialPlatformConnector:
        if platform not in self._factories:
            raise KeyError(f"unsupported_platform:{platform}")
        return self._factories[platform]()

    def platforms(self) -> list[str]:
        return sorted(self._factories)


connector_registry = ConnectorRegistry()

