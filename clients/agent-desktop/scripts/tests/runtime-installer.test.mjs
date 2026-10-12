import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomUUID, createHash } from 'node:crypto';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Real builder Section and file/install-registry macros, isolated payload/home/keys.
// UAC original-user callbacks are deliberately simulated. This is NOT real elevation.
const directory = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(directory, '../..');
const repo = path.resolve(desktop, '../..');
const template = path.join(desktop, 'node_modules/app-builder-lib/templates/nsis');
const source = path.join(desktop, 'scripts/runtime-installer.nsh');
const powershell = path.join(process.env.WINDIR ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
const cache = path.join(process.env.LOCALAPPDATA ?? '', 'electron-builder/Cache/nsis');
function findCache(prefix, relative) {
  if (!fs.existsSync(cache)) return undefined;
  return fs.readdirSync(cache).filter(name => name.startsWith(prefix)).map(name => path.join(cache, name, relative)).find(file => fs.existsSync(file));
}
const compiler = process.platform === 'win32' ? findCache('nsis-', 'makensis.exe') : undefined;
const plugins = process.platform === 'win32' ? findCache('nsis-resources-', 'plugins/x86-unicode') : undefined;
const native = path.join(repo, 'clients/agent-tool-runtime/dist/src/native/RuntimeProcessSupervisor.exe');
const legacyScript = path.join(desktop, 'scripts/runtime-legacy-upgrade.ps1');
const csc = path.join(process.env.WINDIR ?? 'C:/Windows', 'Microsoft.NET/Framework64/v4.0.30319/csc.exe');
const unavailable = process.platform !== 'win32' ? 'Windows-only real NSIS fixture' : !compiler || !plugins || !fs.existsSync(template) || !fs.existsSync(native) || !fs.existsSync(csc) ? 'NSIS cache/plugins, compiled native helper or .NET compiler unavailable' : false;
const digest = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const ns = value => String(value).replaceAll('/', '\\').replaceAll('$', '$$');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function bounded(promise, label, duration = 30000) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`Fixture timeout: ${label}`)), duration); })]); }
  finally { clearTimeout(timer); }
}
async function until(predicate, label, duration = 20000) {
  const deadline = Date.now() + duration;
  while (Date.now() < deadline) { if (await predicate()) return; await sleep(50); }
  throw new Error(`Fixture timeout: ${label}`);
}
function run(executable, args, options) {
  const child = spawn(executable, args, { windowsHide: true, stdio: 'pipe', ...options });
  let stderr = ''; child.stderr?.on('data', data => { stderr += data; }); child.stdout?.resume();
  const done = new Promise((resolve, reject) => { child.on('error', reject); child.on('close', code => resolve(code)); });
  // Observe immediately so a failed spawn cannot become an unhandled rejection.
  void done.catch(() => {});
  const observed = bounded(done, 'owned process exit'); void observed.catch(() => {});
  return { child, done: observed, completion: done, get stderr() { return stderr; } };
}
function registry(root, command) {
  // Only generated UUID namespaces are interpolated into PowerShell, never user data.
  assert.match(root, /^Software\\AidWorkRuntime(?:Install|Upgrade)Fixtures\\[0-9a-f-]+$/);
  return execFileSync(powershell, ['-NoProfile', '-Command', command], { encoding: 'utf8' }).trim();
}
async function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aidns-'));
  const id = randomUUID();
  const appName = `RTIF-${id.slice(0,8)}`;
  const appData = path.join(root, 'ad');
  const local = path.join(root, 'local');
  const installRegistry = `Software\\AidWorkRuntimeInstallFixtures\\${id}`;
  const upgradeRegistry = `Software\\AidWorkRuntimeUpgradeFixtures\\${id}`;
  const payload = path.join(root, 'payload');
  for (const dir of [appData, local, payload]) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(payload, 'isolated-payload.txt'), 'isolated fixture, no business');
  fs.writeFileSync(path.join(root, 'dummy-uninstaller.exe'), 'never executed; fresh fixture keys');
  const timer = path.join(root, 'timer.exe');
  execFileSync(csc, ['/nologo', '/target:exe', '/out:' + timer, path.join(directory, 'fixtures/runtime-installer-timer.cs')], { stdio: 'pipe' });
  const children = [];
  return { root, id, appName, appData, payload, timer, installRegistry, upgradeRegistry, children,
    env: { ...process.env, APPDATA: appData, LOCALAPPDATA: local, USERPROFILE: path.join(root, 'profile') },
    home: path.join(appData, 'aidwork-tool-runtime-acceptance'), userData: path.join(appData, 'aidwork-runtime-app-acceptance'),
    async cleanup() {
      // Release every checkpoint/task first. Never enumerate/terminate by name.
      for (const item of children) for (const file of item.release ?? []) fs.writeFileSync(file, 'fixture cleanup release');
      for (const item of children) {
        try { await bounded(item.process.completion, 'owned fixture cleanup', 10000); }
        catch {
          // Failure-only fallback is limited to the exact ChildProcess we spawned.
          if (item.process.child.pid && item.process.child.exitCode === null) item.process.child.kill();
          await bounded(item.process.completion.catch(() => {}), 'exact owned child cleanup', 5000);
        }
      }
      for (const key of [installRegistry, upgradeRegistry]) registry(key, `$r=[Microsoft.Win32.Registry]::CurrentUser;$r.DeleteSubKeyTree('${key}',$false);$r.Dispose()`);
      // Temp artifacts are retained for evidence; no shared resources are removed.
    } };
}
function build(f, name, { inner = false, callback = 'direct', checkpoint = false, cancel = false, timing = false, uninstall = false, pendingFault } = {}) {
  const target = path.join(f.root, name, f.appName);
  const log = path.join(f.root, name + '.log'); fs.writeFileSync(log, '');
  let hook = fs.readFileSync(source, 'utf8').replace(/^\uFEFF/, '').replaceAll('\r\n','\n').replaceAll('-InstallDirectory "$INSTDIR"', `-InstallDirectory "$INSTDIR" -FixtureRegistryId "${f.id}"`).replaceAll('!insertmacro UAC_AsUser_Call Function', '!insertmacro Fixture_AsUser_Call Function');
  const write = value => `FileOpen $9 "${ns(log)}" a\nFileSeek $9 0 END\nFileWrite $9 "${value}$\\r$\\n"\nFileClose $9\n`;
  hook = hook.replace('  Call runtimeCallPendingUpgrade\n  ${If} $R0 != 0', `  Call runtimeCallPendingUpgrade\n${write('restore_exit=$R0')}  \${If} $R0 != 0`);
  const pending = hook.indexOf('      nsExec::ExecToStack', hook.indexOf('Function runtimePendingUpgrade'));
  assert.ok(pending > 0)
  if (pendingFault) {
    const end = hook.indexOf('\n', pending); const command = hook.slice(pending, end)
    hook = hook.slice(0, pending) + `IntOp $FixturePendingAttempts $FixturePendingAttempts + 1\n${write('pending_attempt=$FixturePendingAttempts')}\${If} $FixturePendingAttempts == 1\nPush "FIXTURE_LAUNCH_CLASS"\nPush "${pendingFault === 'validation' ? '1' : '-532462766'}"\n\${Else}\n${write('pending_ps_invoked=1')}${command}\n\${EndIf}` + hook.slice(end)
  } else hook = hook.slice(0, pending) + write('pending_ps_invoked=1') + hook.slice(pending)
  hook = hook.replace('    StrCpy $R9 2\n  InitPluginsDir', `    StrCpy $R9 2\n\${If} $INSTDIR == "${ns(target)}"\n${write('guard_target_match=1;mode=$installMode')}\${Else}\n${write('guard_target_match=0;mode=$installMode')}\${EndIf}\n  InitPluginsDir`);
  hook = hook.replace('        Pop $R1\n        ${If} $R0 == 0', `        Pop $R1\n${write('prepare_exit=$R0;code=$R1')}        \${If} $R0 == 0`);
  hook = hook.replace('            Pop $R1\n            ${If} $R0 == 0', `            Pop $R1\n${write('prepare_exit=$R0;code=$R1')}            \${If} $R0 == 0`);
  // Prove Restore owns its payload even if the earlier guard's copy is unavailable.
  hook = hook.replace('!macro customInstall\n', '!macro customInstall\n  Delete "$PLUGINSDIR\\runtime-legacy-upgrade.ps1"\n');
  fs.writeFileSync(path.join(f.root, name + '-hook.nsh'), '\uFEFF' + hook);
  // Use real mode-selection macros. Only their HKLM location lookup is redirected
  // to a controlled target, so no elevated fixture registry writes are needed.
  let multiUser = fs.readFileSync(path.join(template, 'multiUser.nsh'), 'utf8');
  multiUser = multiUser.replace('ReadRegStr $perMachineInstallationFolder HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation', `StrCpy $perMachineInstallationFolder "${ns(target)}"`);
  fs.writeFileSync(path.join(f.root, name + '-multiUser.nsh'), multiUser);
  const assisted = fs.readFileSync(path.join(template, 'assistedInstaller.nsh'), 'utf8');
  const init = assisted.slice(assisted.indexOf('!macro initMultiUser'));
  fs.writeFileSync(path.join(f.root, name + '-init.nsh'), init);
  const script = `Unicode true
OutFile "${name}${uninstall ? '-builder' : ''}.exe"
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
SetCompressor zlib
!define PRODUCT_NAME "Isolated Runtime installer fixture"
!define PRODUCT_FILENAME "${f.appName}"
!define APP_FILENAME "${f.appName}"
!define VERSION "0.0.1"
!define APP_ID "cn.aidingyi.agent.runtime.acceptance"
!define APP_GUID "${f.id}"
!define UNINSTALL_APP_KEY "${f.id}"
!define INSTALL_REGISTRY_KEY "${f.installRegistry}\\${name}\\Install"
!define UNINSTALL_REGISTRY_KEY "${f.installRegistry}\\${name}\\Uninstall"
!define SHORTCUT_NAME "${f.appName}"
!define UNINSTALL_DISPLAY_NAME "Isolated fixture"
!define APP_DESCRIPTION "Isolated fixture"
!define APP_BUILD_DIR "${ns(f.payload)}"
!define UNINSTALLER_OUT_FILE "${ns(path.join(f.root, 'dummy-uninstaller.exe'))}"
!define ESTIMATED_SIZE 1
!define INSTALL_MODE_PER_ALL_USERS_REQUIRED
!define DO_NOT_CREATE_START_MENU_SHORTCUT
!define DO_NOT_CREATE_DESKTOP_SHORTCUT
!define allowToChangeInstallationDirectory
!define isUpdated '0 == 1'
!define isDeleteAppData '0 == 1'
!define isForceRun '0 == 1'
${uninstall ? '!define BUILD_UNINSTALLER' : ''}
!define RUNTIME_LEGACY_UPGRADE_SOURCE "${ns(legacyScript)}"
!define RUNTIME_SUPERVISOR_SOURCE "${ns(native)}"
!addincludedir "${ns(template)}"
!addincludedir "${ns(path.join(template, 'include'))}"
!addplugindir "${ns(plugins)}"
!include LogicLib.nsh
!include FileFunc.nsh
!include StdUtils.nsh
!include UAC.nsh
${inner ? '!undef UAC_IsInnerInstance\n!define UAC_IsInnerInstance \'1 == 1\'' : ''}
${timing ? '!undef UAC_IsAdmin\n!define UAC_IsAdmin \'1 == 1\'' : ''}
!macro Fixture_AsUser_Call type name flags
 ${callback === 'noop' || callback === 'failure' ? '' : 'Call \${name}'}
 ${callback === 'failure' ? 'SetErrors' : ''}
!macroend
!include "common.nsh"
!include /CHARSET=UTF8 "${name}-hook.nsh"
!include "allowOnlyOneInstallerInstance.nsh"
!include /CHARSET=UTF8 "${name}-multiUser.nsh"
Var appExe
Var FixturePendingAttempts
Var launchLink
Var newStartMenuLink
Var oldStartMenuLink
Var newDesktopLink
Var oldDesktopLink
Var oldShortcutName
Var oldMenuDirectory
LangString uninstallFailed 1033 "FIXTURE_UNINSTALL_FAILED"
LangString appCannotBeClosed 1033 "FIXTURE_CANNOT_CLOSE"
!include "installUtil.nsh"
!include /CHARSET=UTF8 "${name}-init.nsh"
!include StrContains.nsh
${!uninstall ? `Function instFilesPre
 \${StrContains} $0 "\${APP_FILENAME}" $INSTDIR
 \${If} $0 == ""
  StrCpy $INSTDIR "$INSTDIR\\\${APP_FILENAME}"
 \${EndIf}
FunctionEnd
!insertmacro customPageAfterChangeDir` : ''}
!insertmacro customHeader
Function .onInit
 ${uninstall ? `WriteUninstaller "${ns(path.join(f.root, name + '.exe'))}"\nSetErrorLevel 0\nQuit` : `StrCpy $INSTDIR "${ns(target)}"
 StrCpy $installMode "CurrentUser"
 SetShellVarContext current
 ${timing ? `!insertmacro initMultiUser\n\${If} $INSTDIR == "${ns(path.join(f.root, 'previous-current-user', f.appName))}"\n${write('initial_target_match=1;mode=$installMode')}\${Else}\n${write('initial_target_match=0;mode=$installMode')}\${EndIf}` : ''}
 !insertmacro customInit
 ${write('init_completed=1')}
 ${checkpoint ? `${write('init_ready=1')}\n\${Do}\nSleep 50\n\${LoopUntil} \${FileExists} "${ns(path.join(f.root, name + '-continue'))}"\nCall runtimeInstFilesPre` : ''}`}
FunctionEnd
${uninstall ? `Function un.onInit
 StrCpy $INSTDIR "${ns(target)}"
 StrCpy $installMode "CurrentUser"
 !insertmacro CHECK_APP_RUNNING
FunctionEnd
Section Uninstall
 ${write('section_completed=1')}
SectionEnd
Section
SectionEnd` : `Section
 ${cancel ? 'Call runtimeCancelPendingUpgrade\nSetErrorLevel 0\nQuit' : `SetShellVarContext current
 !include "${ns(path.join(template, 'installSection.nsh'))}"
 ${write('section_completed=1')}`}
SectionEnd`}
`;
  fs.writeFileSync(path.join(f.root, name + '.nsi'), '\uFEFF' + script);
  execFileSync(compiler, ['/INPUTCHARSET', 'UTF8', name + '.nsi'], { cwd: f.root, stdio: 'pipe' });
  const exe = path.join(f.root, name + '.exe');
  if (uninstall) execFileSync(path.join(f.root, name + '-builder.exe'), ['/S'], { cwd: f.root, stdio: 'pipe' });
  return { exe, target, log, text: () => fs.readFileSync(log, 'utf8'), launch: env => {
    const process = run(exe, uninstall ? ['/S', '_?=' + f.root] : ['/S'], { cwd: f.root, env: env ?? f.env });
    f.children.push({ process, release: checkpoint ? [path.join(f.root, name + '-continue')] : [] });
    return process;
  } };
}
async function timer(f, build, modern = false) {
  fs.mkdirSync(build.target, { recursive: true });
  fs.copyFileSync(f.timer, path.join(build.target, f.appName + '.exe'));
  if (modern) { const dir = path.join(build.target, 'resources/runtime/host/native'); fs.mkdirSync(dir, { recursive: true }); fs.copyFileSync(native, path.join(dir, 'RuntimeProcessSupervisor.exe')); }
  const process = run(path.join(build.target, f.appName + '.exe'), [], { env: f.env });
  f.children.push({ process, release: [path.join(build.target, 'fixture-release.txt')] });
  await until(() => fs.existsSync(path.join(build.target, 'fixture-started.txt')), 'timer startup');
  return { ...process, async release() { fs.writeFileSync(path.join(build.target, 'fixture-release.txt'), 'release'); assert.equal(await process.done, 0); } };
}
function prepare(f, b) {
  fs.mkdirSync(f.home, { recursive: true }); fs.mkdirSync(b.target, { recursive: true });
  fs.writeFileSync(path.join(f.home, 'host-running.json'), JSON.stringify({ instance_id: randomUUID() }));
  const command = `"isolated-original.exe" --aidwork-runtime-login`;
  registry(f.upgradeRegistry, `$r=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('${f.upgradeRegistry}\\Run');$r.SetValue('cn.aidingyi.agent.runtime.acceptance','${command}');$r.Dispose()`);
  const result = execFileSync(powershell, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', legacyScript, '-Mode', 'Prepare', '-AppId', 'cn.aidingyi.agent.runtime.acceptance', '-InstallerPath', b.exe, '-InstallDirectory', b.target, '-SupervisorPath', native, '-FixtureRegistryId', f.id], { env: f.env, encoding: 'utf8' });
  assert.match(result, /PREPARED|REGISTERED/);
}
function runValue(f) { return registry(f.upgradeRegistry, `$r=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('${f.upgradeRegistry}\\Run');if($r){[string]$r.GetValue('cn.aidingyi.agent.runtime.acceptance','ABSENT');$r.Dispose()}else{'ABSENT'}`); }

test('real builder no-pending/invalid-home and pending self-contained Restore/Cancel', { skip: unavailable }, async () => {
  const f = await fixture();
  try {
    const fresh = build(f, 'fresh'); assert.equal(await fresh.launch().done, 0); assert.match(fresh.text(), /restore_exit=0/); assert.doesNotMatch(fresh.text(), /pending_ps_invoked/);
    const invalid = build(f, 'invalid'); assert.equal(await invalid.launch({ ...f.env, APPDATA: path.join(f.root, 'nonexistent') }).done, 0); assert.match(invalid.text(), /restore_exit=1004/); assert.doesNotMatch(invalid.text(), /pending_ps_invoked/);
    const empty = build(f, 'empty'); assert.equal(await empty.launch({ ...f.env, APPDATA: '' }).done, 0); assert.match(empty.text(), /restore_exit=1004/); assert.doesNotMatch(empty.text(), /pending_ps_invoked/);
    const pending = build(f, 'pending'); prepare(f, pending); assert.equal(runValue(f), 'ABSENT'); assert.equal(await pending.launch().done, 0); assert.match(pending.text(), /pending_ps_invoked=1/); assert.match(pending.text(), /restore_exit=0/); assert.match(runValue(f), /isolated-original/); assert.equal(fs.existsSync(path.join(f.userData, 'upgrade-pending/state.bin')), false);
    // A new isolated home avoids rebinding the immutable previous installer proof.
    const cancelled = await fixture();
    try { const cancel = build(cancelled, 'cancel', { cancel: true }); prepare(cancelled, cancel); assert.equal(await cancel.launch().done, 0); assert.match(runValue(cancelled), /isolated-original/); assert.equal(fs.existsSync(path.join(cancelled.userData, 'upgrade-pending/state.bin')), true); }
    finally { await cancelled.cleanup(); }
    const corrupted = await fixture();
    try { const broken = build(corrupted, 'broken'); const pendingDirectory = path.join(corrupted.userData, 'upgrade-pending'); fs.mkdirSync(pendingDirectory, { recursive: true }); fs.writeFileSync(path.join(pendingDirectory, 'state.bin'), 'invalid fixture proof'); assert.equal(await broken.launch().done, 0); assert.match(broken.text(), /restore_exit=1/); assert.match(broken.text(), /pending_ps_invoked=1/); assert.equal(fs.readFileSync(path.join(pendingDirectory, 'state.bin'), 'utf8'), 'invalid fixture proof'); }
    finally { await corrupted.cleanup(); }
  } finally { await f.cleanup(); }
});

test('inner callback missing/error refuses before actual builder file writes; timing resolves final machine target', { skip: unavailable }, async () => {
  const f = await fixture();
  try {
    for (const callback of ['noop', 'failure']) { const b = build(f, callback, { inner: true, callback }); assert.equal(await b.launch().done, 2); assert.doesNotMatch(b.text(), /section_completed/); assert.equal(fs.existsSync(path.join(b.target, 'isolated-payload.txt')), false); }
    const name = 'timing'; const old = path.join(f.root, 'previous-current-user', f.appName);
    registry(f.installRegistry, `$r=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('${f.installRegistry}\\${name}\\Install');$r.SetValue('InstallLocation','${old.replaceAll('/', '\\')}');$r.Dispose()`);
    const b = build(f, name, { inner: true, timing: true }); assert.equal(await b.launch().done, 0); assert.match(b.text(), /initial_target_match=1;mode=CurrentUser/); assert.match(b.text(), /guard_target_match=1;mode=all/); assert.equal(fs.existsSync(path.join(old, 'isolated-payload.txt')), false); assert.equal(fs.existsSync(path.join(b.target, 'isolated-payload.txt')), true);
  } finally { await f.cleanup(); }
});

test('direct-call PRE callback rechecks restarted legacy timer before writes; uninstaller refuses legacy timer', { skip: unavailable }, async () => {
  const f = await fixture(); let owned, installer;
  try {
    const b = build(f, 'pre', { inner: true, checkpoint: true }); fs.mkdirSync(f.home, { recursive: true }); fs.writeFileSync(path.join(f.home, 'host-running.json'), JSON.stringify({ instance_id: randomUUID() }));
    installer = b.launch(); await until(() => b.text().includes('init_ready=1'), 'first guard completed'); owned = await timer(f, b); fs.writeFileSync(path.join(f.root, 'pre-continue'), 'continue'); assert.equal(await installer.done, 3010); assert.equal(owned.child.exitCode, null); assert.doesNotMatch(b.text(), /section_completed/); assert.equal(fs.existsSync(path.join(b.target, 'isolated-payload.txt')), false); await owned.release(); owned = undefined;
    const un = build(f, 'uninstall', { uninstall: true }); owned = await timer(f, un); assert.equal(await un.launch().done, 2); assert.equal(owned.child.exitCode, null); assert.doesNotMatch(un.text(), /section_completed/);
  } finally { await f.cleanup(); }
});

test('modern guard registers strictly v1 recovery after fifteen seconds, keeps v2 task drain and never kills the timer', { skip: unavailable }, async () => {
  for (const version of ['v1', 'v2']) {
    const f = await fixture(); let owned
    try {
      const b = build(f, version); fs.mkdirSync(f.home, { recursive: true })
      const marker = JSON.stringify({ instance_id: randomUUID(), ...(version === 'v2' ? { supervision: 'isolated-v2-proof' } : {}) })
      fs.writeFileSync(path.join(f.home, 'host-running.json'), marker)
      owned = await timer(f, b, true); const startedAt = Date.now(); const installer = b.launch()
      if (version === 'v1') {
        assert.equal(await installer.done, 3010); assert.ok(Date.now() - startedAt >= 15000); assert.equal(owned.child.exitCode, null)
        assert.equal(fs.readFileSync(path.join(b.target, 'fixture-requested.txt'), 'utf8'), '--aidwork-exit-for-update')
        assert.match(b.text(), /prepare_exit=0;code=PREPARED/); assert.doesNotMatch(b.text(), /section_completed/)
        assert.equal(fs.existsSync(path.join(b.target, 'isolated-payload.txt')), false)
      } else {
        await until(() => b.text().includes('code=UNSUPPORTED_LEGACY_MARKER'), 'v2 preparation refused', 25000)
        assert.equal(owned.child.exitCode, null); assert.equal(installer.child.exitCode, null)
        assert.equal(fs.existsSync(path.join(f.userData, 'upgrade-pending/state.bin')), false)
        assert.equal(fs.existsSync(path.join(b.target, 'isolated-payload.txt')), false)
        await owned.release(); owned = undefined; assert.equal(await installer.done, 0); assert.match(b.text(), /section_completed/)
      }
      assert.equal(fs.readFileSync(path.join(f.home, 'host-running.json'), 'utf8'), marker)
    } finally { await f.cleanup() }
  }
});

test('pending Restore retries one simulated CLR launcher failure but never retries a validation exit1', { skip: unavailable }, async () => {
  for (const pendingFault of ['negative', 'validation']) {
    const f = await fixture()
    try {
      const b = build(f, pendingFault, { pendingFault }); prepare(f, b); assert.equal(await b.launch().done, 0)
      if (pendingFault === 'negative') {
        assert.match(b.text(), /pending_attempt=1/); assert.match(b.text(), /pending_attempt=2/); assert.doesNotMatch(b.text(), /pending_attempt=3/)
        assert.match(b.text(), /restore_exit=0/); assert.match(runValue(f), /isolated-original/)
      } else {
        assert.match(b.text(), /pending_attempt=1/); assert.doesNotMatch(b.text(), /pending_attempt=2/)
        assert.match(b.text(), /restore_exit=1/); assert.equal(runValue(f), 'ABSENT'); assert.equal(fs.existsSync(path.join(f.userData, 'upgrade-pending/state.bin')), true)
      }
    } finally { await f.cleanup() }
  }
});
