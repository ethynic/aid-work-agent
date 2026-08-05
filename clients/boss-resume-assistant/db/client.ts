/**
 * SQLite 客户端单例（better-sqlite3）。
 * - WAL 模式 + foreign_keys + synchronous=NORMAL
 * - 启动时自动跑迁移
 * - fail-loud：打开/迁移失败直接抛错，应用层应据此阻塞进入主界面
 *
 * db 模块不决定存储位置，dbPath 由调用方显式传入（CLI 走 initDatabaseAt），
 * 这样 tests/ 可用临时文件独立测试。
 */
import BetterSqliteDatabase from 'better-sqlite3'
import type { Database as BetterSqliteDatabaseType } from 'better-sqlite3'
import { runMigrations, getCurrentVersion } from './migrations.js'
import { TARGET_SCHEMA_VERSION } from './schema.js'

let instance: BetterSqliteDatabaseType | null = null
let currentDbPath = ''
let currentSchemaVersion = 0

/** 打开数据库（指定路径），配置 pragma 并跑迁移。fail-loud。 */
export function openDatabase(dbPath: string): BetterSqliteDatabaseType {
  const db = new BetterSqliteDatabase(dbPath)
  db.pragma('journal_mode = WAL')
  db.pragma('foreign_keys = ON')
  db.pragma('synchronous = NORMAL')

  runMigrations(db)
  const version = getCurrentVersion(db)
  if (version !== TARGET_SCHEMA_VERSION) {
    // 迁移后版本号仍不匹配，说明迁移定义有缺漏，fail-loud
    throw new Error(`schema version mismatch after migration: got ${version}, expected ${TARGET_SCHEMA_VERSION}`)
  }
  currentSchemaVersion = version
  return db
}

/** 初始化 DB 单例到指定路径（CLI 入口用）。fail-loud：失败抛错。 */
export function initDatabaseAt(dbPath: string): void {
  if (instance) return
  instance = openDatabase(dbPath)
  currentDbPath = dbPath
}

/** 获取单例。未初始化抛错（fail-loud）。 */
export function getClient(): BetterSqliteDatabaseType {
  if (!instance) {
    throw new Error('database not initialized; call initDatabaseAt() first')
  }
  return instance
}

/** 健康检查：SELECT 1，失败抛错 */
export async function healthCheck(): Promise<void> {
  const db = getClient()
  const row = db.prepare('SELECT 1 AS ok').get() as { ok: number } | undefined
  if (!row || row.ok !== 1) {
    throw new Error('health check failed: unexpected SELECT 1 result')
  }
}

export function getDbPath(): string {
  return currentDbPath
}

export function getSchemaVersion(): number {
  return currentSchemaVersion
}

/** 关闭单例（测试 / 应用退出用） */
export function closeDatabase(): void {
  if (instance) {
    instance.close()
    instance = null
    currentDbPath = ''
    currentSchemaVersion = 0
  }
}

/** 仅供测试：重置单例状态，用临时库重新打开 */
export function resetForTest(_dbPath: string): BetterSqliteDatabaseType {
  if (instance) {
    instance.close()
    instance = null
  }
  currentDbPath = _dbPath
  instance = openDatabase(_dbPath)
  return instance
}
