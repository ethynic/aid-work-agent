import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID, createHash } from 'node:crypto'
import { spawn, spawnSync, execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const productScript = path.join(desktop,'scripts/runtime-clean-install.ps1')
const productHook = path.join(desktop,'scripts/runtime-installer.nsh')
const powershell = path.join(process.env.WINDIR ?? 'C:/Windows','System32/WindowsPowerShell/v1.0/powershell.exe')
const installer = process.env.AID_UPGRADE_TEST_INSTALLER
const windows = process.platform === 'win32'
const evidenceRoot = process.env.AID_UPGRADE_TEST_NO_FIXTURE_ONLY === '1'
  ? path.join(desktop,'build/runtime-interactive-repro/independent') : path.join(desktop,'build/upgrade-confirmation-independent')
const digest = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex')
const results = []
const bindings = {nsis:digest(productHook),powershell:digest(productScript)}
console.log('UPGRADE_CONFIRMATION_SOURCE '+JSON.stringify(bindings))

function fixture() {
  const id=randomUUID(), root=path.join(os.tmpdir(),'aid-runtime-install-fixture-'+id)
  const appData=path.join(root,'appData'), install=path.join(root,'install'), namespace='Software\\AidWorkRuntimeCleanInstallFixtures\\'+id
  fs.mkdirSync(appData,{recursive:true});fs.mkdirSync(install)
  const userData=path.join(appData,'aidwork-runtime-app-acceptance'), metadata=path.join(userData,'installation-v2')
  fs.mkdirSync(metadata,{recursive:true})
  const files=[path.join(metadata,'current.json'),path.join(userData,'login-startup-initialized'),path.join(install,'old-install.txt')]
  for(const file of files)fs.writeFileSync(file,'preserved-before-user-consent')
  const env={...process.env,APPDATA:appData,AIDWORK_RUNTIME_INSTALL_FIXTURE:id};delete env.PSModulePath
  const children=[]
  const ps=command=>execFileSync(powershell,['-NoProfile','-NonInteractive','-Command','[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);'+command],{encoding:'utf8',windowsHide:true,env,timeout:30000}).trim()
  ps(`$k=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('${namespace}\\Run');$k.SetValue('cn.aidingyi.agent.runtime.acceptance','old-fixture-startup');$k.SetValue('unrelated','preserved');$k.Dispose()`)
  const registry=()=>ps(`$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('${namespace}\\Run');try{@($k.GetValueNames()|Sort-Object|ForEach-Object{$_+':'+$k.GetValue($_)})-join '|'}finally{$k.Dispose()}`)
  const snapshot=()=>({files:Object.fromEntries(files.map(file=>[path.basename(file),fs.existsSync(file)?digest(file):null])),registry:registry()})
  return {id,root,appData,install,env,namespace,userData,metadata,children,ps,files,registry,snapshot,
    run(mode,flags=[]) {return spawnSync(powershell,['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',productScript,'-Mode',mode,'-AppId','cn.aidingyi.agent.runtime.acceptance','-InstallDirectory',install,'-FixtureRegistryId',id,...flags],{encoding:'utf8',windowsHide:true,env,timeout:30000})},
    async clean() {
      for(const child of children)if(child.exitCode===null){
        spawnSync(path.join(process.env.SystemRoot,'System32/taskkill.exe'),['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'})
        await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})
      }
      ps(`[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree('${namespace}',$false)`)
      assert.equal(path.dirname(root),path.resolve(os.tmpdir()));assert.equal(path.basename(root),'aid-runtime-install-fixture-'+id)
      fs.rmSync(root,{recursive:true,force:true,maxRetries:20,retryDelay:100})
    } }
}
async function startEntry(f,entry) {
  const child=spawn(process.execPath,[entry],{env:f.env,windowsHide:true,stdio:'ignore'});f.children.push(child)
  await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject)})
  return child
}
async function oldRuntime(f) {
  const cli=path.join(f.appData,'npm/node_modules/agent-tool-runtime/dist/src/cli.js'), descendant=path.join(f.root,'descendant.cjs'), pidFile=path.join(f.root,'descendant.pid')
  fs.mkdirSync(path.dirname(cli),{recursive:true});fs.writeFileSync(descendant,'setInterval(()=>{},1000)')
  fs.writeFileSync(cli,`const {spawn}=require('node:child_process');const fs=require('node:fs');const p=spawn(process.execPath,[${JSON.stringify(descendant)}],{stdio:'ignore'});fs.writeFileSync(${JSON.stringify(pidFile)},String(p.pid));setInterval(()=>{},1000)`)
  const child=await startEntry(f,cli)
  for(let i=0;i<100&&!fs.existsSync(pidFile);i++)await new Promise(resolve=>setTimeout(resolve,30))
  assert.ok(fs.existsSync(pidFile),'owned descendant did not initialize')
  return {child,descendant:Number(fs.readFileSync(pidFile,'utf8'))}
}
function isAlive(f,pid) {return f.ps(`if(Get-Process -Id ${pid} -ErrorAction SilentlyContinue){'alive'}else{'stopped'}`)==='alive'}
function preserved(f,old,before) {assert.equal(old.child.exitCode,null);assert.ok(isAlive(f,old.descendant));assert.deepEqual(f.snapshot(),before)}

