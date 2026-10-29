"""Fresh request authorization precedes control idempotency and acceptance."""

from .contracts import RunnerError
from .control_contracts import public_control
from .control_repository import ControlRepository
from .repository import public_runner
from .source_receipts import source_offload


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
        if row.get('source') == 'scheduled':
            # 控制操作范围矩阵（设计 §5.2）：用户（Bearer token）对 scheduled
            # runner 不开放自由文本作答与控制动作（reply/pause/resume 一律
            # SCHEDULED_CONTROL_FORBIDDEN，非交互要求覆盖所有控制入口）；仅
            # scheduler 服务可提交无补充输入的 resume（恢复驱动 §4.6——携带
            # answer/attachments 的 resume 同样拒绝，矩阵未放行补充输入）。
            # 查询/订阅/cancel 走各自读取与专用端点，不经此处。
            if (credentials.get('user_token') or request.action != 'resume'
                    or request.answer.strip() or request.attachments):
                raise RunnerError('SCHEDULED_CONTROL_FORBIDDEN',403)
        principal = self.manager._authorize_row(row,credentials)
        previous = self.controls.find_request(principal,runner_id,request)
        return row,principal,previous

    def _existing(self, runner_id, previous):
        return public_control(previous),False,public_runner(self.manager.repository.get(runner_id))

    async def submit_async(self, runner_id, request, credentials):
        row,_,previous=await source_offload(self._find,runner_id,request,credentials)
        if previous is not None:
            return await source_offload(self._existing,runner_id,previous)
        return await source_offload(self.submit,runner_id,request,credentials)

    def submit(self, runner_id, request, credentials):
        """Synchronous callers retain the original non-native control path."""
        row,principal,previous=self._find(runner_id,request,credentials)
        if previous is not None:
            return self._existing(runner_id,previous)
        if request.action in {'resume','reply'}:
            # The original service/actor binding and actual resolved profile are
            # revalidated; an expired original bearer is not stored or required.
            self.manager.authorizer.authorize_persisted(row)
            self.manager.authorizer.assert_credit(principal)
        control, created = self.controls.submit(principal,runner_id,request)
        return public_control(control),created,public_runner(self.manager.repository.get(runner_id))

    def get(self, runner_id, control_id, credentials):
        self.manager.authorizer.verify_service(credentials['service_id'],credentials['service_token'],None)
        row = self.manager.repository.get(runner_id)
        principal = self.manager._authorize_row(row,credentials)
        return public_control(self.controls.get(principal,runner_id,control_id))
