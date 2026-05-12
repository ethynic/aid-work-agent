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


class WecomMessageConfig(BaseModel):
    """企业微信消息配置"""
    default_type: str = "markdown"  # text | markdown
    max_bytes: int = 2048
    split_on_paragraph: bool = True


class StorageConfig(BaseModel):
    """存储配置（所有业务数据集中于此，便于备份和迁移）"""
    base_dir: str = "storage"  # 存储根目录
    uploads_dir: str = "storage/uploads"  # 上传文件根目录
    memories_dir: str = "storage/memories"  # 长期记忆文件目录
    # 注意：代码内部使用字节单位，.env 中配置使用 MB 单位
    max_knowledge_file_size: int = 50 * 1024 * 1024  # 知识库文件大小限制（默认 50MB），支持 .env 覆盖
    max_general_file_size: int = 20 * 1024 * 1024  # 通用上传文件大小限制（默认 20MB），支持 .env 覆盖


class WecomMediaConfig(BaseModel):
    """企业微信媒体配置"""
    upload_dir: str = "./storage/uploads/wecom"  # 跟随 uploads 迁移到 storage
    max_file_size: int = 20971520  # 20MB（WeCom 限制）


class WecomRateLimitConfig(BaseModel):
    """企业微信速率限制配置"""
    enabled: bool = True
    max_per_minute: int = 10


class WecomRetryConfig(BaseModel):
    """企业微信重试配置"""
    max_attempts: int = 3
    backoff_base: float = 1.0


class WecomConfig(BaseModel):
    """企业微信配置"""
    enabled: bool = False
    corp_id: str = ""
    agent_id: str = ""
    secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""
    message: WecomMessageConfig = Field(default_factory=WecomMessageConfig)
    media: WecomMediaConfig = Field(default_factory=WecomMediaConfig)
    rate_limit: WecomRateLimitConfig = Field(default_factory=WecomRateLimitConfig)
    retry: WecomRetryConfig = Field(default_factory=WecomRetryConfig)
    welcome_message: str = ""


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
    max_messages: int = 100
    ttl: int = 3600


class MemoryConfig(BaseModel):
    """记忆配置"""
    short_term: ShortTermMemoryConfig = Field(default_factory=ShortTermMemoryConfig)
    cleanup_interval: int = 300  # 过期会话清理间隔（秒）


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
    llm_debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000


class SaasConfig(BaseModel):
    """SaaS 多租户配置"""
    enabled: bool = False
    tenant_skills_dir: str = "tenants"
    default_max_instances: int = 5
    default_max_users: int = 50


class CorsConfig(BaseModel):
    """CORS 配置"""
    allowed_origins: List[str] = Field(default_factory=lambda: [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:7860",
        "https://*.aidingyi.cn",
    ])


class DemoConfig(BaseModel):
    """演示模式配置"""
    enabled: bool = True
    mock_password: str = "888888"


