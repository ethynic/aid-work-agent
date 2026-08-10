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
    # 日报/周报/月报生成专用小模型（独立于主链路 model，降低成本）
    # 未配置（None / 空字符串）时，调用方应 fallback 到 model
    # 详见 docs/research/ai-agent-experience-daily-report-research.md §4.7.6
    report_model: Optional[str] = None
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

    def get_report_model(self) -> str:
        """获取报告专用模型，未配置时 fallback 到主模型 model"""
        return self.report_model or self.model


class CircuitBreakerConfig(BaseModel):
    """熔断器配置"""
    failure_threshold: int = 3    # 连续失败 N 次触发熔断
    recovery_timeout: float = 60  # 熔断恢复时间（秒）


class RetryConfig(BaseModel):
    """重试配置"""
    max_retries: int = 2          # 同一 provider 内重试次数（含首次调用）
    retry_delay: float = 1.0      # 重试间隔（秒），指数退避基数


class FailoverAlertConfig(BaseModel):
    """Failover 告警配置"""
    enabled: bool = True
    channel: str = "webhook"      # 告警渠道：webhook / email
    cooldown: int = 300           # 同一 provider 告警冷却时间（秒）


class FailoverConfig(BaseModel):
    """Failover 配置"""
    enabled: bool = False
    providers: List[str] = Field(default_factory=list)  # failover 优先级链
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    alert: FailoverAlertConfig = Field(default_factory=FailoverAlertConfig)


class WanxConfig(BaseModel):
    """通义万相（图生视频）配置。

    万相与 Qwen 同属阿里云百炼，同一个 API key 通用，故 api_key 默认回退到
    settings.llm.qwen.api_keys[0]。MVP 用旧域名 dashscope.aliyuncs.com，无需 workspace_id。
    详见 docs/system/content-production/mvp-design.md §4。
    """
    api_key: str = ""                          # 优先读 WANX_API_KEY；空则回退 qwen.api_keys[0]
    model: str = "wan2.7-r2v"                    # r2v（reference-to-video）；用稳定别名，百炼自动指向最新小版本，与 token_cost_prices.model_name 严格相等
    poll_interval_seconds: int = 30            # 后台轮询间隔
    task_max_age_hours: int = 24               # 万相 task_id 查询有效期


class MinimaxConfig(BaseModel):
    """MiniMax-H3（视频生成）配置。

    MiniMax API 文档见 ext/Minimax-H3.md。与万相独立 API key，不与 Qwen 共用。
    """
    api_key: str = ""                          # 必须读 MINIMAX_API_KEY
    model: str = "MiniMax-H3"
    base_url: str = "https://api.minimaxi.com"
    poll_interval_seconds: int = 30
    task_max_age_hours: int = 168              # 7 天查询窗口


class VideoGenConfig(BaseModel):
    """视频生成配置（统一管理多 provider）。

    provider 切换：通过 VIDEO_GEN_PROVIDER=wanx|minimax 切换，同时只一个 provider 生效。
    """
    provider: str = "wanx"
    wanx: WanxConfig = Field(default_factory=WanxConfig)
    minimax: MinimaxConfig = Field(default_factory=MinimaxConfig)
    poll_interval_seconds: int = 30
    task_max_age_hours: int = 24               # 全局兜底（provider 未声明时用）


class LLMConfig(BaseModel):
    """LLM配置"""
    provider: str = "zhipu"
    qwen: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    zhipu: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    deepseek: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    failover: FailoverConfig = Field(default_factory=FailoverConfig)
    # 注：wanx 已迁移到 settings.video_gen.wanx，请改用 settings.video_gen.wanx.*


