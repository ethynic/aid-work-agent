/**
 * scripts/three-state-acceptance.mjs — 三态验收采集工具（验收说明：
 * docs/plans/wecom-cli-localdb-three-state-acceptance.md 前置准备项 P-1/P-2/P-3）。
 *
 * 子命令：
 *   probe            输出合并观测 JSON（登录态/账号/版本/矩阵/模式/密钥与水位文件状态）
 *   snapshot [标签]  把 state/水位/日志尾部快照到 %LOCALAPPDATA%\AidWorkAgent\wecom-cli\acceptance\<时间戳-标签>\
 *   set-fake-key     向 keystore 预置 32-hex 假密钥（binding=当前账号；已有 key.bin 先备份 .acceptance-backup）
 *   clear-key        删除假密钥；存在 .acceptance-backup 则恢复
 *
 * 安全：假密钥为固定测试值（非真实凭据）；key.bin 的 DPAPI blob 绝不复制到快照/输出；
 * 本脚本只读企微数据目录之外的本 CLI 自身状态文件，不做任何部署动作。
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'
import { homedir } from 'node:os'
import process from 'node:process'

const dist = '../dist/src/platform/localdb/index.js'
const {
  probeLoginState, detectInstall, detectRunning,
  loadMatrix, lookupVersion, readLocalState, saveKey, clearKey,
} = await import(new URL(dist, import.meta.url).href)
// probeCurrentAccount 未入 barrel（operations 直接引用），单独导入：
const { probeCurrentAccount } = await import(new URL('../dist/src/platform/localdb/identity.js', import.meta.url).href)

const localappdata = process.env.LOCALAPPDATA
const localdbDir = localappdata ? join(localappdata, 'AidWorkAgent', 'wecom-cli', 'localdb') : null
const keyBin = localdbDir ? join(localdbDir, 'key.bin') : null
const watchState = localdbDir ? join(localdbDir, 'localdb-watch-state.json') : null
const acceptanceDir = localappdata ? join(localappdata, 'AidWorkAgent', 'wecom-cli', 'acceptance') : null

const signal = new AbortController().signal
const ctx = { signal, progress: () => {} }
const fileStat = (p) => {
  if (!p || !existsSync(p)) return null
  const st = statSync(p)
  return { exists: true, mtime_ms: Math.round(st.mtimeMs), size: st.size }
}

async function probe() {
  const out = { timestamp: new Date().toISOString() }
  const running = await detectRunning().catch(() => null)
  out.process = running
    ? { probe: running.probe, running: running.running, pids: running.pids }
    : { probe: 'failed' }
  try {
    const login = await probeLoginState({ signal })
    out.login_state = login.login_state
    out.window_pid = login.window_pid ?? null
  } catch (err) {
    out.login_state = 'probe-failed'
    out.login_error = String(err?.message ?? err).slice(0, 200)
  }
  try {
    const current = await probeCurrentAccount(ctx)
    out.account = current.account
    out.identity_confirmed = current.dataDir.identity?.identity_confirmed === true
    out.identity_reason = current.dataDir.identity?.reason ?? null
    out.data_dir = current.dataDir.dir
    out.source = current.dataDir.source
    try {
      const install = await detectInstall({ processPid: current.windowPid })
      out.version = install.version
      out.version_source = install.version_source
      out.install_pid = install.pid
      out.process_started_at = install.process_started_at ?? null
      const matrix = loadMatrix()
      const hit = lookupVersion(matrix, install.version)
      out.matrix_status = hit.status
    } catch (err) {
      out.version = null
      out.version_error = String(err?.message ?? err).slice(0, 200)
    }
  } catch (err) {
    out.account = null
    out.account_error_code = err?.code ?? null
    out.account_error = String(err?.message ?? err).slice(0, 200)
  }
  out.state = readLocalState()
  out.key_file = fileStat(keyBin)
  out.watch_state_file = fileStat(watchState)
  if (watchState && existsSync(watchState)) {
    try {
      const ws = JSON.parse(readFileSync(watchState, 'utf8'))
      out.watch_state = { account: ws.account ?? null, conversations: Object.keys(ws.conversations ?? {}).length }
    } catch { out.watch_state = { account: null, conversations: -1 } }
  }
  console.log(JSON.stringify(out, null, 2))
  return out
}

function snapshot(label) {
  if (!acceptanceDir) { console.error('LOCALAPPDATA 未设置'); process.exit(1) }
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const dir = join(acceptanceDir, `${ts}-${label || 'unlabeled'}`)
  mkdirSync(dir, { recursive: true })
  const copied = []
  for (const [name, path] of [['localdb-state.json', localdbDir ? join(localdbDir, 'localdb-state.json') : null], ['localdb-watch-state.json', watchState]]) {
    if (path && existsSync(path)) { copyFileSync(path, join(dir, name)); copied.push(name) }
  }
  if (localdbDir && existsSync(join(localdbDir, 'localdb.log'))) {
    const lines = readFileSync(join(localdbDir, 'localdb.log'), 'utf8').trimEnd().split('\n')
    writeFileSync(join(dir, 'localdb.log.tail'), lines.slice(-50).join('\n') + '\n', 'utf8')
    copied.push('localdb.log.tail')
  }
  const record = {
    label: label || 'unlabeled', timestamp: new Date().toISOString(),
    key_file: fileStat(keyBin), watch_state_file: fileStat(watchState), copied,
  }
  writeFileSync(join(dir, 'record.json'), JSON.stringify(record, null, 2), 'utf8')
  console.log(JSON.stringify({ dir, ...record }, null, 2))
}

function setFakeKey() {
  if (!keyBin || !acceptanceDir) { console.error('LOCALAPPDATA 未设置'); process.exit(1) }
  if (existsSync(keyBin) && !existsSync(keyBin + '.acceptance-backup')) {
    copyFileSync(keyBin, keyBin + '.acceptance-backup')
    console.log('已有 key.bin 备份为 key.bin.acceptance-backup')
  }
  return probe().then((out) => {
    if (!out.account) { console.error('当前账号不可得（需登录态），拒绝预置假密钥'); process.exit(1) }
    const binding = { user_id: out.account, data_dir: out.data_dir, algorithm: 'wxaes128cbc-nonlegacy' }
    return saveKey({ keyHex: 'ac'.repeat(16), binding, dir: localdbDir }).then((saved) => {
      console.log(JSON.stringify({ fake_key_preset: saved, binding }, null, 2))
    })
  })
}

function restoreOrClearKey() {
  const backup = keyBin ? keyBin + '.acceptance-backup' : null
  if (backup && existsSync(backup)) {
    if (existsSync(keyBin)) renameSync(keyBin, keyBin + '.removed-fake')
    renameSync(backup, keyBin)
    console.log('已恢复备份的真实 key.bin（假密钥文件移动为 key.bin.removed-fake，可手工删除）')
    return
  }
  if (keyBin && existsSync(keyBin)) { import('node:fs').then(fs => fs.unlinkSync(keyBin)); console.log('假密钥已删除（无备份）') }
  else console.log('key.bin 不存在，无需清理')
}

/** 锁屏检测：LogonUI.exe 在场 = 会话已锁（同会话进程视角）。 */
async function detectLocked() {
  const { execFile } = await import('node:child_process')
  return new Promise((resolve) => {
    execFile('powershell.exe', ['-NoProfile', '-Command', "if (Get-Process LogonUI -ErrorAction SilentlyContinue) { 'LOCKED' } else { 'UNLOCKED' }"], { timeout: 8000, windowsHide: true }, (err, stdout) => {
      resolve(err ? 'unknown' : String(stdout).trim())
    })
  })
}

