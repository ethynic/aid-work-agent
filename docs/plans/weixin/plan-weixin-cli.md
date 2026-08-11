# weixin-cli 第一方 CLI / MCP Provider 开发计划

> 状态：📋 待开发
>
> 日期：2026-08-11
>
> 设计：[weixin-cli 设计](../../design/weixin/weixin-cli-design.md)
>
> 流程：非平凡代码阶段严格执行 `.claude/rules/dev_workflow.md`（开发→独立测试→独立 Code Review）；不自动提交或 push。

## 1. 交付策略

按“先建立独立 Provider、再只读扩展、最后受控写动作”推进。每个新能力先做 probe，达不到可验证成功终态就停留在实验区，不为了凑 tool 数量发布。协会客户端保持冻结，只作为只读技术参考，不在本计划中修改。

阶段依赖：

```text
M0 基线冻结
  → M1 Provider 骨架
  → M2 搜一搜产品化（v0.1）
  → M3 文章 probes 与只读 tools（v0.2）
  → M4 好友/群 probes 与检索 tool（v0.3）
  → M5 消息/关注 probes 与写 tools（v0.4）
  → M6 打包、跨 Host 与发布
```

总原则：M2 未完成前不开展写动作；协会客户端及其脚本不属于本计划改动范围；M5 每个写 tool 独立过门禁，不捆绑放行。

硬性禁改路径：

```text
clients/association-client-cli/**
clients/association-client/**
src/services/association_enrichment_providers.py
tests/unit/services/test_association_*.py
docs/tools/association-client-*.md
```

这些路径允许只读分析和复制代码片段到 `clients/weixin-cli/`，禁止编辑、删除、移动、建立 symlink、抽共享模块或增加对 `weixin-cli` 的调用。每个开发、测试和 Code Review 智能体的任务说明都必须包含这份禁改清单。

## 2. M0：现状冻结与实验规范

状态：✅ 已完成（2026-08-11，产物见 `docs/research/weixin-cli/`）

任务：

- [x] 记录 `clients/wechat-souyisou-rpa/` 当前离线测试和真机基线（见 [m0-baseline-freeze.md](../../research/weixin-cli/m0-baseline-freeze.md) §2/§3）；
- [x] 只读比对协会客户端和旧 RPA 的核心脚本，记录可复用算法与已知踩坑（同上 §4/§5/§6：4 个 ps1 字节一致，Python 业务层有差异且不进入 weixin-cli）；
- [x] 冻结旧 ps1 stdin/stdout、错误码、artifact 和 cleanup 行为样本（见 [m0-frozen-protocol-samples.md](../../research/weixin-cli/m0-frozen-protocol-samples.md)）；
- [x] 建立 `docs/research/weixin-cli/` 与 [probe 报告模板](../../research/weixin-cli/probe-report-template.md)；
- [x] 建立 [P0/P1/P2/P3 probe 风险等级和测试目标白名单格式](../../research/weixin-cli/probe-risk-and-whitelist.md)；
- [x] 将上述硬性禁改路径写入各阶段实施规格和智能体任务说明（§1 清单 + probe 规范文件头部强制引用）；
- [x] 在 `docs/ideas.md` 标记开始开发时更新为 🔧 部分完成。

验收：不操作真实微信即可用冻结样本验证后续 driver；`git diff` 确认协会禁改路径为零改动（M0 仅新增 `docs/research/weixin-cli/` 四个文档，禁改路径零改动）。

## 3. M1：TypeScript Provider 骨架

状态：✅ 已完成（2026-08-11，三智能体流程：开发→独立测试→独立 CR，主控终检通过）

任务：

- [x] 创建 `clients/weixin-cli/` Node.js 22 + TypeScript 工程；
- [x] 从 BOSS reference provider 复用结构，不复制招聘领域代码；
- [x] 实现 `OperationResult`、错误映射、progress、AbortSignal 和 operation 注册表；
- [x] 实现 `aid-weixin mcp --stdio`、`doctor`、`version --json`；
- [x] 实现 Provider manifest、tool schema 单一来源和 digest；
- [x] Provider ID=`ai.aidwork.weixin`、target=`local_required`、platform=`win32-x64`；
- [x] 接入进程级单飞和跨进程命名互斥（Windows 命名管道 `\\.\pipe\AidWorkAgent.AidWeixin.<scope>`，进程死亡 OS 自动回收）；
- [x] 接入 `clients/shared/mcp-conformance/`；
- [x] `doctor` 保证严格只读；
- [x] 测试 stdout 零污染、stderr 脱敏、关闭无孤儿进程、取消后锁释放。

