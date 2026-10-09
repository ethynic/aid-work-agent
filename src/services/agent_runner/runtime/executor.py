"""Compose real capabilities around the transport-independent AgentEngine."""

import asyncio
import copy
import shutil
from loguru import logger
from src.core.agent_engine import AgentEngine, Outcome
from src.core.agent_events import make_event
from src.tools.context import ExecutionContextFactory
from .attachments import AttachmentAssembler
from .clarification import ClarificationStore, ClarificationCoordinator
from .compression import CompressionCoordinator
from .context_assembler import ContextAssembler
from .controls import ControlToolAdapter
from .history import SessionHistory
from .history_repository import HistoryRepository
from .local_tools import LocalToolAdapter
from .profile_resolver import ProfileResolver
from .prompt_sources import PromptSources
from .remember import RememberCoordinator
from .skill_session import SkillSession
from .tool_catalog import ToolCatalog
from .tools import ToolDispatcher


class ModelAdapter:
    def __init__(self, gateway, defer_names=None, deferred_call_id=None, resource_directory=None):
        self.gateway = gateway
        self.defer_names, self.deferred_call_id = defer_names, deferred_call_id
        self.resource_directory = resource_directory

    @staticmethod
    def _message_has_image_references(message):
        content = message.get("content") if isinstance(message, dict) else None
        return isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "runner_image" for part in content)

    def _resolve_image_references(self, state):
        """按执行 owner 校验并装配 provider wire；绝不回写 state.messages。

        仅含 runner_image 部件的消息被深拷贝，其余消息按引用传递（控制成本）。
        校验：workspace_directory 包含性 + state.resources['image_artifacts']
        登记 + 落盘文件尺寸/哈希复查；任一不符按内核 ValueError 语义失败。
        """
        from ..resource_paths import workspace_directory
        import base64
        import hashlib
        from pathlib import Path
        expected = workspace_directory(self.resource_directory, state.identity, state.execution_id)
        registered = {item.get("sha256"): item
                      for item in state.resources.get("image_artifacts", [])
                      if isinstance(item, dict) and item.get("sha256")}
        messages, replaced = [], False
        for message in state.messages:
            if not self._message_has_image_references(message):
                messages.append(message)
                continue
            replaced = True
            copied = copy.deepcopy(message)
            parts = []
            for part in copied["content"]:
                if not (isinstance(part, dict) and part.get("type") == "runner_image"):
                    parts.append(part)
                    continue
                digest = part.get("sha256")
                artifact = registered.get(digest)
                if (artifact is None or artifact.get("mime_type") != part.get("mime_type")
                        or artifact.get("size_bytes") != part.get("size_bytes")):
                    raise ValueError("IMAGE_ARTIFACT_NOT_REGISTERED")
                path = Path(str(artifact.get("path") or ""))
                if (path.is_symlink() or path.name.split(".", 1)[0] != "image_" + str(digest)
                        or path.resolve().parent != expected):
                    raise ValueError("IMAGE_ARTIFACT_SCOPE_INVALID")
                try:
                    data = path.read_bytes()
                except OSError as error:
                    raise ValueError("IMAGE_ARTIFACT_UNREADABLE") from error
                if len(data) != part.get("size_bytes") or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("IMAGE_ARTIFACT_CONTENT_CHANGED")
                encoded = base64.b64encode(data).decode("ascii")
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:{part['mime_type']};base64,{encoded}"}})
            copied["content"] = parts
            messages.append(copied)
        if not replaced:
            return state.messages
        return messages

    async def complete(self, state, call_id, tools):
        from src.services.agent_runner.usage_context import execution_call
        messages = state.messages
        if self.resource_directory is not None and any(
                self._message_has_image_references(message) for message in messages):
            messages = self._resolve_image_references(state)
        with execution_call(state.execution_id, call_id):
            response = await self.gateway.chat_with_tools(
                system_prompt=state.system_prompt, messages=messages, tools=tools)
        response = dict(response)
        response.setdefault("model", self.gateway.get_model_name())
        response.setdefault("provider", self.gateway.get_provider_name())
        if self.defer_names is not None and response.get("tool_calls"):
            # Existing D1 is a single-step remote-tool protocol, not a second Loop.
            from src.core.agent_engine.engine import normalize_calls
            try:
                calls = normalize_calls(response["tool_calls"])
                if len(calls) > 1:
                    response["protocol_error"] = "DESKTOP_MULTIPLE_TOOL_CALLS_UNSUPPORTED"
                else:
                    response["tool_calls"] = [call.message_call() for call in calls]
                    if calls and self.deferred_call_id:
                        response["tool_calls"][0]["id"] = self.deferred_call_id
            except ValueError as error:
                response["protocol_error"] = str(error)
        return response


