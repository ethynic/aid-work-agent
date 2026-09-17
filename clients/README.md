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

### 1. 一键打包（推荐）

```bash
bash clients/pack.sh 0.2.15        # 版本号必填；--skip-smoke 可跳过冒烟
```

脚本自动完成并校验（2026-09-17 固化，每步防一个真实踩过的坑）：

- 两处 `package.json` 版本统一修改 + 两个 lockfile 同步（runtime 捆绑 boss CLI 是否刷新
  **只看版本号**，版本不变会把删 OCR 之前的旧拷贝原样打进包——0.2.13 重打包仍 140MB 即此因）
- 打包前清 stale 捆绑拷贝 / `__pycache__` / 旧 dist（防陈旧产物混入）
- `npm pack`（prepack 自动构建 boss CLI → runtime → install-links 捆绑）
- 产物硬校验：包体 ≤15MB（OCR 环境混入就是 ~140MB，体积是最后防线）、无 ocr-python/
  rapidocr 残留、捆绑 boss 版本=目标版本、简历管线 4 个 ps1 脚本与双 CLI 入口齐全
- 冒烟：临时目录安装 tgz，`aid-runtime --help` 与 boss CLI `--help` 均可执行
- 产物移入 `clients/release/`，并在 `release/VERSION.txt` 顶部插入占位条目

### 2. 手工打包（备用；不推荐）

`clients/boss-resume-assistant` 与 `clients/agent-tool-runtime` 两处 `package.json` 的
`version` 同步修改后：

```bash
cd clients/agent-tool-runtime
npm pack
```
`prepack` 钩子会自动依次：构建 boss CLI → 构建 runtime →
`npm install --install-links`（把 file: 符号链接依赖转成实体目录打进 tar，避免 `..` 路径错乱）；
`postpack` 打完自动恢复开发态。**产物：`agent-tool-runtime-<版本>.tgz`（boss CLI 已捆绑在内，
客户机无需单独安装 boss 包）。**

> ⚠️ 2026-09-17 去 OCR 化：简历识别改为云端 GLM-5.3-Flash 多模态（0.2.14 起），包内不再捆绑
> `ocr-python/` Python 环境——体积从 ~140MB 降到 ~3.4MB，客户机不再需要任何本地 OCR 依赖。
> ⚠️ 手工打包前**必须升 boss CLI 版本号**并删 `agent-tool-runtime/node_modules/boss-resume-assistant`：
> `--install-links` 按 version 判断是否刷新捆绑拷贝，版本不变会打进旧副本
> （0.2.13→0.2.14 就栽在这：OCR 已删但包里还有 140MB 旧环境）。

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
- 配置：`%APPDATA%\aidwork-tool-runtime\config.json`（server 地址 / device_id / providers 入口）
- 凭证：`%APPDATA%\aidwork-tool-runtime\credentials.bin`（DPAPI 加密，勿手工编辑/拷贝到别的机器——绑定了本机）

### Runtime 多 Provider 配置（微信等新能力的启用入口）

Runtime 从多 Provider 版本起支持在**一台设备上承载多个受信 Provider**（BOSS 直聘之外如微信操作能力）。配置入口是 `config.json` 的 `providers` 字段（key → 各 Provider CLI 入口绝对路径，本地管理员手工配置，**禁止云端下发**）：

```json
{
  "server": "https://agent2.aidingyi.cn",
  "device_id": "...",
  "bossCliEntry": "C:\\...\\boss-resume-assistant\\dist\\src\\cli\\index.js",
  "providers": {
    "weixin": { "entry": "C:\\...\\weixin-cli\\dist\\src\\cli\\index.js" }
  }
}
```

规则与现状（与 `clients/agent-tool-runtime/src/providers.ts` 受信注册表一致）：
- `boss-recruiting` 恒可用：`bossCliEntry` > `providers['boss-recruiting'].entry` > 包内默认入口（只装 BOSS 的存量设备零配置不变）；
- 其余 Provider（当前为 `weixin`）**仅在 `providers` 显式配置 entry 时启用**——未配置=未安装，不上报 capability、不领取对应任务；
- capability 上报为 `providers` 数组 + `provider_manifests`（provider_id/manifest_digest/protocol_version），服务端按此路由任务。

**微信 Provider（`ai.aidwork.weixin`）启用前提**（三者缺一不可）：
1. **安装**：目标机器具备微信操作 CLI（weixin-cli 构建产物），并在 `config.json` 的 `providers.weixin.entry` 配置其入口路径；`aid-runtime doctor` 逐 Provider 自检通过、`status` 列出 weixin；
2. **受信**：CLI 的工具集必须落在服务端与 Runtime 双侧受信清单内——设备侧 `weixin_probe` / `weixin_chat_search` / `weixin_history_read` / `weixin_unread_list`（只读）与写操作 `weixin_message_send`（服务端另批准 v2 统一操作名 `weixin_message_send_v2`，走许可/证据链，不经 v1 发送）；清单外工具一律 TOOL_NOT_ALLOWED；
3. **capability**：配对后设备上报的 capabilities 含 weixin 条目（manifest digest 匹配），服务端才会把微信任务派给该设备。

> 版本边界：weixin Provider 当前 `protocol_version=1`（v2 受控写协议随真机验收 P0 交付前，v2 任务在 Runtime 侧 PROTOCOL_NOT_SUPPORTED 拒绝、不降级旧发送）。因此**仅安装+配置 entry 不会产生任何自动发送行为**；发送链路的服务端总开关见 `docs/ops/weixin-marketing-rollout.md`。

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

### 简历识别（v2 云端化，客户机零识别依赖）

简历读取（resume-detail / resume-batch）0.2.14 起**改为云端识别**：客户机只做滚动截图+
拼接，拼接长图随结果回传，由服务端调 GLM-5.3-Flash 多模态一次产出（姓名核对 + 人物总结 +
职位匹配评分 + key_info）。客户机**不再需要 Python/RapidOCR/任何本地识别引擎**（0.2.13 及
以前捆绑的 `ocr-python/` 环境已移除，包体积 ~140MB → ~3.4MB）。

- 姓名核对：页面姓名与图中姓名由服务端比对（≤1 字容差），不符该份不入库不扣费
- 计费：识别费 1 积分/份，云端评估成功即扣（截图不扣费）；单价服务端 config 可调
- 服务端依赖：部署 ≥ 含 resume_vl 的服务端版本，且服务端配置 ZHIPU_API_KEYS（GLM-5.3-Flash）

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
| 简历识别失败/提示服务端评估失败 | 0.2.14 起识别在云端：查服务端日志（简历评估/ZHIPU key 配置）；客户机只需确认截图拼接正常 |
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