function compileHook(f,script=productScript,hook=productHook,name='confirmation',readProbe=false) {
  const cache=path.join(process.env.LOCALAPPDATA,'electron-builder/Cache/nsis'), version=fs.readdirSync(cache).find(name=>name.startsWith('nsis-3.'))
  const compiler=path.join(cache,version,'makensis.exe'), source=path.join(f.root,name+'.nsi'), exe=path.join(f.root,name+'.exe')
  const ns=value=>value.replaceAll('/','\\').replaceAll('$','$$')
  const action=readProbe?'ClearErrors\n!insertmacro runtimeReadFixture\n${If} ${Errors}\nSetErrorLevel 2\nQuit\n${EndIf}':'!insertmacro runtimeCheckBeforeInstall'
  fs.writeFileSync(source,`Unicode true\n!include "LogicLib.nsh"\nName "Independent Runtime confirmation"\nOutFile "${ns(exe)}"\nRequestExecutionLevel user\nAutoCloseWindow true\n!define APP_ID "cn.aidingyi.agent.runtime.acceptance"\n!define APP_GUID "independent-fixture"\n!define UNINSTALL_APP_KEY "independent-fixture"\n!define RUNTIME_CLEAN_INSTALL_SOURCE "${ns(script)}"\n!define UAC_IsInnerInstance '0 == 1'\n!define UAC_SYNCREGISTERS 0\n!define UAC_SYNCINSTDIR 0\n!macro UAC_AsUser_Call kind function flags\nCall \${function}\n!macroend\n!include "${ns(hook)}"\n!insertmacro customHeader\nSection\nStrCpy $INSTDIR "${ns(f.install)}"\n${action}\nFileOpen $R0 "$INSTDIR\\fixture-install-continued" w\nFileWrite $R0 "consented"\nFileClose $R0\nSetErrorLevel 0\nSectionEnd\n`,'utf8')
  execFileSync(compiler,['/V2',source],{encoding:'utf8',windowsHide:true,timeout:30000})
  return exe
}

