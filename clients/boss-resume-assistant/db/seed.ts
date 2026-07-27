/**
 * 开发期种子数据。仅用于本地调试，生产打包不执行。
 * 通过环境变量 BOSS_RESUME_SEED=1 在启动时触发。
 */
import type { Database as BetterSqliteDatabaseType } from 'better-sqlite3'

export function seedDevData(db: BetterSqliteDatabaseType): void {
  const existing = db.prepare('SELECT COUNT(*) AS c FROM jobs').get() as { c: number }
  if (existing.c > 0) return // 已有数据不重复播种

  db.prepare(
    `INSERT INTO jobs (name, knowledge_version, rule_version, hard_rules, preferences, greeting_template)
     VALUES (?, ?, ?, ?, ?, ?)`,
  ).run(
    '示例岗位-前端工程师',
    'v1',
    'v1',
    JSON.stringify({ city: ['北京'], minYears: 3, requiredSkills: ['Vue', 'TypeScript'] }),
    JSON.stringify({ preferredSkills: ['Electron'], excludeIndustries: [] }),
    '您好，看到您的简历，想和您聊聊这个前端岗位，方便吗？',
  )
}
