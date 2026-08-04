/**
 * 数据库 schema 定义。对应设计文档 §12.1 核心表 + 计划 §4.2。
 * 明文存储（Phase 1 决策，加密降级到后续阶段）。
 * 所有业务表含 created_at（数据库默认值）。
 *
 * 表按迁移版本号分组：v1 为初始 schema。后续新增字段/表用新版本号的迁移。
 */

/** 迁移版本 1：初始 schema（Phase 1） */
export const MIGRATION_V1 = `
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  knowledge_version TEXT,
  rule_version TEXT,
  hard_rules TEXT,
  preferences TEXT,
  greeting_template TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chrome_profile_path_hash TEXT,
  cdp_port_hash TEXT,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  status TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  fingerprint TEXT NOT NULL UNIQUE,
  list_summary TEXT,
  first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_candidates_job ON candidates(job_id);

CREATE TABLE IF NOT EXISTS resume_views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  candidate_id INTEGER,
  viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  source TEXT,
  completeness TEXT,
  summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_resume_views_candidate ON resume_views(candidate_id);
CREATE INDEX IF NOT EXISTS idx_resume_views_job ON resume_views(job_id);

CREATE TABLE IF NOT EXISTS captures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resume_view_id INTEGER,
  kind TEXT,
  path TEXT,
  width INTEGER,
  height INTEGER,
  integrity_status TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_captures_view ON captures(resume_view_id);

CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER,
  conclusion TEXT,
  reason TEXT,
  evidence TEXT,
  model TEXT,
  prompt_version TEXT,
  input_hash TEXT,
  duration_ms INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_evaluations_candidate ON evaluations(candidate_id);

CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER,
  action TEXT,
  reason TEXT,
  status TEXT,
  before_screenshot_path TEXT,
  after_screenshot_path TEXT,
  error TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_actions_candidate ON actions(candidate_id);

CREATE TABLE IF NOT EXISTS cdp_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id_hash TEXT,
  method TEXT,
  params_summary TEXT,
  duration_ms INTEGER,
  status TEXT,
  at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cdp_audit_method ON cdp_audit(method);
CREATE INDEX IF NOT EXISTS idx_cdp_audit_session ON cdp_audit(session_id_hash);
`

/**
 * 迁移列表：按版本号升序。每项 { version, sql }。
 * 应用时跳过已记录在 schema_migrations 的版本。
 * 新增迁移追加到末尾，版本号严格递增。
 */
export interface Migration {
  version: number
  description: string
  sql: string
}

/**
 * 迁移版本 2：actions 表补幂等字段（Phase 7，设计文档 §10/§11）。
 * - unique_key：幂等唯一键（candidate_fingerprint|action_type），防重复打招呼
 * - session_id：所属会话，用于会话级动作上限统计
 * - sent_at / confirmed_at：状态机 SENT / CONFIRMED 时间戳，重启恢复用
 */
export const MIGRATION_V2 = `
ALTER TABLE actions ADD COLUMN unique_key TEXT;
ALTER TABLE actions ADD COLUMN session_id INTEGER;
ALTER TABLE actions ADD COLUMN sent_at TIMESTAMP;
ALTER TABLE actions ADD COLUMN confirmed_at TIMESTAMP;
CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_unique_key ON actions(unique_key);
CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id);
`

/**
 * 迁移版本 3：Phase 8 编排与界面支撑。
 * - jobs：动作上限（会话/日）列，界面上可配置；排除项合并存 hard_rules JSON，不单独加列
 * - resume_views / evaluations：session_id 列，用于会话级统计与恢复
 * - review_overrides：人工复核改判记录（原结论+改判结论+理由，原评估不可变）
 */
export const MIGRATION_V3 = `
ALTER TABLE jobs ADD COLUMN action_limit_session INTEGER;
ALTER TABLE jobs ADD COLUMN action_limit_day INTEGER;
ALTER TABLE resume_views ADD COLUMN session_id INTEGER;
ALTER TABLE evaluations ADD COLUMN session_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_resume_views_session ON resume_views(session_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_session ON evaluations(session_id);

CREATE TABLE IF NOT EXISTS review_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evaluation_id INTEGER NOT NULL,
  candidate_id INTEGER,
  original_conclusion TEXT NOT NULL,
  override_conclusion TEXT NOT NULL,
  override_reason TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_review_overrides_evaluation ON review_overrides(evaluation_id);
`

/**
 * 迁移版本 4：resume_views 补 OCR markdown 列（Phase 9 CLI 复核报告）。
 * CLI 静态复核报告需要展示 OCR 归一化 markdown 原文；
 * 此前 markdown 只在内存中供筛选使用，不落库。
 */
export const MIGRATION_V4 = `
ALTER TABLE resume_views ADD COLUMN ocr_markdown TEXT;
`

export const MIGRATIONS: readonly Migration[] = [
  { version: 1, description: 'initial schema (Phase 1)', sql: MIGRATION_V1 },
  { version: 2, description: 'actions idempotency fields (Phase 7)', sql: MIGRATION_V2 },
  { version: 3, description: 'session stats + review overrides (Phase 8)', sql: MIGRATION_V3 },
  { version: 4, description: 'resume_views.ocr_markdown for CLI review report (Phase 9)', sql: MIGRATION_V4 },
] as const

/** 当前 schema 应到达的版本号 */
export const TARGET_SCHEMA_VERSION = MIGRATIONS[MIGRATIONS.length - 1]!.version