class CompatibilityControl:
    """Legacy caller owns execution; persistent runner supplies a durable port."""
    def __init__(self, cancel_check=None):
        self.cancel_check = cancel_check

    def command(self):
        return "cancel" if self.cancel_check and self.cancel_check() else None

    async def save(self, state, boundary):
        pass


class CompatibilityObserver:
    def __init__(self, record=None, task_record=None, context_repository=None):
        self.record, self.task_record = record, task_record
        self.context_repository = context_repository

    async def model_usage(self, state, fact):
        from src.services.session_record import SessionRecordManager
        record = self.record or SessionRecordManager.get_current_record()
        if record:
            record.add_llm_usage(fact["usage"])
            if state.role.value != "subagent" and not fact.get("child_execution_id"):
                record.increment_iterations()
            if not record.provider:
                record.set_model(fact["model"])
                record.set_provider(fact["provider"])
        state.usage_watermark = len(state.model_calls)
        if self.context_repository is not None and not fact.get("child_execution_id"):
            count = int(fact["usage"].get("prompt_tokens", 0) or 0) + int(fact["usage"].get("completion_tokens", 0) or 0)
            if count > 0:
                try:
                    await asyncio.to_thread(self.context_repository.update_context_tokens, count)
                except Exception:
                    logger.opt(exception=True).debug("会话上下文token缓存更新失败")
        if self.task_record:
            self.task_record.update_progress(min(90, state.iteration * 5), f"Processing iteration {state.iteration}")

    async def event(self, state, event):
        pass


