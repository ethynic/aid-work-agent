import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import http from 'node:http'
import net from 'node:net'
import { spawn, spawnSync, execFileSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { pathToFileURL } from 'node:url'
import { UUID } from 'builder-util-runtime'

// Set only to a release already reviewed for acceptance fixture isolation.
const installer = process.env.AID_CLEAN_TEST_INSTALLER
const skip = !installer || process.platform !== 'win32' ? 'requires reviewed final Windows installer' : false
const powershell = path.join(process.env.WINDIR ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe')
const profile = 'cn.aidingyi.agent.runtime.acceptance'
const evidenceDirectory = path.resolve('clients/agent-desktop/build/independent-validation')
const payloadFiles = ['AID Work Runtime 验收版.exe', 'resources/app.asar', 'resources/runtime/node/win-x64/node.exe',
  'resources/runtime/host/cleanRegistration.js', 'resources/runtime/host/runtimeHost.js', 'resources/runtime/host/node_modules/@aid/local-tool-host-core/dist/legacy/apiClient.js']
const payloadHashes = root => Object.fromEntries(payloadFiles.map(file => [file, createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex')]))
function evidence(name, value) { fs.mkdirSync(evidenceDirectory, { recursive:true }); fs.writeFileSync(path.join(evidenceDirectory, name), JSON.stringify(value,null,2)) }
const appGuid = UUID.v5(profile, UUID.parse('50e065bc-3134-11e6-9bab-38c9862bdaf3'))
function ordinaryRegistryFingerprint(env) {
  const keys = ['Software\\Microsoft\\Windows\\CurrentVersion\\Run', 'Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce',
    'Software\\' + appGuid, 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + appGuid]
  const command = `$s=[Text.StringBuilder]::new();foreach($h in @([Microsoft.Win32.Registry]::CurrentUser,[Microsoft.Win32.Registry]::LocalMachine)){foreach($p in @(${keys.map(k => "'" + k + "'").join(',')})){$k=$h.OpenSubKey($p);[void]$s.Append($h.Name+$p);if($k){try{foreach($n in @($k.GetValueNames()|Sort-Object)){[void]$s.Append($n+':'+[string]$k.GetValue($n))}}finally{$k.Dispose()}}}};$a=[Security.Cryptography.SHA256]::Create();([BitConverter]::ToString($a.ComputeHash([Text.Encoding]::UTF8.GetBytes($s.ToString())))).Replace('-','')`
  return execFileSync(powershell, ['-NoProfile','-NonInteractive','-Command', command], { encoding:'utf8', windowsHide:true, env }).trim()
}
async function debugPort() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port
}
async function connectDebug(port) {
  let info
  for (let attempt = 0; attempt < 120; attempt++) {
    try { info = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break } catch {}
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  assert.ok(info?.webSocketDebuggerUrl, 'actual Electron debugger did not open')
  const socket = new WebSocket(info.webSocketDebuggerUrl), pending = new Map(); let id = 0, pageSession, failure
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }) })
  const send = (method, params = {}, sessionId, timeout = 15000) => new Promise((resolve, reject) => {
    const request = ++id, timer = setTimeout(() => { pending.delete(request); reject(new Error(`CDP timed out: ${method}`)) }, timeout)
    pending.set(request, { resolve, reject, timer }); socket.send(JSON.stringify({ id: request, method, params, ...(sessionId ? { sessionId } : {}) }))
  })
  socket.addEventListener('message', async event => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const p = pending.get(message.id); pending.delete(message.id); clearTimeout(p.timer)
      if (message.error) p.reject(new Error(message.error.message)); else p.resolve(message.result)
    }
    if (message.method === 'Target.attachedToTarget' && message.params.targetInfo.type === 'page') {
      const session = message.params.sessionId
      try {
        // Page commands cannot respond until Electron releases the newly
        // attached renderer. Its Vue startup still awaits asynchronous IPC.
        await send('Runtime.runIfWaitingForDebugger', {}, session)
        const source = "Object.defineProperty(document,'title',{configurable:true,get(){return globalThis.__independentSmokeTitle||''},set(value){globalThis.__independentSmokeTitle=value}})"
        await send('Page.addScriptToEvaluateOnNewDocument', { source }, session)
        await send('Runtime.evaluate', { expression: source }, session)
        pageSession = session
      } catch (error) { failure = error }
    }
  })
  await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true })
  return { send,
    notify(method,params,sessionId) { socket.send(JSON.stringify({id:++id,method,params,sessionId})) },
    async page() { for (let i = 0; i < 150; i++) { if (failure) throw failure; if (pageSession) return pageSession; await new Promise(resolve => setTimeout(resolve, 100)) } throw new Error('actual renderer did not attach') },
    close() { for (const p of pending.values()) clearTimeout(p.timer); socket.close() } }
}
function fixture() {
  const id = randomUUID(), root = path.join(os.tmpdir(), 'aid-runtime-install-fixture-' + id)
  const appData = path.join(root, 'appData'), install = path.join(root, 'install')
  fs.mkdirSync(appData, { recursive: true }); fs.mkdirSync(install)
  const env = { ...process.env, APPDATA: appData, AIDWORK_RUNTIME_INSTALL_FIXTURE: id }; delete env.PSModulePath
  const namespace = `Software\\AidWorkRuntimeCleanInstallFixtures\\${id}`
  const baseline = ordinaryRegistryFingerprint(env)
  return { id, root, appData, install, env,
    run(args = []) {
      const result = spawnSync(installer, ['/S', ...args, '/D=' + install], { env, windowsHide: true, encoding: 'utf8', timeout: 120000 })
      assert.equal(result.error, undefined); assert.equal(result.status, 0, 'final NSIS install failed')
      const checks = execFileSync(powershell, ['-NoProfile','-NonInteractive','-Command', `$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('${namespace}\\BuilderInstall');if(!$k -or $k.GetValue('InstallLocation') -ne '${install}'){exit 2};$k.Dispose();$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('${namespace}\\BuilderUninstall');if(!$k -or $null -ne $k.GetValue('UninstallString') -or $null -ne $k.GetValue('QuietUninstallString')){exit 3};$k.Dispose();'ISOLATED_BUILDER_REGISTRY_PASS'`], { env, windowsHide:true, encoding:'utf8' }).trim()
      assert.equal(checks, 'ISOLATED_BUILDER_REGISTRY_PASS')
      assert.equal(ordinaryRegistryFingerprint(env), baseline, 'final installer changed ordinary product or startup registry')
    },
    clean() {
      assert.equal(ordinaryRegistryFingerprint(env), baseline, 'ordinary registry changed during fixture validation')
      execFileSync(powershell, ['-NoProfile','-NonInteractive','-Command', `[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree('${namespace}',$false)`], { encoding:'utf8', windowsHide:true, env })
      assert.equal(path.dirname(root), path.resolve(os.tmpdir())); assert.equal(path.basename(root), 'aid-runtime-install-fixture-' + id)
      fs.rmSync(root, { recursive: true, force: true, maxRetries: 30, retryDelay: 200 })
    } }
}

