# M0.4 实施规格：Local Tool Runtime

> 关联：[plan-recruiting-cli-agent-integration.md](plan-recruiting-cli-agent-integration.md) M0.4；上位设计：[recruiting-cli-agent-integration-design.md](../../design/recruiting/recruiting-cli-agent-integration-design.md) §9；云端协议见 [m03-implementation-spec.md](m03-implementation-spec.md) §4 Runtime API。
>
> 本文件是 M0.4 开发的具体实施决策。

## 1. 项目骨架

新增 `clients/agent-tool-runtime/`：Node 22 + TypeScript ESM，工程约定**完全对齐 boss-resume-assistant**（tsconfig 抄其 tsconfig.main.json 风格、`npm run build` = tsc、`npm test` = build + node --test dist/tests、ESM `.js` 后缀 import）。

```
clients/agent-tool-runtime/
├─ package.json           # 依赖：@modelcontextprotocol/sdk（精确锁定，与 boss CLI 同 1.30.0）
├─ tsconfig.main.json
├─ src/
│  ├─ config.ts           # 配置解析：云端 baseUrl、boss CLI 入口路径；%APPDATA%/aidwork-tool-runtime/config.json
│  ├─ credentials.ts      # DPAPI token 存取（CurrentUser scope）
│  ├─ apiClient.ts        # HTTPS API 客户端（pair/heartbeat/claim/started/progress/result）
│  ├─ pollLoop.ts         # 心跳 + 长轮询主循环 + 指数退避
│  ├─ invocationRunner.ts # 单 invocation 执行：started→MCP call→progress→result，协作式取消
│  ├─ providerManager.ts  # MCP stdio Provider 生命周期（spawn/单飞/崩溃处理/限时回收）
│  ├─ manifestVerifier.ts # 内置受信 manifest + tool_name/digest 校验
│  ├─ dpapi.ts            # PowerShell ProtectedData helper（被 credentials 用）
│  ├─ log.ts              # stderr 日志，脱敏（绝不输出 token/claim_token/简历内容）
│  └─ cli.ts              # pair / start / status / doctor / unpair
└─ tests/                 # node:test，fake cloud（node:http）+ fake MCP provider
```

## 2. CLI 命令

```
agent-tool-runtime pair --code <8位配对码> [--server https://...] [--name 设备名]
agent-tool-runtime start [--server https://...]     # 主循环：heartbeat + claim + 执行
agent-tool-runtime status                           # 本地配置/配对状态/最近错误（offline 视图）
agent-tool-runtime doctor                           # 只读检查：配置、token 可读、server 可达、boss CLI 入口存在、boss doctor 可跑
agent-tool-runtime unpair                           # 调云端撤销？（MVP：仅本地清除 token 与配置，提示用户在 Web 解绑）
```

- pair：生成 device 密钥对不需要——直接 POST /runtime/pair {code, name, platform='win32-x64', runtime_version, capabilities:{providers:['boss-recruiting']}, machine_fingerprint} → 得 device_id + device_token → DPAPI 加密存 `%APPDATA%/aidwork-tool-runtime/credentials.bin`，config.json 存 device_id/server/name（**不含 token**）。machine_fingerprint：取 `machineGuid`（注册表 HKLM\SOFTWARE\Microsoft\Cryptography）+ hostname 的 sha256，**只发 hash**。
- start：先校验 credentials 存在，再进 pollLoop。控制台输出关键事件（配对设备、领取 invocation、进度、结果、错误），全部走 stderr/stdout 均可（人工 CLI，无 MCP 污染问题），但**不打印 token/claim_token**。
- unpair：MVP 只本地清除（Web 端 DELETE 设备才是真正撤销），输出提示文案。不调用云端（设备 token 撤销 API 在 Web 用户侧）。

## 3. 云端协议对接（与 M0.3 API 严格对齐）

- `POST /api/local-tools/runtime/pair`（无 token）→ `{device_id, device_token}`
- `POST /api/local-tools/runtime/heartbeat`（Bearer device_token）→ `{selected, server_time}`；**心跳间隔 5 秒**（计划写 2 秒，此处偏离：云端 online 阈值 30s，5s 有 6x 余量，降低 demo 期服务器无意义负载；执行期 progress 仍 2s 续租，计划语义不变）
- `POST /api/local-tools/runtime/claim?wait=20`（长轮询；响应 `{invocation_id, tool_name, arguments, claim_token, lease_expires_at, provider}` 或 `{invocation: null}`）
- `POST .../invocations/{id}/started` `{claim_token}`
- `POST .../invocations/{id}/progress` `{claim_token, stage, current, total, message}` → `{seq, cancel}`（**执行中每 2 秒或每个业务进度一次**，cancel=true 时协作式中止）
- `POST .../invocations/{id}/result` `{claim_token, success, code, message, effect, data, retryable}`（幂等，断线恢复后重发同一终态安全）

- 断线指数退避：网络错误后 1s→2s→4s→…→最大 30s 重试 heartbeat/claim；恢复后重置。
- **写动作断线策略**（设计 §9.3）：执行中断网 → 本地继续完成当前原子动作与页面校验 → 恢复网络后回传终态；无法证明效果 → `effect=unknown, code=EXECUTION_UNKNOWN`。
- claim_token 仅内存持有，不落盘、不打印。

## 4. Provider 管理（providerManager + manifestVerifier）

