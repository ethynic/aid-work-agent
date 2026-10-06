"""Prepare execution once; a checkpoint resumes directly without history cleaning."""

import asyncio
import json
import shutil
import uuid
from datetime import datetime
from src.core.agent_engine.contracts import AgentMode, ExecutionState
from src.core.agent_engine.context import reorder_history


class ContextAssembler:
    def __init__(self, *, identity, role, profile_config, history, compression,
                 prompt_sources, skills, remember, attachments, visibility):
        self.identity, self.role, self.profile_config = identity, role, profile_config
        self.history, self.compression, self.prompt_sources = history, compression, prompt_sources
        self.skills, self.remember, self.attachments, self.visibility = skills, remember, attachments, visibility
        self.initial_input_ref = None
        self.initial_input_text = None
        self.initial_input_projection = None

    async def prepare(self, text, execution_id=None, user=None, attachments=None,
                      request_context=None, extra_system_prompt=None, continuation=None,
                      image_paths=None):
        if (self.initial_input_projection is not None
                and (self.initial_input_projection.get('preparation_ref') or self.initial_input_projection.get('batch_ref'))
                and self.role != AgentMode.SUBAGENT):
            text = self.initial_input_projection['model_text']
            attachments = None
            image_paths = None
        workspace = None
        # continuation 分支不装配图片附件，必须预置 None——公开续跑路径
        # （agent.continue_tool_call）走 continuation 分支，缺省会 UnboundLocalError
        image_artifacts = None
        handed_off = False
        try:
            await asyncio.to_thread(self.history.reader.assert_authorized)
            await asyncio.to_thread(self.skills._ensure_tenant_skills_loaded)
            await self.visibility.prime()
            events = []
            if self.role != AgentMode.SUBAGENT:
                if continuation is None:
                    await self.compression._run_compression_phase(self.identity.session_id)
                    if self.compression.event:
                        events.append(self.compression.event)
                await asyncio.to_thread(self.history._reload_memory_from_db, self.identity.session_id, text)
                raw_history = self.history.memory.get_context(self.identity.session_id)
            else:
                raw_history = []
            incoming = None
            if continuation:
                call_id = str(continuation.get("tool_call_id") or "")
                unresolved = {call.get("id") for message in raw_history
                              for call in message.get("tool_calls", [])}
                unresolved -= {message.get("tool_call_id") for message in raw_history if message.get("role") == "tool"}
                if not call_id or call_id not in unresolved:
                    raise RuntimeError("CONTINUATION_CONTEXT_LOST")
                content = continuation.get("content")
                content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                resumed_tool = {"role": "tool", "tool_call_id": call_id, "content": content}
                raw_history.append(resumed_tool)
                # The incoming tool result is new persistence input, although it
                # belongs to the initial model context rather than this Loop's ledger.
                from src.core.agent_events import make_event
                events.append(make_event("tool_messages", messages=[resumed_tool]))
            else:
                now = datetime.now()
                timestamp = f"[当前时间: {now.strftime('%Y年%m月%d日 %H:%M:%S')}, {now.strftime('%A')}, 今年是{now.year}年]\n\n"
                enhanced, injection, workspace = await asyncio.to_thread(
                    self.attachments.prepare, timestamp + text, attachments, self.identity, self.skills.skill_registry)
                content, image_artifacts = await asyncio.to_thread(
                    self.attachments.build_image_reference_content, enhanced, image_paths)
                if content is None:
                    # 无 owner workspace（旧内存路径）或图片全部无效时回退 wire 形态。
                    content = await asyncio.to_thread(
                        self.attachments._build_multimodal_user_content, enhanced, image_paths)
                incoming = {"role": "user", "content": content if content is not None else enhanced}
                if self.initial_input_ref and self.role != AgentMode.SUBAGENT:
                    incoming["metadata"] = {"input_ref": self.initial_input_ref,
                                            "submitted_text": self.initial_input_text,
                                            "preparation_ref": (self.initial_input_projection or {}).get('preparation_ref')}
                    if (self.initial_input_projection or {}).get('batch_ref'):
                        import copy
                        incoming['metadata'].update({key: copy.deepcopy(self.initial_input_projection[key])
                            for key in ('batch_ref','history_group_ref','input_refs','merged_segments')})
                else:
                    raw_history.append(incoming)
                await self.remember._handle_remember_intent(text, user)
            markers = [message for message in raw_history if message.get("role") == "system"]
            history = [message for message in raw_history if message.get("role") != "system"]
            # Ordinary historical pairs may be cleaned. Continuation input is paired
            # before cleaning; saved execution/checkpoint never takes this path.
            messages = reorder_history(history)
            if incoming is not None and incoming.get("metadata"):
                # Clean only the old history. Preserve the actual incoming object
                # rather than trying to identify it after the formatter drops metadata.
                if not any(message.get("role") == "user" for message in messages):
                    messages = []
                if messages and messages[-1].get("role") == "user":
                    messages.pop()
                messages.append(incoming)
            if self.role != AgentMode.SUBAGENT:
                summary = await asyncio.to_thread(self.history.active_summary, self.identity.session_id)
                if summary:
                    messages = [{"role": "user", "content": f"[📋 之前对话摘要]\n{summary}"},
                        {"role": "assistant", "content": "好的，已了解之前对话的要点。"}, *messages]
            prompt = await asyncio.to_thread(self.prompt_sources._build_system_prompt, user,
                                             extra_system_prompt=extra_system_prompt)
            if markers:
                prompt += "\n\n" + "\n".join(message.get("content", "") for message in markers)
            if request_context:
                messages.extend({"role": "user", "content": augmentation}
                                for augmentation in request_context.prompt_augmentations)
            if not continuation and injection:
                messages.append({"role": "user", "content": injection})
            state = ExecutionState(identity=self.identity, execution_id=execution_id or f"ae_{uuid.uuid4().hex}",
                role=self.role, system_prompt=prompt, messages=messages, initial_len=len(messages),
                max_iterations=self.profile_config.get_max_iterations() if self.profile_config else 20,
                profile_id=getattr(self.profile_config, "dir_name", "main"),
                profile_version=getattr(self.profile_config, "version", None))
            if not continuation and workspace:
                state.resources["workspace"] = workspace
            if image_artifacts:
                # 引用登记随 'prepared' control.save 一并持久化；部件本身只含
                # name/mime_type/size_bytes/sha256，checkpoint 不再携带 data URL。
                state.resources["image_artifacts"] = image_artifacts
            handed_off = True
            return state, events
        finally:
            if workspace and not handed_off:
                await asyncio.to_thread(shutil.rmtree, workspace, True)
