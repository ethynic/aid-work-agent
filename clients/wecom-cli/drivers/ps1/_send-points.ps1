# 双点标定与企微标题复制；UTF-8 BOM
function Get-WeComPointCachePath {
    return (Join-Path (Join-Path $env:LOCALAPPDATA 'AidWorkAgent\wecom-cli') 'send-points.json')
}

function Get-WeComCachedPoints([int64]$Hwnd) {
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $dpi = [int][WeComWin32]::GetDpiForWindow([IntPtr]$Hwnd)
    $path = Get-WeComPointCachePath
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { $point = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $null }
    if ($point.schema -ne 2 -or $point.w -ne $main.W -or $point.h -ne $main.H -or
            $point.dpi -ne $dpi -or $point.title_x -isnot [int] -or
            $point.title_y -isnot [int] -or $point.input_x -isnot [int] -or
            $point.input_y -isnot [int] -or $point.title_x -lt 0 -or
            $point.title_x -ge $main.W -or $point.title_y -lt 0 -or
            $point.title_y -ge $main.H -or $point.input_x -lt 0 -or
            $point.input_x -ge $main.W -or $point.input_y -lt 0 -or
            $point.input_y -ge $main.H) { return $null }
    return @{ x = [int]$point.title_x; y = [int]$point.title_y;
        input_x = [int]$point.input_x; input_y = [int]$point.input_y;
        cache_path = $path; source = 'cache' }
}

function Get-WeComTitlePoint([int64]$Hwnd, [string]$ImagePath) {
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $dpi = [int][WeComWin32]::GetDpiForWindow([IntPtr]$Hwnd)
    if ($dpi -le 0) { Throw-DriverError 'UI_CHANGED' '无法取得企业微信窗口 DPI，未发送消息' }
    $url = ([string]$env:AID_WECOM_SERVER_URL).TrimEnd('/')
    $token = [string]$env:AID_WECOM_SERVER_TOKEN
    if (-not $url -or -not $token) { Throw-DriverError 'CONFIG_MISSING' '缺少 GLM 标定服务配置，未发送消息' }
    Add-Type -AssemblyName System.Drawing
    $source = [Drawing.Image]::FromFile($ImagePath)
    try {
        if ($source.Width -ne $main.W -or $source.Height -ne $main.H) {
            Throw-DriverError 'UI_CHANGED' '标定截图与当前窗口尺寸不一致，未发送消息'
        }
        $memory = [IO.MemoryStream]::new()
        try {
            $source.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
            $bytes = $memory.ToArray()
        } finally { $memory.Dispose() }
    } finally { $source.Dispose() }
    $image = 'data:image/png;base64,' + [Convert]::ToBase64String($bytes)
    $system = '只定位企业微信右侧聊天区顶部标题第一个字中心，以及底部大块文本输入框中心。返回单个 JSON 对象，字段 divider_x、title_x、title_y、input_x、input_y 均为相对完整截图左上角的整数像素坐标。divider_x 是左侧列表与右侧聊天区分界线。看不清时返回 {"error":"point_not_found"}。不要返回文字内容。'
    $request = @{
        model = 'GLM-5.3-Flash'; purpose = 'wecom-title-coordinate'; temperature = 0
        messages = @(
            @{ role = 'system'; content = $system },
            @{ role = 'user'; content = @(
                @{ type = 'text'; text = '按截图原像素给出两个中心坐标；不要缩放。' },
                @{ type = 'image_url'; image_url = @{ url = $image } }
            ) }
        )
    }
    try {
        $body = [Text.Encoding]::UTF8.GetBytes(($request | ConvertTo-Json -Depth 8 -Compress))
        $response = Invoke-RestMethod -Uri ($url + '/api/client/v1/llm/chat') -Method Post -Headers @{ Authorization = ('Bearer ' + $token) } -ContentType 'application/json' -Body $body -TimeoutSec 60
    } catch {
        $status = 0
        if ($null -ne $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 401) { Throw-DriverError 'CONFIG_MISSING' 'GLM 标定鉴权失败，未发送消息' }
        Throw-DriverError 'CONTENT_UNAVAILABLE' ('GLM 双点标定失败（HTTP ' + $status + '），未发送消息')
    }
    $raw = ([string]$response.content).Trim()
    if ($raw.StartsWith('```')) { $raw = ($raw -replace '^```(?:json)?\s*', '' -replace '\s*```$', '').Trim() }
    try { $point = $raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Throw-DriverError 'CONTENT_UNAVAILABLE' 'GLM 双点坐标不是 JSON，未发送消息' }
    foreach ($field in @('divider_x','title_x','title_y','input_x','input_y')) {
        if ($point.$field -isnot [int] -and $point.$field -isnot [long]) {
            Throw-DriverError 'CONTENT_UNAVAILABLE' ('GLM 双点坐标缺少整数 ' + $field + '，未发送消息')
        }
    }
    if ($point.error -or $point.divider_x -lt [int]($main.W * 0.20) -or
            $point.divider_x -gt [int]($main.W * 0.70) -or
            $point.title_x -lt ($point.divider_x + [int](8 * $dpi / 96)) -or
            $point.title_x -gt ($point.divider_x + [int](100 * $dpi / 96)) -or
            $point.title_y -lt [int](12 * $dpi / 96) -or
            $point.title_y -gt [int](56 * $dpi / 96) -or
            $point.input_x -le $point.divider_x -or $point.input_x -ge $main.W -or
            $point.input_y -le [int]($main.H * 0.80) -or $point.input_y -ge $main.H) {
        Throw-DriverError 'CONTENT_UNAVAILABLE' 'GLM 双点不在标题或输入区域，未发送消息'
    }
    return @{ x = [int]$point.title_x; y = [int]$point.title_y;
        input_x = [int]$point.input_x; input_y = [int]$point.input_y;
        w = $main.W; h = $main.H; dpi = $dpi;
        cache_path = (Get-WeComPointCachePath); source = 'glm' }
}

