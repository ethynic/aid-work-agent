# 客户端工具部署手册（boss CLI + agent-tool-runtime）

> 面向角色：给客户 Windows 电脑安装「招聘智能体本地执行组件」的实施/支持人员。
> 两个组件的关系：**agent-tool-runtime（执行节点）在打包时已捆绑 boss CLI（BOSS 直聘
> 操作能力）**——客户机只需安装一个 tgz 包，得到 `aid-runtime` 命令。

---

## 一、前置要求（客户电脑）

| 项 | 要求 | 检查方式 |
|---|---|---|
| 操作系统 | Windows 10/11 x64 | — |
| Node.js | **20 或更高（当前 LTS 24.x，开发验证环境即 v24；老版本如 v16 无法运行）** | `node -v`；未装/过旧去 https://nodejs.org 下 LTS 安装（覆盖安装即可，package.json 已声明 engines>=20） |
| Chrome | 已安装并可登录 BOSS 直聘 | — |
| 网络 | 能访问服务端地址（如 https://agent2.aidingyi.cn） | 浏览器打开该地址 |

> ⚠️ 全程**不需要**管理员权限（npm 用户级安装 + DPAPI 用户级加密）。
> 多人共用一台电脑时：每个 Windows 账户各自安装、各自配对（凭证按账户隔离）。

## 二、编译新版本（在开发机/构建机上）

### 1. 升版本号
两处 `package.json` 的 `version` 同步修改：
- `clients/boss-resume-assistant/package.json`
- `clients/agent-tool-runtime/package.json`

### 2. 打包（一条命令，钩子自动完成全部构建）
```bash
cd clients/agent-tool-runtime
npm pack
```
`prepack` 钩子会自动依次：构建 boss CLI → 构建 runtime → `npm install --install-links`
（把 file: 符号链接依赖转成实体目录打进 tar，避免 `..` 路径错乱）；`postpack` 打完
自动恢复开发态。**产物：`agent-tool-runtime-<版本>.tgz`（boss CLI 已捆绑在内，
客户机无需单独安装 boss 包）。**

### 3. 冒烟验证（发布前必做，防 files 漏文件）
```bash
mkdir %TEMP%\smoke && cd %TEMP%\smoke
npm install <tgz 绝对路径>
npx aid-runtime doctor --help   # 能执行即安装完整（doctor 会检查脚本齐全性）
```

## 三、安装（客户电脑）

```bash
npm install -g <tgz 文件路径>
```
装完得到命令 `aid-runtime`（boss-cli 捆绑在包内，由 runtime 自动调用，无需直接使用）。

验证：`aid-runtime status`（应显示配置目录与配对状态；未配对属正常，见 §五）

## 四、Chrome 调试实例（v0.2.1 起自动拉起，手工方式仅兜底）

**默认无需任何手工配置**：首次执行任意 BOSS 操作时，CLI 检测调试端口（默认 9222）不通
会自动拉起一个**固定持久 profile**（`C:\chrome-debug`）的 Chrome 调试实例并打开
zhipin.com（ChromeLauncher：App Paths 注册表/常见路径定位 chrome.exe → detached
spawn → 轮询端口就绪 ≤15s；找不到 Chrome/拉起失败/超时一律 fail-open 按原错误上报）。
用户**只需在弹出的窗口里登录一次 BOSS**。用户偏好自己管理时设 `AID_BOSS_AUTO_CHROME=0` 关闭。

背景：Chrome 136+ 默认用户数据目录会静默忽略调试端口参数，必须独立数据目录。当初
「CLI 绝不启动 Chrome」决策针对的是临时 profile（指纹/登录态每次全新，触发 BOSS 风控）；
固定持久 profile 与手动开快捷方式完全等价，不属禁区（ChromeAttacher 决策 6 修订二）。

手工兜底（自动拉起失败的少数情况）：

