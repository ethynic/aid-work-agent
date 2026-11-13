"""Proof and input resolution for repeatable server tool calls."""

import os
import re
import shutil
import tempfile
from pathlib import Path
from shlex import quote

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ToolCall
from src.core.storage import configured_storage_root, is_tenant_owned_file, normalize_tenant_id
from .resource_paths import workspace_directory


QUOTATION_SKILL = 'quotation-pricing'
QUOTATION_DIRECTORY = Path(__file__).resolve().parents[2] / 'skills' / 'quotation-pricing-1.0.0'
_OFFICE_DOCUMENT_SUFFIXES = {'.doc','.docx','.xls','.xlsx','.ppt','.pptx'}
_QUOTATION_PARSE = re.compile(
    r'OUT=/app/storage/tmp/quoteprice_\$\(date \+%s\) && mkdir -p \$OUT && echo "OUT=\$OUT"'
    r' && cd /app/src/skills/quotation-pricing-1\.0\.0 && python scripts/parse_quotation\.py'
    r' "(?P<source>[^"\\`${}\x00-\x1f]+\.xlsx)" --json (?:\$OUT/items_extracted\.json|"\$OUT/items_extracted\.json")'
    r'(?: --project "(?P<project>[^"\\`${}\x00-\x1f]*)")?'
    r'(?P<preview> && echo "=====JSON HEAD=====" && head -c 3000 \$OUT/items_extracted\.json)?'
)
_COMMAND_REWRITE = re.compile(r'--user-id|--(?:file-path|input|file|source|document)\b|scripts/|parse_quotation\.py')
TOOL_UNAVAILABLE = 'RECOVERY_TOOL_UNAVAILABLE'
INPUT_UNAVAILABLE = 'RECOVERY_INPUT_UNAVAILABLE'
_INTERRUPTION_MESSAGES = {
    TOOL_UNAVAILABLE: '原工具当前不可用于恢复。请检查工具配置后继续，或停止任务。',
    INPUT_UNAVAILABLE: '原文件暂时无法读取，任务尚未继续。',
}


class ServerToolRecoveryInterrupted(CheckpointFailure):
    def __init__(self, code):
        super().__init__(code)
        self.public_verification = _INTERRUPTION_MESSAGES[code]


def is_builtin_quotation_skill(skill):
    return (skill is not None and Path(skill.dir).resolve() == QUOTATION_DIRECTORY
            and skill.version == '1.0.0')


def recovery_skill(state, profiles, name):
    from .runtime.resource_cache import allowed_skill_names, cached_skill_registry
    tenant_id = state.identity.tenant_id
    config, _ = profiles.resolve(state.profile_id, tenant_id)
    allowed = allowed_skill_names(state.role == AgentMode.MASTER, config)
    if tenant_id:
        from src.saas.services.tenant_skill_cache import tenant_skill_cache
        skills = tenant_skill_cache.get_or_load(tenant_id, QUOTATION_DIRECTORY.parent, allowed)
        return skills.get(name)
    return cached_skill_registry(allowed).get(name)


def quotation_available(state, profiles):
    return is_builtin_quotation_skill(recovery_skill(state, profiles, QUOTATION_SKILL))


def repeatable_server_tool(call):
    if call.name == 'knowledge_file_search':
        from src.tools.knowledge.knowledge_file_search_tool import KnowledgeFileSearchTool
        return KnowledgeFileSearchTool
    if call.name == 'read':
        from src.tools.file.read_tool import ReadTool
        value = call.arguments.get('file_path')
        if not isinstance(value,str) or not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = QUOTATION_DIRECTORY.parents[2] / path
        try:
            # Read resolves symlinks before choosing its Office parser, which
            # can spill a newly named output file.
            if path.resolve().suffix.lower() not in _OFFICE_DOCUMENT_SUFFIXES:
                return ReadTool
        except (OSError,RuntimeError,ValueError):
            return None
    if call.name == 'grep':
        from src.tools.file.grep_tool import GrepTool
        pattern = call.arguments.get('pattern')
        # The normal tool passes a positional pattern to rg. Neither an option
        # argument nor an inherited config that may install a preprocessor is
        # evidence of a read-only operation.
        if isinstance(pattern,str) and pattern and not pattern.startswith('-') and not os.getenv('RIPGREP_CONFIG_PATH'):
            return GrepTool
    return None


def can_repeat_server_call(fact):
    if fact.phase != 'dispatching' or fact.result is not None or fact.invocation_id or fact.result_recorded:
        return False
    call = fact.call
    return (repeatable_server_tool(call) is not None
            or call.name == 'skill_execute' and quotation_call(call) is not None)