/** 锁屏采样：每 15s 一轮（锁状态+登录态+身份+强制本地库读取），JSONL 输出。 */
async function lockSample(minutes) {
  const total = Math.max(1, Math.min(9, Number(minutes) || 6))
  const samples = []
  const deadline = Date.now() + total * 60_000
  let i = 0
  while (Date.now() < deadline) {
    i += 1
    const sample = { i, t: new Date().toISOString(), locked: await detectLocked() }
    try {
      const login = await probeLoginState({ signal })
      sample.login_state = login.login_state
      sample.window_pid = login.window_pid ?? null
    } catch (err) { sample.login_state = 'probe-failed: ' + String(err?.message ?? err).slice(0, 80) }
    try {
      const current = await probeCurrentAccount(ctx)
      sample.account_confirmed = true
      sample.identity_confirmed = current.dataDir.identity?.identity_confirmed === true
    } catch (err) {
      sample.account_confirmed = false
      sample.identity_error_code = err?.code ?? null
    }
    try {
      const r = await runLocaldbPy(['query', '--workdir', 'N/A'], { signal, timeoutMs: 15_000 })
      sample.unread_probe = 'skipped'
    } catch { sample.unread_probe = 'skipped' }
    // 强制本地库 unread（不经状态分派；读取成败即锁屏可用性证据）
    try {
      const { execFileSync } = await import('node:child_process')
      const out = execFileSync('node', [join('dist', 'src', 'cli', 'index.js'), 'unread', '--channel', 'local-db', '--json'], { timeout: 90_000, encoding: 'utf8', windowsHide: true, cwd: process.cwd() })
      const line = out.trimEnd().split('\n').reverse().find(l => l.trim().startsWith('{'))
      const parsed = line ? JSON.parse(line) : null
      sample.unread_db = { success: parsed?.success === true, code: parsed?.code ?? null, conversations: Array.isArray(parsed?.data?.conversations) ? parsed.data.conversations.length : null }
    } catch (err) {
      const m = String(err?.message ?? err)
      let code = null
      const mm = m.match(/"code":\s*"([A-Z_]+)"/)
      if (mm) code = mm[1]
      sample.unread_db = { success: false, code, error: m.slice(0, 120) }
    }
    samples.push(sample)
    console.log(JSON.stringify(sample))
    if (Date.now() + 15_000 > deadline) break
    await new Promise(r => setTimeout(r, 15_000))
  }
  const dir = acceptanceDir
  if (dir) {
    mkdirSync(dir, { recursive: true })
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    writeFileSync(join(dir, `lock-samples-${ts}.jsonl`), samples.map(x => JSON.stringify(x)).join('\n') + '\n', 'utf8')
    console.log(`采样完成：${samples.length} 轮，已写 acceptance/lock-samples-${ts}.jsonl`)
  }
}

const cmd = process.argv[2]
const arg3 = process.argv[3]
if (cmd === 'probe') await probe()
else if (cmd === 'snapshot') snapshot(arg3)
else if (cmd === 'set-fake-key') await setFakeKey()
else if (cmd === 'clear-key') restoreOrClearKey()
else if (cmd === 'lock-sample') await lockSample(arg3)
else { console.log('用法：node scripts/three-state-acceptance.mjs probe|snapshot [标签]|set-fake-key|clear-key'); process.exit(2) }
