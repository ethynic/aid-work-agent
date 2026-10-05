"""Only Redis ticket-write transport fails; all auth/state stays original."""
import redis

original_set = redis.Redis.set


def set_with_ticket_fault(self, name, *args, **kwargs):
    if isinstance(name, str) and ':browser_view_ticket:' in name:
        raise redis.ConnectionError('Fixture ticket storage unavailable')
    return original_set(self, name, *args, **kwargs)


def create_app():
    from .browser_observation_api import create_app as original_app
    redis.Redis.set = set_with_ticket_fault
    return original_app()
