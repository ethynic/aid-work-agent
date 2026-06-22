using System.Runtime.InteropServices;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.Win32;

/// <summary>
/// 剪贴板独占守卫（protocol.md §C.4 IClipboardGuard 的 Automation 实现）。
/// 执行一次自动化发送前 BackupAndEmpty()，发送完成后 Restore()，
/// 避免客户端读写剪贴板与用户其它复制行为相互覆盖。
/// </summary>
public sealed class ClipboardGuard : IClipboardGuard
{
    private string? _backupText;
    private bool _hasBackup;
    private readonly object _lock = new();

    /// <summary>
    /// 备份当前剪贴板 Unicode 文本，随后清空剪贴板。
    /// 多次调用：仅第一次生效（幂等），避免嵌套自动化的备份覆盖。
    /// </summary>
    public void BackupAndEmpty()
    {
        lock (_lock)
        {
            if (_hasBackup)
            {
                return;
            }

            _backupText = TryReadUnicodeText();
            TryEmpty();
            _hasBackup = true;
        }
    }

    /// <summary>恢复 BackupAndEmpty 时备份的文本；无备份则清空（保证发送后剪贴板不含遗留消息内容）。</summary>
    public void Restore()
    {
        lock (_lock)
        {
            if (!_hasBackup)
            {
                return;
            }

            if (!string.IsNullOrEmpty(_backupText))
            {
                TryWriteUnicodeText(_backupText);
            }
            else
            {
                TryEmpty();
            }

            _hasBackup = false;
            _backupText = null;
        }
    }

    public void Dispose()
    {
        Restore();
    }

    // -------- 内部：原生剪贴板读写（带 OpenClipboard 重试） --------

    private static string? TryReadUnicodeText()
    {
        for (int attempt = 0; attempt < 5; attempt++)
        {
            if (!NativeMethods.OpenClipboard(IntPtr.Zero))
            {
                System.Threading.Thread.Sleep(20);
                continue;
            }

            try
            {
                if (!NativeMethods.IsClipboardFormatAvailable(NativeMethods.CF_UNICODETEXT))
                {
                    return null;
                }

                IntPtr handle = NativeMethods.GetClipboardData(NativeMethods.CF_UNICODETEXT);
                if (handle == IntPtr.Zero)
                {
                    return null;
                }

                IntPtr ptr = NativeMethods.GlobalLock(handle);
                if (ptr == IntPtr.Zero)
                {
                    return null;
                }

                try
                {
                    ulong size = (ulong)NativeMethods.GlobalSize(handle);
                    int length = (int)(size / 2);
                    if (length <= 0)
                    {
                        return string.Empty;
                    }

                    char[] buffer = new char[length];
                    Marshal.Copy(ptr, buffer, 0, length);

                    // 文本以 null 结尾，截断到首个 \0。
                    int nullIdx = Array.IndexOf(buffer, '\0');
                    return nullIdx >= 0
                        ? new string(buffer, 0, nullIdx)
                        : new string(buffer);
                }
                finally
                {
                    NativeMethods.GlobalUnlock(handle);
                }
            }
            finally
            {
                NativeMethods.CloseClipboard();
            }
        }

        return null;
    }

    private static void TryWriteUnicodeText(string text)
    {
        for (int attempt = 0; attempt < 5; attempt++)
        {
            if (!NativeMethods.OpenClipboard(IntPtr.Zero))
            {
                System.Threading.Thread.Sleep(20);
                continue;
            }

            try
            {
                NativeMethods.EmptyClipboard();

                // 分配全局内存：包含末尾 \0，字节数 = (charCount + 1) * 2。
                int bytes = (text.Length + 1) * 2;
                IntPtr hMem = NativeMethods.GlobalAlloc(NativeMethods.GMEM_MOVEABLE, (UIntPtr)bytes);
                if (hMem == IntPtr.Zero)
                {
                    return;
                }

                IntPtr ptr = NativeMethods.GlobalLock(hMem);
                if (ptr == IntPtr.Zero)
                {
                    NativeMethods.GlobalFree(hMem);
                    return;
                }

                try
                {
                    char[] chars = text.ToCharArray();
                    Marshal.Copy(chars, 0, ptr, chars.Length);
                    // 追加 \0 终止符。
                    Marshal.WriteInt16(ptr, chars.Length * 2, 0);
                }
                finally
                {
                    NativeMethods.GlobalUnlock(hMem);
                }

                // SetClipboardData 接管 hMem 所有权，成功后无需 GlobalFree。
                IntPtr result = NativeMethods.SetClipboardData(NativeMethods.CF_UNICODETEXT, hMem);
                if (result == IntPtr.Zero)
                {
                    NativeMethods.GlobalFree(hMem);
                }

                return;
            }
            finally
            {
                NativeMethods.CloseClipboard();
            }
        }
    }

    private static void TryEmpty()
    {
        for (int attempt = 0; attempt < 5; attempt++)
        {
            if (!NativeMethods.OpenClipboard(IntPtr.Zero))
            {
                System.Threading.Thread.Sleep(20);
                continue;
            }

            try
            {
                NativeMethods.EmptyClipboard();
                return;
            }
            finally
            {
                NativeMethods.CloseClipboard();
            }
        }
    }
}
