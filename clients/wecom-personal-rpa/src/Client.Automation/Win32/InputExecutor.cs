// ====================================================================================
// InputExecutor：把"屏幕绝对坐标 → SendInput 点击"与"剪贴板粘贴文本"封装成一类操作单元。
//
// 设计要点：
//   1) ClickElement(windowOrigin, offsetX, offsetY) — 把窗口相对坐标换算为屏幕绝对坐标，
//      再调 SendInput 绝对坐标点击。
//   2) TypeText(text, pressEnterAfter) — 写剪贴板 → Ctrl+V → 可选 Enter。
//      调用方负责 ClipboardGuard 备份 / 清空 / 还原。
//   3) 失败 loud：坐标越界（超过屏幕分辨率）抛 ArgumentException；SendInput 返回数
//      不匹配抛 AutomationLayerException。
//
// Phase 2：PowerShell 后端会重新决定是否复用本类（坐标点击 + 剪贴板粘贴逻辑通用）。
// ====================================================================================

using System.Runtime.InteropServices;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
// Phase 2 will replace with PowershellAutomationBackend
// using WeCom.PersonalRpa.Automation.Vision;

namespace WeCom.PersonalRpa.Automation.Win32;

/// <summary>
/// 视觉定位 → 屏幕输入执行器。把 bbox 换算成屏幕坐标后点击，以及把文本写入剪贴板后粘贴。
/// 所有 Win32 SendInput 都在本类完成，调用方无需关心坐标换算与 P/Invoke 细节。
/// </summary>
public class InputExecutor
{
    // Phase 2 will replace with PowershellAutomationBackend
    // /// <summary>
    // /// 把 bbox 中心点换算成屏幕绝对坐标后点击。
    // /// </summary>
    // /// <param name="bbox">控件 bbox（截图坐标系，相对企微主窗口左上）。</param>
    // /// <param name="windowOrigin">企微主窗口左上角屏幕坐标 (Left, Top)。</param>
    // /// <exception cref="ArgumentNullException"><paramref name="bbox"/> 为 null。</exception>
    // /// <exception cref="ArgumentException">换算后的屏幕坐标越出屏幕分辨率。</exception>
    // public virtual void ClickElement(BoundingBox bbox, (int Left, int Top) windowOrigin)
    // {
    //     if (bbox is null)
    //     {
    //         throw new ArgumentNullException(nameof(bbox));
    //     }
    //
    //     (int cx, int cy) = bbox.Center;
    //     int screenX = windowOrigin.Left + cx;
    //     int screenY = windowOrigin.Top + cy;
    //
    //     int screenW = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
    //     int screenH = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
    //     if (screenX < 0 || screenY < 0 || screenX > screenW || screenY > screenH)
    //     {
    //         throw new ArgumentException(
    //             $"点击坐标越界：screen=({screenX},{screenY})，分辨率=({screenW},{screenH})，windowOrigin=({windowOrigin.Left},{windowOrigin.Top})，bbox={bbox}");
    //     }
    //
    //     Log.Information(
    //         "后端日志：InputExecutor.ClickElement bbox={Bbox} windowOrigin=({L},{T}) → screen=({X},{Y})",
    //         bbox, windowOrigin.Left, windowOrigin.Top, screenX, screenY);
    //
    //     SendAbsoluteClick(screenX, screenY, screenW, screenH);
    //
    //     // 给目标控件处理点击留出时间（同步降级路径，Thread.Sleep 短时占线程可接受）。
    //     Thread.Sleep(80);
    // }

    /// <summary>
    /// 直接按屏幕绝对坐标点击（几何兜底专用，绕过 bbox 视觉定位）。
    /// 用于视觉定位失败时的降级：调用方按窗口尺寸算出大致坐标后调本方法。
    /// </summary>
    public virtual void ClickAtScreen(int screenX, int screenY)
    {
        int screenW = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
        int screenH = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
        if (screenX < 0 || screenY < 0 || screenX > screenW || screenY > screenH)
        {
            throw new ArgumentException(
                $"点击坐标越界：screen=({screenX},{screenY})，分辨率=({screenW},{screenH})");
        }

        Log.Information(
            "后端日志：InputExecutor.ClickAtScreen screen=({X},{Y}) [几何兜底]",
            screenX, screenY);
        SendAbsoluteClick(screenX, screenY, screenW, screenH);
        Thread.Sleep(80);
    }

    /// <summary>
    /// 剪贴板粘贴文本（调用方负责 ClipboardGuard 备份 / 清空 / 还原）+ 按 Enter。
    /// </summary>
    /// <param name="text">要粘贴的文本。</param>
    /// <param name="pressEnterAfter">粘贴后是否按 Enter。发送消息场景为 true，输入框填充场景为 false。</param>
    /// <exception cref="ArgumentNullException"><paramref name="text"/> 为 null。</exception>
    public virtual void TypeText(string text, bool pressEnterAfter = false)
    {
        if (text is null)
        {
            throw new ArgumentNullException(nameof(text));
        }

        // 写剪贴板（不走 ClipboardGuard，调用方负责备份/还原）。
        // 文本为空时仍粘贴（场景：主动清空输入框），不抛异常。
        try
        {
            WriteClipboardText(text);
        }
        catch (AutomationLayerException)
        {
            throw;
        }
        catch (Exception ex)
        {
            throw new AutomationLayerException("Win32.Input", "写剪贴板失败", ex);
        }

        // Ctrl+V
        SendPaste();

        if (pressEnterAfter)
        {
            SendEnter();
        }
    }

    // =================================================================================
    // 私有：SendInput 拼装
    // =================================================================================

