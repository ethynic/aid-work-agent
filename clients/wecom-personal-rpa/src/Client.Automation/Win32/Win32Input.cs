using System.Runtime.InteropServices;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.Win32;

/// <summary>
/// Win32 输入降级路径：用 SendInput 发送 Ctrl+V（粘贴剪贴板文本）与 Enter。
/// 仅在 FlaUI 不可达时由 <c>ActionLocator</c> 第二层调用，输入焦点已由上层保证位于消息框。
/// </summary>
internal static class Win32Input
{
    /// <summary>发送 Ctrl+V：模拟粘贴剪贴板当前文本到拥有焦点的控件。</summary>
    public static void SendPaste()
    {
        var inputs = new NativeMethods.INPUT[4];

        // Ctrl down
        inputs[0].type = NativeMethods.INPUT_KEYBOARD;
        inputs[0].u.ki.wVk = NativeMethods.VK_CONTROL;
        inputs[0].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYDOWN;

        // V down / up
        inputs[1].type = NativeMethods.INPUT_KEYBOARD;
        inputs[1].u.ki.wVk = NativeMethods.VK_V;
        inputs[1].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYDOWN;

        inputs[2].type = NativeMethods.INPUT_KEYBOARD;
        inputs[2].u.ki.wVk = NativeMethods.VK_V;
        inputs[2].u.ki.dwFlags = NativeMethods.KEYEVENTF_KEYUP;

        // Ctrl up
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

        // 给目标控件处理粘贴留出时间。
        SleepUnblocked(80);
    }

    /// <summary>发送 Enter：在企微消息输入框中触发发送。</summary>
    public static void SendEnter()
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

        SleepUnblocked(50);
    }

    /// <summary>组合：用 Ctrl+V 把已写入剪贴板的文本粘贴并发送。</summary>
    public static void SendTextViaClipboard(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            throw new AutomationLayerException("Win32.Input", "SendTextViaClipboard 文本为空");
        }

        SendPaste();
        SendEnter();
    }

    /// <summary>不阻塞线程池的轻量等待（避免在 async 上下文里 Thread.Sleep 长时间占线程）。</summary>
    private static void SleepUnblocked(int milliseconds)
    {
        // 此处是 Win32 同步降级路径，被 ClipboardGuard / ActionLocator 在独立执行线程调用，
        // 短时 Thread.Sleep 可接受；不走 Task.Delay 避免把同步 API 改成 async。
        System.Threading.Thread.Sleep(milliseconds);
    }
}
