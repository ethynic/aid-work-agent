"""Main agents get room for multi-step work without overriding explicit profile limits."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.core.agent_engine.contracts import AgentMode, Identity
from src.services.agent_runner.runtime.context_assembler import ContextAssembler

@pytest.mark.parametrize("mode,explicit,expected", [
    (AgentMode.MASTER,None,50), (AgentMode.STANDALONE,None,20), (AgentMode.SUBAGENT,None,20),
    (AgentMode.MASTER,7,7), (AgentMode.STANDALONE,7,7), (AgentMode.SUBAGENT,9,9),
])
async def test_main_default_expands_without_replacing_profile_limits(mode, explicit, expected):
    history=SimpleNamespace(reader=SimpleNamespace(assert_authorized=lambda: None),
        _reload_memory_from_db=lambda *args: None, memory=SimpleNamespace(get_context=lambda session: []),
        active_summary=lambda session: None)
    attachments=SimpleNamespace(prepare=lambda text,*args: (text,None,None),
        build_image_reference_content=lambda *args: (None,None),
        _build_multimodal_user_content=lambda text,*args: text)
    profile=SimpleNamespace(get_max_iterations=lambda: explicit) if explicit is not None else None
    assembler=ContextAssembler(identity=Identity("unit_tenant","unit_user","unit_session"),role=mode,
        profile_config=profile,history=history,
        compression=SimpleNamespace(_run_compression_phase=AsyncMock(),event=None),
        prompt_sources=SimpleNamespace(_build_system_prompt=lambda *args,**kwargs: "Unit prompt"),
        skills=SimpleNamespace(_ensure_tenant_skills_loaded=lambda: None,skill_registry=None),
        remember=SimpleNamespace(_handle_remember_intent=AsyncMock()),attachments=attachments,
        visibility=SimpleNamespace(prime=AsyncMock()))
    state,_=await assembler.prepare("Perform a multi-step task")
    assert state.role==mode and state.max_iterations==expected