class RuntimeExecution:
    def __init__(self, resources, identity, *, control=None, observer=None, history_reader=None,
                 tolerate_history_failure=False, legacy_clarification_authorizer=None, initial_followup_inputs=None):
        self.resources, self.identity = resources, identity
        self.control = control or CompatibilityControl()
        self.observer = observer or CompatibilityObserver()
        self.visibility = ProfileResolver(resources.subagent_registry, identity.tenant_id)
        self.catalog = ToolCatalog(resources.mode, resources.tool_registry, resources._tool_controls,
                                   self.visibility, resources.skill_registry)
        self.skills = SkillSession(identity.tenant_id, resources.skill_registry, resources.memory)
        history_source = identity.source if identity.session_kind == "channel" else "chat"
        self.history = SessionHistory(resources.memory, history_source,
                                      history_reader or HistoryRepository(identity), tolerate_history_failure,
                                      session_kind=identity.session_kind)
        if isinstance(self.observer, CompatibilityObserver):
            self.observer.context_repository = self.history.reader
        self.compression = CompressionCoordinator(history_source, self.history.reader)
        self.clarification = ClarificationStore(authorize_legacy=legacy_clarification_authorizer)
        self.prompt_sources = PromptSources(mode=resources.mode, subagent_config=resources.subagent_config,
            tenant_id=identity.tenant_id, skill_registry=resources.skill_registry,
            style_manager=resources.style_manager, prompt_manager=resources.prompt_manager,
            tool_controls=resources._tool_controls, subagent_registry=resources.subagent_registry,
            tools=self.catalog._get_tools, available_subagents=self.visibility.available,
            usage_guides=self.catalog._collect_tool_usage_guides)
        workspace_root = None
        if getattr(self.control, 'durable_owner', False):
            from ..resource_paths import workspace_directory
            workspace_root = workspace_directory(resources.resource_directory, identity, resources.execution_id)
        self.assembler = ContextAssembler(identity=identity, role=resources.mode,
            profile_config=resources.subagent_config, history=self.history,
            compression=self.compression, prompt_sources=self.prompt_sources, skills=self.skills,
            remember=RememberCoordinator(identity.tenant_id, resources.style_manager),
            attachments=AttachmentAssembler(workspace_root), visibility=self.visibility)
        self.state = None
        self.initial_followup_inputs = copy.deepcopy(initial_followup_inputs or [])

    async def _tool_context(self, request_context):
        env_vars = {}
        config = self.resources.subagent_config
        if self.identity.tenant_id and config:
            from src.db.subagent_env_var import SubagentEnvVarDB
            variables = await asyncio.to_thread(SubagentEnvVarDB.get_vars,
                                               self.identity.tenant_id, config.dir_name)
            env_vars = {item["var_name"]: item.get("var_value", "")
                        for item in variables if item.get("var_name") and item.get("var_value")}
        return ExecutionContextFactory.for_agent_call(
            tenant_id=self.identity.tenant_id, user_id=self.identity.user_id,
            session_id=self.identity.session_id, channel=self.identity.source,
            subagent_id=getattr(config, "dir_name", None), agent_execution_id=self.state.execution_id,
            request_data=request_context.request_data if request_context else {},
            env_vars=env_vars, llm_gateway=self.resources.llm,infer_legacy_identity=False)

    async def run(self, text, user=None, attachments=None, request_context=None,
                  extra_system_prompt=None, continuation=None, defer_names=None,
                  deferred_call_id=None, verbose_config=None, verbose_state=None,
                  task_record=None, image_paths=None, state=None):
        engine_started = False
        owned_state = None
        resources_committed = False
        reply_workspaces = []
        try:
            preparation_events = []
            if state is None:
                self.state, preparation_events = await self.assembler.prepare(text,
                    execution_id=self.resources.execution_id, user=user, attachments=attachments,
                    request_context=request_context, extra_system_prompt=extra_system_prompt,
                    continuation=continuation, image_paths=image_paths)
            else:
                if state.identity != self.identity or state.role != self.resources.mode:
                    raise ValueError("CHECKPOINT_IDENTITY_MISMATCH")
                if state.execution_id != self.resources.execution_id:
                    raise ValueError("CHECKPOINT_EXECUTION_MISMATCH")
                expected_profile = getattr(self.resources.subagent_config, "dir_name", "main")
                expected_version = getattr(self.resources.subagent_config, "version", None)
                if state.profile_id != expected_profile or state.profile_version != expected_version:
                    raise ValueError("CHECKPOINT_PROFILE_MISMATCH")
                await asyncio.to_thread(self.history.reader.assert_authorized)
                self.state = state
                await asyncio.to_thread(self.skills._ensure_tenant_skills_loaded)
                await self.visibility.prime()
            if state is None and self.initial_followup_inputs:
                self.state.followup_messages.extend(copy.deepcopy(self.initial_followup_inputs))
                for message in self.initial_followup_inputs:
                    control_id = (message.get('metadata') or {}).get('control_id')
                    if control_id:
                        self.state.resources.setdefault('continuation_inputs',{})[control_id] = 'queued'
            # Both a first run after queued pause and a restored run use the
            # same authorized attachment assembler before the kernel sees input.
            for message in self.state.followup_messages:
                reply_attachments = message.pop('_attachments',None)
                if reply_attachments:
                    enhanced, injection, workspace = await asyncio.to_thread(self.assembler.attachments.prepare,
                        message['content'],reply_attachments,self.identity,self.skills.skill_registry)
                    paths = [item['url'] for item in reply_attachments
                             if item.get('type')=='image' and item.get('url')]
                    content, artifacts = await asyncio.to_thread(
                        self.assembler.attachments.build_image_reference_content, enhanced, paths)
                    if content is None:
                        content = await asyncio.to_thread(self.assembler.attachments._build_multimodal_user_content,
                            enhanced,paths)
                    message['content'] = content or enhanced
                    if artifacts:
                        # 登记先于下方 'prepared' control.save，与装配点1 语义一致：
                        # checkpoint 只存引用部件，data URL 仅存在于 provider wire。
                        registered = self.state.resources.setdefault('image_artifacts',[])
                        known = {item.get('sha256') for item in registered}
                        registered.extend(item for item in artifacts if item['sha256'] not in known)
                    if injection:
                        self.state.followup_messages.append({'role':'user','content':injection})
                    if workspace:
                        self.state.resources.setdefault('reply_workspaces',[]).append(workspace)
                        reply_workspaces.append(workspace)
            owned_state = self.state
            if getattr(self.control,'durable_owner',False):
                from ..profiles import MainProfileCatalog
                from ..runtime_manifest import configuration_fingerprint
                from ..adapter_contract import (ADAPTER_CONTRACT_VERSION, adapter_contract_mismatch)
                # Restore gate for the persisted adapter contract version. This
                # mirrors RECOVERY_CONFIGURATION_CHANGED below and backstops the
                # RecoveryCoordinator gate for restores that never pass through
                # it (e.g. the interrupted+cancel_requested direct acquire in
                # ExecutionRepository). Deliberate failure semantics: a plain
                # ValueError -> EXECUTION_FAILED terminal, unlike CheckpointFailure's
                # preserve+interrupt, so an incompatible row cannot churn forever
                # on the cancel-acquire path.
                if state is not None and adapter_contract_mismatch(self.state.resources):
                    raise ValueError('RECOVERY_ADAPTER_VERSION_UNSUPPORTED')
                _, profile_fingerprint = await asyncio.to_thread(MainProfileCatalog().resolve,self.state.profile_id)
                fingerprint = await asyncio.to_thread(configuration_fingerprint,profile_fingerprint)
                previous = self.state.resources.get('configuration_fingerprint')
                if state is not None and previous != fingerprint:
                    raise ValueError('RECOVERY_CONFIGURATION_CHANGED')
                self.state.resources['configuration_fingerprint'] = fingerprint
                # Stamp persists with the 'prepared' control.save below; child
                # states embed via ChildControl.save into the parent checkpoint.
                self.state.resources['adapter_contract_version'] = ADAPTER_CONTRACT_VERSION
                if not hasattr(self,'child_recovery'):
                    from .child_recovery import ChildRecoveryAdapter
                    self.child_recovery = ChildRecoveryAdapter(self,MainProfileCatalog())
                if state is not None:
                    continued = await self.child_recovery.restore_orphaned_children(self.state)
                    if not continued:
                        await self.control.save(self.state,'restored_children')
                        resources_committed = True
                        return
                    from ..aggregate_tree import child_tree_finished
                    if (self.state.outcome in {Outcome.COMPLETED,Outcome.FAILED,Outcome.CANCELLED,Outcome.ITERATION_LIMIT}
                            and child_tree_finished(self.state.checkpoint())):
                        # Recovery preserves closed model/tool steps. In particular,
                        # an empty resume of a saved failure must not retry a model.
                        await self.control.save(self.state,'restored_terminal')
                        resources_committed = True
                        return
            if (state is None and continuation is None and self.resources.mode.value == "master"
                    and not getattr(self.control,'durable_owner',False)):
                await asyncio.to_thread(ClarificationCoordinator(self.clarification).prepare_reply, self.state, text)
            if self.resources.subagent_executor is not None:
                from .child import ChildControl, ChildObserver, child_factory
                from src.tools.context import current_tool_execution_context
                parent_state, parent_control, parent_observer = self.state, self.control, self.observer
                def ports_factory():
                    context = current_tool_execution_context()
                    if context is None or not context.tool_call_id:
                        raise RuntimeError("CHILD_TOOL_CONTEXT_REQUIRED")
                    call_id = context.tool_call_id
                    def ports(task):
                        return (ChildControl(parent_state, parent_control, call_id),
                                ChildObserver(parent_state, parent_control, parent_observer, task))
                    return ports
                self.resources.subagent_executor.child_factory = child_factory(
                    self.identity, ports_factory, self.history.reader, self.resources.resource_directory,
                    self.resources.plan_manager.plan_store is not None)
            tool_context = await self._tool_context(request_context)
            controls = ControlToolAdapter(controls=self.resources._tool_controls, skills=self.skills,
                registry=self.resources.skill_registry, plan_manager=self.resources.plan_manager,
                clarification_store=self.clarification, context=tool_context,
                user_input=text, task_record=task_record,
                durable_owner=getattr(self.control,'durable_owner',False))
            dispatcher = ToolDispatcher(catalog=self.catalog, registry=self.resources.tool_registry,
                executor=self.resources.tool_executor, control_tools=controls,
                local=LocalToolAdapter(session_id=self.identity.session_id,
                    execution_id=self.state.execution_id, subagent_config=self.resources.subagent_config,
                    llm=self.resources.llm, tool_executor=self.resources.tool_executor,
                    cancel_on_detach=not getattr(self.control, "durable_owner", False),
                    control=self.control, state=self.state),
                skills=self.skills, plan_manager=self.resources.plan_manager,
                subagent_config=self.resources.subagent_config, control=self.control,
                defer_names=defer_names, verbose_config=verbose_config, verbose_state=verbose_state,
                policy_source=self.catalog,
                child_recovery=self.child_recovery if hasattr(self,'child_recovery') else None)
            for event in preparation_events:
                await self.observer.event(self.state,event)
                yield event
            if getattr(self.control,'durable_owner',False):
                await self.control.save(self.state,'prepared')
                resources_committed = True
            engine_started = True
            async for event in AgentEngine().run(self.state,
                ModelAdapter(self.resources.llm, defer_names, deferred_call_id,
                             resource_directory=self.resources.resource_directory), dispatcher,
                self.control, self.observer):
                if event["type"] == "execution_completed":
                    yield make_event("progress", data="✅ 任务完成，正在生成回复...")
                elif event["type"] == "iteration_limit":
                    yield make_event("response", data="抱歉，这个问题处理时间较长，暂时未能完成。请稍后重试，或把问题拆分成几个小问题分别问我。")
                else:
                    yield event
        finally:
            # Waiting/paused execution retains resources needed by its checkpoint.
            workspace = owned_state.resources.get("workspace") if owned_state else None
            finished = owned_state and owned_state.outcome in {Outcome.COMPLETED, Outcome.CANCELLED, Outcome.ITERATION_LIMIT}
            failed_preparation = state is None and not engine_started
            if workspace and (finished or failed_preparation) and not getattr(self.control,'durable_owner',False):
                await asyncio.to_thread(shutil.rmtree, workspace, True)
            if getattr(self.control,'durable_owner',False) and not resources_committed:
                # Resource ownership transfers only with its checkpoint. Keep
                # prior restored workspaces; remove newly prepared, uncommitted IO.
                pending_cleanup = reply_workspaces + ([workspace] if workspace and state is None else [])
                for path in pending_cleanup:
                    await asyncio.to_thread(shutil.rmtree,path,True)
