# 客户端工具部署手册（boss CLI + agent-tool-runtime）

> 面向角色：给客户 Windows 电脑安装「招聘智能体本地执行组件」的实施/支持人员。
> 两个组件的关系：**agent-tool-runtime（执行节点）在打包时已捆绑 boss CLI（BOSS 直聘
> 操作能力）**——客户机只需安装一个 tgz 包，得到 `aid-runtime` 命令。

---

## 一、前置要求（客户电脑）

| 项 | 要求 | 检查方式 |
|---|---|---|
| 操作系统 | Windows 10/11 x64 | — |
| Node.js | **22 LTS 或更高** | `node -v`；未装去 https://nodejs.org 下 LTS 安装 |
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

## 四、Chrome 调试实例（关键步骤，缺了工具连不上浏览器）

Chrome 136+ 出于安全限制，**默认用户数据目录下会静默忽略调试端口参数**——必须用
独立数据目录起一个「调试实例」：

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
- 日志位置：`%APPDATA%\aidwork-tool-runtime\logs\runtime.log`（`runtime.old.log` 为轮转前一代）
- bat 优先用 npm 全局命令 `aid-runtime`，找不到时回退开发机仓库路径（客户机走前者）
- 临时停止：`Stop-ScheduledTask -TaskName AidWorkToolRuntime`（同时结束 bat 宿主 cmd 进程）；
  彻底移除：`Unregister-ScheduledTask -TaskName AidWorkToolRuntime`
- **不要**做成 Windows 系统服务（SYSTEM/Session 0 无法操作用户桌面的 Chrome 与鼠标，
  任务计划的登录任务是唯一正确形态）

## 七、升级 / 卸载

```bash
# 升级（配置与配对凭证自动保留，无需重新配对）
npm install -g <新版本 tgz 路径>
# 然后重启 runtime 进程（结束旧进程再 aid-runtime start，或注销重登）

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
| 提示付费墙（该职位无开聊权益） | BOSS 账号权益问题，切换职位或开通权益 |

---
*一键安装脚本（install.ps1/upgrade.ps1）与看门狗自愈在《部署封装设计》中规划，
当前以本手册手工流程为准。设计文档：`docs/design/recruiting/recruiting-client-deployment-design.md`*
