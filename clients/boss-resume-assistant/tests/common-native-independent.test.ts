import assert from 'node:assert/strict'
import test from 'node:test'
import { copyFile, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execPowerShellScript } from '../src/main/input/powerShell.js'

// 2026-10-21：原「missing/hidden bound render widgets 走零输入 exit 2」用例随 win-click.ps1
// 删除而移除——该守卫的现行实现在共享包 @aid/win32-native-input（windows/sequence），由其自带测试覆盖。

test('independent: production native ownership selects one main window behind owned dialogs and refuses two independent windows',
  { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'boss-native-independent-'))
  assert.ok(directory.startsWith(join(tmpdir(), 'boss-native-independent-')))
  try {
    const source = await readFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), 'utf8')
    const start = source.indexOf('    public static IntPtr Find(')
    const end = source.indexOf('    public static IntPtr[] OwnedDialogs(', start)
    assert.ok(start > 0 && end > start)
    // Execute the actual two ownership algorithms; replace only their native boundary.
    const compiled = `using System; using System.Collections.Generic;
public static class IndependentNativeOwnership {
    public delegate bool EnumProc(IntPtr window, IntPtr data);
    static int[] Windows = new int[] { 20, 12, 11, 10 };
    static bool SecondMain;
    static bool EnumWindows(EnumProc cb, IntPtr data) { foreach(int id in Windows) cb(new IntPtr(id),data); if(SecondMain) cb(new IntPtr(30),data); return true; }
    static bool IsWindowVisible(IntPtr window) { return true; }
    static uint ProcessId(IntPtr window) { return window.ToInt32() == 20 ? (uint)200 : (uint)100; }
    static string ClassName(IntPtr window) { return "Chrome_WidgetWin_1"; }
    static string Title(IntPtr window) { return "BOSS"; }
    static IntPtr GetAncestor(IntPtr window, uint flags) { return window; }
    static IntPtr GetWindow(IntPtr window, uint command) { return new IntPtr(window.ToInt32() == 12 ? 11 : window.ToInt32() == 11 ? 10 : 0); }
${source.slice(start, end)}
    public static void Probe() {
        if(Find(100).ToInt32()!=10) throw new Exception("Owned print/dialog must not displace main browser");
        if(Find(200).ToInt32()!=20) throw new Exception("Other debug PID must stay isolated");
        if(!BelongsTo(new IntPtr(12),new IntPtr(10))) throw new Exception("Nested owned dialog chain lost");
        if(BelongsTo(new IntPtr(20),new IntPtr(10))) throw new Exception("Foreign PID admitted");
        SecondMain=true;
        bool refused=false;
        try { Find(100); } catch(InvalidOperationException) { refused=true; }
        if(!refused) throw new Exception("Ambiguous independent browser windows must not use foreground guessing");
    }
}`
    // 载体借用实验工具 window-experiment.ps1 之名（execPowerShellScript 白名单唯一入口，
    // visual-region-compare.ps1 已迁 Node 删除）并携带其真实依赖 .cs（随加固 staging 拷贝，
    // 本探针不使用），继续在同一加固执行路径上跑真实算法。
    const script = join(directory, 'window-experiment.ps1')
    await Promise.all([
      writeFile(script, `Add-Type -TypeDefinition @'
${compiled}
'@
[IndependentNativeOwnership]::Probe()
Write-Output 'independent-native-ownership-passed'
`),
      copyFile(fileURLToPath(new URL('../../scripts/BossPhysicalInput.cs', import.meta.url)), join(directory, 'BossPhysicalInput.cs')),
      copyFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), join(directory, 'BossWindowExperiment.cs')),
    ])
    const result = await execPowerShellScript(script, [], { timeout: 15_000 })
    assert.match(result.stdout, /independent-native-ownership-passed/)
  } finally { await rm(directory, { recursive: true, force: true }) }
})
