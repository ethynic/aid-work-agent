# BOSS CLI 部署方案调研：员工电脑本地虚拟机 vs 云桌面（远程主机）

> 场景：客户有 100 个 BOSS 账号、100 台员工日常办公电脑。员工电脑不能专用于 boss-cli，
> 需要在不影响员工日常工作的前提下部署 agent-tool-runtime + boss-cli。
> 本文评估两个方案：A. 在员工电脑的 Windows 虚拟机中部署；B. 租用云桌面/云电脑（类似豆包工作的远程主机）。

## 1. 先决条件：boss-cli / runtime 的运行机制决定了硬约束

以下约束来自 `clients/boss-resume-assistant` 和 `clients/agent-tool-runtime` 的实际实现，是评估一切部署形态的基础：

| 约束 | 依据 |
|------|------|
| 仅支持 Windows x64 | `agent-tool-runtime/src/config.ts` PLATFORM='win32-x64'，依赖 DPAPI / Win32 user32 / PowerShell / 任务计划程序 |
| Node ≥ 20 + 本机 Chrome | `clients/release/安装手册.md`；专用调试 profile `C:\chrome-debug`，端口 9222 |
| **必须有可交互桌面会话且未锁屏** | `src/desktopCheck.ts` 执行前用 Win32 `OpenInputDesktop` 检查锁屏，锁屏即拒绝执行 |
| **点击占用真实鼠标光标**（约 1 秒/次），执行期间不能有人动鼠标 | `scripts/win-click.ps1`（SetCursorPos / mouse_event / SendInput 真实事件，防 BOSS 风控检测合成事件） |
| Chrome 窗口不能被遮挡 | `WindowFromPoint` 落点守卫 |
| 常驻在线：心跳 5s + claim 长轮询 20s，断线指数退避重连 | `src/pollLoop.ts` |
| 网络只需出方向 HTTPS 443 到云端，无入站端口 | `安装手册.md`（示例 `https://agent2.aidingyi.cn`） |
| 一台 Windows 用户会话 = 一个设备 = 一个 BOSS 登录态 | BOSS 账号凭据不落库，是 Chrome profile 里一次人工扫码登录；100 账号 = 100 个独立会话 |
| 简历读取走 canvas 截图 + Tesseract OCR，有一定 CPU 开销 | 仓库根 `chi_sim.traineddata` |

**推论**：这套东西本质上要的是「一台有真实桌面、永不锁屏、没人抢鼠标的 Windows 电脑」。
虚拟机恰好能提供这样一个隔离的桌面会话——这正是方案 A 的立足点。

## 2. 方案 A：在员工电脑的 Windows 虚拟机中部署

### 2.1 可行性：✅ 可行

VM 内的 Windows 是一个完全独立的桌面会话：员工在宿主机上动鼠标、锁屏、遮挡窗口，都不影响 VM 内的会话。
这直接化解了 boss-cli 最大的部署矛盾（执行时抢占真实光标、要求未锁屏、窗口不被遮挡）。
Hyper-V / VMware Workstation / VirtualBox 均可，VM 内安装流程与物理机完全一致（同一个 tgz 安装包 + 配对码 + `register_runtime_task.ps1` 自启）。

### 2.2 虚拟机需要满足的条件

宿主机：
- CPU 支持硬件虚拟化并在 BIOS 开启 VT-x / AMD-V（近 5 年的办公电脑基本都支持，但**需在 BIOS 确认已开启**）
- 用 Hyper-V 需 Windows 10/11 **专业版及以上**（家庭版没有 Hyper-V，可退而用 VMware Workstation Player / VirtualBox）
- 内存建议 ≥ 16GB（宿主机留 8-12GB，VM 分 4GB）；8GB 内存的旧机器跑 VM 会明显卡，不建议
- SSD 剩余空间 ≥ 60GB

VM 配置（每账号一台）：
- Windows 10/11 x64，2 vCPU / 4GB 内存（Chrome + OCR 场景不建议低于 4GB），60GB 动态磁盘
- 网络用默认 NAT 即可（只需出方向 443）
- VM 内电源策略：永不睡眠、永不关屏、永不锁屏；设置 Windows 自动登录
- 装 Node 20+、Chrome，然后按 `clients/release/安装手册.md` 正常安装配对

运维项：
- Hyper-V 设置 VM 自动启动（宿主机重启后自动拉起）；VM 内自动登录 + 任务计划自启 aid-runtime（现有脚本直接复用）
- 设备在线状态在 Web 端「本地工具/设备管理」可见，员工误关 VM 能被发现

