/**
 * CLI 子命令 export：复用 export/exporter 的查询与 CSV/JSON 构建。
 * 默认输出 ./exports/evaluations-{时间戳}.{csv|json}，--out 可指定文件路径。
 */
import fs from 'node:fs'
import path from 'node:path'
import { getClient } from '../../../db/client.js'
import { queryExportRows, buildCsv, buildJson } from '../../main/export/exporter.js'
import * as rt from '../cliRuntime.js'

export function exportCommand(opts: { dataDir: string; format: 'csv' | 'json'; out?: string; exportsDir: string }): number {
  rt.openCliDb({ dataDir: opts.dataDir })
  try {
    const rows = queryExportRows(getClient())
    const content = opts.format === 'csv' ? buildCsv(rows) : buildJson(rows)
    let outPath: string
    if (opts.out) {
      outPath = path.resolve(opts.out)
    } else {
      fs.mkdirSync(opts.exportsDir, { recursive: true })
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      outPath = path.resolve(path.join(opts.exportsDir, `evaluations-${stamp}.${opts.format}`))
    }
    fs.mkdirSync(path.dirname(outPath), { recursive: true })
    fs.writeFileSync(outPath, content, 'utf8')
    console.log(`导出完成（${rows.length} 行，${opts.format}）：${outPath}`)
    return 0
  } finally {
    rt.closeQuietly()
  }
}
