"""
配置管理模块

支持从环境变量和YAML配置文件加载配置
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field, validator
from dotenv import load_dotenv

load_dotenv()


class LLMProviderConfig(BaseModel):
    """LLM提供者配置"""
    api_keys: List[str] = Field(default_factory=list)  # 多 Key 池
    model: str = ""
    base_url: Optional[str] = None
    # Key 池并发控制
    max_concurrent_per_key: int = 2   # 每个 Key 最大并发数
    queue_timeout: float = 30.0       # 等待可用 Key 的超时秒数

    @validator("api_keys", pre=True, always=True)
    def parse_api_keys(cls, v):
        """
        支持逗号分隔字符串或列表。
        """
        if isinstance(v, str):
            parsed = [k.strip() for k in v.split(",") if k.strip()]
        elif isinstance(v, list):
            parsed = [k.strip() for k in v if k.strip()]
        else:
            parsed = []
        return parsed

    def get_effective_keys(self) -> List[str]:
        """获取有效的 Key 列表（已去重、去空）"""
        return self.api_keys


class LLMConfig(BaseModel):
    """LLM配置"""
    provider: str = "zhipu"
    qwen: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    zhipu: LLMProviderConfig = Field(default_factory=LLMProviderConfig)


class WecomConfig(BaseModel):
    """企业微信配置"""
    enabled: bool = False
    corp_id: str = ""
    agent_id: str = ""
    secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""


class DingtalkConfig(BaseModel):
    """钉钉配置"""
    enabled: bool = False
    app_key: str = ""
    app_secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""


class FeishuConfig(BaseModel):
    """飞书配置"""
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""


class ChannelsConfig(BaseModel):
    """渠道配置"""
    wecom: WecomConfig = Field(default_factory=WecomConfig)
    dingtalk: DingtalkConfig = Field(default_factory=DingtalkConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)


class EmailToolConfig(BaseModel):
    """邮件工具配置"""
    smtp_server: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    imap_server: str = ""
    imap_port: int = 993


class OCRToolConfig(BaseModel):
    """OCR工具配置"""
    provider: str = "baidu"
    baidu_api_key: str = ""
    baidu_secret_key: str = ""


class SearchToolConfig(BaseModel):
    """搜索工具配置"""
    tavily_api_key: str = ""
    max_results: int = 5  # 控制上下文长度
    include_answer: bool = True  # 返回 AI 生成的答案摘要
    search_depth: str = "basic"  # basic | advanced


class BrowserToolConfig(BaseModel):
    """浏览器工具配置"""
    headless: bool = True  # 是否无头模式
    timeout: int = 30000  # 默认超时时间（毫秒）
    viewport_width: int = 1920  # 视口宽度
    viewport_height: int = 1080  # 视口高度


class ToolsConfig(BaseModel):
    """工具配置"""
    email: EmailToolConfig = Field(default_factory=EmailToolConfig)
    ocr: OCRToolConfig = Field(default_factory=OCRToolConfig)
    search: SearchToolConfig = Field(default_factory=SearchToolConfig)
    browser: BrowserToolConfig = Field(default_factory=BrowserToolConfig)


class ShortTermMemoryConfig(BaseModel):
    """短期记忆配置"""
    max_messages: int = 10
    ttl: int = 3600


class MemoryConfig(BaseModel):
    """记忆配置"""
    short_term: ShortTermMemoryConfig = Field(default_factory=ShortTermMemoryConfig)


class AuthConfig(BaseModel):
    """认证配置"""
    enabled: bool = True
    default_role: str = "employee"


class MasterAgentSkillsConfig(BaseModel):
    """主智能体 Skill 配置"""
    allowed: List[str] = Field(default_factory=list)  # 允许的 skills 列表，空列表表示允许所有


class SubagentSkillsConfig(BaseModel):
    """子智能体 Skill 配置"""
    default_allowed: List[str] = Field(default_factory=list)  # 默认允许列表


class SkillsConfig(BaseModel):
    """Skill 全局配置"""
    master_agent: MasterAgentSkillsConfig = Field(default_factory=MasterAgentSkillsConfig)
    subagent: SubagentSkillsConfig = Field(default_factory=SubagentSkillsConfig)


class AppConfig(BaseModel):
    """应用配置"""
    name: str = "aid-work-agent"
    version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000


class SaasConfig(BaseModel):
    """SaaS 多租户配置"""
    enabled: bool = False
    tenant_skills_dir: str = "storage/tenants"
    default_max_instances: int = 5
    default_max_users: int = 50


class Settings(BaseModel):
    """全局配置"""
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    saas: SaasConfig = Field(default_factory=SaasConfig)

    class Config:
        extra = "allow"


def _substitute_env_vars(value: Any) -> Any:
    """递归替换环境变量占位符"""
    if isinstance(value, str):
        # 匹配 ${VAR_NAME} 格式
        if value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.getenv(env_var, "")
        return value
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


def load_yaml_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """加载YAML配置文件"""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "configs" / "config.yaml"
    
    if not config_path.exists():
        return {}
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 替换环境变量
    return _substitute_env_vars(config) if config else {}


def create_settings(config_path: Optional[Path] = None) -> Settings:
    """创建配置实例"""
    # 从YAML文件加载配置
    yaml_config = load_yaml_config(config_path)
    
    # 从环境变量覆盖配置
    if os.getenv("LLM_PROVIDER"):
        yaml_config.setdefault("llm", {})["provider"] = os.getenv("LLM_PROVIDER")
    
    # Qwen：只使用 QWEN_API_KEYS（逗号分隔的多 Key）
    if os.getenv("QWEN_API_KEYS"):
        yaml_config.setdefault("llm", {}).setdefault("qwen", {})["api_keys"] = os.getenv("QWEN_API_KEYS")

    # Zhipu：只使用 ZHIPU_API_KEYS（逗号分隔的多 Key）
    if os.getenv("ZHIPU_API_KEYS"):
        yaml_config.setdefault("llm", {}).setdefault("zhipu", {})["api_keys"] = os.getenv("ZHIPU_API_KEYS")
    
    if os.getenv("WECOM_CORP_ID"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["corp_id"] = os.getenv("WECOM_CORP_ID")
    
    if os.getenv("WECOM_AGENT_ID"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["agent_id"] = os.getenv("WECOM_AGENT_ID")
    
    if os.getenv("WECOM_SECRET"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["secret"] = os.getenv("WECOM_SECRET")

    if os.getenv("DINGTALK_APP_KEY"):
        yaml_config.setdefault("channels", {}).setdefault("dingtalk", {})["app_key"] = os.getenv("DINGTALK_APP_KEY")

    if os.getenv("DINGTALK_APP_SECRET"):
        yaml_config.setdefault("channels", {}).setdefault("dingtalk", {})["app_secret"] = os.getenv("DINGTALK_APP_SECRET")

    if os.getenv("DINGTALK_TOKEN"):
        yaml_config.setdefault("channels", {}).setdefault("dingtalk", {})["token"] = os.getenv("DINGTALK_TOKEN")

    if os.getenv("DINGTALK_ENCODING_AES_KEY"):
        yaml_config.setdefault("channels", {}).setdefault("dingtalk", {})["encoding_aes_key"] = os.getenv("DINGTALK_ENCODING_AES_KEY")

    if os.getenv("FEISHU_APP_ID"):
        yaml_config.setdefault("channels", {}).setdefault("feishu", {})["app_id"] = os.getenv("FEISHU_APP_ID")

    if os.getenv("FEISHU_APP_SECRET"):
        yaml_config.setdefault("channels", {}).setdefault("feishu", {})["app_secret"] = os.getenv("FEISHU_APP_SECRET")

    if os.getenv("FEISHU_VERIFICATION_TOKEN"):
        yaml_config.setdefault("channels", {}).setdefault("feishu", {})["verification_token"] = os.getenv("FEISHU_VERIFICATION_TOKEN")

    if os.getenv("FEISHU_ENCRYPT_KEY"):
        yaml_config.setdefault("channels", {}).setdefault("feishu", {})["encrypt_key"] = os.getenv("FEISHU_ENCRYPT_KEY")

    # 启用渠道
    if os.getenv("WECOM_ENABLED", "").lower() in ("true", "1", "yes"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["enabled"] = True

    if os.getenv("DINGTALK_ENABLED", "").lower() in ("true", "1", "yes"):
        yaml_config.setdefault("channels", {}).setdefault("dingtalk", {})["enabled"] = True

    if os.getenv("FEISHU_ENABLED", "").lower() in ("true", "1", "yes"):
        yaml_config.setdefault("channels", {}).setdefault("feishu", {})["enabled"] = True

    if os.getenv("DEBUG", "").lower() in ("true", "1", "yes"):
        yaml_config.setdefault("app", {})["debug"] = True

    # 搜索工具配置
    if os.getenv("TAVILY_API_KEY"):
        yaml_config.setdefault("tools", {}).setdefault("search", {})["tavily_api_key"] = os.getenv("TAVILY_API_KEY")

    return Settings(**yaml_config)


# 全局配置实例
settings = create_settings()