- **内置受信 manifest**（manifestVerifier.ts 静态常量，不接受云端下发 executable/cwd/env/argv）：
  ```ts
  {
    provider_id: 'ai.aidwork.boss-recruiting',
    tools: ['boss_filter','boss_clear_filter','boss_goto','boss_greet','boss_accept_resume','boss_reject_current','boss_interview_demo'],
    execution_target: 'local_required',
  }
  ```
- boss CLI 入口解析（config.bossCliEntry）：默认 `../boss-resume-assistant/dist/src/cli/index.js`（相对 runtime 包目录，开发态），可被 config.json 覆盖为绝对路径（本地管理员配置，非云端下发）。spawn 参数固定：`node <entry> mcp --stdio`，**不用 shell**。
- MCP client：官方 SDK `@modelcontextprotocol/sdk` Client + StdioClientTransport。
- 单飞：进程级同时只执行一个 invocation；云端协议本身一设备一任务，本地仍兜底。
- manifest 校验：claim 到的 tool_name 必须 ∈ manifest.tools，否则直接回 result（success=false, code=TOOL_NOT_ALLOWED 不对——用 `INVALID_ARGUMENT`... 不对，用云端约定：回 success=false, code='INTERNAL_ERROR' 不合适。**决策**：本地校验失败回 `success=false, code='TOOL_NOT_ALLOWED', retryable=false`，与 M0.3 云端毒消息处理码一致）。
- Provider 崩溃（MCP 进程退出/stdio 断开）：执行中崩溃 → result `EXECUTION_UNKNOWN`（写动作）/ `INTERNAL_ERROR`（只读），随后 providerManager 自动 respawn 供下一个 invocation。
- Runtime 退出（SIGINT/SIGTERM）：停止 claim 新任务；当前 invocation 若是写动作等待其到达下一个可取消点；关闭 MCP stdin，**限时 5s** 后 SIGKILL 子进程；测试断言无遗留 node/powershell 进程。

## 5. DPAPI（dpapi.ts + credentials.ts）

- 通过 `child_process.execFile('powershell.exe', ['-NoProfile','-NonInteractive','-Command', ...])` 调用 .NET `System.Security.Cryptography.ProtectedData`（CurrentUser scope）。脚本以 `-Command` 内联字符串传入（**不落 .ps1 文件**，规避编码问题）；输入输出走 base64 stdin/stdout。
- 加密：`[Convert]::ToBase64String([ProtectedData]::Protect([Convert]::FromBase64String($input),'null','CurrentUser'))`；解密反向。
- 存取失败（非 Windows/PS 不可用）fail-loud，doctor 必检。
- credentials.bin 文件 ACL 默认即可（DPAPI CurrentUser 已绑用户）；日志永不输出明文或密文内容。

## 6. 锁屏/不可交互桌面检测（MVP 版）

- `desktopCheck.ts`：PowerShell Add-Type P/Invoke 调 `user32!OpenInputDesktop`，返回 0 视为锁屏/不可交互桌面（锁屏或 WinSta 切换时 OpenInputDesktop 失败）。
- 触发点：`doctor` 必检 + 每次 invocation 开始前检一次。
- 检测不通过：写动作直接拒绝执行，回 `success=false, code='DESKTOP_NOT_INTERACTIVE', retryable=true, message='Windows 桌面锁屏或不可交互，请解锁后重试'`。
- **码表扩展决策**：上位设计 §10.3 的 10 个码无对应项，新增 `DESKTOP_NOT_INTERACTIVE`（Runtime 本地拒绝时回传）。云端不校验 code 枚举——M0.3 已确认云端仅按 success 与 code==EXECUTION_UNKNOWN 映射 state，新码落 failed 态正确。M0.5 子智能体负责把该码翻译为「请解锁电脑后重试」的用户引导。

## 7. 测试（tests/，node:test，全部 fake，不依赖真实云端/Chrome/BOSS）

- `fakeCloud.ts`：node:http 实现 M0.3 全部 6 个端点的内存版（配对码、token、队列、取消标志），支持注入故障（断线、延迟、500）。
- `fakeProvider.mjs`：一个最小 MCP stdio server（用 SDK 实现），回显工具调用、支持进度通知、可被指示崩溃。
- 用例：
  1. pair 成功/错误码过期 401、token 落盘为 DPAPI 密文（断言文件内容不是明文 token）。
  2. 完整协议：fake cloud 注入 invocation → runtime 领取 → started → progress（seq 递增）→ result → fake cloud 断言顺序与幂等（重复 result 返回原终态）。
  3. 断线：执行中 fake cloud 断网 → runtime 完成本地动作后恢复回传；heartbeat 退避序列断言。
  4. 取消：fake cloud 在 progress 响应返回 cancel=true → runner 协作式中止并回 cancelled 终态。
  5. MCP 崩溃：fake provider 执行中退出 → EXECUTION_UNKNOWN/INTERNAL_ERROR 回传 + provider respawn。
  6. manifest 校验：非法 tool_name → TOOL_NOT_ALLOWED。
  7. 进程清理：runtime SIGINT 后断言无遗留子进程（spawn 的 node/powershell 句柄全部关闭，fake provider 退出）。
  8. DPAPI：加解密 round-trip（Windows 上真实跑；非 Windows skip）。
  9. desktopCheck 单测可注入 mock；真实 Windows 下 doctor 路径手测留 M0.7。

## 8. 不做（本阶段）

- Windows 服务/开机自启（设计明确 MVP 命令行进程）。
- 安装器、自动更新。
- 多 Provider 并行、第三方 Provider。
- WSS transport。
