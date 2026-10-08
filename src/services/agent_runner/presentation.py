"""User-visible tool facts, distinct from private execution messages/results."""

import copy

from src.core.agent_events import mask_tool_args


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
                                  'download_file_name','file_size','download_url','mime_type','visible'))
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


def browser_display(value):
    result = scalar_fields(value, ('assistance_id','run_id','continuation_id','reason_code','surface',
                                  'title','completion_mode','completion_status','expires_at','state'))
    if isinstance(value, dict) and type(value.get('view_available')) is bool:
        result['view_available'] = value['view_available']
    if isinstance(value, dict):
        for key in ('steps','missing_conditions'):
            if isinstance(value.get(key),list):
                result[key] = [item for item in value[key] if isinstance(item,str)]
    return result


def project_user_event(snapshot, event):
    if event.get('type') == 'browser_human_required':
        snapshot['browserAssistance'] = browser_display(event)
    elif event.get('type') == 'images':
        snapshot['imagesPlacement'] = event.get('placement','after_text')
    elif event.get('type') == 'tool_result' and event.get('success'):
        result = tool_result_display(event.get('result'))
        options = (result.get('data') or {}).get('options')
        if options:
            snapshot['quickOptions'] = copy.deepcopy(options)