### 2.3 「虚拟机窗口需要一直显示着吗？」——不需要，但会话必须活着

这是最常见的疑问，拆开说：

- **VM 窗口（Hyper-V 虚拟机连接 / VMware 窗口）不需要一直开着。** 关掉窗口只是断开"显示器"，VM 内的 Windows 照常运行、会话保持登录不锁屏，boss-cli 不受影响。员工平时根本不用打开这个窗口。
- **必须保持的是：VM 处于运行状态 + 里面 Windows 会话已登录且未锁屏。** 所以 VM 内要关掉自动锁屏和睡眠。
- **⚠️ RDP 的坑**：如果运维时习惯用远程桌面（mstsc）连 VM，**断开 RDP 默认会把会话锁屏**，boss-cli 会检测到锁屏拒绝执行。规避办法：
  1. 优先用 Hyper-V 自带的「虚拟机连接」（VMConnect）做运维，关掉它不影响会话状态（推荐）；
  2. 或断开 RDP 前在 VM 内执行 `tscon <会话ID> /dest:console` 把会话还给控制台；
  3. 组策略禁用交互式会话锁屏（`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI\SessionData` 的 `AllowLockScreen` 等）。

### 2.4 方案 A 风险

- 员工可能关机、关 VM、拔网 → 设备离线（有心跳监控可发现，但需要运维纪律）
- 100 台电脑硬件/系统版本参差，逐台装机和排障成本高（虽然有一次性安装手册）
- **对 BOSS 风控最友好**：用的是员工真实办公网络 IP 和真实办公电脑环境，与人工操作的网络特征一致

## 3. 方案 B：云桌面 / 云电脑（类似豆包工作的远程主机）

### 3.1 可行性：✅ 可行，且是云厂商的成熟场景

