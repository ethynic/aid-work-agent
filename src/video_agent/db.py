"""视频创作智能体建表（幂等）。

两张表（Phase 1）：
- asset_library: 跨会话的图片/视频素材库（视频创作聊天自动入库 + 用户手动上传）
- prompt_library: 提示词库（留用 / 黑名单 / 模版 三类合并一表）

视频库复用 work_outcomes 表（outcome_type='file' + subagent_id='video-agent'），不新建。
subagent_definitions 扩展两列（chat_toolbar JSONB + upload_accept TEXT）由 deploy/db_update.sql 兜底，
本模块仅在启动时幂等 ALTER，保证开发环境未跑迁移也能用。

设计依据：docs/plans/plan-video-agent-phase1.md §1.1 / §1.2 / §1.5
"""
from loguru import logger


def init_video_agent_tables(conn) -> None:
    """幂等建 video_agent 表 + 兜底 subagent_definitions 扩展列。"""
    cursor = conn.cursor()

    # asset_library：素材库
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asset_library (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            file_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes BIGINT NOT NULL,
            source TEXT NOT NULL,
            scene TEXT,
            width INT,
            height INT,
            portrait_authorized BOOLEAN DEFAULT FALSE,
            portrait_auth_expire_at TIMESTAMP,
            portrait_auth_scope TEXT,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_asset_library_tenant
        ON asset_library(tenant_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_asset_library_source
        ON asset_library(source)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_asset_library_tenant_scene
        ON asset_library(tenant_id, scene)
    """)

    # prompt_library：提示词库（kept / blacklist / template 三类合并一表）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prompt_library (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            category TEXT NOT NULL,
            business_prompt TEXT NOT NULL,
            craft_prompt TEXT NOT NULL,
            model_params JSONB,
            industry_tag TEXT,
            scene_tag TEXT,
            source_video_file_id TEXT,
            source_chat_session_id TEXT,
            dislike_reason TEXT,
            promoted_from_kept_id INT,
            promoted_by_user_id TEXT,
            promoted_at TIMESTAMP,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_category
        ON prompt_library(tenant_id, category)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_scene
        ON prompt_library(tenant_id, scene_tag)
    """)

    # subagent_definitions 扩展两列（声明式 UI 配置）
    # deploy/db_update.sql 也会兜底，这里再保险一次，开发环境未跑迁移也能用
    cursor.execute("""
        ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS chat_toolbar JSONB DEFAULT '[]'::jsonb
    """)
    cursor.execute("""
        ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS upload_accept TEXT
    """)

    # chat_sessions 扩展 metadata JSONB 列（存 video_gen_params 等会话级元数据）
    cursor.execute("""
        ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS metadata JSONB
    """)

    # token_cost_prices 扩展 price_per_second 列（视频模型按秒计费）
    cursor.execute("""
        ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS price_per_second NUMERIC(10,4)
    """)

    logger.info("video_agent 表已就绪 (asset_library, prompt_library, subagent_definitions 扩展列)")
