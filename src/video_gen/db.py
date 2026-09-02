"""视频生成建表（幂等）。

两张表：gen_sessions（一次抽卡会话）+ gen_cards（抽出的视频卡片）。
设计依据：docs/system/content-production/mvp-design.md §3。
"""
from loguru import logger


def init_video_gen_tables(conn) -> None:
    """幂等建 video_gen 表。照抄 social_media.db 的 CREATE TABLE IF NOT EXISTS 模式。"""
    cursor = conn.cursor()
    # gen_sessions：一次抽卡会话（选场景+上传1张产品图+填文案+生成N条）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_sessions (
            session_id        TEXT PRIMARY KEY,          -- sess_<12hex>
            tenant_id         TEXT,                       -- 租户（后台任务无租户上下文时可空）
            user_id           TEXT,                       -- 发起用户
            scene_id          TEXT NOT NULL,             -- 场景预设 id（硬编码，如 product_showcase）
            product_image_fid TEXT NOT NULL,             -- 产品图 file_id（r2v 作 reference_image，锁定产品外观防变形）
            model_image_fid   TEXT,                       -- 模特图 file_id（可选，作 first_frame 控制起始画面；空则用产品图）
            copywriting       TEXT NOT NULL,             -- 运营填写的文案
            expanded_prompt   TEXT,                      -- 提示词引擎扩展后的完整 prompt（可微调）
            card_count        INT NOT NULL DEFAULT 3,    -- 本次抽卡条数（1-3）
            enable_ai_label   BOOLEAN NOT NULL DEFAULT TRUE,  -- 是否烧录 AI 内容角标（合规默认开）
            duration_sec      INT NOT NULL DEFAULT 5,    -- 视频时长（5/10/15 秒，万相 r2v 上限 15s）
            resolution        TEXT NOT NULL DEFAULT '720P',  -- 分辨率（720P/1080P，万相 r2v 不支持 480P）
            ratio             TEXT NOT NULL DEFAULT '9:16',  -- 视频画面比例（9:16 竖版 / 16:9 横版 / 1:1 / 4:3 / 3:4 / 21:9）
            status            TEXT NOT NULL DEFAULT 'generating',  -- generating/done/failed
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 幂等加列：已建表（无 model_image_fid）补列，新表上面 CREATE 已含
    cursor.execute("""
        ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS model_image_fid TEXT
    """)
    # 幂等加列：AI 角标开关 + 视频时长（与 deploy/init-postgres.sql 保持一致，db_update.sql 也会兜底）
    cursor.execute("""
        ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS enable_ai_label BOOLEAN NOT NULL DEFAULT TRUE
    """)
    cursor.execute("""
        ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS duration_sec INT NOT NULL DEFAULT 5
    """)
    cursor.execute("""
        ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS resolution TEXT NOT NULL DEFAULT '720P'
    """)
    # 兼容历史数据：曾用 480P 默认值，万相 r2v 不支持，统一回填为 720P
    cursor.execute("UPDATE gen_sessions SET resolution = '720P' WHERE resolution = '480P'")
    # 幂等加列：视频画面比例（横版/竖版）
    cursor.execute("""
        ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS ratio TEXT NOT NULL DEFAULT '9:16'
    """)
    # gen_cards：抽出来的视频卡片（每条对应一次万相提交）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gen_cards (
            card_id           TEXT PRIMARY KEY,          -- card_<12hex>
            tenant_id         TEXT,
            session_id        TEXT NOT NULL,             -- 逻辑外键 → gen_sessions
            variant_idx       INT NOT NULL,              -- 第几条（0-based）
            seed              BIGINT,                    -- 万相随机种子（差异化来源）
            variant_prompt    TEXT,                      -- 本条差异化后的 prompt
            provider_task_id  TEXT,                      -- 万相返回的 task_id
            provider_status   TEXT,                      -- PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN
            output_fid        TEXT,                      -- 成片 file_id（成功后回填）
            output_url        TEXT,                      -- 万相返回的临时 video_url（24h 有效，下载后可清）
            output_duration   INT,                       -- 成片时长（秒）
            kept              BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否被运营留用
            parent_card_id    TEXT,                      -- 精修溯源（从哪张 card 重新生成）
            error_msg         TEXT,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gen_cards_session
        ON gen_cards(session_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gen_cards_polling
        ON gen_cards(provider_status)
    """)
    logger.info("video_gen 表已就绪 (gen_sessions, gen_cards)")
