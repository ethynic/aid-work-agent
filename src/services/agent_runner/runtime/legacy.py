"""Compatibility storage policy; new runner execution does not use this module."""

from .history_repository import HistoryRepository


class LegacyHistoryReader(HistoryRepository):
    def assert_authorized(self):
        # Existing ingress already owns authorization and may use ephemeral
        # CLI sessions. New service never uses this compatibility policy.
        pass

    def read_web(self, session_id, limit=100):
        from src.db.models import MessageDB
        return MessageDB.list_by_session(session_id, limit=limit)

    def active_summary(self, session_id, source_type):
        try:
            return super().active_summary(session_id, source_type)
        except Exception:
            return None