1. 新建桌面快捷方式，目标填：
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"
   ```
   （Chrome 路径按实际安装位置调整；数据目录固定用 `C:\chrome-debug` 便于支持排障）
2. 用这个快捷方式打开 Chrome，**登录 BOSS 直聘**（登录态保存在独立目录，与日常
   Chrome 互不影响；此后一直用这个快捷方式打开）
3. 验证调试端口：浏览器访问 `http://127.0.0.1:9222/json/version` 能看到 JSON 即可

> ⚠️ 智能体操作期间（筛选/打招呼约 2-3 分钟）请勿移动鼠标、勿遮挡该 Chrome 窗口。

## 五、绑定服务器（配对）

1. 在 Web 管理端（与服务端地址对应的站点）进入「本地工具 / 设备管理」页 →
   **生成配对码**（8 位大写字母数字，**5 分钟内有效、一次性**）
2. 客户电脑执行：
   ```bash
   aid-runtime pair --code <配对码> --server https://agent2.aidingyi.cn --name "张三-办公电脑"
   ```
   （`--server` 换成客户实际环境的服务端地址；配对成功后地址存入配置，后续 start 不再需要传）
3. 启动常驻：
   ```bash
   aid-runtime start
   ```
   看到日志 `[runtime] 已启动 device_id=... server=...` 即成功；Web 设备页应显示
   在线（绿色）。

**配置与凭证位置**（支持排障时用）：
- 配置：`%APPDATA%\aidwork-tool-runtime\config.json`（server 地址 / device_id）
- 凭证：`%APPDATA%\aidwork-tool-runtime\credentials.bin`（DPAPI 加密，勿手工编辑/拷贝到别的机器——绑定了本机）

## 六、自检与开机自启

**自检**（装完/出问题时先跑这个）：
```bash
aid-runtime doctor
```
逐项检查：配置文件 / 凭证解密 / 服务端心跳全链路 / boss CLI 入口 / 子进程 doctor
（脚本齐全 + CDP 端口 + BOSS 登录态）/ 桌面可交互。哪项 ✗ 就按输出提示处理。

**后台常驻与开机自启（无窗口方案）**：

runtime **不需要保持命令行窗口**——用仓库自带的脚本注册为登录自启的后台任务
（无窗口运行 + 崩溃/误杀 10 秒自动拉起 + 日志落文件并按 5MB 轮转）：

```powershell
# 1. 把 clients/scripts/start_runtime.bat 复制到 %APPDATA%\aidwork-tool-runtime\
# 2. 注册并启动（管理员不需要，当前用户即可）
powershell -ExecutionPolicy Bypass -File clients\scripts\register_runtime_task.ps1
```
- 日志位置：`%APPDATA%\aidwork-tool-runtime\logs\runtime.log`（`runtime.old.log` 为轮转前一代）；
  runtime 的 stderr 与其调用的 boss CLI 子进程 stderr 都汇入该文件（自动脱敏：token/长 base64 打码）
- **远程排障采集**：让客户双击 `clients/release/collect_logs.bat` → 桌面生成
  「aidwork-诊断包-时间戳.zip」（日志 + config.json + 环境/包版本 + status/doctor 输出；
  绝不含 credentials.bin），发回即可分析
- bat 优先用 npm 全局命令 `aid-runtime`，找不到时回退开发机仓库路径（客户机走前者）
- 临时停止：`Stop-ScheduledTask -TaskName AidWorkToolRuntime`（同时结束 bat 宿主 cmd 进程）；
  彻底移除：`Unregister-ScheduledTask -TaskName AidWorkToolRuntime`
- **重启（升级后必做）**：`Stop-ScheduledTask -TaskName AidWorkToolRuntime; Start-ScheduledTask -TaskName AidWorkToolRuntime`。
  不要用 Stop-Process 杀 node 进程来重启——会带走整个任务宿主（自愈循环无法接手），任务变 Ready 需手动 Start
- **不要**做成 Windows 系统服务（SYSTEM/Session 0 无法操作用户桌面的 Chrome 与鼠标，
  任务计划的登录任务是唯一正确形态）

### 可选：安装 RapidOCR 提升简历识别精度（推荐）

