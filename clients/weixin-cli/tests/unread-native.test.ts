import assert from 'node:assert/strict'
import { execFile, spawnSync } from 'node:child_process'
import { mkdtempSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

test('unread reads native visible session badges and distinguishes an empty list from a missing list',
  { skip: process.platform !== 'win32' }, () => {
    const dir = mkdtempSync(join(tmpdir(), 'weixin-unread-native-'))
    const script = join(dir, 'test.ps1')
    const driver = fileURLToPath(new URL('../../drivers/ps1/unread-list.ps1', import.meta.url)).replaceAll("'", "''")
    try {
      writeFileSync(script, `\ufeff
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$null = Add-Type -AssemblyName UIAutomationClient
$null = Add-Type -AssemblyName UIAutomationTypes
$null = Add-Type -AssemblyName System.Drawing
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('${driver}', [ref]$null, [ref]$errors)
if ($errors) { throw 'PowerShell driver parse failed' }
foreach ($function in $ast.EndBlock.Statements) {
    if ($function -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($function.Extent.Text))
    }
}
function Throw-DriverError($Code, $Message) { throw "WXDRIVE|$Code|$Message" }
$script:rowTop = 100
function New-SessionRow($Label, $Id, $ProcessId = 42, $Offscreen = $false) {
    $name = ($Label -split '/')[0]
    if (-not $Id) { $Id = 'session_item_' + $name }
    $bounds = @{ Left = 100; Top = $script:rowTop }
    $script:rowTop += 65
    [pscustomobject]@{ Current = @{
        ProcessId = $ProcessId; IsOffscreen = $Offscreen; ClassName = 'mmui::ChatSessionCell'
        AutomationId = $Id; Name = ($Label -replace '/', [char]10); BoundingRectangle = $bounds
    } }
}
function New-SessionList($Rows, $ProcessId = 42) {
    $list = [pscustomobject]@{ Rows = @($Rows); Current = @{
        ProcessId = $ProcessId; IsOffscreen = $false; ClassName = 'mmui::XTableView'
        ControlType = [Windows.Automation.ControlType]::List
    } }
    $list | Add-Member ScriptMethod FindAll { param($Scope, $Condition)
        if ($Scope -ne [Windows.Automation.TreeScope]::Children) { throw 'Expected direct children' }
        return ,$this.Rows
    }
    return $list
}
$rows = @(
    (New-SessionRow '项目群（甲）/[3条] 张三: 已更新/14:21/'),
    (New-SessionRow '邓小青/[2条] /在吗/14:22/'),
    (New-SessionRow '项目群（乙）/[1条] /14:23/'),
    (New-SessionRow '正文伪装/[8条] 与真实角标格式相同/14:24/'),
    (New-SessionRow '红色头像/[7条] 只有头像有红色/14:25/'),
    (New-SessionRow '会话一/正文含 [8条]/时间'),
    (New-SessionRow '会话二/已经全部读完/[9条]'),
    (New-SessionRow '会话三/[0条]'),
    (New-SessionRow '会话四/[9条]无空白'),
    (New-SessionRow '非会话节点/[11条]' 'foreign-id'),
    (New-SessionRow '别的进程/[12条]' '' 99),
    (New-SessionRow '视窗外/[13条]' '' 42 $true)
)
$candidates = @(Read-WeixinUnreadSessions -Lists @((New-SessionList $rows), (New-SessionList $rows 99)) -WeixinProcessId 42)
$image = [Drawing.Bitmap]::new(150, 800)
try {
    $red = [Drawing.Color]::FromArgb(250, 81, 81)
    foreach ($rowNo in @(0, 1, 2)) {
        foreach ($x in 45..51) { foreach ($y in 11..13) { $image.SetPixel($x, (65 * $rowNo + $y), $red) } }
    }
    foreach ($x in 44..47) { foreach ($y in 14..35) { $image.SetPixel($x, (65 * 4 + $y), $red) } }
    $normal = @(Select-WeixinUnreadSessionsWithBadge $candidates $image @{Left=100; Top=100} 1.0)
} finally { $image.Dispose() }
$empty = @(Read-WeixinUnreadSessions -Lists @((New-SessionList @())) -WeixinProcessId 42)
$missing = try {
    Read-WeixinUnreadSessions -Lists @((New-SessionList $rows 99)) -WeixinProcessId 42
} catch { ([string]$_.Exception.Message).Split('|')[1] }
$ambiguous = try {
    Read-WeixinUnreadSessions -Lists @((New-SessionList @()), (New-SessionList @())) -WeixinProcessId 42
} catch { ([string]$_.Exception.Message).Split('|')[1] }
ConvertTo-Json -InputObject @{normal=$normal; empty=$empty; missing=$missing; ambiguous=$ambiguous} -Compress -Depth 4
`, 'utf8')
      const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script],
        { encoding: 'utf8', timeout: 15_000, windowsHide: true })
      assert.equal(result.status, 0, result.stderr)
      assert.deepEqual(JSON.parse(result.stdout), {
        normal: [
          { name: '项目群（甲）', preview: '张三: 已更新', unread_count: 3 },
          { name: '邓小青', preview: '在吗', unread_count: 2 },
          { name: '项目群（乙）', preview: '', unread_count: 1 },
        ],
        empty: [],
        missing: 'UI_CHANGED',
        ambiguous: 'UI_CHANGED',
      })
    } finally {
      unlinkSync(script)
      rmdirSync(dir)
    }
  })

