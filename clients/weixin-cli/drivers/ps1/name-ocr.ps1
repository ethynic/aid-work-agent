param([Parameter(Mandatory)][string]$InputFile)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
Add-Type -AssemblyName System.Security
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AidOcrPixels {
 public static byte[] Rgba(IntPtr scan, int width, int height, int stride) {
  byte[] output=new byte[checked(width*height*4)], row=new byte[width*4];
  for(int y=0;y<height;y++) {
   Marshal.Copy(IntPtr.Add(scan,y*stride),row,0,row.Length);
   for(int x=0;x<width;x++) {int i=x*4,o=(y*width+x)*4;output[o]=row[i+2];output[o+1]=row[i+1];output[o+2]=row[i];output[o+3]=255;}
  }
  return output;
 }
}
'@
Initialize-WeixinProbeWin32
Set-WeixinProbeDpiContext
$work=Join-Path $env:TEMP ('aid-local-ocr-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
function Hash([byte[]]$b){$h=[Security.Cryptography.SHA256]::Create();try{return([BitConverter]::ToString($h.ComputeHash($b))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}}
function CropHash($bmp,$r){$c=$bmp.Clone([Drawing.Rectangle]::new($r.x0,$r.y0,($r.x1-$r.x0),($r.y1-$r.y0)),$bmp.PixelFormat);$s=New-Object IO.MemoryStream;try{$c.Save($s,[Drawing.Imaging.ImageFormat]::Png);return Hash $s.ToArray()}finally{$s.Dispose();$c.Dispose()}}
function SearchCapture {
    $overlay=Get-WeixinSearchOverlayHwnd
    if(-not $overlay){Throw-DriverError 'TARGET_NOT_FOUND' 'Search popup unavailable'}
    $path=Join-Path $work ([guid]::NewGuid().ToString('N')+'.png')
    $rect=Get-WeixinWindowSnapshot -Hwnd $overlay -Path $path
    if(-not $rect[4]){Throw-DriverError 'UI_CHANGED' 'Search PrintWindow required'}
    $bytes=[IO.File]::ReadAllBytes($path)
    return @{handle=$overlay;rect=$rect;width=$rect[2];height=$rect[3];full_frame=(Hash $bytes);png=[Convert]::ToBase64String($bytes)}
}
function Capture {
    param([switch]$SendOnly)
    $path=Join-Path $work ([guid]::NewGuid().ToString('N')+'.png')
    $rect=Get-WeixinWindowSnapshot -Hwnd $script:handle -Path $path
    if(-not $rect[4]){Throw-DriverError 'UI_CHANGED' 'PrintWindow required'}
    $bmp=[Drawing.Bitmap]::FromFile($path)
    try{
        $w=$bmp.Width;$h=$bmp.Height
        # Locate long horizontal composer border in the lower right pane. This
        # uses local pixels only; no screenshot/message is sent to a model.
        $left=[int]($w*0.29);$bestVertical=0
        for($x=[int]($w*0.2);$x -lt [int]($w*0.45);$x++){
            $score=0
            for($y=[int]($h*0.15);$y -lt [int]($h*0.6);$y+=5){$a=$bmp.GetPixel($x,$y);$b=$bmp.GetPixel($x-3,$y);if([Math]::Abs($a.R-$b.R)+[Math]::Abs($a.G-$b.G)+[Math]::Abs($a.B-$b.B) -gt 15){$score++}}
            if($score -gt $bestVertical){$bestVertical=$score;$left=$x}
        }
        if($bestVertical -lt ($h*0.45/5)*0.65){Throw-DriverError 'UI_CHANGED' 'Chat divider unavailable'}
        $bestY=0;$bestScore=0
        for($y=[int]($h*0.65);$y -lt [int]($h*0.93);$y++){
            $score=0
            for($x=[int]($w*0.4);$x -lt [int]($w*0.94);$x+=5){
                $a=$bmp.GetPixel($x,$y);$b=$bmp.GetPixel($x,$y-3)
                if([Math]::Abs($a.R-$b.R)+[Math]::Abs($a.G-$b.G)+[Math]::Abs($a.B-$b.B) -gt 18){$score++}
            }
            if($score -gt $bestScore){$bestY=$y;$bestScore=$score}
        }
        if($bestScore -lt ($w*0.54/5)*0.7){Throw-DriverError 'UI_CHANGED' 'Composer border unavailable'}
        $title=@{x0=$left;y0=[int]($h*0.03);x1=[int]($w*0.75);y1=[int]($h*0.08)}
        $messages=@{x0=$left;y0=[int]($h*0.08);x1=$w-12;y1=$bestY-3}
        $input=@{x0=$left+30;y0=$bestY+18;x1=$w-35;y1=$h-90}
        if($SendOnly){
            return @{handle=$script:handle;rect=@($rect[0],$rect[1],$rect[2],$rect[3]);width=$w;height=$h;title_region=$title;input_region=$input;png=[Convert]::ToBase64String([IO.File]::ReadAllBytes($path))}
        }
        $watch=@{x0=$left;y0=0;x1=$w;y1=$bestY+4}
        $frame=Hash ([Text.Encoding]::ASCII.GetBytes("$w,$h,$bestY,"+(CropHash $bmp $watch)))
        $full=Hash ([IO.File]::ReadAllBytes($path))
        $locked=$bmp.LockBits([Drawing.Rectangle]::new(0,0,$w,$h),[Drawing.Imaging.ImageLockMode]::ReadOnly,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
        try{
            $rgba=[AidOcrPixels]::Rgba($locked.Scan0,$w,$h,$locked.Stride)
        }finally{$bmp.UnlockBits($locked)}
        return @{path=$path;rect=$rect;frame=$frame;full_frame=$full;title_frame=(CropHash $bmp $title);width=$w;height=$h;title_region=$title;message_region=$messages;input_region=$input;png=[Convert]::ToBase64String([IO.File]::ReadAllBytes($path));rgba=[Convert]::ToBase64String($rgba)}
    }finally{$bmp.Dispose()}
}
# Receipt acknowledges accepted input events, not message delivery.
function Send-CheckedComposerClick {
    param([int64]$Hwnd,[int]$ScreenX,[int]$ScreenY)
    $pt=New-Object WeixinProbeWin32+POINT
    $pt.X=$ScreenX;$pt.Y=$ScreenY
    if(-not [WeixinProbeWin32]::ScreenToClient([IntPtr]$Hwnd,[ref]$pt)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Composer coordinates unavailable'}
    $lp=[IntPtr](($pt.Y -shl 16) -bor ($pt.X -band 0xFFFF))
    if(-not [WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd,0x0200,[IntPtr]::Zero,$lp)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Composer move rejected'}
    Start-Sleep -Milliseconds 60
    if(-not [WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd,0x0201,[IntPtr]1,$lp)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Composer press rejected'}
    Start-Sleep -Milliseconds 60
    if(-not [WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd,0x0202,[IntPtr]::Zero,$lp)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Composer release rejected'}
    Start-Sleep -Milliseconds 150
}
try{
    $raw=[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String([IO.File]::ReadAllText($InputFile)),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $req=([Text.Encoding]::UTF8.GetString($raw))|ConvertFrom-Json
    $windows=@(Get-VisibleWindowList|Where-Object{Test-WeixinExecutablePath ([string]$_.ProcessPath)}|ForEach-Object{Get-WindowIdentityByHwnd ([int64]$_.Hwnd)}|Where-Object{$_.Title -ceq $script:WeixinTitle})
    if($windows.Count -ne 1){Throw-DriverError 'WINDOW_AMBIGUOUS' 'Unique main window required'}
    $script:handle=[int64]$windows[0].Hwnd
    if($req.action -eq 'search'){
        if(-not (Invoke-WeixinActivation $script:handle)){Throw-DriverError 'FOREGROUND_LOST' 'Search requires foreground'}
        # Search is available on the empty home screen; it needs only window geometry.
        $searchRect=New-Object WeixinProbeWin32+RECT
        if(-not [WeixinProbeWin32]::GetWindowRect([IntPtr]$script:handle,[ref]$searchRect)){Throw-DriverError 'UI_CHANGED' 'Search window geometry unavailable'}
        Send-WeixinPostMessageClick -Hwnd $script:handle -ScreenX ($searchRect.Left+[int](($searchRect.Right-$searchRect.Left)*0.17)) -ScreenY ($searchRect.Top+[int](($searchRect.Bottom-$searchRect.Top)*0.06))
        Start-Sleep -Milliseconds 350
        Send-WeixinKeyChord -Keys @('CTRL','A') -ForegroundGuard{[WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $script:handle}
        Send-WeixinPostMessageText -Hwnd $script:handle -Text ([string]$req.target_name)
        Start-Sleep -Milliseconds 700
        Write-DriverJson @{ok=$true;data=@{done=$true}}
    }elseif($req.action -eq 'select'){
        $shot=SearchCapture
        if([double]$req.x -le 0 -or [double]$req.x -ge $shot.width -or [double]$req.y -le 0 -or [double]$req.y -ge $shot.height){Throw-DriverError 'UI_CHANGED' 'Search result changed'}
        Send-WeixinPostMessageClick -Hwnd $shot.handle -ScreenX ($shot.rect[0]+[int]$req.x) -ScreenY ($shot.rect[1]+[int]$req.y)
        Start-Sleep -Milliseconds 500
        Write-DriverJson @{ok=$true;data=@{done=$true}}
    }elseif($req.action -eq 'submit'){
        $timer=[Diagnostics.Stopwatch]::StartNew()
        $text=[string]$req.text
        if(-not $text -or $text.Length -gt 500 -or $text -match '[\x00-\x1f\x7f-\x9f]'){Throw-DriverError 'UI_CHANGED' 'Invalid text'}
        if([int64]$req.handle -ne $script:handle -or @($req.rect).Count -lt 4){Throw-DriverError 'UI_CHANGED' 'Send window changed'}
        $expected=@($req.rect)
        $r=$req.input_region
        if($null -eq $r -or $r.x0 -lt 0 -or $r.y0 -lt 0 -or $r.x1 -le $r.x0 -or $r.y1 -le $r.y0 -or $r.x1 -gt $expected[2] -or $r.y1 -gt $expected[3]){Throw-DriverError 'UI_CHANGED' 'Composer coordinates invalid'}
        function Assert-SendWindow {
            if([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -ge [int64]$req.deadline_ms){Throw-DriverError 'UI_CHANGED' 'Action deadline expired'}
            if([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -ne $script:handle){Throw-DriverError 'FOREGROUND_LOST' 'Send focus changed'}
            $current=New-Object WeixinProbeWin32+RECT
            if(-not [WeixinProbeWin32]::GetWindowRect([IntPtr]$script:handle,[ref]$current) -or $current.Left -ne $expected[0] -or $current.Top -ne $expected[1] -or ($current.Right-$current.Left) -ne $expected[2] -or ($current.Bottom-$current.Top) -ne $expected[3]){Throw-DriverError 'UI_CHANGED' 'Send window moved or resized'}
        }
        Assert-SendWindow
        Send-CheckedComposerClick -Hwnd $script:handle -ScreenX ($expected[0]+[int](($r.x0+$r.x1)/2)) -ScreenY ($expected[1]+[int](($r.y0+$r.y1)/2))
        $clickMs=$timer.ElapsedMilliseconds
        # Keep the copied text on the clipboard, as an ordinary copy/paste does.
        # Restoring immediately would race the target application's paste handler.
        Set-ClipboardTextRetry -Text $text
        $clipboardMs=$timer.ElapsedMilliseconds-$clickMs
        Assert-SendWindow
        # One ordered input batch: select all, paste the whole text, then Enter.
        # SendInput serializes the events; no per-character delay or screenshot.
        $events=New-Object 'System.Collections.Generic.List[WeixinProbeWin32+INPUT]'
        foreach($key in @(@(0x11,0),@(0x41,0),@(0x41,2),@(0x11,2),@(0x11,0),@(0x56,0),@(0x56,2),@(0x11,2),@(0x0D,0),@(0x0D,2))){
            $event=New-Object WeixinProbeWin32+INPUT
            $keyboard=New-Object WeixinProbeWin32+KEYBDINPUT
            $keyboard.wVk=[uint16]$key[0];$keyboard.dwFlags=[uint32]$key[1]
            $union=New-Object WeixinProbeWin32+INPUTUNION
            $union.ki=$keyboard
            $event.type=1;$event.u=$union
            $events.Add($event)
        }
        $sent=[WeixinProbeWin32]::SendInput($events.Count,$events.ToArray(),[Runtime.InteropServices.Marshal]::SizeOf([type][WeixinProbeWin32+INPUT]))
        if($sent -ne $events.Count){
            # Release modifiers on partial insertion; never retry paste or Enter.
            [WeixinProbeWin32]::keybd_event(0x11,0,2,[IntPtr]::Zero)
            [WeixinProbeWin32]::keybd_event(0x41,0,2,[IntPtr]::Zero)
            [WeixinProbeWin32]::keybd_event(0x56,0,2,[IntPtr]::Zero)
            [WeixinProbeWin32]::keybd_event(0x0D,0,2,[IntPtr]::Zero)
            Throw-DriverError 'EXECUTION_UNKNOWN' 'Input batch incomplete'
        }
        Write-DriverJson @{ok=$true;data=@{done=$true;timings_ms=@{click=$clickMs;clipboard=$clipboardMs;input=($timer.ElapsedMilliseconds-$clickMs-$clipboardMs);total=$timer.ElapsedMilliseconds}}}
    }elseif($req.action -eq 'type' -or $req.action -eq 'enter'){
        if($req.action -eq 'type' -and -not (Invoke-WeixinActivation $script:handle)){Throw-DriverError 'FOREGROUND_LOST' 'Composer replacement requires foreground'}
        $shot=Capture
        if([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -ge [int64]$req.deadline_ms){Throw-DriverError 'UI_CHANGED' 'Action deadline expired'}
        if($req.action -eq 'type'){
            $text=[string]$req.text
            if(-not $text -or $text.Length -gt 500 -or $text -match '[\x00-\x1f\x7f-\x9f]'){Throw-DriverError 'UI_CHANGED' 'Invalid text'}
            $r=$shot.input_region
            Send-CheckedComposerClick -Hwnd $script:handle -ScreenX ($shot.rect[0]+[int](($r.x0+$r.x1)/2)) -ScreenY ($shot.rect[1]+[int](($r.y0+$r.y1)/2))
            Send-WeixinKeyChord -Keys @('CTRL','A') -ForegroundGuard{[WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $script:handle}
            if([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -ne $script:handle){Throw-DriverError 'FOREGROUND_LOST' 'Composer focus changed'}
            foreach($ch in $text.ToCharArray()){
                if(-not [WeixinProbeWin32]::PostMessage([IntPtr]$script:handle,0x0102,[IntPtr][int][char]$ch,[IntPtr]1)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Text event rejected'}
                Start-Sleep -Milliseconds 30
            }
        }else{
            $r=$shot.input_region
            Send-CheckedComposerClick -Hwnd $script:handle -ScreenX ($shot.rect[0]+[int](($r.x0+$r.x1)/2)) -ScreenY ($shot.rect[1]+[int](($r.y0+$r.y1)/2))
            $lpDown=[IntPtr](1 -bor (0x1C -shl 16))
            $lpUp=[IntPtr]((1 -bor (0x1C -shl 16)) -bor 0xC0000000)
            if(-not [WeixinProbeWin32]::PostMessage([IntPtr]$script:handle,0x0100,[IntPtr]0x0D,$lpDown)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Enter event rejected'}
            Start-Sleep -Milliseconds 40
            if(-not [WeixinProbeWin32]::PostMessage([IntPtr]$script:handle,0x0101,[IntPtr]0x0D,$lpUp)){Throw-DriverError 'EXECUTION_UNKNOWN' 'Enter release rejected'}
        }
        Write-DriverJson @{ok=$true;data=@{done=$true}}
    }elseif($req.action -eq 'send_capture'){
        if(-not (Invoke-WeixinActivation $script:handle)){Throw-DriverError 'FOREGROUND_LOST' 'Send capture requires foreground'}
        $shot=Capture -SendOnly
        Write-DriverJson @{ok=$true;data=$shot}
    }elseif($req.action -eq 'search_capture'){
        $shot=SearchCapture;$shot.Remove('handle');$shot.Remove('rect')
        Write-DriverJson @{ok=$true;data=$shot}
    }elseif($req.action -eq 'capture'){
        $shot=Capture;$shot.Remove('path');$shot.Remove('rect')
        Write-DriverJson @{ok=$true;data=$shot}
    }else{Throw-DriverError 'UI_CHANGED' 'Unsupported local OCR action'}
}catch{
    $safeCode='UI_CHANGED';$m=[regex]::Match([string]$_.Exception.Message,'^WXDRIVE\|([A-Z_]+)\|');if($m.Success){$safeCode=$m.Groups[1].Value}
    # Only fixed driver reasons may enter diagnostics; never echo arbitrary exception text.
    $reason='unclassified'
    $knownReasons=@{
        'Search PrintWindow required'='search_printwindow_unavailable'
        'PrintWindow required'='printwindow_unavailable'
        'Chat divider unavailable'='chat_divider_unavailable'
        'Composer border unavailable'='composer_border_unavailable'
        'Search popup unavailable'='search_popup_unavailable'
        'Unique main window required'='main_window_not_unique'
        'Search requires foreground'='search_foreground_unavailable'
        'Search window geometry unavailable'='search_window_geometry_unavailable'
        'Search result changed'='search_result_changed'
    }
    $detail=[regex]::Match([string]$_.Exception.Message,'^WXDRIVE\|[A-Z_]+\|(.+)$')
    if($detail.Success -and $knownReasons.ContainsKey($detail.Groups[1].Value)){$reason=$knownReasons[$detail.Groups[1].Value]}
    Write-DriverJson @{ok=$false;code=$safeCode;message=('Local OCR precondition failed; reason='+$reason+'; line='+$_.InvocationInfo.ScriptLineNumber+'; type='+$_.Exception.GetType().Name)}
}
finally{if([IO.Directory]::Exists($work)){[IO.Directory]::Delete($work,$true)}}