def quotation_call(call):
    args = call.arguments
    if set(args) != {'skill', 'command'} or args['skill'] != QUOTATION_SKILL or not isinstance(args['command'], str):
        return None
    match = _QUOTATION_PARSE.fullmatch(args['command'])
    if match and not any(_COMMAND_REWRITE.search(value) for value in (match['source'],match['project'] or '')):
        return match
    return None


def _prior_delivery(runner_id, source, connection_factory):
    with connection_factory() as connection, connection.cursor() as cursor:
        # A global scratch path isn't a file grant. Use a committed copy from
        # an earlier Runner of this exact session and actor instead.
        cursor.execute("""
            WITH RECURSIVE owned_steps AS (
                SELECT prior.queue_order, original.tenant_id, original.user_id,
                    prior.checkpoint->'execution' AS node
                FROM agent_runners original
                JOIN agent_runners prior ON prior.scope_key=original.scope_key
                    AND prior.session_kind=original.session_kind AND prior.session_id=original.session_id
                    AND prior.actor_kind=original.actor_kind AND prior.actor_id=original.actor_id
                    AND prior.source=original.source AND prior.user_id IS NOT DISTINCT FROM original.user_id
                    AND prior.queue_order<original.queue_order AND prior.status='completed'
                WHERE original.runner_id=%s
                UNION ALL
                SELECT steps.queue_order, steps.tenant_id, steps.user_id, child.value->'checkpoint'
                FROM owned_steps steps CROSS JOIN LATERAL jsonb_each(steps.node->'children') child
            )
            SELECT a.path FROM owned_steps steps
            CROSS JOIN LATERAL jsonb_each(steps.node->'tools') tool
            JOIN file_assets a ON a.file_id=tool.value#>>'{result,file_id}'
                AND a.path=tool.value#>>'{result,file_path}' AND a.source='delivery'
                AND a.tenant_id IS NOT DISTINCT FROM steps.tenant_id
                AND a.user_id IS NOT DISTINCT FROM steps.user_id
            WHERE tool.value->>'phase'='completed' AND tool.value->>'success'='true'
                AND tool.value#>>'{call,name}'='cp'
                AND tool.value#>>'{call,arguments,source_file_path}'=%s
            ORDER BY steps.queue_order DESC LIMIT 1
        """, (runner_id, source))
        row = cursor.fetchone()
        return Path(row['path']) if row else None


def quotation_input(match, state, runner_id, connection_factory):
    source = Path(match['source'])
    if not source.is_absolute() or '..' in source.parts:
        return None
    resolved = source.resolve()
    workspace = state.resources.get('workspace')
    if (resolved.is_file() and (
            workspace and resolved.is_relative_to(Path(workspace).resolve())
            or is_tenant_owned_file(str(resolved), state.identity.tenant_id))):
        return resolved
    if not re.fullmatch(r'/app/storage/tmp/spec2quote_\d+/excel/[^/]+\.xlsx', match['source']):
        return None
    delivered = _prior_delivery(runner_id, match['source'], connection_factory)
    if delivered is not None:
        directory = (configured_storage_root() / 'tenants'
                     / normalize_tenant_id(state.identity.tenant_id or '_anonymous') / 'conversation')
        if (delivered.resolve() == delivered and delivered.parent == directory
                and delivered.suffix == '.xlsx' and delivered.is_file()):
            return delivered
    return None


def create_recovery_workspace(state, resource_directory):
    root = workspace_directory(resource_directory,state.identity,state.execution_id)
    root.mkdir(parents=True,exist_ok=True)
    if root.resolve() != root:
        raise ValueError('RECOVERY_RESOURCE_SCOPE_INVALID')
    return Path(tempfile.mkdtemp(prefix='skill_ws_',dir=root))


def safe_workbook_name(filename):
    candidate = Path((filename or '').replace('\\','/')).name
    if (Path(candidate).suffix.lower() == '.xlsx' and not _COMMAND_REWRITE.search(candidate)
            and not re.search(r'[\x00-\x1f{}]', candidate) and len(candidate.encode()) <= 255):
        return candidate
    return None


def stage_quotation_call(call, match, source, workspace, *, name=None):
    # Preserve the original file basename recorded in the extracted rows,
    # even when the source is an opaque registered download ID.
    staged = workspace / (name or Path(match['source']).name)
    shutil.copyfile(source,staged)
    output = quote(str(workspace / 'items_extracted.json'))
    script = quote(str(QUOTATION_DIRECTORY / 'scripts' / 'parse_quotation.py'))
    command = f'echo {quote("OUT=" + str(workspace))} && python {script} {quote(str(staged))} --json {output}'
    if match['project'] is not None:
        command += ' --project=' + quote(match['project'])
    if match['preview']:
        command += f' && echo "=====JSON HEAD=====" && head -c 3000 {output}'
    return ToolCall(call.id,call.name,{**call.arguments,'command':command})
