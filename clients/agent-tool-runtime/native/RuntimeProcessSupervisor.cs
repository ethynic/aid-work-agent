// Windows-only process containment for the Runtime MCP stdio adapter.
// Build with .NET Framework 4.x csc; no third-party dependencies.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

internal static class RuntimeProcessSupervisor
{
    const uint JOB_QUERY = 4, JOB_TERMINATE = 8, SYNCHRONIZE = 0x100000;
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    const uint KILL_ON_CLOSE = 0x2000, WAIT_OBJECT_0 = 0, WAIT_TIMEOUT = 258;
    const int JobBasicAccounting = 1, JobBasicProcessIds = 3, JobExtendedLimits = 9;
    const uint CREATE_SUSPENDED = 4, CREATE_NO_WINDOW = 0x08000000, EXTENDED_STARTUPINFO_PRESENT = 0x80000;
    const uint STARTF_USESTDHANDLES = 0x100, DUPLICATE_SAME_ACCESS = 2;
    static readonly Regex JobPattern = new Regex(@"\AAidWorkRuntime\.[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z", RegexOptions.CultureInvariant);

    sealed class Failure : Exception { public readonly string Code; public Failure(string code) { Code = code; } }
    static void Need(bool valid, string code) { if (!valid) throw new Failure(code); }
    static int Main(string[] args)
    {
        try
        {
            if (args.Length == 1 && args[0] == "session-id")
            {
                Console.WriteLine("{\"sessionId\":" + Process.GetCurrentProcess().SessionId + "}"); return 0;
            }
            if (args.Length == 1 && args[0] == "boot-id")
            {
                SYSTEM_BOOT_ENVIRONMENT_INFORMATION boot;
                uint returned;
                Need(NtQuerySystemInformation(90, out boot, (uint)Marshal.SizeOf(typeof(SYSTEM_BOOT_ENVIRONMENT_INFORMATION)), out returned) == 0
                    && returned >= 16 && boot.BootIdentifier != Guid.Empty, "BOOT_ID_UNAVAILABLE");
                Console.WriteLine("{\"bootId\":\"" + boot.BootIdentifier.ToString("D") + "\"}"); return 0;
            }
            Need(args.Length >= 3, "INVALID_ARGUMENTS");
            var options = new Dictionary<string, string>(StringComparer.Ordinal);
            bool preserve = false;
            for (int i = 1; i < args.Length; i++)
            {
                string key = args[i];
                if (key == "--preserve-chrome") { Need(!preserve, "INVALID_ARGUMENTS"); preserve = true; continue; }
                Need(i + 1 < args.Length && !options.ContainsKey(key), "INVALID_ARGUMENTS");
                options.Add(key, args[++i]);
            }
            string name;
            Need(options.TryGetValue("--job-name", out name) && JobPattern.IsMatch(name), "INVALID_JOB_NAME");
            if (args[0] == "probe" || args[0] == "terminate")
            {
                Need(options.Count == 1 && !preserve, "INVALID_ARGUMENTS");
                return Inspect(name, args[0] == "terminate");
            }
            Need(args[0] == "run" && options.Count == 6, "INVALID_ARGUMENTS");
            string node = null, entry = null, receipt = null, ownerText = null, ownerStartText = null;
            Need(options.TryGetValue("--node", out node) && options.TryGetValue("--entry", out entry)
                && options.TryGetValue("--receipt", out receipt) && options.TryGetValue("--owner-pid", out ownerText)
                && options.TryGetValue("--owner-start-time", out ownerStartText), "INVALID_ARGUMENTS");
            int owner;
            long ownerStart;
            Need(Int32.TryParse(ownerText, out owner) && owner > 0 && owner != Process.GetCurrentProcess().Id, "INVALID_OWNER");
            Need(Int64.TryParse(ownerStartText, out ownerStart) && ownerStart > 0, "INVALID_OWNER_START_TIME");
            Need(AbsoluteFile(node) && File.Exists(node) && String.Equals(Path.GetFileName(node), "node.exe", StringComparison.OrdinalIgnoreCase), "INVALID_NODE");
            Need(AbsoluteFile(entry) && File.Exists(entry), "INVALID_ENTRY");
            Need(AbsoluteFile(receipt) && Directory.Exists(Path.GetDirectoryName(receipt)) && !File.Exists(receipt), "INVALID_RECEIPT");
            return Run(name, node, entry, owner, ownerStart, receipt, preserve);
        }
        catch (Failure f) { Console.Error.WriteLine("{\"error\":\"" + f.Code + "\"}"); return 1; }
        catch { Console.Error.WriteLine("{\"error\":\"SUPERVISOR_FAILED\"}"); return 1; }
    }
    static bool AbsoluteFile(string path)
    {
        return !String.IsNullOrEmpty(path) && Path.IsPathRooted(path) && !path.StartsWith(@"\\", StringComparison.Ordinal)
            && path.Length > 3 && path[1] == ':' && (path[2] == '\\' || path[2] == '/')
            && String.Equals(Path.GetFullPath(path), path, StringComparison.OrdinalIgnoreCase);
    }
    static int Inspect(string name, bool terminate)
    {
        IntPtr job = OpenJobObject(JOB_QUERY | (terminate ? JOB_TERMINATE : 0), false, name);
        if (job == IntPtr.Zero)
        {
            Need(Marshal.GetLastWin32Error() == 2, "JOB_OPEN_FAILED");
            Console.WriteLine("{\"exists\":false,\"activeProcesses\":0}"); return 0;
        }
        try
        {
            if (terminate) Stop(job);
            Console.WriteLine("{\"exists\":true,\"activeProcesses\":" + Active(job) + "}"); return 0;
        }
        finally { CloseHandle(job); }
    }
    static uint Active(IntPtr job)
    {
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info;
        Need(QueryAccounting(job, JobBasicAccounting, out info, (uint)Marshal.SizeOf(typeof(JOBOBJECT_BASIC_ACCOUNTING_INFORMATION)), IntPtr.Zero), "JOB_QUERY_FAILED");
        return info.ActiveProcesses;
    }
    static void Stop(IntPtr job)
    {
        Need(TerminateJobObject(job, 1), "JOB_TERMINATE_FAILED");
        var timer = Stopwatch.StartNew();
        while (Active(job) != 0)
        {
            Need(timer.ElapsedMilliseconds < 15000, "JOB_STOP_TIMEOUT"); Thread.Sleep(25);
        }
    }
    static void Limits(IntPtr job, uint flags)
    {
        var limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        limits.BasicLimitInformation.LimitFlags = flags;
        Need(SetInformationJobObject(job, JobExtendedLimits, ref limits, (uint)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION))), "JOB_LIMIT_FAILED");
    }
    static int Run(string name, string node, string entry, int ownerPid, long ownerStart, string receipt, bool preserve)
    {
        IntPtr owner = IntPtr.Zero, job = IntPtr.Zero, security = IntPtr.Zero;
        PROCESS_INFORMATION child = new PROCESS_INFORMATION();
        try
        {
            owner = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, false, ownerPid);
            Need(owner != IntPtr.Zero && WaitForSingleObject(owner, 0) == WAIT_TIMEOUT, "OWNER_UNAVAILABLE");
            long created, exited, kernel, user;
            Need(GetProcessTimes(owner, out created, out exited, out kernel, out user)
                && DateTime.FromFileTimeUtc(created).Ticks == ownerStart, "OWNER_IDENTITY_MISMATCH");
            string sid = WindowsIdentity.GetCurrent().User.Value;
            Need(ConvertStringSecurityDescriptorToSecurityDescriptor("D:P(A;;GA;;;SY)(A;;GA;;;" + sid + ")", 1, out security, IntPtr.Zero), "JOB_SECURITY_FAILED");
            var attributes = new SECURITY_ATTRIBUTES { nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES)), lpSecurityDescriptor = security, bInheritHandle = false };
            job = CreateJobObject(ref attributes, name);
            int createError = Marshal.GetLastWin32Error();
            Need(job != IntPtr.Zero, "JOB_CREATE_FAILED");
            // Never commandeer an existing job, even if its name matches exactly.
            Need(createError != 183, "JOB_ALREADY_EXISTS");
            Limits(job, KILL_ON_CLOSE);
            child = Launch(node, entry);
            if (!AssignProcessToJobObject(job, child.hProcess))
            {
                // Still suspended: no JS or descendants have run.
                TerminateProcess(child.hProcess, 1);
                WaitForSingleObject(child.hProcess, 15000);
                throw new Failure("JOB_ASSIGN_FAILED");
            }
            Need(WaitForSingleObject(owner, 0) == WAIT_TIMEOUT, "OWNER_UNAVAILABLE");
            Need(ResumeThread(child.hThread) != UInt32.MaxValue, "CHILD_RESUME_FAILED");
            CloseHandle(child.hThread); child.hThread = IntPtr.Zero;
            var handles = new[] { owner, child.hProcess };
            uint ended = WaitForMultipleObjects(2, handles, false, UInt32.MaxValue);
            Need(ended == WAIT_OBJECT_0 || ended == WAIT_OBJECT_0 + 1, "PROCESS_WAIT_FAILED");
            bool ownerGone = ended == WAIT_OBJECT_0 || WaitForSingleObject(owner, 0) == WAIT_OBJECT_0;
            int remaining = 0;
            uint childExit;
            Need(GetExitCodeProcess(child.hProcess, out childExit), "CHILD_EXIT_QUERY_FAILED");
            if (!ownerGone && preserve && childExit == 0)
            {
                remaining = ChromeOnly(job);
                if (remaining > 0 && WaitForSingleObject(owner, 0) == WAIT_TIMEOUT)
                {
                    Limits(job, 0); // Only own job members; never enumerate/kill existing Chrome.
                    if (WaitForSingleObject(owner, 0) != WAIT_TIMEOUT) { Stop(job); remaining = 0; ownerGone = true; }
                }
                else { Stop(job); remaining = 0; }
            }
            else Stop(job);
            Need(WaitForSingleObject(child.hProcess, 15000) == WAIT_OBJECT_0, "CHILD_STOP_UNCONFIRMED");
            if (remaining == 0) Need(Active(job) == 0, "JOB_STOP_UNCONFIRMED");
            WriteReceipt(receipt, name, ownerPid, child.dwProcessId, remaining, ownerGone ? "owner-exited" : "node-exited", sid);
            return 0;
        }
        finally
        {
            if (child.hThread != IntPtr.Zero) CloseHandle(child.hThread);
            if (child.hProcess != IntPtr.Zero) CloseHandle(child.hProcess);
            if (job != IntPtr.Zero) CloseHandle(job);
            if (owner != IntPtr.Zero) CloseHandle(owner);
            if (security != IntPtr.Zero) LocalFree(security);
        }
    }
    static int ChromeOnly(IntPtr job)
    {
        // Query membership again after checking images. Any ambiguity fails closed.
        for (int attempt = 0; attempt < 3; attempt++)
        {
            uint active = Active(job);
            if (active == 0) return 0;
            if (active > 16384) return -1;
            int size = checked(8 + ((int)active + 32) * IntPtr.Size);
            IntPtr buffer = Marshal.AllocHGlobal(size);
            try
            {
                uint returned;
                if (!QueryInformationJobObject(job, JobBasicProcessIds, buffer, (uint)size, out returned)) return -1;
                int count = Marshal.ReadInt32(buffer, 4), assigned = Marshal.ReadInt32(buffer, 0);
                if (count != assigned || count > (size - 8) / IntPtr.Size) continue;
                var ids = new HashSet<uint>();
                for (int i = 0; i < count; i++)
                {
                    long pid = Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size).ToInt64();
                    if (pid <= 0 || pid > UInt32.MaxValue) return -1;
                    ids.Add((uint)pid);
                    IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, false, (int)pid);
                    if (process == IntPtr.Zero) return -1;
                    try
                    {
                        bool member;
                        if (!IsProcessInJob(process, job, out member) || !member || WaitForSingleObject(process, 0) != WAIT_TIMEOUT) return -1;
                        var image = new StringBuilder(32768); uint length = (uint)image.Capacity;
                        if (!QueryFullProcessImageName(process, 0, image, ref length)
                            || !String.Equals(Path.GetFileName(image.ToString()), "chrome.exe", StringComparison.OrdinalIgnoreCase)) return -1;
                    }
                    finally { CloseHandle(process); }
                }
                if (!QueryInformationJobObject(job, JobBasicProcessIds, buffer, (uint)size, out returned)) return -1;
                if (Marshal.ReadInt32(buffer, 0) != count || Marshal.ReadInt32(buffer, 4) != count) continue;
                bool same = true;
                for (int i = 0; i < count; i++) if (!ids.Contains((uint)Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size).ToInt64())) same = false;
                if (same) return count;
            }
            finally { Marshal.FreeHGlobal(buffer); }
        }
        return -1;
    }
    static PROCESS_INFORMATION Launch(string node, string entry)
    {
        IntPtr attributeList = IntPtr.Zero, handlesPointer = IntPtr.Zero;
        bool attributesInitialized = false;
        var inherited = new IntPtr[3];
        try
        {
            IntPtr self = GetCurrentProcess();
            for (int i = 0; i < 3; i++)
            {
                IntPtr source = GetStdHandle(-10 - i);
                Need(source != IntPtr.Zero && source != new IntPtr(-1)
                    && DuplicateHandle(self, source, self, out inherited[i], 0, true, DUPLICATE_SAME_ACCESS), "STDIO_HANDLE_FAILED");
            }
            IntPtr bytes = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref bytes);
            Need(bytes.ToInt64() > 0, "STDIO_ATTRIBUTES_FAILED");
            attributeList = Marshal.AllocHGlobal(bytes);
            Need(InitializeProcThreadAttributeList(attributeList, 1, 0, ref bytes), "STDIO_ATTRIBUTES_FAILED");
            attributesInitialized = true;
            handlesPointer = Marshal.AllocHGlobal(IntPtr.Size * 3);
            for (int i = 0; i < 3; i++) Marshal.WriteIntPtr(handlesPointer, i * IntPtr.Size, inherited[i]);
            Need(UpdateProcThreadAttribute(attributeList, 0, new IntPtr(0x20002), handlesPointer, new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "STDIO_ATTRIBUTES_FAILED");
            var startup = new STARTUPINFOEX(); startup.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            startup.StartupInfo.hStdInput = inherited[0]; startup.StartupInfo.hStdOutput = inherited[1]; startup.StartupInfo.hStdError = inherited[2];
            startup.lpAttributeList = attributeList;
            PROCESS_INFORMATION child;
            var command = new StringBuilder(Quote(node) + " " + Quote(entry) + " mcp --stdio");
            Need(CreateProcess(node, command, IntPtr.Zero, IntPtr.Zero, true,
                CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, IntPtr.Zero, Path.GetDirectoryName(entry), ref startup, out child), "CHILD_CREATE_FAILED");
            return child;
        }
        finally
        {
            if (attributeList != IntPtr.Zero) { if (attributesInitialized) DeleteProcThreadAttributeList(attributeList); Marshal.FreeHGlobal(attributeList); }
            if (handlesPointer != IntPtr.Zero) Marshal.FreeHGlobal(handlesPointer);
            foreach (IntPtr handle in inherited) if (handle != IntPtr.Zero) CloseHandle(handle);
        }
    }
    static string Quote(string arg)
    {
        // Windows command-line quoting; neither a shell nor command substitution.
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1); else result.Append('\\', slashes);
            slashes = 0; result.Append(c);
        }
        result.Append('\\', slashes * 2); return result.Append('"').ToString();
    }
    static void WriteReceipt(string path, string name, int owner, uint node, int remaining, string reason, string sid)
    {
        string payload = "{\"schemaVersion\":1,\"jobName\":\"" + name + "\",\"ownerPid\":" + owner
            + ",\"nodePid\":" + node + ",\"processStopped\":true,\"remainingBusinessApps\":" + remaining + ",\"exitReason\":\"" + reason + "\"}";
        byte[] protectedBytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(payload), null, DataProtectionScope.CurrentUser);
        byte[] data = Encoding.UTF8.GetBytes("{\"schemaVersion\":1,\"protectedData\":\"" + Convert.ToBase64String(protectedBytes) + "\"}");
        string temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            var security = new FileSecurity(); security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid), FileSystemRights.FullControl, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl, AccessControlType.Allow));
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileSystemRights.Write, FileShare.None, 4096, FileOptions.WriteThrough, security))
            { stream.Write(data, 0, data.Length); stream.Flush(true); }
            // Never overwrite an existing receipt: each job/run owns a fresh scoped path.
            Need(MoveFileEx(temp, path, 8), "RECEIPT_PUBLISH_FAILED");
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }

    [StructLayout(LayoutKind.Sequential)] struct SECURITY_ATTRIBUTES { public int nLength; public IntPtr lpSecurityDescriptor; [MarshalAs(UnmanagedType.Bool)] public bool bInheritHandle; }
    [StructLayout(LayoutKind.Sequential)] struct SYSTEM_BOOT_ENVIRONMENT_INFORMATION { public Guid BootIdentifier; public uint FirmwareType; public ulong BootFlags; }
    [StructLayout(LayoutKind.Sequential)] struct STARTUPINFO { public int cb; public IntPtr lpReserved, lpDesktop, lpTitle; public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags; public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError; }
    [StructLayout(LayoutKind.Sequential)] struct STARTUPINFOEX { public STARTUPINFO StartupInfo; public IntPtr lpAttributeList; }
    [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public uint dwProcessId, dwThreadId; }
    [StructLayout(LayoutKind.Sequential)] struct JOBOBJECT_BASIC_LIMIT_INFORMATION { public long PerProcessUserTimeLimit, PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass; }
    [StructLayout(LayoutKind.Sequential)] struct IO_COUNTERS { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
    [StructLayout(LayoutKind.Sequential)] struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION { public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation; public IO_COUNTERS IoInfo; public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed; }
    [StructLayout(LayoutKind.Sequential)] struct JOBOBJECT_BASIC_ACCOUNTING_INFORMATION { public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime; public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(ref SECURITY_ATTRIBUTES attributes, string name);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr OpenJobObject(uint access, bool inherit, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int type, ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION data, uint size);
    [DllImport("kernel32.dll", EntryPoint = "QueryInformationJobObject", SetLastError = true)] static extern bool QueryAccounting(IntPtr job, int type, out JOBOBJECT_BASIC_ACCOUNTING_INFORMATION data, uint size, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool QueryInformationJobObject(IntPtr job, int type, IntPtr data, uint size, out uint returned);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetProcessTimes(IntPtr process, out long creation, out long exit, out long kernel, out long user);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
    [DllImport("ntdll.dll")] static extern int NtQuerySystemInformation(int informationClass, out SYSTEM_BOOT_ENVIRONMENT_INFORMATION info, uint size, out uint returned);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr handle, uint ms);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForMultipleObjects(uint count, IntPtr[] handles, bool all, uint ms);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr GetStdHandle(int value);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref IntPtr bytes);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr bytes, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string app, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string directory, ref STARTUPINFOEX startup, out PROCESS_INFORMATION process);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string sddl, uint revision, out IntPtr descriptor, IntPtr size);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool MoveFileEx(string from, string to, uint flags);
}