class StorageConfig(BaseModel):
    """存储配置（所有业务数据集中于此，便于备份和迁移）"""
    base_dir: str = "storage"  # 存储根目录
    uploads_dir: str = "storage/uploads"  # 上传文件根目录
    memories_dir: str = "storage/memories"  # 长期记忆文件目录
    # 注意：代码内部使用字节单位，.env 中配置使用 MB 单位
    max_knowledge_file_size: int = 50 * 1024 * 1024  # 知识库文件大小限制（默认 50MB），支持 .env 覆盖
    max_general_file_size: int = 20 * 1024 * 1024  # 通用上传文件大小限制（默认 20MB），支持 .env 覆盖


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
    task_timeout: float = 300.0  # 单次工具总超时（秒）
    command_timeout: float = 30.0  # worker 单命令超时（秒）
    owner_lease_ttl: int = 30  # owner lease TTL（秒）
    owner_renew_interval: float = 10.0  # owner lease 续租间隔（秒）
    reaper_interval: float = 15.0  # 过期 run 回收周期（秒）
    viewport_width: int = 1920  # 视口宽度
    viewport_height: int = 1080  # 视口高度


class MapsToolConfig(BaseModel):
    """地图工具配置"""
    amap_api_key: str = ""


class ASRToolConfig(BaseModel):
    """语音转文字工具配置（阿里云智能语音交互）"""
    provider: str = "aliyun"
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_appkey: str = ""  # 智能语音交互项目 Appkey
    endpoint: str = "nls-gateway-cn-shanghai.aliyuncs.com"  # 阿里云 NLS 一句话识别官方域名


class ToolsConfig(BaseModel):
    """工具配置"""
    email: EmailToolConfig = Field(default_factory=EmailToolConfig)
    ocr: OCRToolConfig = Field(default_factory=OCRToolConfig)
    search: SearchToolConfig = Field(default_factory=SearchToolConfig)
    browser: BrowserToolConfig = Field(default_factory=BrowserToolConfig)
    maps: MapsToolConfig = Field(default_factory=MapsToolConfig)
    asr: ASRToolConfig = Field(default_factory=ASRToolConfig)


class ShortTermMemoryConfig(BaseModel):
    """短期记忆配置"""
    max_messages: int = 200
    ttl: int = 3600


class SummaryLLMConfig(BaseModel):
    """摘要 LLM 配置（独立于主 LLM，可走便宜模型）"""
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    timeout_sec: int = 30


class MidTermMemoryConfig(BaseModel):
    """中期记忆配置（会话内上下文压缩）

    详见 docs/infrastructure/memory/context_compression_design.md
    """
    enabled: bool = True
    # 触发条件（双阈值，任一满足即触发）
    token_threshold_ratio: float = 0.7       # token 主阈值：占模型上下文上限的比例
    message_count_threshold: int = 200       # 消息数兜底阈值（含工具消息）；缓存=0 时靠它兜底极端长会话
    # 分段保留
    header_keep: int = 3                     # 头部保留消息数
    tail_keep: int = 30                      # 尾部保留消息数（按工具链边界对齐）
    # 摘要 LLM
    summary_max_tokens: int = 1500
    summary_llm_retry: int = 2               # 摘要 LLM 调用重试次数
    summary_llm: SummaryLLMConfig = Field(default_factory=SummaryLLMConfig)
    # 工具结果预处理
    large_tool_result_truncate_chars: int = 2000
    # 后台定时任务扫描（Phase 8 补漏机制，§2.5）
    background_scan_enabled: bool = False       # 默认关闭，部署时在 config.yaml 开启
    background_scan_interval_sec: int = 600     # 扫描周期，默认 10 分钟
    background_scan_batch_size: int = 50        # 单次扫描最多处理 session 数


class LongTermMemoryConfig(BaseModel):
    """长期记忆配置"""
    enabled: bool = False
    storage_dir: str = "storage/memory"    # 记忆文件根目录，实际文件在 storage/memory/{tenant_id}/ 下
    summary_cron: str = "0 2 * * *"        # 每日自动总结执行时间
    max_users_per_run: int = 50            # 单次总结最多处理用户数
    max_inject_tokens: int = 2000          # 注入上下文的最大 token 数


class MemoryConfig(BaseModel):
    """记忆配置"""
    short_term: ShortTermMemoryConfig = Field(default_factory=ShortTermMemoryConfig)
    mid_term: MidTermMemoryConfig = Field(default_factory=MidTermMemoryConfig)
    long_term: LongTermMemoryConfig = Field(default_factory=LongTermMemoryConfig)
    cleanup_interval: int = 300  # 过期会话清理间隔（秒）


