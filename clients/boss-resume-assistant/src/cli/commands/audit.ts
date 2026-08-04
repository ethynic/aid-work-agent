/**
 * CLI 子命令 audit：终端表格输出最近动作 + CDP 审计摘要。
 * 简单实现：最近 N 条动作明细 + CDP 按方法×状态计数。
 */
import { getClient } from '../../../db/client.js'
import { AuditStore, type ActionLogRow, type CdpAuditRow } from '../../main/storage/auditStore.js'
import * as rt from '../cliRuntime.js'

/** 动作日志行 → 终端行（纯函数，可单测） */
export function renderActionLine(row: ActionLogRow): string {
  const name = row.candidateName ?? (row.fingerprint ? row.fingerprint.slice(0, 8) : '-')
  const err = row.error ? `｜${row.error.slice(0, 40)}` : ''
  return `${row.createdAt}\t${name}\t${row.action}\t${row.status}${err}`
}

/** CDP 审计 → 方法×状态计数表（纯函数，可单测） */
export function summarizeCdpAudit(rows: CdpAuditRow[]): Array<{ method: string; status: string; count: number }> {
  const counter = new Map<string, number>()
  for (const r of rows) {
    const key = `${r.method}	${r.status}`
    counter.set(key, (counter.get(key) ?? 0) + 1)
  }
  return [...counter.entries()]
    .map(([key, count]) => {
      const [method, status] = key.split('	') as [string, string]
      return { method, status, count }
    })
    .sort((a, b) => b.count - a.count)
}

export function auditCommand(opts: { dataDir: string; limit: number }): number {
  rt.openCliDb({ dataDir: opts.dataDir })
  try {
    const store = new AuditStore(getClient())

    const actions = store.queryActions({ limit: opts.limit })
    console.log(`最近动作（${actions.length} 条）：`)
    console.log('时间\t候选人\t动作\t状态')
    for (const row of actions) console.log(renderActionLine(row))

    const cdpRows = store.queryCdpAudit({ limit: 1000 })
    console.log(`\nCDP 审计摘要（最近 ${cdpRows.length} 条，按方法×状态计数）：`)
    console.log('方法\t状态\t次数')
    for (const s of summarizeCdpAudit(cdpRows)) {
      console.log(`${s.method}\t${s.status}\t${s.count}`)
    }
    return 0
  } finally {
    rt.closeQuietly()
  }
}
