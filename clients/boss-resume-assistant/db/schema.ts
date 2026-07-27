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

export const MIGRATIONS: readonly Migration[] = [
  { version: 1, description: 'initial schema (Phase 1)', sql: MIGRATION_V1 },
] as const

/** 当前 schema 应到达的版本号 */
export const TARGET_SCHEMA_VERSION = MIGRATIONS[MIGRATIONS.length - 1]!.version