// Exact PID-bound standard MessageBox automation. No mouse, coordinates,
// foreground activation or enumeration of text from unrelated applications.
function uiHelper(f) {
  const assembly=path.join(f.root,'OwnedDialog.dll'), definition=String.raw`
using System;using System.Text;using System.Threading;using System.Runtime.InteropServices;
public static class OwnedDialog {
public delegate bool EnumProc(IntPtr w,IntPtr p);
[DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p,IntPtr x);
[DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr w,EnumProc p,IntPtr x);
[DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr w,out uint p);
[DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr w,StringBuilder b,int n);
[DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr w,StringBuilder b,int n);
[DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr w,uint m,IntPtr a,IntPtr b);
[DllImport("user32.dll")] static extern IntPtr GetDlgItem(IntPtr w,int id);
public static long Find(uint pid) {long answer=0;EnumWindows((w,x)=>{uint p;GetWindowThreadProcessId(w,out p);if(p!=pid)return true;var c=new StringBuilder(256);GetClassName(w,c,256);if(c.ToString()!="#32770")return true;string text="";EnumChildWindows(w,(v,y)=>{var b=new StringBuilder(4096);GetWindowText(v,b,4096);text+=b.ToString();return true;},IntPtr.Zero);if(text.Contains("\u68c0\u6d4b\u5230\u65e7 Runtime")){answer=w.ToInt64();return false;}return true;},IntPtr.Zero);return answer;}
public static string Text(long handle,uint pid){uint p;var w=new IntPtr(handle);GetWindowThreadProcessId(w,out p);if(p!=pid)throw new Exception("PID_SCOPE_MISMATCH");string text="";EnumChildWindows(w,(v,y)=>{var b=new StringBuilder(4096);GetWindowText(v,b,4096);text+=b.ToString()+"\n";return true;},IntPtr.Zero);return text;}
public static int Default(long handle,uint pid){Text(handle,pid);return (int)(SendMessage(new IntPtr(handle),0x400,IntPtr.Zero,IntPtr.Zero).ToInt64()&0xffff);}
public static void Click(long handle,uint pid,int id){Text(handle,pid);if(id!=1&&id!=2)throw new Exception("BUTTON_SCOPE_MISMATCH");var b=GetDlgItem(new IntPtr(handle),id);if(b==IntPtr.Zero)throw new Exception("BUTTON_NOT_FOUND");SendMessage(b,0xf5,IntPtr.Zero,IntPtr.Zero);}
}`
  const code=path.join(f.root,'OwnedDialog.cs');fs.writeFileSync(code,definition)
  f.ps(`Add-Type -TypeDefinition ([IO.File]::ReadAllText('${code}')) -OutputAssembly '${assembly}'`)
  return { async find(pid) {
    const command=`[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);Add-Type -Path '${assembly}';$w=0;for($i=0;$i -lt 600 -and $w -eq 0;$i++){$w=[OwnedDialog]::Find(${pid});if($w -eq 0){Start-Sleep -Milliseconds 25}};if($w -eq 0){throw 'OWNED_DIALOG_NOT_FOUND'};@{handle=$w;pid=${pid};default_button=[OwnedDialog]::Default($w,${pid});text=[OwnedDialog]::Text($w,${pid})}|ConvertTo-Json -Compress`
    return new Promise((resolve,reject)=>{
      const p=spawn(powershell,['-NoProfile','-NonInteractive','-Command',command],{env:f.env,windowsHide:true,stdio:['ignore','pipe','pipe']});let out='',err=''
      p.stdout.on('data',b=>out+=b.toString());p.stderr.on('data',b=>err+=b.toString());p.once('exit',code=>code===0?resolve(JSON.parse(out)):reject(new Error('owned MessageBox automation failed: '+err)))
    })
  }, click(dialog,id) {f.ps(`Add-Type -Path '${assembly}';[OwnedDialog]::Click(${dialog.handle},${dialog.pid},${id})`)} }
}
function launch(f,exe,args=[],environment=f.env) {
  const child=spawn(exe,args,{env:environment,windowsHide:true,stdio:'ignore'});f.children.push(child)
  const exited=new Promise((resolve,reject)=>{child.once('exit',resolve);child.once('error',reject)})
  return {child,async exit(timeout=30000){let timer;try{return await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('owned installer timeout')),timeout)})])}finally{clearTimeout(timer)}}}
}
function record(name,fields) {results.push({name,...fields});console.log('INDEPENDENT_UPGRADE_PASS '+name)}

test('ordinary missing fixture variable continues a successful native guard; direct optional-read error flag probe distinguishes the fix', {skip:!windows}, async()=>{
  const f=fixture()
  try {
    // The ordinary environment is genuine. Only the PS operation boundary is
    // a readonly responder, avoiding global Stop while a real install proceeds.
    const environment={...process.env};delete environment.AIDWORK_RUNTIME_INSTALL_FIXTURE
    const responder=path.join(f.root,'readonly-operation-boundary.ps1'), calls=path.join(f.root,'operation-calls.txt')
    fs.writeFileSync(responder,`param([string]$Mode,[string]$AppId,[string]$InstallDirectory,[string]$FixtureRegistryId,[switch]$RequireIdle)\n[IO.File]::AppendAllText('${calls}',$Mode+':'+[string]$RequireIdle.IsPresent+':'+[string][bool]$FixtureRegistryId+[Environment]::NewLine)\nif($Mode -eq 'Detect'){Write-Output 'PROCESS_CLEAR'}elseif($Mode -eq 'Stop'){Write-Output 'PROCESS_TAKEOVER_PASS'}else{exit 1}\n`)
    const before=f.snapshot(), fixed=compileHook(f,responder,productHook,'ordinary-fixed'), run=launch(f,fixed,['/S'],environment)
    assert.equal(await run.exit(),0);assert.ok(fs.existsSync(path.join(f.install,'fixture-install-continued')));assert.deepEqual(f.snapshot(),before)
    assert.deepEqual(fs.readFileSync(calls,'utf8').trim().split(/\r?\n/),['Detect:False:False','Stop:True:False'])
    fs.unlinkSync(path.join(f.install,'fixture-install-continued'));fs.unlinkSync(calls)
    const source=fs.readFileSync(productHook,'utf8'), location=source.indexOf('ClearErrors',source.indexOf('ReadEnvStr'))
    assert.ok(location>0)
    const oldHook=path.join(f.root,'without-optional-clear.nsh');fs.writeFileSync(oldHook,source.slice(0,location)+source.slice(location+'ClearErrors'.length))
    const negative=compileHook(f,responder,oldHook,'ordinary-negative'), rejected=launch(f,negative,['/S'],environment)
    // Later plugin/file commands can clear Errors in this minimal guard. Keep
    // that observation: this harness does not reproduce the complete old wizard.
    const negativeGuardExit=await rejected.exit();assert.equal(negativeGuardExit,0)
    fs.unlinkSync(path.join(f.install,'fixture-install-continued'))
    const negativeProbe=compileHook(f,responder,oldHook,'read-probe-old',true), failedRead=launch(f,negativeProbe,['/S'],environment)
    assert.equal(await failedRead.exit(),2);assert.equal(fs.existsSync(path.join(f.install,'fixture-install-continued')),false)
    const fixedProbe=compileHook(f,responder,productHook,'read-probe-fixed',true), successfulRead=launch(f,fixedProbe,['/S'],environment)
    assert.equal(await successfulRead.exit(),0);assert.deepEqual(f.snapshot(),before)
    record('ordinary_missing_variable_clear_errors',{fixture_variable_absent:true,ordinary_appdata:true,unchanged_native_guard:true,powershell_boundary:'readonly success responder; not a global process Stop',fixed_guard_exit_code:0,old_minimal_guard_exit_code:negativeGuardExit,old_direct_read_probe_exit_code:2,fixed_direct_read_probe_exit_code:0,old_full_wizard_failure_not_reproduced_by_minimal_guard:true,fixture_state_preserved:true})
  }finally{await f.clean()}
})