验收：无微信环境时 initialize/list/call 都快速返回结构化结果 ✅；conformance 全绿（npm test 内含 8 项 + 独立 CLI 模式 6/6）✅；doctor 不激活窗口、不发送输入 ✅。测试 **53/53**、typecheck 0 错误。CR 修复：命名管道常驻 error 监听、互斥系统错误映射 INTERNAL_ERROR、toolDefs↔registry 启动期一致性守卫。

开发流程：本阶段完成后独立测试和 CR，主控者亲自运行 build、typecheck、conformance。✅ 已执行。

## 4. M2：现有搜一搜能力产品化到 weixin-cli（v0.1）

状态：📋 待开发

### M2.1 独立 PowerShell driver

- [ ] 从协会现有微信脚本一次性复制所需代码到 `clients/weixin-cli/automation/powershell/`，源文件保持不变；
- [ ] 复制后去除协会业务命名和业务依赖，在新目录内建立独立实现；
- [ ] 禁止通过 import、相对路径、symlink、submodule 或运行时 spawn 使用协会目录文件；
- [ ] 拆分固定 driver JSON 与人工 CLI/MCP adapter；
- [ ] 禁止 driver 接收任意 executable/cwd/env/raw argv；
- [ ] 保留窗口身份、前台守卫、KeyUp、DPI、剪贴板、DPAPI、精确 HWND cleanup；
- [ ] TypeScript 子进程超时需先协作取消，再限时回收；
- [ ] stdout 单行 JSON，stderr/日志脱敏；
- [ ] 用 M0 冻结样本跑行为等价回归。

### M2.2 三个首发 operation

- [ ] `weixin_probe`；
- [ ] `weixin_souyisou_search(query, category=all, limit<=10)`；
- [ ] `weixin_souyisou_collect(query, required_terms<=5, limit<=10)`；
- [ ] 移除 operation 中的协会、秘书长、手机号归属和 LLM 概念；
- [ ] 搜索/详情证据返回加密 artifact refs；
- [ ] 创建临时插件窗口的结果必须含 `session_closed`；
- [ ] cleanup 失败覆盖业务成功，返回 `SESSION_CLEANUP_FAILED`；
- [ ] MCP annotations、instructions、错误码和 effect 与设计一致。

### M2.3 测试与真机

- [ ] 将适用的旧 PowerShell 测试复制并改造成 `weixin-cli` 自有测试，旧测试保持不变；
- [ ] driver/operation/MCP 三层单测；
- [ ] 100%/125%/150% DPI、单/双屏、深/浅色真机矩阵；
- [ ] 打开/搜索/复制各 30 次，目标成功率 ≥98%；
- [ ] 焦点抢占、锁屏、退出微信、慢加载、隐藏插件 HWND；
- [ ] artifact、日志和 stdout 敏感信息扫描。

验收：v0.1 manifest 只列 3 个已验证 tool；CLI 与 MCP 调用同一 operation；现有搜一搜真机能力没有稳定性退化。

## 5. M3：公众号文章 probes 与只读 tools（v0.2）

状态：📋 待开发

### M3.1 Probe A：文章分类和结果

- [ ] 验证“文章”分类的 UIA/键盘/剪贴板可达路径；
- [ ] 验证不切分类时查询约束能否稳定得到文章结果；
- [ ] 记录不同主题、DPI、窗口大小、微信版本；
- [ ] 定义 article result fingerprint 和短 TTL result ref；
- [ ] 连续 30 次目标文章定位，不允许按固定坐标盲点。

### M3.2 Probe B：文章 URL

- [ ] 依次验证 CF_HTML href、微信“复制链接”、外部浏览器地址；
- [ ] 验证得到的是文章 canonical/可访问 URL 而非临时跳转或无关链接；
- [ ] 无真实 URL 证据时稳定返回 `CONTENT_UNAVAILABLE`；
- [ ] URL 不写入猜测或 LLM 补全逻辑。

### M3.3 Probe C：文章正文

- [ ] 验证标题、公众号、时间、正文的复制路径；
- [ ] 验证长文滚动、重复段去重、图片/PDF/外链文章；
- [ ] 定义正文最大 inline 字符数和 artifact 溢出策略；
- [ ] 正文读取失败时恢复到结果页并清理会话。

### M3.4 产品化

- [ ] `weixin_article_search`；
- [ ] `weixin_article_read`；
- [ ] `weixin_article_get_url`；
- [ ] tool schema、错误码、注解、conformance 和跨 Host smoke test；
- [ ] 只有通过对应 probe 的 tool 才进入 v0.2 manifest。

验收：URL 100% 来自可回溯 UI/剪贴板证据；文章正文和标题可机器验证对应；30 次只读闭环达到目标且无残留窗口。

## 6. M4：好友和群检索 probes（v0.3）

状态：📋 待开发

任务：