test('final NSIS installer preserves a compatible home and only explicit Clean allocates a new home', { skip }, async () => {
  assert.equal(path.isAbsolute(installer), true)
  console.log('FINAL_INSTALLER_SHA256 ' + createHash('sha256').update(fs.readFileSync(installer)).digest('hex'))
  const f = fixture()
  const metadata = path.join(f.appData, 'aidwork-runtime-app-acceptance/installation-v2')
  const pointer = path.join(metadata, 'current.json')
  try {
    f.run()
    const initial = JSON.parse(fs.readFileSync(pointer, 'utf8'))
    assert.equal(initial.version, 2); assert.equal(initial.initialized, false)
    assert.equal(initial.home, path.join(f.appData, 'aidwork-runtime-app-acceptance', 'rt'))
    assert.deepEqual(fs.readdirSync(initial.home), [])
    const retained = { ...initial, initialized: true }
    fs.writeFileSync(pointer, JSON.stringify(retained))
    const facts = ['config.json', 'credentials.bin', 'journal/unknown.json', 'outbox/pending.json']
    const before = {}
    for (const name of facts) {
      const file = path.join(initial.home, name)
      fs.mkdirSync(path.dirname(file), { recursive: true })
      fs.writeFileSync(file, 'isolated retained fact: ' + name)
      before[name] = fs.readFileSync(file)
    }
    f.run()
    const upgraded = JSON.parse(fs.readFileSync(pointer, 'utf8'))
    assert.equal(upgraded.id, retained.id); assert.equal(upgraded.home, retained.home)
    assert.equal(upgraded.initialized, true, 'ordinary binary replacement must preserve the initialized connection')
    assert.equal(fs.readFileSync(path.join(f.install, 'runtime-installation.id'), 'utf8').trim(), retained.id)
    for (const name of facts) assert.deepEqual(fs.readFileSync(path.join(initial.home, name)), before[name])
    f.run(['/Clean'])
    const clean = JSON.parse(fs.readFileSync(pointer, 'utf8'))
    assert.notEqual(clean.id, retained.id); assert.notEqual(clean.home, retained.home)
    assert.equal(clean.initialized, false); assert.equal(clean.priorHome, retained.home)
    assert.equal(path.dirname(clean.home), path.join(f.appData, 'aidwork-runtime-app-acceptance', 'connections'))
    assert.deepEqual(fs.readdirSync(clean.home), [])
    for (const name of facts) assert.deepEqual(fs.readFileSync(path.join(initial.home, name)), before[name], 'Clean must retain old identity facts rather than transfer or erase unknown work')
    console.log('FINAL_NSIS_PRESERVE_AND_EXPLICIT_CLEAN_PASS')
  } finally { f.clean() }
})