function Save-WeComTitlePoint($Point) {
    $dir = Split-Path -Parent $Point.cache_path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    @{ schema = 2; w = $Point.w; h = $Point.h; dpi = $Point.dpi;
        title_x = $Point.x; title_y = $Point.y;
        input_x = $Point.input_x; input_y = $Point.input_y } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $Point.cache_path -Encoding UTF8
}

function Clear-WeComCachedTitlePointOnCopyFailure($Point, [string]$Failure) {
    if ($Point.source -ne 'cache' -or
            $Failure -notin @('title_edit_not_focused', 'title_copy_unconfirmed')) { return }
    if (Test-Path -LiteralPath $Point.cache_path) {
        Remove-Item -LiteralPath $Point.cache_path -Force -ErrorAction Stop
    }
}
function Send-WeComTitleDoubleClick([int64]$Hwnd, [int]$ScreenX, [int]$ScreenY) {
    # 两次 WM_LBUTTONDOWN 只是两个单击；企微单聊标题需真正的 WM_LBUTTONDBLCLK。
    $point = New-Object WeComWin32+POINT
    $point.X = $ScreenX; $point.Y = $ScreenY
    [void][WeComWin32]::ScreenToClient([IntPtr]$Hwnd, [ref]$point)
    $lp = [IntPtr](($point.Y -shl 16) -bor ($point.X -band 0xFFFF))
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0201, [IntPtr]1, $lp)
    Start-Sleep -Milliseconds 60
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0202, [IntPtr]::Zero, $lp) -and $ok
    Start-Sleep -Milliseconds 80
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0203, [IntPtr]1, $lp) -and $ok
    Start-Sleep -Milliseconds 60
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0202, [IntPtr]::Zero, $lp) -and $ok
    if (-not $ok) { Throw-DriverError 'UI_CHANGED' '单聊标题双击消息未成功投递，未触碰输入区' }
}

