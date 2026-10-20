import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { execFileSync, spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createPackage } from '@electron/asar'

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const script = path.join(desktop, 'scripts/runtime-clean-install.ps1')
const powershell = path.join(process.env.WINDIR ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe')
const skip = process.platform !== 'win32' ? 'requires real Windows CurrentUser DPAPI/CIM/registry' : false
function fixture() {
  const id = randomUUID()
  const root = path.join(os.tmpdir(), 'aid-runtime-install-fixture-' + id)
  fs.mkdirSync(root)
  const appData = path.join(root, 'appData'), install = path.join(root, 'install')
  fs.mkdirSync(appData); fs.mkdirSync(install)
  const namespace = `Software\\AidWorkRuntimeCleanInstallFixtures\\${id}`
  const env = { ...process.env, APPDATA: appData, AIDWORK_RUNTIME_INSTALL_FIXTURE: id }
  delete env.PSModulePath
  return { root, appData, install, id, namespace, env,
    run(mode) { return execFileSync(powershell, ['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',script,'-Mode',mode,'-AppId','cn.aidingyi.agent.runtime.acceptance','-InstallDirectory',install,'-FixtureRegistryId',id], { encoding:'utf8', windowsHide:true, env, timeout:30000 }).trim() },
    ps(command) { return execFileSync(powershell, ['-NoProfile','-Command',command], { encoding:'utf8', windowsHide:true, env, timeout:30000 }).trim() },
    cleanup() {
      this.ps(`[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree('${namespace}',$false)`)
      assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()))
      assert.equal(path.basename(root), 'aid-runtime-install-fixture-' + id)
      fs.rmSync(root, { recursive:true, force:true })
    },
  }
}
function child(entry, env) {
  const processChild = spawn(process.execPath,[entry],{windowsHide:true,stdio:'ignore',env})
  const exit = new Promise(resolve => processChild.once('exit', resolve))
  return { processChild, exit }
}
async function ready(processChild) {
  await new Promise((resolve, reject) => { processChild.once('spawn', resolve); processChild.once('error', reject) })
  await new Promise(resolve=>setTimeout(resolve,200))
}
async function exited(item) {
  let timer
  try { await Promise.race([item.exit, new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('owned fixture did not stop')),5000)})]) }
  finally { clearTimeout(timer) }
}

