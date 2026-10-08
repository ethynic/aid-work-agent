"""Finite preparation composition and neutral local input projections."""

import asyncio
import base64
from .source_receipts import SourceLocator, SourceUnavailable, source_offload, LocalPreparationFailed


def project_ref_in_tx(cursor, runner, input_ref, projector):
    cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s', (input_ref,))
    fact=cursor.fetchone()
    if not fact or runner['runner_id'] not in {fact['current_runner_id'],fact['accepted_runner_id']}:
        raise SourceUnavailable('SOURCE_PREPARATION_INPUT_CHANGED')
    if runner['source']!=fact['source']:
        raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
    cursor.execute('SELECT provenance FROM agent_runner_inputs WHERE input_ref=%s',
                   ((runner.get('checkpoint') or {}).get('source_initial_ref'),))
    original=cursor.fetchone()
    if not original or original['provenance'].get('execution_binding')!=fact['provenance'].get('execution_binding'):
        raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
    if any(runner[k]!=fact['provenance'][p] for k,p in (
            ('tenant_id','tenant_id'),('session_id','session_id'),('profile_id','profile_id'),('user_id','user_id'))):
        raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
    return projector(cursor,fact)


async def drain_owned(task):
    """Detach/cancel waits for the actual operation, including its SQL result."""
    cancelled=False
    while not task.done():
        try: await asyncio.shield(task)
        except asyncio.CancelledError: cancelled=True
        except BaseException: break
    if cancelled:
        if not task.cancelled(): task.exception()
        raise asyncio.CancelledError
    return task.result()


class SourcePreparation:
    def __init__(self,provider,repository,media):
        self.provider,self.repository,self.media=provider,repository,media
        self.tasks=set()

    async def assert_recoverable(self,row):
        # A dispatched call is never repeated. The old chat experience continues
        # with a placeholder; its unresolved receipt remains with the same owner.
        candidates=await source_offload(self.repository.candidates,row['runner_id'])
        for candidate in candidates:
            if candidate.existing and candidate.existing['phase'] in {'started','unknown'}:
                await source_offload(self.repository.choose_placeholder,candidate.fact)

    async def prepare_inputs(self,row,attempt,dispatch_check):
        if not (row.get('checkpoint') or {}).get('source_initial_ref'): return
        candidates=await source_offload(self.repository.candidates,attempt.runner_id)
        state=(row.get('checkpoint') or {}).get('execution')
        if (isinstance(state,dict) and type(state.get('iteration')) is int
                and type(state.get('max_iterations')) is int and state['iteration']>=state['max_iterations']):
            # The last permitted model already ran. Unstarted voice remains an
            # unused stable input for the original atomic budget handoff.
            candidates=[candidate for candidate in candidates
                        if candidate.existing and candidate.existing['phase'] in {'known','started','unknown'}]
        for candidate in candidates:
            fact,recognition,prep=candidate.fact,candidate.recognition,candidate.existing
            if prep and prep['phase']=='known': continue
            if prep and prep['phase'] in {'started','unknown'}:
                await source_offload(self.repository.choose_placeholder,fact)
                continue
            # A strong task holds the HTTP/codec and all original SQL operations.
            task=asyncio.create_task(self._prepare_one(row,attempt,fact,recognition,prep,dispatch_check))
            self.tasks.add(task)
            try: await drain_owned(task)
            finally: self.tasks.discard(task)
        return bool(candidates)

    async def _prepare_one(self,row,attempt,fact,recognition,prep,dispatch_check):
        from src.tools.asr.call_observer import asr_call_observer,AsrCallUnknown
        from src.tools.asr.speech_to_text_tool import SpeechToTextTool
        from src.services.agent_runner.contracts import RunnerError
        from .ownership import StopRequested
        locator=SourceLocator(**fact['locator'])
        prepared=await self.provider.prepare_accepted(locator,row['service_id'])
        if prep is None:
            if isinstance(recognition,str) and recognition.strip():
                await source_offload(self.repository.remember,attempt,fact,prepared,recognition=recognition)
                return
            artifact=None
            try:
                artifact=await self.media.download(fact,self.repository.preparation_id(fact['input_ref']))
            except LocalPreparationFailed:
                # An unstarted media failure is a known zero-paid-call result.
                artifact=None
            prepared=await self.provider.prepare_accepted(locator,row['service_id'])
            prep=await source_offload(self.repository.remember,attempt,fact,prepared,
                                      artifact=artifact,failed=artifact is None)
            if prep['phase']=='known': return
        artifact=prep['artifact']
        data=await source_offload(self.media.read,row['tenant_id'],artifact)
        # Prices are read before the root transaction, never on its cursor.
        price=await source_offload(self.repository.usage.price_resolver,'asr','aliyun-nls-asr','main')
        prepared=await self.provider.prepare_accepted(locator,row['service_id'])
        owner=self
        class Observer:
            started=False
            observation_error=None
            async def before_post(self,**metadata):
                if (metadata['audio_size']!=len(data) or metadata['audio_format']!=artifact['audio_format']
                        or metadata['sample_rate']!=artifact['audio_sample_rate']):
                    raise SourceUnavailable('SOURCE_MEDIA_BINDING_CHANGED')
                await dispatch_check()
                current_prepared=await owner.provider.prepare_accepted(locator,row['service_id'])
                await source_offload(owner.repository.start,attempt,fact,current_prepared,artifact,price)
                self.started=True
            async def known(self,**result):
                try:
                    await source_offload(owner.repository.result,fact['input_ref'],**result)
                except BaseException as error:
                    self.observation_error=error
                    raise
            async def unknown(self,code):
                if self.observation_error is not None:
                    code='SOURCE_PREPARATION_OBSERVATION_FAILED'
                await source_offload(owner.repository.uncertain,fact['input_ref'],code)
        observer=Observer()
        try:
            with asr_call_observer(observer):
                result=await SpeechToTextTool().execute(audio_content=base64.b64encode(data).decode('ascii'),
                    format=artifact['audio_format'],sample_rate=artifact['audio_sample_rate'])
            if not observer.started:
                await source_offload(self.repository.preflight_failed,fact['input_ref'])
            elif not isinstance(result,dict):
                raise SourceUnavailable('SOURCE_PREPARATION_RESULT_UNKNOWN')
        except StopRequested as error:
            if error.command == 'cancel' and not observer.started:
                await source_offload(self.repository.preflight_failed,fact['input_ref'])
            raise
        except AsrCallUnknown as error:
            if observer.observation_error is not None:
                raise SourceUnavailable('SOURCE_PREPARATION_OBSERVATION_FAILED') from observer.observation_error
            await source_offload(self.repository.choose_placeholder,fact)
        except RunnerError as error:
            raise SourceUnavailable('SOURCE_PREPARATION_PERMISSION_CHANGED') from error

    async def close(self):
        for task in tuple(self.tasks): await drain_owned(task)