test('ordinary missing fixture variable still rejects a real product PowerShell directory failure', {skip:!windows}, async()=>{
  const f=fixture()
  try {
    const environment={...process.env};delete environment.AIDWORK_RUNTIME_INSTALL_FIXTURE
    const target=path.join(f.root,'junction-target');fs.mkdirSync(target)
    assert.equal(path.dirname(f.install),f.root);fs.rmSync(f.install,{recursive:true,force:true});fs.symlinkSync(target,f.install,'junction')
    const before=f.snapshot(), actual=spawnSync(powershell,['-NoProfile','-NonInteractive','-File',productScript,'-Mode','Detect','-AppId','cn.aidingyi.agent.runtime.acceptance','-InstallDirectory',f.install],{env:environment,windowsHide:true,encoding:'utf8',timeout:30000})
    assert.equal(actual.status,1);assert.equal(actual.stdout.trim(),'DIRECTORY_LINK_REJECTED')
    const exe=compileHook(f,productScript,productHook,'ordinary-real-error'), run=launch(f,exe,['/S'],environment)
    assert.equal(await run.exit(),2);assert.equal(fs.existsSync(path.join(target,'fixture-install-continued')),false);assert.deepEqual(f.snapshot(),before)
    record('ordinary_real_powershell_failure',{fixture_variable_absent:true,ordinary_appdata:true,powershell_exit_code:1,powershell_machine_code:'DIRECTORY_LINK_REJECTED',native_guard_exit_code:2,no_install_continuation:true,no_cim_or_global_stop:true})
  }finally{await f.clean()}
})

test('real PS Detect running and clear is read only; RequireIdle refuses a newly started Runtime', {skip:!windows}, async()=>{
  const f=fixture()
  try {
    const before=f.snapshot(), clear=f.run('Detect')
    assert.equal(clear.status,0);assert.equal(clear.stdout.trim(),'PROCESS_CLEAR');assert.deepEqual(f.snapshot(),before)
    const old=await oldRuntime(f), detected=f.run('Detect')
    assert.equal(detected.status,0);assert.equal(detected.stdout.trim(),'PROCESS_RUNNING');preserved(f,old,before)
    const raced=f.run('Stop',['-RequireIdle'])
    assert.equal(raced.status,1);assert.equal(raced.stdout.trim(),'PROCESS_STARTED_DURING_INSTALL');preserved(f,old,before)
    record('detect_readonly_and_idle_race',{default_action:'no termination',before_after_state_equal:true})
  }finally{await f.clean()}
})

test('real NSIS default cancel and explicit cancel leave Runtime, its child, startup and old files intact', {skip:!windows}, async()=>{
  const f=fixture()
  try {
    const exe=compileHook(f), ui=uiHelper(f), old=await oldRuntime(f), before=f.snapshot(), run=launch(f,exe)
    const dialog=await ui.find(run.child.pid)
    assert.equal(dialog.default_button,2);assert.match(dialog.text,/当前任务会中断/);assert.match(dialog.text,/旧客户端继续运行/)
    preserved(f,old,before);ui.click(dialog,2)
    assert.equal(await run.exit(),1);preserved(f,old,before);assert.equal(fs.existsSync(path.join(f.install,'fixture-install-continued')),false)
    record('native_nsis_cancel',{default_button:dialog.default_button,exit_code:1,old_runtime_preserved:true})
  }finally{await f.clean()}
})

