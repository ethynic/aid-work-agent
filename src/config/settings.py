"""
配置管理模块

支持从环境变量和YAML配置文件加载配置
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class LLMProviderConfig(BaseModel):
    """LLM提供者配置"""
    api_key: str = ""
    model: str = ""
    base_url: Optional[str] = None


class LLMConfig(BaseModel):
    """LLM配置"""
    provider: str = "zhipu"
    qwen: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    zhipu: LLMProviderConfig = Field(default_factory=LLMProviderConfig)


class WecomConfig(BaseModel):
    """企业微信配置"""
    corp_id: str = ""
    agent_id: str = ""
    secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""


class ChannelsConfig(BaseModel):
    """渠道配置"""
    wecom: WecomConfig = Field(default_factory=WecomConfig)


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


class ToolsConfig(BaseModel):
    """工具配置"""
    email: EmailToolConfig = Field(default_factory=EmailToolConfig)
    ocr: OCRToolConfig = Field(default_factory=OCRToolConfig)
    search: SearchToolConfig = Field(default_factory=SearchToolConfig)


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


class AppConfig(BaseModel):
    """应用配置"""
    name: str = "aid-work-agent"
    version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000


class Settings(BaseModel):
    """全局配置"""
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

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
    
    if os.getenv("QWEN_API_KEY"):
        yaml_config.setdefault("llm", {}).setdefault("qwen", {})["api_key"] = os.getenv("QWEN_API_KEY")
    
    if os.getenv("ZHIPU_API_KEY"):
        yaml_config.setdefault("llm", {}).setdefault("zhipu", {})["api_key"] = os.getenv("ZHIPU_API_KEY")
    
    if os.getenv("WECOM_CORP_ID"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["corp_id"] = os.getenv("WECOM_CORP_ID")
    
    if os.getenv("WECOM_AGENT_ID"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["agent_id"] = os.getenv("WECOM_AGENT_ID")
    
    if os.getenv("WECOM_SECRET"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["secret"] = os.getenv("WECOM_SECRET")
    
    if os.getenv("DEBUG", "").lower() in ("true", "1", "yes"):
        yaml_config.setdefault("app", {})["debug"] = True

    # 搜索工具配置
    if os.getenv("TAVILY_API_KEY"):
        yaml_config.setdefault("tools", {}).setdefault("search", {})["tavily_api_key"] = os.getenv("TAVILY_API_KEY")

    return Settings(**yaml_config)


# 全局配置实例
settings = create_settings()