test('each reinstall allocates a fresh generation, preserving prior identity source and stable unknown registration', {skip}, () => {
  const f=fixture()
  try {
    const metadata=path.join(f.appData,'aidwork-runtime-app-acceptance/installation-v2')
    const ids=new Set(), homes=new Set()
    let previous=path.join(f.appData,'aidwork-tool-runtime-acceptance')
    for(let i=0;i<10;i++) {
      const id=f.run('Finalize')
      assert.match(id,/^[a-f0-9-]{36}$/)
      const pointer=JSON.parse(fs.readFileSync(path.join(metadata,'current.json'),'utf8'))
      assert.equal(pointer.id,id); assert.equal(pointer.initialized,false)
      assert.equal(pointer.priorHome,previous)
      assert.equal(fs.existsSync(pointer.home),true)
      assert.deepEqual(fs.readdirSync(pointer.home),[])
      ids.add(id); homes.add(pointer.home); previous=pointer.home
      fs.writeFileSync(path.join(metadata,'clean-registration.bin'),'protected unknown registration')
    }
    assert.equal(ids.size,10); assert.equal(homes.size,10)
    assert.equal(fs.readFileSync(path.join(metadata,'clean-registration.bin'),'utf8'),'protected unknown registration')
  } finally { f.cleanup() }
})
test('corrupt old pointer does not block installer allocation', {skip}, () => {
  const f=fixture()
  try {
    f.run('Finalize')
    const pointer=path.join(f.appData,'aidwork-runtime-app-acceptance/installation-v2/current.json')
    fs.writeFileSync(pointer,'{broken old state')
    f.run('Finalize')
    const current=JSON.parse(fs.readFileSync(pointer,'utf8'))
    assert.equal(current.priorHome,path.join(f.appData,'aidwork-tool-runtime-acceptance'))
  } finally { f.cleanup() }
})
test('acceptance installer stops current-user verified npm CLI and its child, preserving unrelated node and fixture-only HKCU values', {skip}, async () => {
  const f=fixture(); const children=[]
  try {
    const cli=path.join(f.appData,'npm/node_modules/agent-tool-runtime/dist/src/cli.js')
    fs.mkdirSync(path.dirname(cli),{recursive:true})
    const unrelated=path.join(f.root,'unrelated.cjs'), descendant=path.join(f.root,'descendant.cjs'), descendantPid=path.join(f.root,'child.pid')
    fs.writeFileSync(unrelated,'setInterval(()=>{},1000)')
    fs.writeFileSync(descendant,'setInterval(()=>{},1000)')
    fs.writeFileSync(cli,`const {spawn}=require('node:child_process');const fs=require('node:fs');const p=spawn(process.execPath,[${JSON.stringify(descendant)}],{stdio:'ignore'});fs.writeFileSync(${JSON.stringify(descendantPid)},String(p.pid));setInterval(()=>{},1000)`)
    const legacy=child(cli,f.env), other=child(unrelated,f.env);children.push(legacy,other)
    await Promise.all([ready(legacy.processChild),ready(other.processChild)])
    while(!fs.existsSync(descendantPid)) await new Promise(resolve=>setTimeout(resolve,50))
    const pid=Number(fs.readFileSync(descendantPid,'utf8'))
    f.ps(`$r=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('${f.namespace}\\Run');$r.SetValue('cn.aidingyi.agent.runtime.acceptance','old-product');$r.SetValue('other-product','unrelated');$r.SetValue('old-cli','"${process.execPath}" "${cli}" start');$r.Dispose()`)
    assert.equal(f.run('Detect'),'PROCESS_RUNNING')
    assert.equal(legacy.processChild.exitCode,null)
    assert.equal(f.ps(`$r=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('${f.namespace}\\Run');$r.GetValueNames().Count;$r.Dispose()`),'3')
    assert.throws(() => execFileSync(powershell, ['-NoProfile','-NonInteractive','-File',script,'-Mode','Stop','-RequireIdle','-AppId','cn.aidingyi.agent.runtime.acceptance','-InstallDirectory',f.install,'-FixtureRegistryId',f.id], { encoding:'utf8',windowsHide:true,env:f.env,timeout:30000 }),
      error => error.status === 1 && error.stdout.trim() === 'PROCESS_STARTED_DURING_INSTALL')
    assert.equal(legacy.processChild.exitCode,null)
    assert.equal(f.run('Stop'),'PROCESS_TAKEOVER_PASS')
    await exited(legacy)
    assert.equal(other.processChild.exitCode,null)
    assert.equal(f.ps(`if(Get-Process -Id ${pid} -ErrorAction SilentlyContinue){'alive'}else{'stopped'}`),'stopped')
    assert.equal(f.ps(`$r=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('${f.namespace}\\Run');$r.GetValueNames() -join ',';$r.Dispose()`),'other-product')
    assert.equal(f.run('Detect'),'PROCESS_CLEAR')
  } finally {
    for(const item of children) { if(item.processChild.exitCode===null)item.processChild.kill();await exited(item) }
    f.cleanup()
  }
})
test('final check reports concrete asset failure without printing private paths', {skip}, () => {
  const f=fixture()
  try {
    assert.throws(()=>f.run('Verify'), error=>error.status===1 && error.stdout.trim()==='INSTALLATION_ASSET_MISSING' && !error.stdout.includes(f.root))
  } finally { f.cleanup() }
})
test('real NSIS hook compiles without legacy drain or reboot continuation gates', {skip}, () => {
  const f=fixture()
  try {
    const cache=path.join(process.env.LOCALAPPDATA,'electron-builder/Cache/nsis')
    const nsis=fs.readdirSync(cache).find(name=>name.startsWith('nsis-3.'))
    const compiler=path.join(cache,nsis,'makensis.exe')
    const ns=value=>value.replaceAll('/','\\').replaceAll('$','$$')
    const source=path.join(f.root,'compile.nsi')
    fs.writeFileSync(source,`Unicode true\n!include "LogicLib.nsh"\nName "Isolated Runtime hook"\nOutFile "${ns(path.join(f.root,'fixture.exe'))}"\nRequestExecutionLevel user\n!define APP_ID "cn.aidingyi.agent.runtime.acceptance"\n!define APP_GUID "isolated-fixture"\n!define UNINSTALL_APP_KEY "isolated-fixture"\n!define RUNTIME_CLEAN_INSTALL_SOURCE "${ns(script)}"\n!define UAC_IsInnerInstance '0 == 1'\n!define UAC_SYNCREGISTERS 0\n!define UAC_SYNCINSTDIR 0\n!macro UAC_AsUser_Call kind function flags\nCall \${function}\n!macroend\n!include "${ns(path.join(desktop,'scripts/runtime-installer.nsh'))}"\n!insertmacro customHeader\nSection\nStrCpy $INSTDIR "${ns(f.install)}"\n!insertmacro runtimeCheckBeforeInstall\n!insertmacro customInstall\nSectionEnd\n`,'utf8')
    execFileSync(compiler,['/V2',source],{encoding:'utf8',windowsHide:true,timeout:30000})
    assert.equal(fs.existsSync(path.join(f.root,'fixture.exe')),true)
    const hook=fs.readFileSync(path.join(desktop,'scripts/runtime-installer.nsh'),'utf8')
    assert.equal(/PrepareDeferred|runtimePendingUpgrade|600|3010/.test(hook),false)
  } finally { f.cleanup() }
})
test('fixture seam rejects production and directory scope mismatches without touching real registry', {skip}, () => {
  const f = fixture()
  try {
    for (const [appId, install, code] of [
      ['cn.aidingyi.agent.runtime', f.install, 'FIXTURE_NOT_ALLOWED_IN_PRODUCTION'],
      ['cn.aidingyi.agent.runtime.acceptance', f.root, 'FIXTURE_DIRECTORY_SCOPE_MISMATCH'],
    ]) {
      assert.throws(() => execFileSync(powershell, ['-NoProfile','-File',script,'-Mode','Stop','-AppId',appId,'-InstallDirectory',install,'-FixtureRegistryId',f.id], { encoding:'utf8',windowsHide:true,env:f.env,timeout:30000 }),
        error => error.status === 1 && error.stdout.trim() === code)
    }
  } finally { f.cleanup() }
})
test('fresh installation probe rejects a Host whose describe succeeds while getState is blocked', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aid-host-check-'))
  try {
    const resources = path.join(root, 'resources'), host = path.join(resources, 'runtime/host'), metadata = path.join(root, 'metadata')
    const archiveSource = path.join(root, 'archive/dist/renderer')
    fs.mkdirSync(host, {recursive:true}); fs.mkdirSync(archiveSource, {recursive:true})
    fs.writeFileSync(path.join(archiveSource,'index.html'), '<html><body>fixture page</body></html>')
    await createPackage(path.join(root,'archive'), path.join(resources,'app.asar'))
    fs.writeFileSync(path.join(host,'package.json'), '{"type":"module"}')
    fs.writeFileSync(path.join(host,'dpapi.js'), 'export async function dpapiProtect(v){return v}; export async function dpapiUnprotect(v){return v}')
    const entry = path.join(host,'managed-entry.js'), checker = path.join(desktop,'scripts/runtime-installation-check.mjs')
    for (const blocked of [false,true]) {
      fs.writeFileSync(entry, `process.on('message',r=>process.send({request_id:r.request_id,method:r.method,code:0,error:'',result:r.method==='describe'?{api_major:1,features:['first_party_plugins']}:{state:'${blocked?'blocked':'stopped'}',connection:'unpaired',${blocked?"blocked_reason:'fixture block'":''}}}));process.on('disconnect',()=>process.exit(0))`)
      const run=()=>execFileSync(process.execPath,[checker,entry,metadata],{encoding:'utf8',windowsHide:true,timeout:30000})
      if (blocked) assert.throws(run,error=>error.status!==0 && error.stderr.includes('FRESH_HOST_NOT_USABLE'))
      else assert.equal(run().trim(),'INSTALLATION_CHECK_PASS')
    }
  } finally {
    assert.equal(path.dirname(path.resolve(root)),path.resolve(os.tmpdir()))
    assert.match(path.basename(root),/^aid-host-check-/)
    fs.rmSync(root,{recursive:true,force:true})
  }
})
