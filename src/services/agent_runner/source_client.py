"""Fixed internal source-admission HTTP client. No storage or execution owner."""

import os
import json
import httpx
from .contracts import RunnerError


class SourceClient:
    def __init__(self, config, token=None):
        self.config = config
        self.token = token if token is not None else os.environ.get('AGENT_RUNNER_WECOM_KF_SERVICE_TOKEN','')

    async def accept(self, locator):
        if not self.token:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        headers={'X-AgentRunner-Service':self.config.wecom_kf.service_id,
                 'X-AgentRunner-Service-Token':self.token}
        body={**locator.value(),'client_request_id':locator.stable_key}
        try:
            async with httpx.AsyncClient(base_url=self.config.api_url.rstrip('/'),timeout=45,
                    follow_redirects=False,trust_env=False) as client:
                async with client.stream('POST','/v1/source-inputs',headers=headers,json=body) as response:
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data)+len(chunk)>16384:
                            raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
                        data.extend(chunk)
                    try: value=json.loads(data)
                    except (ValueError,UnicodeError): raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502) from None
                    if not response.is_success:
                        raise RunnerError('SOURCE_INPUT_NOT_ACCEPTED',response.status_code if response.status_code>=400 else 502)
            if (not isinstance(value,dict) or value.get('success') is not True
                    or type(value.get('created')) is not bool
                    or any(not isinstance(value.get(k),str) or not value[k] or len(value[k])>128
                           for k in ('input_ref','accepted_runner_id','current_runner_id','disposition'))):
                raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
            return value
        except httpx.HTTPError:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503) from None

    async def read(self,locator,*,presentation=False):
        """Fixed, bounded observation transport, never an execution permit."""
        if not self.token: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        headers={'X-AgentRunner-Service':self.config.wecom_kf.service_id,
                 'X-AgentRunner-Service-Token':self.token}
        try:
            async with httpx.AsyncClient(base_url=self.config.api_url.rstrip('/'),
                    timeout=httpx.Timeout(5,connect=3),follow_redirects=False,trust_env=False) as client:
                async with client.stream('GET','/v1/source-presentations' if presentation else '/v1/source-inputs',headers=headers,params=locator.value()) as response:
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data)+len(chunk)>(2097152 if presentation else 16384): raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
                        data.extend(chunk)
                    try: value=json.loads(data)
                    except (ValueError,UnicodeError): raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502) from None
                    if not response.is_success: raise RunnerError('SOURCE_OBSERVATION_UNAVAILABLE',response.status_code)
            from datetime import datetime
            state=value.get('runner') if isinstance(value,dict) else None
            if (not isinstance(value,dict) or value.get('success') is not True
                    or value.get('locator')!=locator.value() or value.get('client_request_id')!=locator.stable_key
                    or any(not isinstance(value.get(k),str) or not value[k] or len(value[k])>128
                        for k in ('input_ref','accepted_runner_id','current_runner_id','disposition'))
                    or not isinstance(state,dict) or state.get('runner_id')!=value['current_runner_id']
                    or state.get('status') not in {'queued','running','waiting','paused','interrupted','finalizing','completed','failed','cancelled'}
                    or state.get('settlement_status') not in {'pending','settled'}
                    or type(state.get('resume_requested')) is not bool
                    or any(type(state.get(k)) is not int or not 0<=state[k]<=2**63-1
                        for k in ('revision','view_revision','control_revision'))
                    or not isinstance(state.get('profile_id'),str) or not state['profile_id'] or len(state['profile_id'])>128
                    or not isinstance(state.get('session'),dict) or state['session'].get('kind')!='channel'
                    or not isinstance(state['session'].get('session_id'),str) or not state['session']['session_id']
                    or len(state['session']['session_id'])>256):
                raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
            try: observed=datetime.fromisoformat(value['observed_at'])
            except (ValueError,TypeError,KeyError): raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502) from None
            if observed.tzinfo is None: raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
            return value
        except httpx.HTTPError:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503) from None

    async def accept_batch(self, batch):
        if not self.token:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE', 503)
        headers = {'X-AgentRunner-Service': self.config.wecom_kf.service_id,
                   'X-AgentRunner-Service-Token': self.token}
        try:
            async with httpx.AsyncClient(base_url=self.config.api_url.rstrip('/'), timeout=45,
                    follow_redirects=False, trust_env=False) as client:
                async with client.stream('POST', '/v1/source-input-batches', headers=headers,
                        json={'members': batch.value(), 'client_request_id': batch.stable_key}) as response:
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 65536:
                            raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE', 502)
                        data.extend(chunk)
                    try:
                        value = json.loads(data)
                    except (ValueError, UnicodeError):
                        raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE', 502) from None
                    if not response.is_success:
                        raise RunnerError('SOURCE_INPUT_NOT_ACCEPTED', response.status_code)
            if (not isinstance(value, dict) or value.get('success') is not True
                    or type(value.get('created')) is not bool or value.get('batch_ref') != batch.stable_key
                    or not isinstance(value.get('current_runner_id'), str)
                    or not 1 <= len(value['current_runner_id']) <= 128
                    or not isinstance(value.get('members'), list) or len(value['members']) != len(batch.members)):
                raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE', 502)
            for member, result in zip(batch.members, value['members']):
                if (not isinstance(result, dict) or result.get('success') is not True
                        or result.get('input_ref') != member.stable_key
                        or result.get('current_runner_id') != value['current_runner_id']
                        or type(result.get('created')) is not bool
                        or any(not isinstance(result.get(key), str) or not 1 <= len(result[key]) <= 128
                               for key in ('accepted_runner_id', 'disposition'))):
                    raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE', 502)
            return value
        except httpx.HTTPError:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE', 503) from None

    async def finish_delivery(self,locator,delivery_id):
        if not self.token: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        if not isinstance(delivery_id,str) or len(delivery_id)!=76 or not delivery_id.startswith('kf_delivery_'):
            raise RunnerError('SOURCE_DELIVERY_INVALID',422)
        headers={'X-AgentRunner-Service':self.config.wecom_kf.service_id,'X-AgentRunner-Service-Token':self.token}
        try:
            async with httpx.AsyncClient(base_url=self.config.api_url.rstrip('/'),timeout=10,trust_env=False,follow_redirects=False) as client:
                async with client.stream('POST','/v1/source-deliveries/'+delivery_id+'/finish',headers=headers,json=locator.value()) as response:
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data)+len(chunk)>16384: raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
                        data.extend(chunk)
                    try: value=json.loads(data)
                    except (ValueError,UnicodeError): raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502) from None
                    if not response.is_success: raise RunnerError('SOURCE_DELIVERY_NOT_CLOSED',response.status_code)
            if (not isinstance(value,dict) or value.get('success') is not True or value.get('delivery_id')!=delivery_id
                    or value.get('outcome') not in {'closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown'}):
                raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE',502)
            return value
        except httpx.HTTPError: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503) from None

    async def close(self):
        # Every request owns and closes its actual HTTP context.
        return None