简历读取（resume-detail / resume-batch）P2 起以 **RapidOCR 为主引擎**，机器上不可用时自动回退
系统 WinRT OCR（零依赖可用性，不装也能用，但 WinRT 中文错字率高且字符间全是空格）。安装后
识别质量显著提升（真机实测近乎完美 vs WinRT 满篇错字），代价是每份简历约多 20-30 秒
（仍在 MCP timeout 600s 内）。

```bash
# 前提：机器上有 Python 3（python.org 安装时勾选 Add python.exe to PATH）
pip install rapidocr-onnxruntime==1.4.4 Pillow   # 版本与开发 venv 对齐；模型随 wheel 内置、离线可用、无需下载
pip install onnxruntime-directml==1.20.1         # 可选：DirectML GPU 加速（任意 DX12 显卡/核显，无需 NVIDIA），OCR 约 3 倍提速
```

- 验证：`aid-runtime doctor`（boss 子进程 doctor）应显示「OCR 引擎（简历读取）：✅ RapidOCR（python: ...，DirectML GPU 加速，单次推理实测 X.Xs）」；
  未装 onnxruntime-directml 时显示 CPU 与纯 CPU 耗时，功能不受影响
- 排障：DirectML 推理异常时适配器自动整批回退 CPU 重跑，无需人工干预；`AID_BOSS_OCR_DML=0` 可强制关闭 GPU 加速
- 引擎选择（环境变量，均不需要管理员权限）：
  - `AID_BOSS_RAPIDOCR_PY=<python.exe 路径>`：显式指定解释器（Python 装在非 PATH 位置时用；
    缺省探测顺序 = 该 env > 仓库根 `venv/Scripts/python.exe`（仅开发布局）> PATH 上的 `python`）
  - `AID_BOSS_OCR_ENGINE`：`auto`（缺省，自动探测；不可用或运行期失败回退 WinRT 并在 stderr
    留一行原因）/ `rapid`（强制 RapidOCR：探测不到**或运行期批量失败**都直接报错并提示部署要求，
    绝不静默降级——要允许回退请用 auto）/ `winrt`（强制回退系统 WinRT OCR）
- 装好 RapidOCR 后如需强制退回 WinRT 排障，设 `AID_BOSS_OCR_ENGINE=winrt` 即可

## 七、升级 / 卸载

```bash
# 升级（配置与配对凭证自动保留，无需重新配对）
npm install -g <新版本 tgz 路径>
# 然后重启后台任务（见 §六：Stop-ScheduledTask + Start-ScheduledTask）

# 卸载
npm rm -g agent-tool-runtime
rmdir /s /q "%APPDATA%\aidwork-tool-runtime"   # 可选：清配置与凭证
```

## 八、常见问题

| 现象 | 处置 |
|---|---|
| doctor 报 CDP 连不上 9222 | 没用调试实例快捷方式开 Chrome（见 §四）；或 Chrome 未开 |
| doctor 报无 BOSS 登录态 | 在调试实例 Chrome 里重新登录 zhipin.com |
| 设备页不在线 | runtime 窗口是否还在运行；`--server` 地址是否正确；服务器网络是否可达 |
| pair 报配对码无效 | 码超 5 分钟过期，重新生成 |
| 打招呼/筛选执行失败 EXECUTION_UNKNOWN | 按工具返回的中文提示人工查看页面（多为确认弹层/风控拦截），勿连续重试 |
| 简历 OCR 错字多 / doctor 显示 OCR 引擎为 WinRT 兜底 | 属可用兜底态；可选安装 RapidOCR 提升精度（见 §六），装后 doctor 复验 |
| 提示付费墙（该职位无开聊权益） | BOSS 账号权益问题，切换职位或开通权益 |

---
*一键安装脚本（install.ps1/upgrade.ps1）与看门狗自愈在《部署封装设计》中规划，
当前以本手册手工流程为准。设计文档：`docs/design/recruiting/recruiting-client-deployment-design.md`*

## 九、客户端计费接入规范（三模式，新客户端必读）