function Get-WeComTitleEditWindow([int64]$Hwnd, [int]$ScreenX, [int]$ScreenY) {
    # 标题编辑框可能已因上一次点击存在；从目标主窗枚举，避免 WindowFromPoint
    # 在企微被其他窗口遮挡时误取到前台应用的 HWND。
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $dpi = [int][WeComWin32]::GetDpiForWindow([IntPtr]$Hwnd)
    $headerBottom = $main.Y + [int][Math]::Ceiling(64 * $dpi / 96)
    $edits = New-Object System.Collections.ArrayList
    $callback = [WeComWin32+EnumWindowsProc]{
        param($child, $unused)
        $info = Get-WeComWindowInfo $child
        if ($info.Class -eq 'EditWnd' -and $info.Visible -and $info.Pid -eq $main.Pid -and
                $info.X -le $ScreenX -and $ScreenX -lt ($info.X + $info.W) -and
                $info.Y -le $ScreenY -and $ScreenY -lt ($info.Y + $info.H) -and
                $info.Y -ge $main.Y -and ($info.Y + $info.H) -le $headerBottom) {
            [void]$edits.Add($info.Hwnd)
        }
        return $true
    }
    [void][WeComWin32]::EnumChildWindows([IntPtr]$Hwnd, $callback, [IntPtr]::Zero)
    if ($edits.Count -gt 1) { Throw-DriverError 'UI_CHANGED' '标题位置存在多个编辑框，未触碰输入区' }
    if ($edits.Count -eq 0) { return [IntPtr]::Zero }
    return [IntPtr][int64]$edits[0]
}

function Get-WeComGroupTitleEditWindow([int64]$Hwnd, [int]$ScreenX, [int]$ScreenY) {
    $edit = Get-WeComTitleEditWindow -Hwnd $Hwnd -ScreenX $ScreenX -ScreenY $ScreenY
    if ($edit -ne [IntPtr]::Zero) { return $edit }
    # 一次点击会从未选中态选中完整群名；重复点击可能取消全选。
    [void](Send-WeComClick -Hwnd $Hwnd -ScreenX $ScreenX -ScreenY $ScreenY)
    Start-Sleep -Milliseconds 150
    $edit = Get-WeComTitleEditWindow -Hwnd $Hwnd -ScreenX $ScreenX -ScreenY $ScreenY
    if ($edit -eq [IntPtr]::Zero) { throw 'title_edit_not_focused' }
    return $edit
}

function Write-WeComTitleCopyTrace($Clock, [string]$Phase, [int]$MainPid) {
    # Metadata only: never record clipboard text, title or nonce.
    $sequence = [WeComWin32]::GetClipboardSequenceNumber()
    $owner = [WeComWin32]::GetClipboardOwner()
    $ownerPid = 0
    if ($owner -ne [IntPtr]::Zero) {
        [void][WeComWin32]::GetWindowThreadProcessId($owner, [ref]$ownerPid)
    }
    Write-DriverLog ('title_copy phase=' + $Phase + ' elapsed_ms=' + $Clock.ElapsedMilliseconds +
        ' seq=' + $sequence + ' owner_pid=' + $ownerPid + ' owner_is_wecom=' + ($ownerPid -eq $MainPid))
}

