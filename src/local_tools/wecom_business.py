"""The three model-facing WeCom operations; native recovery stays internal."""

from typing import Annotated

from pydantic import BaseModel, Field


from .catalog import agent_tools, get_provider

BUSINESS_TOOLS = frozenset(agent_tools('wecom'))
LEGACY_READ_TOOLS = frozenset(get_provider('wecom')['agent_tool_aliases'])


class SessionInput(BaseModel):
    model_config = {'extra': 'forbid'}
    session: str = Field(..., min_length=1, max_length=2048,
        description='读取结果返回的原会话标识；首次也可用完整会话名。单聊和群均适用，不自行拼目标句柄。')


class ReadInput(SessionInput):
    cursor: str | None = Field(None, min_length=1, max_length=16384,
        description='该会话上次返回的增量游标，原样回传；首次省略，历史仅作上下文。')
    limit: int | None = Field(None, ge=1, le=50,
        description='本批最多消息数；has_more时继续读取，未返回的消息不推进。')


class SendInput(SessionInput):
    text: str = Field(..., min_length=1, max_length=2000, description='本次发送的完整文本。')


class ReadSessionsInput(BaseModel):
    model_config = {'extra': 'forbid'}
    sessions: list[Annotated[str, Field(min_length=1, max_length=2048)]] | None = Field(None, min_length=1, max_length=50,
        description='指定会话标识或完整名称；省略时读取当前任务范围内已同步会话。')
    cursor: str | None = Field(None, min_length=1, max_length=16384,
        description='上次批量读取返回的游标，原样回传；各任务独立保存。')
    limit: int | None = Field(None, ge=1, le=50,
        description='整个响应的消息数预算；has_more时继续排空，再休眠。')


def project_definitions(definitions):
    """Keep old registry entries for frozen calls, without advertising their steps."""
    return [item for item in definitions
            if not item.get('name', '').startswith('wecom_') or item['name'] in BUSINESS_TOOLS]
