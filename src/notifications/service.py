"""Composition root; legacy NotificationService callers retain their old contract."""
from .contracts import AdapterRegistry
from .repository import NotificationRepository
from .web_push import WebPushAdapter


def notification_repository(config=None):
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    return NotificationRepository(config, AdapterRegistry([WebPushAdapter(config.web_push)]))