> 设计总纲：[docs/design/billing/client-billing-integration-design.md](../docs/design/billing/client-billing-integration-design.md)
> 铁律：**客户端永远不上报金额**。金额由服务端按价目表/用量计算，客户端只产生"事实"。

### 1. 选模式

| 模式 | 适用场景 | 计费方式 | 需要开发的工作 |
|------|---------|---------|--------------|
| A 代理模式 | 客户端需要 LLM 能力 | 调用经服务端代理，按 token 计费 | 走 `POST /api/client/v1/llm/chat`（协会信息收集客户端同款） |
| B 动作模式 | 客户端执行**服务端下发**的命令 | 按价目表对成功命令计费 | 实现 MCP Provider 接入 agent-tool-runtime（boss cli 同款），价目配 `boss_tool_billing` 同款配置 |
| C 上报模式 | 客户端**本地自主执行**，不经云端下发、不经服务端代理 | 客户端报事实，服务端按 `client_usage_report` 价目表计费 | 见下方接口契约 |

接入流程（三种模式通用前置）：找管理员拿激活码 → `POST /api/client/v1/activate` 换 `access_token`（绑定机器）→ 之后所有请求带 `Authorization: Bearer <token>` → 计费数据自动出现在租户计费页面与管理后台，无需前端改动。

### 2. C 模式接口契约（`POST /api/client/v1/usage/report`）

请求（单条对象与 `{"reports": [...]}` 批量（≤100 条）二选一）：

```json
{
  "client_ref_id": "uuid-v4（幂等键，8-100 字符，网络重试复用同一个）",
  "command": "weixin_add_friend",
  "kind": "action",
  "quantity": 1,
  "arguments_summary": {"target": "张三"},
  "session_id": "客户端本地会话标识（可选）",
  "occurred_at": "2026-09-01T12:00:00+08:00（可选，仅存档）",
  "detail": {"任意事实字段（可选）"}
}
```

- **payload 不含金额字段**，传了也会被忽略；金额 = 服务端价目单价 × quantity（十进制 ceil 到分）
- 价目配置在服务端 `configs/config.yaml` 的 `client_usage_report` 节（匹配优先级 `client名:命令` > `命令` > default），**大小写敏感精确匹配**，未列名命令按 default 计费（default 默认 0=免费——管理员配价时建议给地板价，防客户端换名命令绕费），改价重启生效
- `kind` 枚举：`action`（默认）/ `llm` / `custom`
- 响应 envelope：`{success, accepted, failed, results}`；`success` 仅在全部条目成功时为 true，部分失败看逐条 `results`
- `results` 每条两种形状：成功 `{client_ref_id, success: true, duplicate, credit_cost, balance_after}`；
  失败 `{client_ref_id, success: false, error: "RECORD_FAILED"}`（可原样重试，复用同一 client_ref_id）
- `duplicate=true`（此前已上报过）与 0 元条目的 `balance_after` 为 `null`，表示"本次调用未动余额"，对账以 `credit_cost` 为准
- 截断规则：`arguments_summary` 序列化 >1000 字符截断；`detail` 整体 >1500 字符整块丢弃替换为 `{"_truncated": ...}`；`command/quantity/user_id` 等计费事实键以服务端为准，客户端传同名键无效
- **端点默认关闭**（`client_usage_report.enabled: false`）：未开启时请求返回 HTTP 404 `{"detail": "USAGE_REPORT_DISABLED"}`（鉴权仍先行：无 token 是 401）。接入前联系管理员开启并配价
- 网络失败本地重试必须复用同一 `client_ref_id`；服务端不可达时本地排队补报即可，不会丢账（只要最终报上来）
- 余额不足不阻断上报（动作已发生），余额走负后由充值/提醒机制兜底

### 3. 已接入客户端

| 客户端 | 模式 | 台账 stage |
|--------|------|-----------|
| association-client（协会信息收集） | A 代理 | `official_profile` / `search_profile` / `wechat_*` 等 |
| boss cli（招聘） | B 动作 | `boss_tool` |
| （预留）本地自主执行客户端 | C 上报 | `<client_name>_<kind>` |
