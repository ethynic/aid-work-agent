import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runCliOperation } from '../render.js'
import { runWecomOperation } from '../../operations/context.js'
import { CodedOperationError } from '../../operations/types.js'
import { runPowerShellDriver } from '../../platform/powershell.js'
import { artifactDir } from '../../platform/environment.js'
import { probeCurrentAccount } from '../../platform/localdb/identity.js'
import { withSendSnapshot } from '../../platform/localdb/sendVerification.js'

const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/point-calibrate.ps1', import.meta.url))

export async function calibrateCommand(opts: { json?: boolean }): Promise<number> {
  const operation = {
    name: 'wecom_calibrate',
    execute: (_args: Record<string, never>, ctx: Parameters<typeof runWecomOperation>[1]) =>
      runWecomOperation('write', ctx, () => null, async (opCtx, tracker) => {
        const current = await probeCurrentAccount(opCtx)
        let selfName = ''
        await withSendSnapshot(current, opCtx, {}, 'send-baseline', async query => {
          const meta = await query(['--task', 'context', '--account', current.account])
          if (meta.self_user_id !== current.account || typeof meta.self_name !== 'string' || !meta.self_name.trim()) {
            throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本地库本人显示名未确认，未执行坐标标定')
          }
          selfName = meta.self_name as string
        })
        const root = artifactDir()
        if (!root) throw new CodedOperationError('INTERNAL_ERROR', '无法定位标定记录目录', 'none')
        const dir = join(root, `calibrate-${Date.now()}-${process.pid}`)
        mkdirSync(dir, { recursive: true })
        const data = await runPowerShellDriver({ script: DRIVER_PATH,
          args: ['-SelfName', selfName, '-ArtifactDir', dir], signal: opCtx.signal })
        tracker.completed = 1
        return { message: '企微标题与输入区双点标定完成', data }
      }),
  }
  return runCliOperation(operation, {}, { exclusive: true, json: opts.json })
}