class RedisConfig(BaseModel):
    """Redis 配置"""
    enabled: bool = False
    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    ssl: bool = False
    key_prefix: str = ""  # Key 前缀，多实例共享同一 Redis 时用于隔离


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
    # 对外可访问的基础 URL，用于构造文件下载链接等完整 URL
    # 生产环境应设置为实际域名，如 "https://your-domain.com"
    public_base_url: str = ""


class SaasConfig(BaseModel):
    """SaaS 多租户配置"""
    enabled: bool = False
    tenant_skills_dir: str = "storage/tenants"
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


class WeComWaitingIndicatorConfig(BaseModel):
    """企业微信等待提示配置"""
    enabled: bool = True
    delay_seconds: float = 5
    messages: List[str] = Field(default_factory=lambda: ["正在思考中..."])


class WeComConfig(BaseModel):
    """企业微信渠道配置"""
    waiting_indicator: WeComWaitingIndicatorConfig = Field(default_factory=WeComWaitingIndicatorConfig)


class BillingConfig(BaseModel):
    """积分计费配置（#37 租户积分充值与计费）

    - usage_factor: 用量系数，token 成本价 × 系数 = 积分用量（向上取整）
    - video_gen_usage_factor: 视频创作用量系数，视频秒数 × 单价 × 系数 = 积分用量（向上取整）
      视频创作智能体（video-agent）按秒计费专用，区别于主业务按 token 计费
    """
    usage_factor: int = 100
    video_gen_usage_factor: int = 33


class ClientConfig(BaseModel):
    """协会客户端配置（docs/tools/association-client-design.md）

    - credit_multiplier: 客户端积分膨胀系数，标准积分 × 此系数 = 客户端实扣（默认5倍）
    - llm_request_timeout: 客户端 LLM 代理请求超时（秒）
    """
    credit_multiplier: float = 5.0
    llm_request_timeout: int = 120


