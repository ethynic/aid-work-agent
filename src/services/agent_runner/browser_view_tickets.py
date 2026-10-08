"""Single-use native view tickets; private token references never enter CP."""

import hashlib
import json
import secrets

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from .browser_web_auth import BrowserViewAssertion
from .contracts import RunnerError


def issue_view_ticket(assertion):
    assertion = BrowserViewAssertion.model_validate(assertion)
    if not redis_client.is_available():
        raise RunnerError('WEB_PRESENCE_REQUIRED',503)
    jti, secret = secrets.token_hex(16), secrets.token_urlsafe(32)
    key = redis_client.make_key(CacheKeys.BROWSER_VIEW_TICKET,jti)
    value = dict(version=1, native=True, assertion=assertion.model_dump(),
                 ticket_hash=hashlib.sha256(secret.encode()).hexdigest())
    # The original generic set can swallow errors/fall back to memory. This
    # security-critical ticket uses the same real client as Browser leases.
    client = redis_client._client
    if client is None:
        raise RunnerError('WEB_PRESENCE_REQUIRED',503)
    try:
        stored = client.set(key,json.dumps(value),ex=60,nx=True)
    except Exception:
        raise RunnerError('WEB_PRESENCE_REQUIRED',503) from None
    if not stored:
        raise RunnerError('WEB_PRESENCE_REQUIRED',503)
    return dict(ticket=f'{jti}.{secret}',expires_in=60)


def consume_view_ticket(run_id, ticket):
    try:
        jti, secret = ticket.split('.',1)
        if (len(jti) != 32 or any(c not in '0123456789abcdef' for c in jti)
                or not 32 <= len(secret) <= 64):
            raise ValueError()
        key = redis_client.make_key(CacheKeys.BROWSER_VIEW_TICKET,jti)
        value = redis_client.getdel(key)
        if (not isinstance(value,dict) or value.get('native') is not True or value.get('version') != 1
                or not secrets.compare_digest(value.get('ticket_hash',''),hashlib.sha256(secret.encode()).hexdigest())):
            raise ValueError()
        assertion = BrowserViewAssertion.model_validate(value['assertion'])
        if assertion.version != 1 or assertion.run_id != run_id:
            raise ValueError()
        return assertion
    except (ValueError,TypeError,KeyError,AttributeError):
        raise RunnerError('BROWSER_VIEW_TICKET_INVALID',403) from None
