# Windows Runtime process supervisor

Build on Windows with `powershell -NoProfile -File native/build.ps1` from the
`clients/agent-tool-runtime` directory. The system .NET Framework 4.x compiler
produces `native/build/RuntimeProcessSupervisor.exe` (AnyCPU). Packaging must
copy this executable into the Runtime's trusted resources; build output and
isolated test fixtures are not source files or plugin resources.

## Private adapter CLI

```text
RuntimeProcessSupervisor.exe boot-id
RuntimeProcessSupervisor.exe session-id
RuntimeProcessSupervisor.exe run --job-name AidWorkRuntime.<UUID> --node <absolute node.exe> --entry <absolute JS> --owner-pid <Host PID> --owner-start-time <UTC ticks> --receipt <new absolute receipt path> [--preserve-chrome]
RuntimeProcessSupervisor.exe probe --job-name AidWorkRuntime.<UUID>
RuntimeProcessSupervisor.exe terminate --job-name AidWorkRuntime.<UUID>
```

The adapter owns a fresh UUID/job/receipt for each MCP process. `owner-start-time`
is `Process.StartTime.ToUniversalTime().Ticks` for the Host (decimal string, not
Unix time). The supervisor opens the exact process handle, verifies creation
time, and rechecks liveness before resuming the child. `boot-id` uses Windows
`SystemBootEnvironmentInformation` and emits `{ "bootId": "<GUID>" }`; failure
does not invent a boot identity.
`session-id` returns only the current Windows session number. Same-boot recovery
must match the protected launcher session before opening a named Job; an absent
name in another login session does not prove the original tree stopped.

`run` creates a job with an ACL limited to the current user and LocalSystem,
sets `KILL_ON_JOB_CLOSE`, creates Node suspended, assigns it, then resumes it.
No breakaway limit is enabled. Child arguments are exactly `node entry mcp
--stdio`; no shell runs. An explicit STARTUPINFOEX handle list inherits only
duplicated stdin/stdout/stderr handles. The native process emits no control
JSON on stdout during `run`, so the MCP transport owns that stream. Launch
the wrapper with redirected stdio and `windowsHide: true`. If the spawning
library's own job kills the wrapper, its last handle still triggers the native
job's cleanup, but there may be no receipt. A surviving wrapper observes the
Host's exact handle and explicitly terminates the job on Host death.

`probe` emits `{ "exists": true|false, "activeProcesses": <integer> }` and
does not expose PIDs. Only `ERROR_FILE_NOT_FOUND` means absent; access denied
and other errors fail. `terminate` opens only a validated job name, terminates
that job and waits up to 15 seconds for active-process count zero. An absent
job returns false/zero. It never kills an arbitrary PID. Probe/query handles
are short-lived; they must not become long-lived owners that delay
`KILL_ON_JOB_CLOSE` cleanup. Errors go to stderr as a fixed machine code
`{ "error": "CODE" }`, exit 1, without arguments, paths or native error text.

## Stop evidence and Chrome

Default behavior cleans up all job descendants on ordinary Node exit as well
as Host death. Before writing evidence, the supervisor confirms Node's actual
process handle has signaled and the job has active count zero. Job accounting
can reach zero slightly before a process handle signals, so both checks matter.

`--preserve-chrome` applies only when Node exits with code zero and the Host is
still alive. Every remaining job member must be queryable, alive, still in
this job and have image basename `chrome.exe`; the membership set is queried
again. Unknown members or ambiguous queries cause full cleanup. Only then is
`KILL_ON_JOB_CLOSE` cleared. Existing Chrome outside this job is never inspected
or modified. This is an image/membership test, not software-signature validation;
the option belongs to trusted first-party launch policy, not plugin input.

A protected receipt has the UTF-8 JSON envelope
`{ "schemaVersion": 1, "protectedData": "<base64>" }`. Decrypt the blob with
Windows DPAPI CurrentUser (no optional entropy). Its inner JSON contains only
`schemaVersion`, `jobName`, `ownerPid`, `nodePid`, `processStopped`,
`remainingBusinessApps` (count), and `exitReason` (`owner-exited` or
`node-exited`). It contains no credential or path. The file is created with a
current-user/LocalSystem ACL, flushed to disk, then renamed with write-through
without replacing an existing receipt. `processStopped` means Node/automation
has stopped; a positive `remainingBusinessApps` records preserved Chrome.
This receipt does not prove a business operation was cancelled or authorize
task replay. Recovery must bind it to the previously protected per-run ledger.

Microsoft's [CreateJobObjectW documentation](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw)
describes job destruction after the last handle closes and associated processes
exit; with `KILL_ON_JOB_CLOSE`, closing the last handle terminates members.
In the isolated Windows fixture, **after clearing that flag and closing the
last handle, opening the job by name returned FILE_NOT_FOUND while the
preserved fake Chrome was still alive**. Therefore named-job absence alone
must not be interpreted as all business applications having exited. A recovery
implementation must retain the kill-on-close/preservation policy and protected
stop evidence. The preservation branch can also lack a receipt if interrupted
after clearing the flag; that ambiguous case requires conservative handling.

## Isolated developer verification (2026-10-12)

Compiled with the installed Framework64 v4.0.30319 compiler and tested using
temporary Node entries and a purpose-built WinExe named `chrome.exe` that
self-exits after three seconds. No real Runtime, Chrome or server was operated.

- Fixed `mcp --stdio` argv, inherited stdin echo/stdout, Node normal exit,
  real descendant termination, absent job after cleanup: passed.
- Named-job explicit terminate and live Host process death, including
  process-handle stop confirmation and protected receipt: passed.
- Abrupt wrapper death triggers kill-on-close, descendants stop, no fabricated
  receipt: passed. Owner creation-time mismatch is rejected before creating
  the job; duplicate job names are rejected without changing the original: passed.
- Invalid job-name rejection and stable current boot GUID: passed.
- Normal exit preserves only fake Chrome; default cleanup kills it; nonzero
  Node exit and non-Chrome descendants fail closed: passed.
- DPAPI decrypt and receipt stop/count fields: passed.

These fixtures validate native process containment. They do not replace tests
of the MCP adapter, installer, protected recovery ledger, real BOSS business
effects or client upgrade UI. Cross-user access-denied injection, OS reboot
and signed production packaging remain separate integration checks.
