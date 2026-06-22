using System.Runtime.InteropServices;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.Win32;

/// <summary>
/// 文件拖放剪贴板写入（CF_HDROP）：用于发送文件 / 图片到企微消息输入框（Ctrl+V 触发）。
/// </summary>
internal static class ClipboardFileDrop
{
    [StructLayout(LayoutKind.Sequential)]
    private struct DROPFILES
    {
        public uint pFiles;       // 文件列表偏移（sizeof(DROPFILES)）
        public int ptX;
        public int ptY;
        public bool fNC;
        public bool fWide;        // true = UTF-16
    }

    private const uint CF_HDROP = 15;

    /// <summary>把一组文件路径写入剪贴板（CF_HDROP 格式）。</summary>
    public static void SetFileDrop(string[] filePaths)
    {
        ArgumentNullException.ThrowIfNull(filePaths);
        if (filePaths.Length == 0)
        {
            throw new ArgumentException("filePaths 为空", nameof(filePaths));
        }

        // 校验文件存在（避免把不存在的 drop 写入剪贴板）。
        foreach (var p in filePaths)
        {
            if (!File.Exists(p))
            {
                throw new AutomationLayerException("Win32.Clipboard", $"文件不存在: {Path.GetFileName(p)}");
            }
        }

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

                // 构造 DROPFILES + 多个 \0 分隔的 UTF-16 路径 + 末尾 \0\0。
                int dropFilesSize = Marshal.SizeOf<DROPFILES>();
                int charsSize = filePaths.Sum(p => p.Length + 1) + 1; // 末尾额外 \0
                int bytes = dropFilesSize + charsSize * 2;

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
                    // 写 DROPFILES 头。
                    var drop = new DROPFILES
                    {
                        pFiles = (uint)dropFilesSize,
                        ptX = 0,
                        ptY = 0,
                        fNC = false,
                        fWide = true,
                    };
                    Marshal.StructureToPtr(drop, ptr, false);

                    // 写 UTF-16 路径列表。
                    int offset = dropFilesSize;
                    foreach (var p in filePaths)
                    {
                        char[] chars = p.ToCharArray();
                        Marshal.Copy(chars, 0, ptr + offset, chars.Length);
                        offset += (chars.Length + 1) * 2; // 含末尾 \0
                    }
                    // 列表末尾再补一个 \0（字符串数组终止符）。
                    Marshal.WriteInt16(ptr + offset, 0);
                }
                finally
                {
                    NativeMethods.GlobalUnlock(hMem);
                }

                IntPtr result = NativeMethods.SetClipboardData(CF_HDROP, hMem);
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
}
