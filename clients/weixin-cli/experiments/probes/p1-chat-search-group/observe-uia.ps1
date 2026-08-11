# p1-chat-search-group P0 观察：微信所有相关 HWND 的 UIA 子树深度枚举
# 目标：判定 Qt 搜索结果项是否在任何一棵 UIA 子树里暴露（决定走 Qt UIA 还是 OCR）
# 零点击、零写动作。仅 Ctrl+F + 输入 + 枚举 + 截图。
param(
    [Parameter(Mandatory)][string]$Query,  # 运行时传入真实查询词（明文真实联系人姓名不入库）
    [int]$TypeDelayMs = 350,
    [int]$ResultWaitMs = 900
)
$ErrorActionPreference = 'Stop'
$probeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $probeDir 'probe-lib.ps1')

$mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WeixinCliProbe.ChatSearchGroup', [ref]$null)
if (-not $mutex.WaitOne(0)) { throw 'PROBE_BUSY' }
try {
    Initialize-WeixinProbeWin32
    Set-WeixinProbeDpiContext

    $windows = @(Get-VisibleWindowList)
    $main = Select-WeixinMainWindow $windows
    $mainHwnd = [int64]$main.Hwnd
    Write-Host "[1] 主窗口 hwnd=$mainHwnd"
    if (-not (Invoke-WeixinActivation $mainHwnd)) { throw 'WX_ACTIVATION_FAILED' }
    $mainGuard = { [WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $mainHwnd }
    if (-not (& $mainGuard)) { throw 'MAIN_NOT_FOREGROUND' }

    Send-WeixinKeyChord @('CTRL', 'F') $mainGuard
    Start-Sleep -Milliseconds 500
    Send-WeixinKeyChord @('CTRL', 'A') $mainGuard
    Start-Sleep -Milliseconds 120
    Send-WeixinKeyChord @('DEL') $mainGuard
    Start-Sleep -Milliseconds 200
    Write-Host "[2] Ctrl+F + 清空"

    $script:originalClipboard = Get-ClipboardTextSafe
    foreach ($ch in $Query.ToCharArray()) {
        Send-WeixinPasteText ([string]$ch) $mainGuard
        Start-Sleep -Milliseconds $TypeDelayMs
    }
    Write-Host "[3] 已输入: $Query"
    Start-Sleep -Milliseconds $ResultWaitMs

    # —— P0 观察：枚举微信所有相关 HWND（主窗口 + 所有 Weixin.exe 的可见顶层窗口）
    $weixinHwnds = @()
    foreach ($w in @(Get-VisibleWindowList)) {
        $id = Get-WindowIdentityByHwnd ([int64]$w.Hwnd)
        if ($null -eq $id) { continue }
        if (-not (Test-WeixinExecutablePath ([string]$id.ProcessPath))) { continue }
        $weixinHwnds += [pscustomobject]@{
            Hwnd = [int64]$id.Hwnd; ClassName = [string]$id.ClassName
            Title = [string]$id.Title
        }
    }
    Write-Host "[4] Weixin 相关可见 HWND 数=$($weixinHwnds.Count)"
    foreach ($h in $weixinHwnds) {
        Write-Host ("    hwnd={0} class={1} title={2}" -f $h.Hwnd, $h.ClassName, $h.Title)
    }

    # —— 对每个 HWND 做深度 UIA 枚举（TreeWalker 深度优先，不限 FindAll 的扁平结果）
    function Get-UiaDeepTree {
        param([Parameter(Mandatory)][int64]$Hwnd, [int]$MaxNodes = 15000)
        $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
        if ($null -eq $root) { return @() }
        $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
        $result = New-Object Collections.Generic.List[object]
        $stack = New-Object Collections.Generic.Stack[object]
        $stack.Push($root)
        $count = 0
        while ($stack.Count -gt 0 -and $count -lt $MaxNodes) {
            $node = $stack.Pop()
            $count++
            try {
                $cur = $node.Current
                $b = $cur.BoundingRectangle
                $hasInvoke = $node.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$null)
                $hasValue = $node.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$null)
                $hasToggle = $node.TryGetCurrentPattern([Windows.Automation.TogglePattern]::Pattern, [ref]$null)
                $result.Add([pscustomobject]@{
                    control_type = [string]$cur.ControlType.ProgrammaticName.Replace('ControlType.', '')
                    name = [string]$cur.Name
                    automation_id = [string]$cur.AutomationId
                    class_name = [string]$cur.ClassName
                    left = [double]$b.Left; top = [double]$b.Top
                    width = [double]$b.Width; height = [double]$b.Height
                    is_offscreen = [bool]$cur.IsOffscreen
                    has_invoke = $hasInvoke; has_value = $hasValue; has_toggle = $hasToggle
                })
            } catch { continue }
            try {
                $child = $walker.GetFirstChild($node)
                $children = @()
                while ($child) { $children += $child; $child = $walker.GetNextSibling($child) }
                # 反向 push 让深度优先接近从上到下
            } catch {}
            if ($children) {
                for ($i = $children.Count - 1; $i -ge 0; $i--) { $stack.Push($children[$i]) }
            }
            if (-not $children) { $children = @() }
        }
        $result.ToArray()
    }

    $allSummary = @()
    foreach ($h in $weixinHwnds) {
        $tree = @(Get-UiaDeepTree $h.Hwnd)
        # 过滤掉无意义节点（无名无矩形的纯容器也保留，但单独标记）
        $named = @($tree | Where-Object { -not [string]::IsNullOrWhiteSpace($_.name) })
        $editable = @($tree | Where-Object { $_.has_value })
        $clickable = @($tree | Where-Object { $_.has_invoke })
        Write-Host ("[5] hwnd={0} class={1} 总节点={2} 有名={3} 可编辑={4} 可调用={5}" -f $h.Hwnd, $h.ClassName, $tree.Count, $named.Count, $editable.Count, $clickable.Count)
        $allSummary += [pscustomobject]@{
            hwnd = $h.Hwnd; class_name = $h.ClassName; title = $h.Title
            total = $tree.Count; named = $named.Count
            editable = $editable.Count; clickable = $clickable.Count
            named_sample = ($named | Select-Object -First 8 | ForEach-Object { $_.name }) -join ' | '
        }
        # 每个 HWND 的完整树落本地 TEMP（不进仓库）
        $treePath = Join-Path $env:TEMP "weixin-probe-p1-tree-$($h.Hwnd).json"
        $tree | ConvertTo-Json -Depth 4 | Set-Content -Path $treePath -Encoding UTF8
    }

    Write-Host "[6] 各 HWND UIA 摘要："
    $allSummary | Format-Table -AutoSize | Out-String | Write-Host

    # 截图（含查询词的真实结果，便于和 UIA 节点对照）
    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    $w = $rect.Right - $rect.Left; $h = $rect.Bottom - $rect.Top
    $bmp = New-Object Drawing.Bitmap $w, $h
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object Drawing.Size $w, $h))
    $shotPath = Join-Path $env:TEMP 'weixin-probe-p1-observe.png'
    $bmp.Save($shotPath, [Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Write-Host "[7] 截图: $shotPath"
    Write-Host "[7] 主窗口 rect=($($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)) ${w}x${h}"

    Write-Host "PROBE_RESULT: OBSERVE_DONE"
} finally {
    if ($null -ne $script:originalClipboard) {
        try { Set-ClipboardTextRetry $script:originalClipboard } catch {}
    }
    [void]$mutex.ReleaseMutex()
    $mutex.Dispose()
}
