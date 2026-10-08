"""The existing selected/online/catalog gate, reusable after a DB lock wait."""

from . import catalog

ONLINE_THRESHOLD_SECONDS = 30


def device_ready_error(device, tool_name, now):
    if not device.get('selected') or device.get('status') != 'active':
        return 'not_selected'
    last_seen = device.get('last_seen_at')
    if not last_seen:
        return 'offline'
    # Preserve legacy naive local-time comparisons; PG TIMESTAMPTZ is aware.
    if last_seen.tzinfo is None:
        now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    elif now.tzinfo is None:
        now = now.astimezone()
    if (now - last_seen).total_seconds() > ONLINE_THRESHOLD_SECONDS:
        return 'offline'
    providers = catalog.get_provider_keys_for_device(device.get('capabilities_json'))
    if not any(catalog.is_tool_allowed(provider, tool_name) for provider in providers):
        return 'unsupported'
    return None
