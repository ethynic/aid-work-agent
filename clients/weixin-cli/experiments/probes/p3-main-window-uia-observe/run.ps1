# p3-main-window-uia-observe — 正常主窗口 UIA 深度观察
# 目标：验证微信 4.x 正常主窗口（非搜索态）的「会话列表」「对话窗口」两个核心区域
#       是否在 UIA 子树中暴露内部元素（对 p1 §5 搜索态结论的二次确认/推广）。
# 零输入、零点击、零剪贴板改动。仅：激活窗口 → TreeWalker 深度枚举 → 截图。
param(
    [int]$MaxNodesPerHwnd = 15000
)
$ErrorActionPreference = 'Stop'
$probeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $probeDir 'probe-lib.ps1')

# 互斥锁，防止与其他 weixin-cli probe 并发
$mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WeixinCliProbe.MainWindowUiaObserve', [ref]$null)
if (-not $mutex.WaitOne(0)) { throw 'PROBE_BUSY' }
try {
    Initialize-WeixinProbeWin32
    Set-WeixinProbeDpiContext

    # —— 1. 定位并激活主窗口（与 p1 同一套验证）
    $windows = @(Get-VisibleWindowList)
    $main = Select-WeixinMainWindow $windows
    $mainHwnd = [int64]$main.Hwnd
    Write-Host "[1] 主窗口 hwnd=$mainHwnd"
    if (-not (Invoke-WeixinActivation $mainHwnd)) { throw 'WX_ACTIVATION_FAILED' }
    $mainId = Get-WindowIdentityByHwnd $mainHwnd
    if (-not (Test-WeixinMainIdentity $mainId $mainHwnd)) { throw 'MAIN_IDENTITY_MISMATCH' }
    Write-Host ("[1] 主窗口身份校验通过 class={0} title={1}" -f $mainId.ClassName, $mainId.Title)
    Start-Sleep -Milliseconds 400  # 等界面稳定（不动任何键，不切换会话）

    # —— 2. 截图主窗口（供与 UIA BoundingRectangle 对照）
    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    $w = $rect.Right - $rect.Left; $h = $rect.Bottom - $rect.Top
    Write-Host "[2] 主窗口 rect=($($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)) ${w}x${h}"
    $bmp = New-Object Drawing.Bitmap $w, $h
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object Drawing.Size $w, $h))
    $shotPath = Join-Path $env:TEMP 'weixin-probe-p3-main.png'
    $bmp.Save($shotPath, [Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Write-Host "[2] 截图: $shotPath"

    # —— 3. 枚举微信所有相关可见 HWND（主窗口 + Weixin.exe 的其它可见顶层窗口）
    $weixinHwnds = @()
    foreach ($win in @(Get-VisibleWindowList)) {
        $id = Get-WindowIdentityByHwnd ([int64]$win.Hwnd)
        if ($null -eq $id) { continue }
        if (-not (Test-WeixinExecutablePath ([string]$id.ProcessPath))) { continue }
        $weixinHwnds += [pscustomobject]@{
            Hwnd = [int64]$id.Hwnd; ClassName = [string]$id.ClassName
            Title = [string]$id.Title
        }
    }
    Write-Host "[3] Weixin 相关可见 HWND 数=$($weixinHwnds.Count)"
    foreach ($hh in $weixinHwnds) {
        Write-Host ("    hwnd={0} class={1} title={2}" -f $hh.Hwnd, $hh.ClassName, $hh.Title)
    }

    # —— 4. 对每个 HWND 做 TreeWalker 深度枚举（复用 p1 observe-uia.ps1 的 Get-UiaDeepTree 逻辑）
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
                $hasScroll = $node.TryGetCurrentPattern([Windows.Automation.ScrollPattern]::Pattern, [ref]$null)
                $hasText = $node.TryGetCurrentPattern([Windows.Automation.TextPattern]::Pattern, [ref]$null)
                $result.Add([pscustomobject]@{
                    control_type = [string]$cur.ControlType.ProgrammaticName.Replace('ControlType.', '')
                    name = [string]$cur.Name
                    automation_id = [string]$cur.AutomationId
                    class_name = [string]$cur.ClassName
                    left = [double]$b.Left; top = [double]$b.Top
                    width = [double]$b.Width; height = [double]$b.Height
                    is_offscreen = [bool]$cur.IsOffscreen
                    has_invoke = $hasInvoke; has_value = $hasValue
                    has_scroll = $hasScroll; has_text = $hasText
                })
            } catch { continue }
            try {
                $child = $walker.GetFirstChild($node)
                $children = @()
                while ($child) { $children += $child; $child = $walker.GetNextSibling($child) }
            } catch { $children = @() }
            if ($children) {
                for ($i = $children.Count - 1; $i -ge 0; $i--) { $stack.Push($children[$i]) }
            }
        }
        $result.ToArray()
    }

    # —— 5. 逐 HWND 枚举 + 统计 + 落盘
    $allSummary = @()
    foreach ($hh in $weixinHwnds) {
        $tree = @(Get-UiaDeepTree $hh.Hwnd $MaxNodesPerHwnd)
        $named = @($tree | Where-Object { -not [string]::IsNullOrWhiteSpace($_.name) })
        $editable = @($tree | Where-Object { $_.has_value })
        $clickable = @($tree | Where-Object { $_.has_invoke })
        $scrollable = @($tree | Where-Object { $_.has_scroll })
        # 区分「在主窗口 rect 内」的节点（可能对应会话列表/对话窗口区域）
        $inMain = @($tree | Where-Object {
            $_.width -gt 0 -and $_.height -gt 0 -and
            $_.left -ge $rect.Left -and $_.top -ge $rect.Top -and
            ($_.left + $_.width) -le $rect.Right -and ($_.top + $_.height) -le $rect.Bottom
        })
        Write-Host ("[5] hwnd={0} class={1} 总节点={2} 有名={3} 可编辑={4} 可点击={5} 可滚动={6} 落主窗口内={7}" -f `
            $hh.Hwnd, $hh.ClassName, $tree.Count, $named.Count, $editable.Count, $clickable.Count, $scrollable.Count, $inMain.Count)
        $allSummary += [pscustomobject]@{
            hwnd = $hh.Hwnd; class_name = $hh.ClassName; title = $hh.Title
            total = $tree.Count; named = $named.Count
            editable = $editable.Count; clickable = $clickable.Count
            scrollable = $scrollable.Count; in_main = $inMain.Count
            named_sample = ($named | Select-Object -First 10 | ForEach-Object { $_.name }) -join ' | '
        }
        # 完整树落本地 TEMP（含真实会话名/消息内容，不进仓库）
        $treePath = Join-Path $env:TEMP "weixin-probe-p3-tree-$($hh.Hwnd).json"
        $tree | ConvertTo-Json -Depth 4 | Set-Content -Path $treePath -Encoding UTF8
        Write-Host ("    完整树 -> {0}" -f $treePath)
    }

    Write-Host "`n[6] 各 HWND UIA 摘要："
    $allSummary | Format-Table -AutoSize | Out-String | Write-Host

    # —— 7. 机器判定：主窗口内是否有节点能覆盖「会话列表」「对话窗口」区域
    #   会话列表 ≈ 主窗口左侧窄长条；对话窗口 ≈ 主窗口右侧大块。
    #   这里不预设具体像素，只统计「主窗口内可见节点」的尺寸分布，供报告判定。
    $mainTree = @(Get-UiaDeepTree $mainHwnd $MaxNodesPerHwnd)
    $visibleInMain = @($mainTree | Where-Object {
        -not $_.is_offscreen -and $_.width -gt 5 -and $_.height -gt 5
    })
    Write-Host "`n[7] 主窗口内可见(>5px)节点数=$($visibleInMain.Count)"
    if ($visibleInMain.Count -gt 0) {
        Write-Host "    主窗口内可见节点明细："
        $visibleInMain | ForEach-Object {
            Write-Host ("      type={0} name='{1}' class={2} rect=({3:F0},{4:F0},{5:F0}x{6:F0}) invoke={7} value={8} scroll={9}" -f `
                $_.control_type, $_.name, $_.class_name, $_.left, $_.top, $_.width, $_.height, $_.has_invoke, $_.has_value, $_.has_scroll)
        }
    }

    Write-Host "`nPROBE_RESULT: OBSERVE_DONE"
    Write-Host "SUMMARY: weixin_hwnds=$($weixinHwnds.Count) main_total_nodes=$($mainTree.Count) main_visible_nodes=$($visibleInMain.Count) main_named=$(@($mainTree | Where-Object { -not [string]::IsNullOrWhiteSpace($_.name) }).Count)"
} finally {
    [void]$mutex.ReleaseMutex()
    $mutex.Dispose()
}