test('actual installed Electron EXE resumes a protected lost response after outage and refreshes its running UI', { skip }, async () => {
  const f = fixture(); let child, debug, timer, committed, unavailable = true, pairCalls = 0, heartbeats = 0
  const server = http.createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    if (req.url === '/api/local-tools/runtime/pair') {
      pairCalls++
      if (!committed) { committed = { ...body, id: randomUUID() }; req.socket.destroy(); return }
      assert.equal(body.code, undefined); assert.equal(body.registration_id, committed.registration_id)
      assert.equal(body.registration_token, committed.registration_token)
      if (unavailable) { res.statusCode = 503; res.end('{}'); return }
      res.end(JSON.stringify({ success: true, pairing_protocol: 3, registration_completed: true, device_id: committed.id, device_token: body.registration_token })); return
    }
    if (req.url === '/api/local-tools/runtime/heartbeat') heartbeats++
    res.end(JSON.stringify({ success: true, selected: true, invocation: null, tasks: [] }))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${server.address().port}`
  try {
    if (!process.env.AID_CLEAN_TEST_EXECUTABLE) f.run()
    const executionRoot = process.env.AID_CLEAN_TEST_EXECUTABLE ? path.dirname(process.env.AID_CLEAN_TEST_EXECUTABLE) : f.install
    const packagedHost = path.join(executionRoot, 'resources/runtime/host/managed-entry.js')
    const registration = await import(pathToFileURL(path.join(path.dirname(packagedHost), 'cleanRegistration.js')))
    // Electron has an existing explicit smoke userData seam because Windows
    // GetFolderPath ignores APPDATA overrides. No real user home or Run key.
    const smokeRoot = path.join(f.root, 'electronSmoke'), legacy = path.join(smokeRoot, 'aidwork-tool-runtime-acceptance')
    const metadata = path.join(smokeRoot, 'aidwork-runtime-app-acceptance/installation-v2')
    fs.mkdirSync(legacy, { recursive: true }); fs.mkdirSync(metadata, { recursive: true })
    for (const name of ['config.json','credentials.bin','management-secret.bin','host-running.json','journal','outbox']) fs.writeFileSync(path.join(legacy, name), '{broken old bytes')
    const r = await registration.beginCleanRegistration(metadata, { server: url, device_name: 'Final installed EXE fixture' }, [legacy])
    await assert.rejects(registration.pairCleanRegistration(metadata, r, 'ISOLATED-OTP'))
    assert.equal((await registration.readCleanRegistration(metadata)).phase, 'requested')
    const exe = process.env.AID_CLEAN_TEST_EXECUTABLE ?? path.join(f.install, 'AID Work Runtime 验收版.exe')
    let stdout = '', stderr = ''
    const port = await debugPort()
    child = spawn(exe, [`--remote-debugging-port=${port}`], { cwd: f.install, windowsHide: true, env: { ...f.env, AID_AGENT_DESKTOP_SMOKE: '1', AID_AGENT_DESKTOP_SMOKE_USER_DATA: smokeRoot }, stdio: ['ignore','pipe','pipe'] })
    child.stdout.on('data', bytes => stdout += bytes.toString()); child.stderr.on('data', bytes => stderr += bytes.toString())
    timer = setTimeout(() => child.kill(), 90000)
    const exited = new Promise((resolve, reject) => { child.once('exit', resolve); child.once('error', reject) })
    debug = await connectDebug(port)
    const session = await debug.page()
    let projection
    for (let attempt = 0; attempt < 80; attempt++) {
      projection = await debug.send('Runtime.evaluate', { expression: "({title:globalThis.__independentSmokeTitle||'',text:document.body.innerText,forms:document.querySelectorAll('form.runtime-pair-form').length,fields:['runtime-server','runtime-device-name','runtime-pairing-code'].map(id=>!!document.getElementById(id))})", returnByValue: true }, session)
      if (projection.result.value?.title) break
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    const firstDom = projection.result.value
    assert.equal(firstDom.title, 'AGENT_DESKTOP_SMOKE_PASS', 'actual renderer management smoke failed')
    assert.equal(/运行中/.test(firstDom.text), false, '503 pending transaction must not claim running')
    unavailable = false
    for (let attempt = 0; attempt < 180; attempt++) {
      projection = await debug.send('Runtime.evaluate', { expression: "({title:globalThis.__independentSmokeTitle||'',text:document.body.innerText,forms:document.querySelectorAll('form.runtime-pair-form').length,fields:['runtime-server','runtime-device-name','runtime-pairing-code'].map(id=>!!document.getElementById(id))})", returnByValue: true }, session)
      if (/运行中/.test(projection.result.value?.text ?? '')) break
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    const dom = projection.result.value
    assert.equal(dom.title, 'AGENT_DESKTOP_SMOKE_PASS', 'actual renderer management smoke failed')
    assert.match(dom.text, /本机执行环境/); assert.match(dom.text, /运行中/)
    assert.equal(dom.forms, 1); assert.ok(dom.fields.every(Boolean))
    assert.equal(/完成连接配置|登记重启恢复|REGISTRATION_PENDING/.test(dom.text), false)
    const layout = await debug.send('Runtime.evaluate', { expression: "({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,forms:[...document.querySelectorAll('form.runtime-pair-form')].map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))})", returnByValue:true }, session)
    const screenshot = { status:'unavailable', reason:'Hidden Electron capture blocked later CDP commands; separate failed tool logs retained. No capture request made in this final native-exit check.' }
    evidence('final-gui-dom.json',{initial_dom:firstDom,recovered_dom:dom,dimensions:layout.result.value,screenshot})
    // The real title event immediately completes Main's original smoke protocol;
    // do not wait for an inspector response after the renderer is destroyed.
    debug.notify('Runtime.evaluate', { expression:"delete document.title;document.title='AGENT_DESKTOP_SMOKE_PASS'" }, session)
    const code = await exited.finally(() => clearTimeout(timer))
    debug.close(); debug = null
    assert.equal(code, 0, stdout + stderr)
    assert.match(stdout, /AGENT_DESKTOP_SMOKE_PASS/)
    assert.equal(pairCalls, 3); assert.ok(heartbeats > 0, 'new packaged execution did not contact service')
    assert.equal(await registration.readCleanRegistration(metadata), null)
    const pointer = JSON.parse(fs.readFileSync(path.join(metadata, 'current.json'), 'utf8'))
    const config = JSON.parse(fs.readFileSync(path.join(pointer.home, 'config.json'), 'utf8'))
    assert.equal(config.device_id, committed.id)
    let remaining = -1
    for (let attempt = 0; attempt < 10; attempt++) {
      remaining = Number(execFileSync(powershell, ['-NoProfile','-NonInteractive','-Command', `$p=@(Get-CimInstance Win32_Process -Filter 'ParentProcessId=${child.pid}' | Where-Object {$_.ExecutablePath -eq '${path.join(executionRoot,'resources/runtime/node/win-x64/node.exe')}'});$p.Count`], {encoding:'utf8',windowsHide:true,env:f.env}).trim())
      if (remaining === 0) break
      await new Promise(resolve => setTimeout(resolve,200))
    }
    assert.equal(remaining,0,'owned packaged Host remained after native EXE exit')
    evidence('final-gui.json', { installer_sha256:createHash('sha256').update(fs.readFileSync(installer)).digest('hex'), execution_root:executionRoot, payload_hashes:payloadHashes(executionRoot), native_exit_code:code,
      owned_host_remaining:remaining, pair_requests:pairCalls, heartbeat_requests:heartbeats, initial_dom:firstDom, recovered_dom:dom, dimensions:layout.result.value, screenshot })
    console.log('FINAL_ELECTRON_LOST_RESPONSE_AUTOSTART_SMOKE_PASS')
  } finally {
    clearTimeout(timer)
    debug?.close()
    if (child?.exitCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)) }
    await new Promise(resolve => { server.close(resolve); server.closeAllConnections() })
    f.clean()
  }
})

test('final installed payload is byte identical to the reviewed unpacked EXE, ASAR, Node and Host modules', { skip }, () => {
  const f = fixture()
  try {
    f.run()
    const actual = payloadHashes(f.install), unpacked = payloadHashes(path.join(path.dirname(installer),'win-unpacked'))
    assert.deepEqual(actual,unpacked)
    evidence('final-installed-payload.json',{ installer_sha256:createHash('sha256').update(fs.readFileSync(installer)).digest('hex'), installed_payload:actual, unpacked_payload:unpacked })
    console.log('FINAL_INSTALLED_PAYLOAD_BYTE_EQUAL_PASS')
  } finally { f.clean() }
})
