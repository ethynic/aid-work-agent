"""Trusted, exact worker-side Browser endpoint configuration."""

from urllib.parse import urlsplit


BROWSER_OWNER_PATH = '/internal/runner-browser'


def validate_browser_endpoint(endpoint):
    if not isinstance(endpoint, str) or not endpoint or any(char in endpoint for char in ('\\', '%')):
        raise ValueError('BROWSER_OWNER_ENDPOINT_INVALID')
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError('BROWSER_OWNER_ENDPOINT_INVALID') from error
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or '?' in endpoint or '#' in endpoint
            or parsed.path != BROWSER_OWNER_PATH or port is None or not 1 <= port <= 65535
            or not parsed.netloc.isascii() or any(char.isspace() for char in endpoint)):
        raise ValueError('BROWSER_OWNER_ENDPOINT_INVALID')
    return endpoint


def trusted_browser_endpoint(endpoint, configured_endpoints):
    validated = validate_browser_endpoint(endpoint)
    allowed = tuple(validate_browser_endpoint(value) for value in configured_endpoints)
    if validated not in allowed:
        raise ValueError('BROWSER_OWNER_ENDPOINT_FORBIDDEN')
    return validated


def browser_view_endpoint(endpoint, configured_endpoints):
    """Derive the one server-owned WS path, never a DB/caller-selected leaf."""
    validated = trusted_browser_endpoint(endpoint,configured_endpoints)
    scheme, rest = validated.split('://',1)
    return ('wss' if scheme == 'https' else 'ws')+'://'+rest+'/view'
