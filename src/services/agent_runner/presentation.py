"""User-visible tool facts, distinct from private execution messages/results."""

from src.core.agent_events import mask_tool_args


PLUGIN_TOOL_NAMES = 'runtime_plugin_tool_names'


def redact_runtime_plugin_calls(value, plugin_names):
    """Project model messages without values from arbitrary plugin-defined inputs."""
    if not plugin_names or not isinstance(value, dict) or not isinstance(value.get('tool_calls'), list):
        return value
    projected = []
    for call in value['tool_calls']:
        function = call.get('function') if isinstance(call, dict) else None
        if isinstance(function, dict) and function.get('name') in plugin_names and 'arguments' in function:
            arguments = '{}' if isinstance(function['arguments'], str) else {}
            projected.append({**call, 'function': {**function, 'arguments': arguments}})
        else:
            projected.append(call)
    return {**value, 'tool_calls': projected}


def redact_runtime_plugin_event(state, event):
    names = state.resources.get(PLUGIN_TOOL_NAMES, ())
    if not names:
        return
    if event.get('type') == 'tool_start' and event.get('toolName') in names:
        event['toolArgs'] = {}
    elif event.get('type') in {'llm_call', 'tool_messages'}:
        if 'messages' in event:
            event['messages'] = [redact_runtime_plugin_calls(message, names) for message in event['messages']]
        if 'response' in event:
            event['response'] = redact_runtime_plugin_calls(event['response'], names)


def scalar_fields(value, keys):
    if not isinstance(value, dict):
        return {}
    return {key:value[key] for key in keys if key in value
            and (value[key] is None or isinstance(value[key], (str,int,float,bool)))}


def option_items(value):
    if not isinstance(value, list):
        return []
    return [scalar_fields(item, ('key','label','description')) for item in value
            if isinstance(item, dict) and isinstance(item.get('key'),str) and item['key']
            and isinstance(item.get('label'),str) and item['label']]


def tool_result_display(value):
    result = scalar_fields(value, ('success','error','message','summary','file_id','file_name',
                                  'download_file_name','file_size','download_url','mime_type','visible',
                                  'file_count'))
    if isinstance(value, dict) and isinstance(value.get('files'), list):
        # 批量交付（cp source_file_paths）：每个文件的下载卡片字段逐项透传
        result['files'] = [scalar_fields(item, ('file_id','file_name','download_file_name','file_size',
                                                'download_url','mime_type','visible'))
                           for item in value['files']
                           if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get('data'), dict):
        options = option_items(value['data'].get('options'))
        if len(options) >= 2:
            result['data'] = {'options':options}
    return result


def progress_display(value):
    result = scalar_fields(value, ('type','data','content','toolName','displayName','toolCallId','success','timestamp'))
    if isinstance(value, dict):
        if isinstance(value.get('toolArgs'), (dict,list)):
            result['toolArgs'] = mask_tool_args(value['toolArgs'])
        if 'result' in value:
            result['result'] = tool_result_display(value['result'])
    return result


def project_user_event(snapshot, event):
    if event.get('type') == 'images':
        snapshot['imagesPlacement'] = event.get('placement','after_text')
