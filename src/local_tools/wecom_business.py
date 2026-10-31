"""The three model-facing WeCom operations; native recovery stays internal."""

from typing import Annotated

from pydantic import BaseModel, Field, model_validator


from .catalog import agent_tools, get_provider

BUSINESS_TOOLS = frozenset(agent_tools('wecom'))
LEGACY_READ_TOOLS = frozenset(get_provider('wecom')['agent_tool_aliases'])


class SessionInput(BaseModel):
    model_config = {'extra': 'forbid'}
    session: str = Field(..., min_length=1, max_length=2048,
        description='读取结果返回的原会话标识；首次也可用完整会话名。单聊和群均适用，不自行拼目标句柄。')


class AttachmentReadInput(BaseModel):
    model_config = {'extra': 'forbid'}
    message_id: int = Field(..., gt=0, strict=True,
        description='原会话读取返回的附件msg_id，不按文件名猜测。')
    offset: int | None = Field(None, ge=0, strict=True, description='文本附件起始字节；首次省略。')
    max_bytes: int | None = Field(None, ge=1, le=8192, strict=True,
        description='本次最多文本字节，默认4096；按next_offset继续。')


class ReadInput(SessionInput):
    cursor: str | None = Field(None, min_length=1, max_length=16384,
        description='该会话上次返回的增量游标，原样回传；首次省略，历史仅作上下文。')
    limit: int | None = Field(None, ge=1, le=50,
        description='本批最多消息数；has_more时继续读取，未返回的消息不推进。')
    attachment: AttachmentReadInput | None = Field(None,
        description='按消息ID展开该条附件，不推进聊天游标；图片交给视觉模型，文本按字节分页。')

    @model_validator(mode='after')
    def _attachment_cursor(self):
        if self.attachment is not None and self.cursor is not None:
            raise ValueError('附件展开与聊天cursor不能同时指定；保留原cursor用于后续聊天增量读取。')
        return self


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
