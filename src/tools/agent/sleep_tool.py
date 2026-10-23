"""A durable wait intent; this tool never owns a timer or blocks a worker."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.tools.base import BaseTool


class SleepInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seconds: StrictInt = Field(..., ge=1, le=604800, description='休眠秒数，1～604800')
    reason: str | None = Field(None, max_length=200, description='简短等待原因')


class AgentSleepTool(BaseTool):
    catalog = False
    name = 'agent_sleep'
    category = 'agent'
    description = '持久保存当前任务并休眠，到期后继续原任务；仅受支持的后台任务可用'
    display_name = '休眠后继续'
    usage_guide = ('持续跟进时，主动读取业务工具并判断是否行动，然后调用 agent_sleep 等待下一次检查。'
                   '休眠释放执行资源；醒来继续原任务。持续任务应在无新内容时休眠，'
                   '无需每次轮询向用户汇报；普通最终回答会结束任务。')
    InputModel = SleepInput

    async def execute(self, **kwargs):
        # Durable ControlToolAdapter owns support/whole-tree checks. Direct and
        # legacy invocation must never silently degrade to an in-memory sleep.
        return {'success':False, 'error_code':'DURABLE_SLEEP_UNSUPPORTED'}
