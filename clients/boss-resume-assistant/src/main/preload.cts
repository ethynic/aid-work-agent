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

const BRIDGE_VERSION = 1
const IPC = Object.freeze({
  DB_HEALTH: 'boss:db:health',
  APP_VERSION: 'boss:app:version',
  APP_RUNTIME: 'boss:app:runtime',
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

contextBridge.exposeInMainWorld(
  'bossResume',
  Object.freeze({
    version: BRIDGE_VERSION,
    db,
    app: appApi,
  }),
)

// 导出供测试断言契约一致性（仅测试环境，不影响 sandbox 运行）
if (typeof module !== 'undefined' && module.exports) {
  module.exports.__test__ = { BRIDGE_VERSION, IPC }
}
