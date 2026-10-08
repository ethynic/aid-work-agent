"""Fresh request authorization precedes control idempotency and acceptance."""

from .contracts import RunnerError
from .control_contracts import public_control
from .control_repository import ControlRepository
from .repository import public_runner
from .source_receipts import SourceUnavailable, source_offload


class ControlApplication:
    PUBLIC_ACTIONS = frozenset({'pause','resume','reply'})

    def __init__(self, manager, controls=None):
        self.manager = manager
        self.controls = controls or ControlRepository(manager.repository.connection_factory)

    def _find(self, runner_id, request, credentials):
        if request.action not in self.PUBLIC_ACTIONS:
            raise RunnerError('CONTROL_COMPLETION_FORBIDDEN',403)
        self.manager.authorizer.verify_service(credentials['service_id'],credentials['service_token'],None)
        row = self.manager.repository.get(runner_id)
        principal = self.manager._authorize_row(row,credentials)
        previous = self.controls.find_request(principal,runner_id,request)
        return row,principal,previous

    def _existing(self, runner_id, previous):
        return public_control(previous),False,public_runner(self.manager.repository.get(runner_id))

    async def submit_async(self, runner_id, request, credentials):
        row,_,previous=await source_offload(self._find,runner_id,request,credentials)
        if previous is not None:
            return await source_offload(self._existing,runner_id,previous)
        prepared=None
        preparation_error=None
        if (request.action in {'resume','reply'}
                and (row.get('checkpoint') or {}).get('source_initial_ref')
                and self.manager.authorizer.source_port is not None):
            # The port owns actual asynchronous source IO outside every SQL
            # transaction. No caller/request boolean is a source permission.
            try:
                prepared=await self.manager.authorizer.source_port.prepare_execution(row)
            except Exception as error:
                preparation_error=error
        # SDK IO may have raced another caller's acceptance. Fresh read rights
        # and same-key lookup precede new execution/credit checks again.
        return await source_offload(self.submit,runner_id,request,credentials,
                                    prepared_source=prepared,preparation_error=preparation_error)

    def submit(self, runner_id, request, credentials, *, prepared_source=None,
               preparation_error=None):
        """Synchronous callers retain the original non-native control path."""
        row,principal,previous=self._find(runner_id,request,credentials)
        if previous is not None:
            return self._existing(runner_id,previous)
        if preparation_error is not None:
            if isinstance(preparation_error,SourceUnavailable) and isinstance(preparation_error.__cause__,RunnerError):
                cause=preparation_error.__cause__
                raise RunnerError(cause.code,cause.status) from None
            raise preparation_error
        if request.action in {'resume','reply'}:
            # The original service/actor binding and actual resolved profile are
            # revalidated; an expired original bearer is not stored or required.
            kwargs={'prepared_source':prepared_source} if prepared_source is not None else {}
            self.manager.authorizer.authorize_persisted(row,**kwargs)
            self.manager.authorizer.assert_credit(principal)
        kwargs={'expected_revision':row['revision']} if prepared_source is not None else {}
        control, created = self.controls.submit(principal,runner_id,request,**kwargs)
        return public_control(control),created,public_runner(self.manager.repository.get(runner_id))

    def get(self, runner_id, control_id, credentials):
        self.manager.authorizer.verify_service(credentials['service_id'],credentials['service_token'],None)
        row = self.manager.repository.get(runner_id)
        principal = self.manager._authorize_row(row,credentials)
        return public_control(self.controls.get(principal,runner_id,control_id))
