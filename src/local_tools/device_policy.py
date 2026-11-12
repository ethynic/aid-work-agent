"""The existing selected/online/catalog gate, reusable after a DB lock wait."""

from . import catalog

ONLINE_THRESHOLD_SECONDS = 30


def device_ready_error(device, tool_name, now, *, require_selected=True):
    if (require_selected and not device.get('selected')) or device.get('status') != 'active':
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
    declared = catalog.declared_tools_for_device(device.get('capabilities_json'))
    for provider in providers:
        if declared is not None and provider in declared:
            # 声明即调度授权：声明覆盖的 provider 以 declared_tools 判定覆盖
            # （新版本插件声明的工具不被静态目录闸门挡住；未声明工具不放行）
            if tool_name in declared[provider]:
                return None
        elif catalog.is_tool_allowed(provider, tool_name):
            return None
    return 'unsupported'