function Get-WeComCopiedTitle([int64]$Hwnd, [string]$Section, $Point) {
    # 保存纯文本剪贴板 → 写唯一 nonce → 选中标题复制 → 按序号 CAS 恢复。
    # 原剪贴板是图片/富格式时不触碰；若外部进程同时改剪贴板，不覆盖其新内容。
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $screenX = [int]($main.X + $Point.x)
    $screenY = [int]($main.Y + $Point.y)
    $nonce = 'AidWecomTitle:' + [guid]::NewGuid().ToString('N')
    $window = $null; $original = $null; $touched = $false
    $copied = ''; $copySeq = $null; $nonceSeq = $null; $failure = ''
    $restored = $false
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $phase = 'original_read'
    try {
        $originalSeq = [WeComWin32]::GetClipboardSequenceNumber()
        Write-WeComTitleCopyTrace $clock 'original_read_begin' $main.Pid
        $original = Get-WeComPlainClipboard
        Write-WeComTitleCopyTrace $clock 'original_read_end' $main.Pid
        if ($originalSeq -ne [WeComWin32]::GetClipboardSequenceNumber()) { throw 'clipboard_changed' }
        $window = New-WeComClipboardOwner
        $written = [uint32]0
        $touched = $true
        $phase = 'nonce_exchange'
        Write-WeComTitleCopyTrace $clock 'nonce_exchange_begin' $main.Pid
        if (-not [WeComWin32]::ExchangeClipboardText($window.Handle, $originalSeq, $original.text,
                $original.empty, $nonce, $false, [ref]$written)) {
            # A false CAS return is before EmptyClipboard; no restore is needed.
            $touched = $false
            throw 'clipboard_changed'
        }
        Write-WeComTitleCopyTrace $clock 'nonce_exchange_end' $main.Pid
        $nonceSeq = [WeComWin32]::GetClipboardSequenceNumber()
        $phase = 'nonce_read'
        Write-WeComTitleCopyTrace $clock 'nonce_read_begin' $main.Pid
        $check = Get-WeComPlainClipboard
        Write-WeComTitleCopyTrace $clock 'nonce_read_end' $main.Pid
        if ($check.empty -or $check.text -cne $nonce -or
                [WeComWin32]::GetClipboardOwner() -ne $window.Handle -or
                $nonceSeq -ne [WeComWin32]::GetClipboardSequenceNumber()) { throw 'clipboard_nonce_unconfirmed' }
        if (-not [WeComWin32]::IsWindow([IntPtr]$Hwnd)) { throw 'window_changed' }
        $phase = 'title_ui'
        Write-WeComTitleCopyTrace $clock 'title_ui_begin' $main.Pid
        if ($Section -eq 'group') {
            $copyHwnd = Get-WeComGroupTitleEditWindow -Hwnd $Hwnd -ScreenX $screenX -ScreenY $screenY
            if (-not (Send-WeComAttachChordKey -Hwnd $copyHwnd.ToInt64() -Vk 0x41)) { throw 'title_select_failed' }
        } elseif ($Section -eq 'contact' -or $Section -eq 'other') {
            Send-WeComTitleDoubleClick -Hwnd $Hwnd -ScreenX $screenX -ScreenY $screenY
            $copyHwnd = [IntPtr]$Hwnd
        } else {
            throw 'section_missing'
        }
        Write-WeComTitleCopyTrace $clock 'title_ui_end' $main.Pid
        Start-Sleep -Milliseconds 120
        $current = Get-WeComWindowInfo ([IntPtr]$Hwnd)
        if ($current.Pid -ne $main.Pid -or $current.X -ne $main.X -or $current.Y -ne $main.Y -or
                $current.W -ne $main.W -or $current.H -ne $main.H) { throw 'window_changed' }
        $phase = 'copy_chord'
        Write-WeComTitleCopyTrace $clock 'copy_chord_begin' $main.Pid
        if (-not (Send-WeComAttachChordKey -Hwnd $copyHwnd.ToInt64() -Vk 0x43)) { throw 'title_copy_failed' }
        Write-WeComTitleCopyTrace $clock 'copy_chord_end' $main.Pid
        $deadline = [DateTime]::UtcNow.AddMilliseconds(1500)
        while ([DateTime]::UtcNow -lt $deadline) {
            $seq = [WeComWin32]::GetClipboardSequenceNumber()
            if ($seq -ne $nonceSeq) {
                # 企微复制标题会同时发布 WeWork/HTML 富格式；只读取其 UnicodeText。
                $phase = 'copy_read'
                Write-WeComTitleCopyTrace $clock 'copy_read_begin' $main.Pid
                $owner = [WeComWin32]::GetClipboardOwner()
                $ownerPid = 0
                [void][WeComWin32]::GetWindowThreadProcessId($owner, [ref]$ownerPid)
                if ($owner -ne $window.Handle -and $ownerPid -ne $main.Pid) {
                    throw 'clipboard_changed'
                }
                $read = @{ empty = -not [Windows.Forms.Clipboard]::ContainsText(); text = [Windows.Forms.Clipboard]::GetText() }
                Write-WeComTitleCopyTrace $clock 'copy_read_end' $main.Pid
                if ($seq -eq [WeComWin32]::GetClipboardSequenceNumber() -and
                        $ownerPid -eq $main.Pid -and -not $read.empty -and
                        $read.text -cne $nonce -and $read.text.Length -gt 0) {
                    $copied = $read.text; $copySeq = $seq
                    break
                }
            }
            Start-Sleep -Milliseconds 75
        }
        if ($null -eq $copySeq) { throw 'title_copy_unconfirmed' }
    } catch {
        $failure = $_.Exception.Message
        Write-WeComTitleCopyTrace $clock ($phase + '_failed') $main.Pid
    }
    finally {
        if ($touched -and $null -ne $original) {
            $restoreReason = 'not_attempted'
            try {
                $phase = 'restore_read'
                Write-WeComTitleCopyTrace $clock 'restore_read_begin' $main.Pid
                $owner = [WeComWin32]::GetClipboardOwner()
                $ownerPid = 0
                [void][WeComWin32]::GetWindowThreadProcessId($owner, [ref]$ownerPid)
                if ($owner -ne $window.Handle -and $ownerPid -ne $main.Pid) {
                    $restoreReason = 'external_owner'
                } else {
                    $seq = [WeComWin32]::GetClipboardSequenceNumber()
                    $now = @{ empty = -not [Windows.Forms.Clipboard]::ContainsText(); text = [Windows.Forms.Clipboard]::GetText() }
                    Write-WeComTitleCopyTrace $clock 'restore_read_end' $main.Pid
                    $stable = $seq -eq [WeComWin32]::GetClipboardSequenceNumber()
                    $nonceOwned = $null -ne $window -and -not $now.empty -and
                        [WeComWin32]::GetClipboardOwner() -eq $window.Handle -and $now.text -ceq $nonce
                    $copyOwned = $null -ne $copySeq -and $seq -eq $copySeq -and $now.text -ceq $copied
                    if ($stable -and ($nonceOwned -or $copyOwned)) {
                        $written = [uint32]0
                        $restoreReason = 'cas_rejected'
                        $phase = 'restore_exchange'
                        Write-WeComTitleCopyTrace $clock 'restore_exchange_begin' $main.Pid
                        $restored = [WeComWin32]::ExchangeClipboardText($window.Handle, $seq, $now.text,
                            $now.empty, $original.text, $original.empty, [ref]$written)
                        Write-WeComTitleCopyTrace $clock 'restore_exchange_end' $main.Pid
                    } elseif (-not $stable) {
                        $restoreReason = 'sequence_changed'
                    } else {
                        $restoreReason = 'ownership_unconfirmed'
                    }
                }
            } catch { $restoreReason = $phase + '_exception' }
            if (-not $restored) {
                Write-WeComTitleCopyTrace $clock ('restore_' + $restoreReason) $main.Pid
                if ($failure) { $failure += ';restore=' + $restoreReason }
                else { $failure = 'clipboard_restore_failed:' + $restoreReason }
            }
        }
        if ($null -ne $window) { $window.Dispose() }
    }
    if ($failure) {
        try { Clear-WeComCachedTitlePointOnCopyFailure -Point $Point -Failure $failure }
        catch { $failure += ';title_cache_invalidate_failed' }
    }
    if ($failure) { Throw-DriverError 'UI_CHANGED' ('标题复制或剪贴板恢复失败（' + $failure + '），未触碰输入区或发送消息') }
    return $copied
}