class SmsConfig(BaseModel):
    """短信配置"""
    channel: str = ""  # 当前使用的短信通道，如 "ZhuTong"
    username: str = ""  # 助通用户名
    password: str = ""  # 助通密码
    signature: str = ""  # 短信签名
    template_yzm: str = ""  # 验证码模板ID
    qb_sms_code: str = ""  # 短信验证码 bypass 码（用于测试/演示）


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
    demo: DemoConfig = Field(default_factory=DemoConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    sms: SmsConfig = Field(default_factory=SmsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    # 认证相关配置（从环境变量加载）
    qb_token: str = ""  # 平台管理员超级token（明文，仅用于向后兼容，推荐使用 qb_token_hash）
    qb_token_hash: str = ""  # 平台管理员超级token 的 bcrypt 哈希值（推荐）
    max_concurrent_tokens: int = 2  # 同一用户最大并发 token 数
    password_rule: str = r"^(?=.*[A-Za-z])(?=.*\d).{8,50}$"  # 密码正则规则
    password_msg: str = "长度8-50位，必须有字母+数字"  # 密码规则提示信息

    class Config:
        extra = "allow"


class _AttrDict:
    """将字典递归转换为支持属性访问的对象，用于自动加载 YAML 配置中的额外字段。

    对于 config.yaml 中未在 Settings.__fields__ 里定义的顶层节点，
    会自动转换为 _AttrDict 实例，使得 settings.xxx.yyy 形式的访问可用。
    """

    def __init__(self, data: dict):
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, _AttrDict(value))
            elif isinstance(value, list):
                setattr(self, key, [
                    _AttrDict(item) if isinstance(item, dict) else item
                    for item in value
                ])
            else:
                setattr(self, key, value)

    def __repr__(self):
        return f"_AttrDict({self.__dict__})"


def _substitute_env_vars(value: Any) -> Any:
    """递归替换环境变量占位符，支持 ${VAR_NAME} 和 ${VAR_NAME:-default} 语法"""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_expr = value[2:-1]
            # 处理 ${VAR:-default} 语法
            if ":-" in env_expr:
                env_var, default_val = env_expr.split(":-", 1)
                return os.getenv(env_var, default_val)
            return os.getenv(env_expr, "")
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
        provider = os.getenv("LLM_PROVIDER")
        yaml_config.setdefault("llm", {})["provider"] = provider

        # 统一环境变量：API_KEYS、BASE_URL、MODEL_CODE
        # 根据 LLM_PROVIDER 的值，写入对应 provider 的配置
        provider_cfg = yaml_config.setdefault("llm", {}).setdefault(provider, {})
        if os.getenv("API_KEYS"):
            provider_cfg["api_keys"] = os.getenv("API_KEYS")
        if os.getenv("BASE_URL"):
            provider_cfg["base_url"] = os.getenv("BASE_URL")
        if os.getenv("MODEL_CODE"):
            provider_cfg["model"] = os.getenv("MODEL_CODE")

    if os.getenv("WECOM_CORP_ID"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["corp_id"] = os.getenv("WECOM_CORP_ID")
    
    if os.getenv("WECOM_AGENT_ID"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["agent_id"] = os.getenv("WECOM_AGENT_ID")
    
    if os.getenv("WECOM_SECRET"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["secret"] = os.getenv("WECOM_SECRET")

    if os.getenv("WECOM_TOKEN"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["token"] = os.getenv("WECOM_TOKEN")

    if os.getenv("WECOM_ENCODING_AES_KEY"):
        yaml_config.setdefault("channels", {}).setdefault("wecom", {})["encoding_aes_key"] = os.getenv("WECOM_ENCODING_AES_KEY")

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

    # 文件上传大小限制（.env 中单位为 MB，代码内部转换为字节）
    if os.getenv("STORAGE_MAX_KNOWLEDGE_FILE_SIZE"):
        try:
            yaml_config.setdefault("storage", {})["max_knowledge_file_size"] = int(os.getenv("STORAGE_MAX_KNOWLEDGE_FILE_SIZE")) * 1024 * 1024
        except ValueError:
            pass
    if os.getenv("STORAGE_MAX_GENERAL_FILE_SIZE"):
        try:
            yaml_config.setdefault("storage", {})["max_general_file_size"] = int(os.getenv("STORAGE_MAX_GENERAL_FILE_SIZE")) * 1024 * 1024
        except ValueError:
            pass

    # 搜索工具配置
    if os.getenv("TAVILY_API_KEY"):
        yaml_config.setdefault("tools", {}).setdefault("search", {})["tavily_api_key"] = os.getenv("TAVILY_API_KEY")

    # 认证相关配置（从环境变量加载）
    if os.getenv("QBTOKEN"):
        yaml_config["qb_token"] = os.getenv("QBTOKEN")
    if os.getenv("QBTOKEN_HASH"):
        yaml_config["qb_token_hash"] = os.getenv("QBTOKEN_HASH")
    if os.getenv("MAX_CONCURRENT_TOKENS"):
        try:
            yaml_config["max_concurrent_tokens"] = int(os.getenv("MAX_CONCURRENT_TOKENS"))
        except ValueError:
            pass
    if os.getenv("PASSWORD_RULE"):
        yaml_config["password_rule"] = os.getenv("PASSWORD_RULE")
    if os.getenv("PASSWORD_MSG"):
        yaml_config["password_msg"] = os.getenv("PASSWORD_MSG")

    # 短信配置
    if os.getenv("SMS_CHANNEL"):
        yaml_config.setdefault("sms", {})["channel"] = os.getenv("SMS_CHANNEL")
    if os.getenv("SMS_USERNAME"):
        yaml_config.setdefault("sms", {})["username"] = os.getenv("SMS_USERNAME")
    if os.getenv("SMS_PASSWORD"):
        yaml_config.setdefault("sms", {})["password"] = os.getenv("SMS_PASSWORD")
    if os.getenv("SMS_SIGNATURE"):
        yaml_config.setdefault("sms", {})["signature"] = os.getenv("SMS_SIGNATURE")
    if os.getenv("SMS_TEMPLATE_YZM"):
        yaml_config.setdefault("sms", {})["template_yzm"] = os.getenv("SMS_TEMPLATE_YZM")
    if os.getenv("QBSMSCODE"):
        yaml_config.setdefault("sms", {})["qb_sms_code"] = os.getenv("QBSMSCODE")

    s = Settings(**yaml_config)

    # 将 YAML 中未在 Settings.__fields__ 里定义的嵌套字典自动转换为属性可访问对象
    # 这样 config.yaml 新增的配置节（如 admin、scheduler 等）无需在 Settings 中声明，
    # 即可通过 settings.xxx.yyy 形式直接访问
    defined_fields = set(getattr(Settings, 'model_fields', {}).keys())
    for key, value in yaml_config.items():
        if key not in defined_fields and isinstance(value, dict):
            setattr(s, key, _AttrDict(value))

    return s


# 全局配置实例
settings = create_settings()