class Settings(BaseModel):
    """全局配置"""
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    saas: SaasConfig = Field(default_factory=SaasConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    sms: SmsConfig = Field(default_factory=SmsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    wecom: WeComConfig = Field(default_factory=WeComConfig)
    billing: BillingConfig = Field(default_factory=BillingConfig)
    video_gen: VideoGenConfig = Field(default_factory=VideoGenConfig)
    client: ClientConfig = Field(default_factory=ClientConfig)

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
            elif isinstance(value, str) and value.lower() == "false":
                setattr(self, key, False)
            elif isinstance(value, str) and value.lower() == "true":
                setattr(self, key, True)
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
    # 主 provider 由 LLM_PROVIDER 指定，各 provider 的密钥/模型/地址统一通过
    # 各自的独立环境变量配置（DEEPSEEK_*、QWEN_*、ZHIPU_*）
    if os.getenv("LLM_PROVIDER"):
        yaml_config.setdefault("llm", {})["provider"] = os.getenv("LLM_PROVIDER")

    # 各 provider 独立环境变量覆盖（主 provider、failover 备用 provider 或子智能体指定）
    # DeepSeek
    ds_cfg = yaml_config.setdefault("llm", {}).setdefault("deepseek", {})
    if os.getenv("DEEPSEEK_API_KEYS"):
        ds_cfg["api_keys"] = os.getenv("DEEPSEEK_API_KEYS")
    if os.getenv("DEEPSEEK_MODEL_CODE"):
        ds_cfg["model"] = os.getenv("DEEPSEEK_MODEL_CODE")
    if os.getenv("DEEPSEEK_BASE_URL"):
        ds_cfg["base_url"] = os.getenv("DEEPSEEK_BASE_URL")
    # 日报/周报/月报专用小模型（独立配置项，不影响主链路 DEEPSEEK_MODEL_CODE）
    if os.getenv("DEEPSEEK_REPORT_MODEL_CODE"):
        ds_cfg["report_model"] = os.getenv("DEEPSEEK_REPORT_MODEL_CODE")

    # Qwen
    qwen_cfg = yaml_config.setdefault("llm", {}).setdefault("qwen", {})
    if os.getenv("QWEN_API_KEYS"):
        qwen_cfg["api_keys"] = os.getenv("QWEN_API_KEYS")
    if os.getenv("QWEN_MODEL_CODE"):
        qwen_cfg["model"] = os.getenv("QWEN_MODEL_CODE")
    if os.getenv("QWEN_BASE_URL"):
        qwen_cfg["base_url"] = os.getenv("QWEN_BASE_URL")

    # Zhipu
    zhipu_cfg = yaml_config.setdefault("llm", {}).setdefault("zhipu", {})
    if os.getenv("ZHIPU_API_KEYS"):
        zhipu_cfg["api_keys"] = os.getenv("ZHIPU_API_KEYS")
    if os.getenv("ZHIPU_MODEL_CODE"):
        zhipu_cfg["model"] = os.getenv("ZHIPU_MODEL_CODE")
    if os.getenv("ZHIPU_BASE_URL"):
        zhipu_cfg["base_url"] = os.getenv("ZHIPU_BASE_URL")

    # 视频生成（多 provider：wanx / minimax）：保留 llm.wanx 兼容旧 yaml，但实际值迁移到 video_gen.wanx
    # 兼容桥接：先把 llm.wanx 复制到 video_gen.wanx（若 video_gen.wanx 未配置），再让 env 覆盖
    legacy_wanx = yaml_config.get("llm", {}).get("wanx") or {}
    vg_cfg = yaml_config.setdefault("video_gen", {})
    vg_wanx = vg_cfg.setdefault("wanx", {})
    for k in ("api_key", "model", "poll_interval_seconds", "task_max_age_hours"):
        if not vg_wanx.get(k) and legacy_wanx.get(k):
            vg_wanx[k] = legacy_wanx[k]
    # 兼容旧 env：WANX_API_KEY 写到 video_gen.wanx.api_key
    if os.getenv("WANX_API_KEY"):
        vg_wanx["api_key"] = os.getenv("WANX_API_KEY")

    # video_gen.provider / minimax 配置
    if os.getenv("VIDEO_GEN_PROVIDER"):
        vg_cfg["provider"] = os.getenv("VIDEO_GEN_PROVIDER")
    vg_minimax = vg_cfg.setdefault("minimax", {})
    if os.getenv("MINIMAX_API_KEY"):
        vg_minimax["api_key"] = os.getenv("MINIMAX_API_KEY")
    if os.getenv("MINIMAX_MODEL_CODE"):
        vg_minimax["model"] = os.getenv("MINIMAX_MODEL_CODE")
    if os.getenv("MINIMAX_BASE_URL"):
        vg_minimax["base_url"] = os.getenv("MINIMAX_BASE_URL")

    if os.getenv("DEBUG", "").lower() in ("true", "1", "yes"):
        yaml_config.setdefault("app", {})["debug"] = True
    if os.getenv("PUBLIC_BASE_URL"):
        yaml_config.setdefault("app", {})["public_base_url"] = os.getenv("PUBLIC_BASE_URL")

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

    # 语音转文字工具配置（阿里云 ASR）
    if os.getenv("ALIYUN_ASR_ACCESS_KEY_ID"):
        yaml_config.setdefault("tools", {}).setdefault("asr", {})["aliyun_access_key_id"] = os.getenv("ALIYUN_ASR_ACCESS_KEY_ID")
    if os.getenv("ALIYUN_ASR_ACCESS_KEY_SECRET"):
        yaml_config.setdefault("tools", {}).setdefault("asr", {})["aliyun_access_key_secret"] = os.getenv("ALIYUN_ASR_ACCESS_KEY_SECRET")
    if os.getenv("ALIYUN_ASR_APPKEY"):
        yaml_config.setdefault("tools", {}).setdefault("asr", {})["aliyun_appkey"] = os.getenv("ALIYUN_ASR_APPKEY")

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

    # Redis 配置：环境变量覆盖，并确保布尔值类型正确
    redis_cfg = yaml_config.setdefault("redis", {})
    if os.getenv("REDIS_ENABLED") is not None:
        redis_cfg["enabled"] = os.getenv("REDIS_ENABLED", "").lower() in ("true", "1", "yes")
    if os.getenv("REDIS_HOST") is not None:
        redis_cfg["host"] = os.getenv("REDIS_HOST")
    if os.getenv("REDIS_PORT") is not None:
        try:
            redis_cfg["port"] = int(os.getenv("REDIS_PORT"))
        except ValueError:
            pass
    if os.getenv("REDIS_PASSWORD") is not None:
        redis_cfg["password"] = os.getenv("REDIS_PASSWORD")
    if os.getenv("REDIS_DB") is not None:
        try:
            redis_cfg["db"] = int(os.getenv("REDIS_DB"))
        except ValueError:
            pass
    if os.getenv("REDIS_SSL") is not None:
        redis_cfg["ssl"] = os.getenv("REDIS_SSL", "").lower() in ("true", "1", "yes")
    if os.getenv("REDIS_KEY_PREFIX") is not None:
        redis_cfg["key_prefix"] = os.getenv("REDIS_KEY_PREFIX")
    # 修复 config.yaml 中 ${...} 替换后遗留的字符串布尔值
    for bool_key in ("enabled", "ssl"):
        if bool_key in redis_cfg and isinstance(redis_cfg[bool_key], str):
            redis_cfg[bool_key] = redis_cfg[bool_key].lower() in ("true", "1", "yes")

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

    # 企业微信等待提示配置：环境变量覆盖
    wecom_cfg = yaml_config.setdefault("wecom", {}).setdefault("waiting_indicator", {})
    if os.getenv("WECOM_WAITING_INDICATOR_ENABLED") is not None:
        wecom_cfg["enabled"] = os.getenv("WECOM_WAITING_INDICATOR_ENABLED", "").lower() in ("true", "1", "yes")
    if os.getenv("WECOM_WAITING_INDICATOR_DELAY_SECONDS") is not None:
        try:
            wecom_cfg["delay_seconds"] = float(os.getenv("WECOM_WAITING_INDICATOR_DELAY_SECONDS"))
        except ValueError:
            pass

    # 积分计费配置：环境变量覆盖
    billing_cfg = yaml_config.setdefault("billing", {})
    if os.getenv("USAGE_FACTOR") is not None:
        try:
            billing_cfg["usage_factor"] = int(os.getenv("USAGE_FACTOR"))
        except ValueError:
            pass
    if os.getenv("VIDEO_GEN_USAGE_FACTOR") is not None:
        try:
            billing_cfg["video_gen_usage_factor"] = int(os.getenv("VIDEO_GEN_USAGE_FACTOR"))
        except ValueError:
            pass

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


def get_embedding_api_key() -> str:
    """
    获取 Embedding API Key。

    优先级：
    1. 环境变量 EMBEDDING_API_KEY（专用 embedding key，用于 LLM 提供者不支持 embedding 的场景）
    2. settings.llm.qwen 的 api_keys（向后兼容，原有逻辑）
    3. 报错提示用户配置

    DeepSeek 等提供者不支持 Embedding API，此时需通过 EMBEDDING_API_KEY 单独指定
    embedding 服务的 API Key（如通义千问的 DashScope API Key）。

    Returns:
        Embedding API Key 字符串
    """
    # 优先级 1：专用 EMBEDDING_API_KEY 环境变量
    key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if key:
        return key

    # 优先级 2：向后兼容，尝试从 qwen provider 获取
    qwen_keys = settings.llm.qwen.get_effective_keys()
    if qwen_keys:
        return qwen_keys[0]

    # 没有可用的 embedding key
    raise ValueError(
        "Embedding API Key 未配置。"
        "请设置环境变量 EMBEDDING_API_KEY（推荐，用于当 LLM 提供者不支持 Embedding 时），"
        "或确保 LLM_PROVIDER 对应的 QWEN API Key 已正确配置。"
        "例如 DeepSeek 不支持 Embedding API，需要单独配置通义千问的 API Key。"
    )
