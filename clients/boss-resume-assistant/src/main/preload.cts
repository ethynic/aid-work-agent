/**
 * Preload (CommonJS)。仅用 contextBridge + ipcRenderer，sandbox 下无 Node API。
 * 暴露冻结对象 window.bossResume，渲染层不接触 ipcRenderer。
 *
 * 注意：preload 是 CJS，不能 require ESM 的 shared/ipc.js，因此 IPC 通道名和版本号
 * 在此处内联。shared/ipc.ts 是同一份契约的 TS 版本（主进程用），二者必须保持一致，
 * 由 tests/ipc.test.ts 断言。
 */
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { contextBridge, ipcRenderer } = require('electron')

const BRIDGE_VERSION = 2
const IPC = Object.freeze({
  DB_HEALTH: 'boss:db:health',
  APP_VERSION: 'boss:app:version',
  APP_RUNTIME: 'boss:app:runtime',
  CHROME_LAUNCH: 'boss:chrome:launch',
  SESSION_CONFIRM_LOGIN: 'boss:session:confirm-login',
  SESSION_START: 'boss:session:start',
  SESSION_PAUSE: 'boss:session:pause',
  SESSION_RESUME: 'boss:session:resume',
  SESSION_STOP: 'boss:session:stop',
  SESSION_STATUS: 'boss:session:status',
  SESSION_RESET: 'boss:session:reset',
  SESSION_EVENT: 'boss:session:event',
  JOB_LIST: 'boss:jobs:list',
  JOB_CREATE: 'boss:jobs:create',
  JOB_UPDATE: 'boss:jobs:update',
  JOB_DELETE: 'boss:jobs:delete',
  REVIEW_LIST: 'boss:review:list',
  REVIEW_OVERRIDE: 'boss:review:override',
  AUDIT_ACTIONS: 'boss:audit:actions',
  AUDIT_CDP: 'boss:audit:cdp',
  EXPORT_EVALUATIONS: 'boss:export:evaluations',
})

function readArgv(name: string): string {
  const prefix = `--boss-${name}=`
  const found = process.argv.find((a: string) => a.startsWith(prefix))
  return found ? found.slice(prefix.length) : ''
}

const expectedBridge = Number(readArgv('bridge-version'))
if (!Number.isFinite(expectedBridge) || expectedBridge !== BRIDGE_VERSION) {
  throw new Error(`unsupported bridge version: expected ${BRIDGE_VERSION}, got ${expectedBridge}`)
}

const db = Object.freeze({
  health: () => ipcRenderer.invoke(IPC.DB_HEALTH),
})

const appApi = Object.freeze({
  version: () => ipcRenderer.invoke(IPC.APP_VERSION),
  runtime: () => ipcRenderer.invoke(IPC.APP_RUNTIME),
})

const chrome = Object.freeze({
  launch: () => ipcRenderer.invoke(IPC.CHROME_LAUNCH),
})

const session = Object.freeze({
  confirmLogin: () => ipcRenderer.invoke(IPC.SESSION_CONFIRM_LOGIN),
  start: (jobId: number) => ipcRenderer.invoke(IPC.SESSION_START, jobId),
  pause: () => ipcRenderer.invoke(IPC.SESSION_PAUSE),
  resume: () => ipcRenderer.invoke(IPC.SESSION_RESUME),
  stop: () => ipcRenderer.invoke(IPC.SESSION_STOP),
  reset: () => ipcRenderer.invoke(IPC.SESSION_RESET),
  status: () => ipcRenderer.invoke(IPC.SESSION_STATUS),
  // 事件订阅：返回取消订阅函数；listener 不暴露 ipcRenderer event 对象
  onEvent: (cb: (payload: unknown) => void) => {
    const listener = (_event: unknown, payload: unknown) => cb(payload)
    ipcRenderer.on(IPC.SESSION_EVENT, listener)
    return () => {
      ipcRenderer.removeListener(IPC.SESSION_EVENT, listener)
    }
  },
})

const jobs = Object.freeze({
  list: () => ipcRenderer.invoke(IPC.JOB_LIST),
  create: (input: unknown) => ipcRenderer.invoke(IPC.JOB_CREATE, input),
  update: (id: number, input: unknown) => ipcRenderer.invoke(IPC.JOB_UPDATE, id, input),
  remove: (id: number) => ipcRenderer.invoke(IPC.JOB_DELETE, id),
})

const review = Object.freeze({
  list: (opts?: unknown) => ipcRenderer.invoke(IPC.REVIEW_LIST, opts),
  override: (input: unknown) => ipcRenderer.invoke(IPC.REVIEW_OVERRIDE, input),
})

const audit = Object.freeze({
  actions: (filters?: unknown) => ipcRenderer.invoke(IPC.AUDIT_ACTIONS, filters),
  cdp: (filters?: unknown) => ipcRenderer.invoke(IPC.AUDIT_CDP, filters),
})

const exporter = Object.freeze({
  evaluations: (req: unknown) => ipcRenderer.invoke(IPC.EXPORT_EVALUATIONS, req),
})

contextBridge.exposeInMainWorld(
  'bossResume',
  Object.freeze({
    version: BRIDGE_VERSION,
    db,
    app: appApi,
    chrome,
    session,
    jobs,
    review,
    audit,
    exporter,
  }),
)

// 导出供测试断言契约一致性（仅测试环境，不影响 sandbox 运行）
if (typeof module !== 'undefined' && module.exports) {
  module.exports.__test__ = { BRIDGE_VERSION, IPC }
}
