using System.Diagnostics;

namespace WeCom.PersonalRpa.App.Outbound;

/// <summary>当前 Windows 登录会话唯一桌面自动化执行权。</summary>
public sealed class DesktopAutomationMutex : IDisposable
{
    private readonly string _mutexName;
    private readonly ManualResetEventSlim _started = new(false);
    private readonly ManualResetEventSlim _release = new(false);
    private readonly object _sync = new();
    private Thread? _ownerThread;
    private volatile bool _owned;
    private bool _disposed;

    public DesktopAutomationMutex()
        : this($@"Local\AidWeComPersonalRpaDesktopAutomation_{Process.GetCurrentProcess().SessionId}")
    {
    }

    internal DesktopAutomationMutex(string mutexName)
    {
        _mutexName = mutexName;
    }

    public bool TryAcquire()
    {
        lock (_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (_ownerThread is not null) return _owned;
            _ownerThread = new Thread(OwnMutex)
            {
                IsBackground = true,
                Name = "WeComRpaDesktopMutexOwner",
            };
            _ownerThread.Start();
        }
        _started.Wait();
        return _owned;
    }

    private void OwnMutex()
    {
        Mutex? mutex = null;
        try
        {
            mutex = new Mutex(false, _mutexName);
            try { _owned = mutex.WaitOne(0); }
            catch (AbandonedMutexException) { _owned = true; }
        }
        catch
        {
            // 命名空间/ACL/句柄创建失败时失败关闭；无论如何都必须唤醒启动线程。
            _owned = false;
        }
        finally { _started.Set(); }
        if (!_owned)
        {
            mutex?.Dispose();
            return;
        }

        _release.Wait();
        try { mutex!.ReleaseMutex(); }
        finally
        {
            _owned = false;
            mutex!.Dispose();
        }
    }

    public void Dispose()
    {
        Thread? ownerThread;
        lock (_sync)
        {
            if (_disposed) return;
            _disposed = true;
            ownerThread = _ownerThread;
            if (ownerThread is not null) _release.Set();
        }
        ownerThread?.Join();
        _started.Dispose();
        _release.Dispose();
    }
}