test('real NSIS confirm ends only its verified Runtime tree and continues installation', {skip:!windows}, async()=>{
  const f=fixture()
  try {
    const exe=compileHook(f), ui=uiHelper(f), old=await oldRuntime(f), otherEntry=path.join(f.root,'unrelated.cjs')
    fs.writeFileSync(otherEntry,'setInterval(()=>{},1000)');const other=await startEntry(f,otherEntry)
    const before=f.snapshot(), run=launch(f,exe), dialog=await ui.find(run.child.pid)
    preserved(f,old,before);assert.equal(other.exitCode,null);ui.click(dialog,1)
    assert.equal(await run.exit(),0);assert.equal(fs.existsSync(path.join(f.install,'fixture-install-continued')),true)
    assert.notEqual(old.child.exitCode,null);assert.equal(isAlive(f,old.descendant),false);assert.equal(other.exitCode,null)
    assert.equal(f.registry(),'unrelated:preserved');assert.equal(digest(path.join(f.install,'old-install.txt')),before.files['old-install.txt'])
    record('native_nsis_confirm',{exit_code:0,old_tree_stopped:true,unrelated_node_preserved:true,installation_continued:true})
  }finally{await f.clean()}
})

test('real NSIS guard automatically continues without running Runtime and silently cancels when it is running', {skip:!windows}, async()=>{
  const f=fixture()
  try {
    const exe=compileHook(f), empty=launch(f,exe)
    assert.equal(await empty.exit(),0);assert.equal(fs.existsSync(path.join(f.install,'fixture-install-continued')),true)
    fs.unlinkSync(path.join(f.install,'fixture-install-continued'))
    const old=await oldRuntime(f), before=f.snapshot(), silent=launch(f,exe,['/S'])
    assert.equal(await silent.exit(),1);preserved(f,old,before);assert.equal(fs.existsSync(path.join(f.install,'fixture-install-continued')),false)
    record('native_nsis_clear_and_silent_cancel',{clear_exit_code:0,silent_running_exit_code:1,silent_preserved_old_runtime:true})
  }finally{await f.clean()}
})

test('final reviewed r12 installer installs idle and silently cancels an upgrade while old Runtime is alive', {skip:!windows||!installer}, async()=>{
  assert.equal(path.isAbsolute(installer),true)
  const f=fixture()
  try {
    const first=launch(f,installer,['/S','/D='+f.install])
    assert.equal(await first.exit(120000),0)
    const output=path.dirname(installer), manifest=JSON.parse(fs.readFileSync(path.join(output,'runtime-release-manifest.json'),'utf8'))
    assert.equal(digest(installer),manifest.sha256)
    const files=['AID Work Runtime 验收版.exe','resources/app.asar','resources/runtime/host/cleanRegistration.js','resources/runtime/host/runtimeHost.js','resources/runtime/host/node_modules/@aid/local-tool-host-core/dist/legacy/apiClient.js']
    const hashes=Object.fromEntries(files.map(file=>{const actual=digest(path.join(f.install,file));assert.equal(actual,digest(path.join(output,'win-unpacked',file)));return[file,actual]}))
    const old=await oldRuntime(f), before=f.snapshot(), cancelled=launch(f,installer,['/S','/D='+f.install])
    assert.equal(await cancelled.exit(),1);preserved(f,old,before)
    for(const file of files)assert.equal(digest(path.join(f.install,file)),hashes[file])
    record('final_r12_install_and_silent_cancel',{installer_sha256:manifest.sha256,payload_hashes:hashes,install_exit_code:0,silent_cancel_exit_code:1,old_runtime_preserved:true})
  }finally{await f.clean()}
})

test.after(()=>{
  fs.mkdirSync(evidenceRoot,{recursive:true})
  fs.writeFileSync(path.join(evidenceRoot,'runtime-upgrade-confirmation-report.json'),JSON.stringify({date:'2026-10-14',source_bindings:bindings,test_script_sha256:digest(fileURLToPath(import.meta.url)),results,
    reused_r11:'Host/GUI/DPAPI/backend evidence remains bound to unchanged r11 Host0.2.19/Core0.2.18; no ten-cycle repeat in this incremental task.',
    scope:'Only exact owned PIDs, nonce HKCU fixture namespace and verified temporary fixture root; native MessageBox UI uses PID-bound handles. No customer/agent2/UAC/boot/BOSS business verification or deployment.'},null,2))
})