test('the unread operation runs PowerShell without opening a proxy connection when activation is configured',
  { skip: process.platform !== 'win32', timeout: 20_000 }, async () => {
    const dir = mkdtempSync(join(tmpdir(), 'weixin-unread-offline-'))
    const fixture = join(dir, 'unread.ps1')
    writeFileSync(fixture, `Write-Output 'DRIVER_JSON: {"ok":true,"data":{"unread":[]}}'\n`, 'utf8')
    let connections = 0
    const requests: Array<string | undefined> = []
    const server = createServer((req, res) => {
      requests.push(req.url)
      req.resume()
      res.writeHead(410, { 'Content-Type': 'application/json' })
      res.end('{"detail":"fixture activation rejected"}')
    })
    server.on('connection', () => { connections++ })
    try {
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
      const address = server.address()
      assert.ok(address && typeof address === 'object')
      const source = `
        import { createWeixinUnreadListOperation } from ${JSON.stringify(new URL('../src/operations/unreadList.js', import.meta.url).href)};
        import { runPowerShellDriver } from ${JSON.stringify(new URL('../src/platform/powershell.js', import.meta.url).href)};
        const [mode, script] = process.argv.slice(1);
        if (mode === 'unread') {
          const operation = createWeixinUnreadListOperation({ runDriverFn: options => runPowerShellDriver({ ...options, script }) });
          const result = await operation.execute({}, { signal: new AbortController().signal, progress: () => {} });
          console.log(JSON.stringify({ success: result.success, effect: result.effect, data: result.data }));
        } else {
          console.log(JSON.stringify(await runPowerShellDriver({ script })));
        }
      `
      const execute = promisify(execFile)
      const run = async (mode: 'unread' | 'default') => {
        const { stdout } = await execute(process.execPath, ['--input-type=module', '--eval', source, mode, fixture], {
          windowsHide: true,
          timeout: 8_000,
          env: {
            ...process.env,
            APPDATA: dir,
            NO_PROXY: '127.0.0.1',
            AID_WEIXIN_SERVER_URL: `http://127.0.0.1:${address.port}`,
            AID_WEIXIN_ACTIVATION_CODE: 'synthetic-test-code',
          },
        })
        return JSON.parse(stdout)
      }
      assert.deepEqual(await run('unread'), { success: true, effect: 'none', data: { unread: [] } })
      assert.equal(connections, 0)
      assert.deepEqual(await run('default'), { unread: [] })
      assert.equal(connections, 1)
      assert.deepEqual(requests, ['/api/client/v1/activate'])
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()))
      unlinkSync(fixture)
      rmdirSync(dir)
    }
  })