- [ ] 观察主窗口顶部搜索与通讯录路径的 UIA/剪贴板结构；
- [ ] 分别验证好友、群、公众号的结果分类；
- [ ] 覆盖备注名、昵称、微信号、重名好友、同名群、中英文和特殊字符；
- [ ] 明确“唯一精确命中”和“候选歧义”的可验证规则；
- [ ] 实现短期签名 `target_ref`，绑定账号/进程/目标指纹/TTL；
- [ ] 防串改、过期、跨账号、微信重启后复用测试；
- [ ] 产品化 `weixin_chat_search`，不读取聊天历史、不打开聊天；
- [ ] 敏感 label 默认脱敏，日志只记 query hash 和候选数量。

验收：重名场景绝不自动选首项；错误 target ref 拒绝率 100%；检索操作 effect=none。

## 7. M5：消息和关注写动作 probes（v0.4）

状态：📋 待开发

### M5.1 单条文本消息

- [ ] 仅使用专用测试好友/测试群白名单做 P2 probe；
- [ ] 从未过期 target ref 恢复并回读聊天目标；
- [ ] 输入但不发送 probe，验证输入框与目标窗口；
- [ ] 单次发送 1 条、首版最大 500 字；
- [ ] 发送后验证最后一条消息特征，不读取历史正文；
- [ ] 覆盖焦点抢占、网络延迟、微信提示、发送后进程崩溃；
- [ ] 无法确认时 `EXECUTION_UNKNOWN/effect=unknown`，不得重试；
- [ ] 产品化 `weixin_message_send`，MCP 不接受裸显示名；
- [ ] 30 次测试目标发送错误目标数必须为 0。

### M5.2 关注公众号

- [ ] 只对专用测试公众号执行 probe；
- [ ] 定义账号唯一指纹，重名或仿冒候选必须歧义失败；
- [ ] 读取关注前状态；
- [ ] 执行一次关注并回读“已关注”；
- [ ] 已关注时返回成功但 effect=none；
- [ ] 动作发出但状态不可读时返回 unknown；
- [ ] 产品化 `weixin_official_account_follow`。

验收：两个写 tool 分别独立通过 P2/P3、测试、CR 和真机门禁；任何一个未达标不影响另一个发布，但不得留在 manifest 中占位。

## 8. M6：打包、Host 兼容与发布

状态：📋 待开发

任务：

- [ ] 生成自包含 `aid-weixin.exe`，内置正式 PowerShell driver；
- [ ] 正式包排除 `experiments/`、测试目标、截图和诊断样本；
- [ ] provider manifest、schema digest、签名和版本校验；
- [ ] `doctor` 覆盖安装、微信版本、交互桌面和 artifact 权限；
- [ ] Local Tool Runtime catalog 注册与直连验收；
- [ ] Codex MCP smoke test；
- [ ] WorkBuddy 或当时选定的国内主流 Host smoke test；
- [ ] 干净 Windows 10/11 机器安装验收；
- [ ] 编写 README、安装、升级、诊断和安全说明；
- [ ] 更新 `docs/ideas.md`，完成的阶段勾选；全部完成后移动至 `docs/ideas_finished.md`。

验收：同一个二进制不依赖 aid-work-agent 云端即可被人工终端和两个外部 Host 使用；Runtime 也调用同一 MCP schema。

## 9. 阶段检查点

| 检查点 | 可交付结果 | 阻塞条件 |
|---|---|---|
| M0 | 冻结协议、样本和隔离边界 | ✅ 已完成（2026-08-11） |
| M1 | 空壳标准 Provider | ✅ 已完成（2026-08-11，53/53 + conformance 全绿） |
| M2 | v0.1 搜一搜 Provider | 真机稳定性或 cleanup 退化则停止 |
| M3 | v0.2 文章只读能力 | URL/正文无法验证的 tool 不发布 |
| M4 | v0.3 好友/群检索 | 重名消歧失败不进入写动作 |
| M5 | v0.4 消息/关注 | 错目标或 unknown 重试风险未消除则不发布 |
| M6 | 可分发产品 | 任一 Host/签名/干净机门禁失败不发布 |

## 10. 预计工作量

| 阶段 | 预估 |
|---|---|
| M0 | 1～2 天 |
| M1 | 2～3 天 |
| M2 | 4～7 天 |
| M3 | 5～9 天（受微信 UI probe 结果影响） |
| M4 | 4～7 天 |
| M5 | 6～10 天 |
| M6 | 3～5 天 |

M0～M2 是第一里程碑，约 7～12 天；后续每种能力按 probe 证据独立排期，不承诺在未知 UI 结构下固定日期。

## 11. 当前下一步

1. ~~先执行 M0，不写产品代码~~（M0 已完成 2026-08-11）；
2. ~~M0 输出经确认后~~，按完整三智能体流程开发 M1；
3. M2 完成且 v0.1 真机验收后，直接进入 M3 公众号文章只读 probe；正式代码阶段仍保持三智能体串行验证。