function Test-WeComTitleMatch {
    param([string]$Title, [string]$Expected, [switch]$RequireWechat, [switch]$Group)
    if ($Group) { return [string]::Equals($Title, $Expected, [StringComparison]::Ordinal) }
    if ($RequireWechat) {
        $name = $Expected -replace '@微信$', ''
        return [string]::Equals($Title, ($name + '@微信'), [StringComparison]::Ordinal) -or
            [string]::Equals($Title, ($name + ' @微信'), [StringComparison]::Ordinal)
    }
    return [string]::Equals($Title, $Expected, [StringComparison]::Ordinal)
}

function Test-WeComSendTitleMatch {
    param([string]$Title, [string]$Expected, [string]$Section)
    # 与 chat-select 一致：先严格全等；只有联系人可再接受两种完整微信后缀。
    $matched = Test-WeComTitleMatch -Title $Title -Expected $Expected -Group:($Section -eq 'group')
    if (-not $matched -and $Section -eq 'contact') {
        $matched = Test-WeComTitleMatch -Title $Title -Expected $Expected -RequireWechat
    }
    return $matched
}

function Assert-WeComSearchCaret([int64]$Hwnd) {
    # 2026-11-06 真机只按 Ctrl+F：主线程 focus/caret 均为主 HWND，caret=(212,27)；
    # 同刻截图搜索框 x≈176..353、y≈23..54。无该证据时不能 Ctrl+A/Delete。
    if (-not ('WeComSearchFocusWin32' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WeComSearchFocusWin32 {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct GUITHREADINFO {
        public uint cbSize, flags;
        public IntPtr hwndActive, hwndFocus, hwndCapture, hwndMenuOwner, hwndMoveSize, hwndCaret;
        public RECT rcCaret;
    }
    [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint thread, ref GUITHREADINFO info);
}
'@
    }
    $ownerPid = [uint32]0
    $thread = [WeComWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd, [ref]$ownerPid)
    $info = New-Object WeComSearchFocusWin32+GUITHREADINFO
    $info.cbSize = [Runtime.InteropServices.Marshal]::SizeOf([type][WeComSearchFocusWin32+GUITHREADINFO])
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $dpi = [int][WeComWin32]::GetDpiForWindow([IntPtr]$Hwnd)
    $scale = $dpi / 96.0
    if ($thread -eq 0 -or $ownerPid -ne $main.Pid -or $dpi -le 0 -or
            -not [WeComSearchFocusWin32]::GetGUIThreadInfo($thread, [ref]$info) -or
            $info.hwndActive -ne [IntPtr]$Hwnd -or $info.hwndFocus -ne [IntPtr]$Hwnd -or
            $info.hwndCaret -ne [IntPtr]$Hwnd -or
            $info.rcCaret.Left -lt [int](166 * $scale) -or
            $info.rcCaret.Right -gt [int](365 * $scale) -or
            $info.rcCaret.Top -lt [int](16 * $scale) -or
            $info.rcCaret.Bottom -gt [int](60 * $scale)) {
        Throw-DriverError 'UI_CHANGED' '无法证明搜索框获得焦点，未清理草稿或输入搜索词'
    }
}

function Open-WeComNamedChat([int64]$Hwnd, [string]$Name) {
    $before = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    [void](Focus-WeComSearchBox -MainHwnd $Hwnd)
    Assert-WeComSearchCaret -Hwnd $Hwnd
    if (-not (Clear-WeComFocusedInput -Hwnd $Hwnd)) {
        Throw-DriverError 'UI_CHANGED' '搜索框清理键未成功投递，未输入搜索词'
    }
    Assert-WeComSearchCaret -Hwnd $Hwnd
    $prior = @(Get-WeComTopLevelWindows | Where-Object {
        $_.Class -eq 'SearchResultWindow2' -and $_.Visible -and $_.Pid -eq $before.Pid
    })
    if ($prior.Count -ne 0) { Throw-DriverError 'UI_CHANGED' '清空搜索框后仍有结果面板，未输入搜索词' }
    Send-WeComText -Hwnd $Hwnd -Text $Name
    Start-Sleep -Milliseconds 400
    $overlayHwnd = Wait-WeComWindow -ClassName 'SearchResultWindow2' -TimeoutMs 8000
    if ($overlayHwnd -eq 0) { Throw-DriverError 'UI_CHANGED' '本次搜索结果面板未出现，未按 Enter' }
    $overlay = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
    if ($overlay.Pid -ne $before.Pid -or -not $overlay.Visible -or
            -not (Test-WeComSearchWindowUnchanged $before (Get-WeComWindowInfo ([IntPtr]$Hwnd)))) {
        Throw-DriverError 'UI_CHANGED' '搜索结果面板或主窗口身份变化，未按 Enter'
    }
    Send-WeComEnter -Hwnd $Hwnd
    Start-Sleep -Milliseconds 700
}

function Invoke-WeComCalibration([string]$SelfName, [string]$ArtifactDir) {
    $mainHwnd = Resolve-WeComMainWindow
    Open-WeComNamedChat -Hwnd $mainHwnd -Name $SelfName
    $mainHwnd = Resolve-WeComMainWindow
    $path = Join-Path $ArtifactDir 'calibration.png'
    [void](Get-WeComWindowSnapshot -Hwnd $mainHwnd -Path $path)
    $point = Get-WeComTitlePoint -Hwnd $mainHwnd -ImagePath $path
    $title = Get-WeComCopiedTitle -Hwnd $mainHwnd -Section 'contact' -Point $point
    if (-not [string]::Equals($title, $SelfName, [StringComparison]::Ordinal)) {
        Throw-DriverError 'UI_CHANGED' '自会话标题与本人显示名不一致，未缓存坐标或发送消息'
    }
    Save-WeComTitlePoint $point
    Write-DriverLog ('双点标定完成 title=(' + $point.x + ',' + $point.y + ') input=(' + $point.input_x + ',' + $point.input_y + ')')
    return $point
}
