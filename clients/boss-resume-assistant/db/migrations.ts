/**
 * 迁移执行器。维护 schema_migrations 版本表，按版本号升序应用未执行迁移。
 * 单事务原子：中途失败回滚，不留半套表。崩溃恢复：重启重新比对即可。
 */
import type { Database as BetterSqliteDatabase } from 'better-sqlite3'
import { MIGRATIONS } from './schema.js'

interface AppliedRow {
  version: number
}

/** 读取已应用的版本号集合 */
export function getAppliedVersions(db: BetterSqliteDatabase): Set<number> {
  // schema_migrations 表由 v1 迁移创建；若表不存在说明尚未应用任何迁移
  const tableExists = db
    .prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'",
    )
    .get() as { name: string } | undefined

  if (!tableExists) return new Set()

  const rows = db.prepare('SELECT version FROM schema_migrations').all() as AppliedRow[]
  return new Set(rows.map((r) => r.version))
}

/** 应用所有未执行的迁移（单事务） */
export function runMigrations(db: BetterSqliteDatabase): { applied: number[] } {
  const applied = getAppliedVersions(db)
  const toApply = MIGRATIONS.filter((m) => !applied.has(m.version))
  if (toApply.length === 0) return { applied: [] }

  const tx = db.transaction(() => {
    for (const migration of toApply) {
      db.exec(migration.sql)
      db.prepare('INSERT INTO schema_migrations (version) VALUES (?)').run(migration.version)
    }
  })
  tx() // 失败自动回滚

  return { applied: toApply.map((m) => m.version) }
}

/** 当前已应用的最高版本号（0 表示无） */
export function getCurrentVersion(db: BetterSqliteDatabase): number {
  const applied = getAppliedVersions(db)
  if (applied.size === 0) return 0
  return Math.max(...applied)
}