云电脑本质就是一台 7×24 在线的 Windows 桌面虚拟机，断开后继续运行是标准行为——
[无影官方文档](https://help.aliyun.com/zh/edsp/user-guide/set-scheduled-task-upon-disconnection)明确「断开云电脑连接后，云电脑默认继续保持运行」，且有断连定时策略可配置（要把自动关机/休眠策略关掉）。阿里云无影甚至有专门的 [RPA 自动化场景方案](https://www.yun88.com/product/7683.html)，描述与本场景（持久运行 + 图形交互自动化）完全吻合。

需要在云电脑内做的配置与方案 A 相同：关锁屏、关睡眠、自动登录、装 Node+Chrome+runtime。

### 3.2 厂商与费用（公开价格快照，2026 年中；促销价波动大，采购前以官网价格计算器为准）

| 厂商产品 | 参考配置 | 参考价格 | 备注 |
|---------|---------|---------|------|
| 阿里云无影云电脑 企业版 | 4C8G | 促销 59 元/3个月、199 元/年（[来源](https://zhuanlan.zhihu.com/p/1958547367360964500)）；常态价通常百元级/月 | 市占最高，有 RPA 场景方案；注意区分个人版「核时额度」套餐（限时运行，不适合 7×24 常驻），要选包月不计时/企业版 |
| 华为云 Flexus 云桌面 | 企业版 | **69.9 元/月起**（首购优惠，[官网](https://www.huaweicloud.com/product/flexus-workspace.html)） | ⚠️ 其卖点「智能休眠，不使用自动休眠不扣费」对本场景是毒药——必须在策略中**关闭休眠**，否则 runtime 心跳会断 |
| 天翼云电脑（公众版） | 2C4G 基础版 | 60 元/月起，限时折扣 21 元/月起（[计费说明](https://www.ctyun.cn/document/10026992/10027298)）；政企版见[文档](https://www.ctyun.cn/document/10027004/10028040) | 电信线路，价格最低一档 |
| 移动云电脑（政企版） | - | 数十元/月量级 | 有 24 小时挂机不断线实测记录（[参考](https://mgoods.taobao.com/t/lvyou_1471/f3169c029de6b6aadf0f0783d4eef073.html)） |
| 传统 Windows Server 云主机（ECS/CVM） | 2C4G | 约 100-200 元/月 | ❌ 不推荐：Windows Server 的交互式会话配置麻烦、镜像含 license 溢价，云桌面产品各方面更贴合 |

**补充（无影 API 自动化，2026-09 确认）**：无影企业版支持 OpenAPI 批量拉起云电脑（产品代码 `ecd/2020-09-30`）：核心接口 [CreateDesktops](https://help.aliyun.com/zh/wuying-workspace/developer-reference/api-ecd-2020-09-30-createdesktops)（`Amount` 批量创建，传 `BundleId` 选 4C8G 模板、`AutoPay`/`AutoRenew`），前置需 CreateSimpleOfficeSite（办公网络）+ DescribeBundles + DescribePolicyGroups 各调一次。支持 `UserCommands` 开机注入脚本（可做 Node/runtime 自动化装机）、`GetConnectionTicket` 免密连接凭证、断连策略 API 配置；多语言 SDK + Terraform，流控 1000 次/60s。**注意**：199 元/年等促销价通常仅限控制台活动页，API 按目录价/商务折扣走，100 台规模建议先找阿里云商务谈价；建议先按量（PostPaid）创建 2-3 台验证风控，通过后再转包月。

**100 台规模估算**：按 30-70 元/台/月，约 **3000-7000 元/月**（量大可谈商务折扣；无影企业版 100 台规模有阶梯价）。

### 3.3 方案 B 特有风险（重要）

- **⚠️ IP 风控（最大风险）**：云电脑出口是数据中心 IP。BOSS 直聘风控对机房 IP 敏感——本项目已有前科：首次真机用「全新 profile Chrome 登录」即被封号（见 [boss-recruiting-agent-research.md](boss-recruiting-agent-research.md)）。100 个账号集中从机房 IP 段登录，风控特征明显，**必须先小规模验证**：建议先开 2-3 台云电脑跑 1-2 周，观察是否触发验证/封号，再决定放量。
- 账号异地登录：账号平时在员工手机/办公网使用，突然固定到异地机房 IP，可能触发登录验证（需员工配合扫码，登录态之后由 Chrome profile 持久化）。
- 无人值守策略：各家默认的断连休眠/自动锁屏策略必须逐个关掉。
- 数据合规：客户员工的 BOSS 账号登录态落在第三方云厂商的机器上，需在合同中向客户说明。

## 4. 对比与建议

| 维度 | A. 员工电脑本地 VM | B. 云桌面 |
|------|------------------|----------|
| 增量成本 | ≈ 0（用现有硬件） | 3000-7000 元/月 |
| BOSS 风控 | 最优（真实办公 IP/环境） | 较差（机房 IP），需先验证 |
| 对员工影响 | 宿主机资源被占 4GB 内存左右；旧电脑可能卡 | 无 |
| 运维 | 分散在 100 台电脑，逐台装机排障 | 集中控制台管理，批量运维容易 |
| 可用性 | 依赖员工电脑开机 | 7×24，厂商 SLA |
| 网络要求 | 员工办公网能出 443 即可 | 员工无任何依赖 |

**建议**：

1. **默认选方案 A（本地 VM）**：零增量成本、风控最友好，且技术上已确认完全可行（VM 窗口无需常开，只要 VM 内会话保持登录未锁屏）。适合电脑配置尚可（≥16GB 内存）的环境。
2. **方案 B 作为补充/兜底**：用于员工电脑太旧跑不动 VM、或客户希望集中运维的场景。若采用，**务必先 2-3 台小规模验证 BOSS 风控 1-2 周**再放量；产品上优先无影企业版（RPA 场景成熟）或天翼云电脑（价格最低），华为 Flexus 需确认关闭智能休眠。
3. 也可混合：大部分机器本地 VM，少数低配机器用云桌面——架构上两者对云端完全等价（都是一台已配对设备），无需任何代码改动。

## 5. 参考资料

- 本项目机制依据：`clients/boss-resume-assistant/AGENTS.md`、`clients/agent-tool-runtime/src/`（config.ts / desktopCheck.ts / pollLoop.ts）、`clients/release/安装手册.md`
- [无影云电脑断连定时策略（断连默认继续运行）](https://help.aliyun.com/zh/edsp/user-guide/set-scheduled-task-upon-disconnection)
- [无影云电脑价格介绍（知乎专栏）](https://zhuanlan.zhihu.com/p/1958547367360964500)、[无影个人版计费](https://help.aliyun.com/zh/edsp/product-overview/personal-edition-billing)
- [华为云 Flexus 云桌面（69.9 元/月起）](https://www.huaweicloud.com/product/flexus-workspace.html)、[华为云桌面定价](https://www.huaweicloud.com/product/workspace/pricing.html)
- [天翼云电脑公众版计费说明](https://www.ctyun.cn/document/10026992/10027298)、[政企版计费说明](https://www.ctyun.cn/document/10027004/10028040)
- [无影 RPA 自动化场景方案](https://www.yun88.com/product/7683.html)