    private static void SendAbsoluteClick(int screenX, int screenY, int screenW, int screenH)
    {
        // SendInput 绝对坐标范围是 [0, 65535]，按比例映射。
        int dx = screenW > 0 ? (int)((double)screenX * 65535 / screenW) : 0;
        int dy = screenH > 0 ? (int)((double)screenY * 65535 / screenH) : 0;

        const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
        const uint MOUSEEVENTF_MOVE = 0x0001;
        const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
        const uint MOUSEEVENTF_LEFTUP = 0x0004;

        var inputs = new NativeMethods.INPUT[3];

        // 1) 移动到目标点
        inputs[0].type = NativeMethods.INPUT_MOUSE;
        inputs[0].u.mi.dx = dx;
        inputs[0].u.mi.dy = dy;
        inputs[0].u.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE;

        // 2) 按下左键
        inputs[1].type = NativeMethods.INPUT_MOUSE;
        inputs[1].u.mi.dx = dx;
        inputs[1].u.mi.dy = dy;
        inputs[1].u.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTDOWN;

        // 3) 释放左键
        inputs[2].type = NativeMethods.INPUT_MOUSE;
        inputs[2].u.mi.dx = dx;
        inputs[2].u.mi.dy = dy;
        inputs[2].u.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTUP;

        uint sent = NativeMethods.SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<NativeMethods.INPUT>());
        if (sent != inputs.Length)
        {
            throw new AutomationLayerException(
                "Win32.Input",
                $"SendInput(MouseClick) 发送数量不匹配，期望 {inputs.Length}，实际 {sent}");
        }
    }

    private static void SendPaste()
    {
        var inputs = new NativeMethods.INPUT[4];

        inputs[0].type = NativeMethods.INPUT_KEYBOARD;
        inputs[0].u.ki.wVk = NativeMethods.VK_CONTROL;
        inputs[0].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYDOWN;

        inputs[1].type = NativeMethods.INPUT_KEYBOARD;
        inputs[1].u.ki.wVk = NativeMethods.VK_V;
        inputs[1].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYDOWN;

        inputs[2].type = NativeMethods.INPUT_KEYBOARD;
        inputs[2].u.ki.wVk = NativeMethods.VK_V;
        inputs[2].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYUP;

        inputs[3].type = NativeMethods.INPUT_KEYBOARD;
        inputs[3].u.ki.wVk = NativeMethods.VK_CONTROL;
        inputs[3].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYUP;

        uint sent = NativeMethods.SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<NativeMethods.INPUT>());
        if (sent != inputs.Length)
        {
            throw new AutomationLayerException(
                "Win32.Input",
                $"SendInput(Ctrl+V) 发送数量不匹配，期望 {inputs.Length}，实际 {sent}");
        }

        Thread.Sleep(80);
    }

    private static void SendEnter()
    {
        var inputs = new NativeMethods.INPUT[2];

        inputs[0].type = NativeMethods.INPUT_KEYBOARD;
        inputs[0].u.ki.wVk = NativeMethods.VK_RETURN;
        inputs[0].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYDOWN;

        inputs[1].type = NativeMethods.INPUT_KEYBOARD;
        inputs[1].u.ki.wVk = NativeMethods.VK_RETURN;
        inputs[1].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYUP;

        uint sent = NativeMethods.SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<NativeMethods.INPUT>());
        if (sent != inputs.Length)
        {
            throw new AutomationLayerException(
                "Win32.Input",
                $"SendInput(Enter) 发送数量不匹配，期望 {inputs.Length}，实际 {sent}");
        }

        Thread.Sleep(50);
    }

    /// <summary>
    /// 用 Win32 原生 API 把文本写入剪贴板（CF_UNICODETEXT）。
    /// 带 OpenClipboard 重试，避免其它进程持有剪贴板锁时失败。
    /// </summary>
    private static void WriteClipboardText(string text)
    {
        for (int attempt = 0; attempt < 5; attempt++)
        {
            if (!NativeMethods.OpenClipboard(IntPtr.Zero))
            {
                Thread.Sleep(20);
                continue;
            }

            try
            {
                NativeMethods.EmptyClipboard();

                int bytes = (text.Length + 1) * 2;
                IntPtr hMem = NativeMethods.GlobalAlloc(NativeMethods.GMEM_MOVEABLE, (UIntPtr)bytes);
                if (hMem == IntPtr.Zero)
                {
                    throw new AutomationLayerException("Win32.Input", "GlobalAlloc 失败");
                }

                IntPtr ptr = NativeMethods.GlobalLock(hMem);
                if (ptr == IntPtr.Zero)
                {
                    NativeMethods.GlobalFree(hMem);
                    throw new AutomationLayerException("Win32.Input", "GlobalLock 失败");
                }

                try
                {
                    char[] chars = text.ToCharArray();
                    Marshal.Copy(chars, 0, ptr, chars.Length);
                    Marshal.WriteInt16(ptr, chars.Length * 2, 0);
                }
                finally
                {
                    NativeMethods.GlobalUnlock(hMem);
                }

                IntPtr result = NativeMethods.SetClipboardData(NativeMethods.CF_UNICODETEXT, hMem);
                if (result == IntPtr.Zero)
                {
                    NativeMethods.GlobalFree(hMem);
                    throw new AutomationLayerException("Win32.Input", "SetClipboardData 失败");
                }

                return;
            }
            finally
            {
                NativeMethods.CloseClipboard();
            }
        }

        throw new AutomationLayerException("Win32.Input", "OpenClipboard 5 次重试均失败");
    }
}
