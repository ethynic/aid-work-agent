# Probe 风险等级与测试目标白名单格式

> 阶段：M0 产物，对应 [开发计划](../../../plans/weixin/plan-weixin-cli.md) §2 与设计 §7
>
> 本文件是 M3~M5 所有 probe 的强制规范；每个开发/测试/CR 智能体的任务说明必须引用本文件与计划 §1 的硬性禁改路径清单。

## 1. 风险等级（P0~P3）

| 级别 | 名称 | 允许动作 | 示例 | 强制要求 |
|---|---|---|---|---|
| P0 | 观察 | 枚举窗口/UIA 树、截图哈希、读剪贴板 format 列表 | 文章页是否暴露链接 | 无业务副作用；不发送输入、不改剪贴板内容、不激活窗口 |
| P1 | 导航 | 激活窗口、打开页面、输入但**不提交**，或只读提交（搜索） | 搜一搜文章分类切换 | 严格前台门禁；结束后自动清理会话并恢复主窗口 |
| P2 | 沙箱写 | 对白名单内的专用测试目标执行**一次**写动作 | 发送 1 条测试消息、关注测试公众号 | 显式人工确认 + 固定白名单目标（见 §2）；写后必须回读校验 |
| P3 | 稳定性 | 重复执行、异常注入、DPI/主题/版本矩阵 | 连续 30 次发送 + 写后校验 | 达标前不得注册 MCP tool；只读 ≥98% 成功率，写动作错目标数 = 0 |

晋级规则：能力按 P0 → P1 →（P2）→ P3 顺序晋级，不得跳级；任一级别不达标即停留在 `experiments/`，不进入 manifest。

## 2. 测试目标白名单格式（P2 强制）

白名单写在 `experiments/probes/<probe-id>/probe.json` 的 `target_whitelist` 字段。**禁止明文记录真实联系人/群/公众号名称**：`label_hash` 用于程序比对，`alias` 仅供人类阅读。

```json
{
  "probe_id": "m5-message-send",
  "risk_level": "P2",
  "hypothesis": "对白名单测试好友可通过 target_ref 唯一恢复目标并完成单条文本发送",
  "allowed_actions": ["activate_window", "type_text_no_send", "send_single_text"],
  "target_whitelist": [
    {
      "alias": "测试好友A（专用）",
      "type": "friend",
      "label_hash": "sha256:<对显示名做 SHA-256 的 hex>",
      "added_on": "2026-08-11",
      "owner": "<登记人>"
    },
    {
      "alias": "测试群B（专用）",
      "type": "group",
      "label_hash": "sha256:<...>",
      "added_on": "2026-08-11",
      "owner": "<登记人>"
    },
    {
      "alias": "测试公众号C（专用）",
      "type": "official_account",
      "label_hash": "sha256:<...>",
      "added_on": "2026-08-11",
      "owner": "<登记人>"
    }
  ],
  "forbidden_actions": ["bulk_send", "read_history", "send_image_or_file", "retry_on_unknown"],
  "success_criteria": "30 次发送，错目标数 = 0；写后校验失败一律 effect=unknown 且不重试",
  "cleanup": "删除测试消息不可行时记录残留；测试目标由登记人保管"
}
```

字段约束：

| 字段 | 约束 |
|---|---|
| `type` | `friend` / `group` / `official_account` |
| `label_hash` | `sha256:` + 显示名 UTF-8 字节的 SHA-256 hex（小写）；运行时目标显示名 hash 不在白名单内 → 立即拒绝 |
| `alias` | 人类可读代号，**不得**是真实姓名/群名/公众号名 |
| `owner` | 该测试目标的保管人；目标变更（改名、解散）时 owner 负责更新白名单 |
| `allowed_actions` | 枚举值，probe 代码不得执行清单外动作 |
| `forbidden_actions` | 显式负面清单，review 时逐条核对 |

## 3. 白名单使用规则

1. P2/P3 probe 运行时读 `probe.json`，目标显示名规范化后算 hash 与白名单比对，不在名单内拒绝执行；
2. 白名单**只在实验区使用**；正式 tool 通过 `target_ref`（短期签名 handle）约束目标，不内嵌任何白名单；
3. `probe.json`、测试目标、截图、诊断样本均不进入正式发布包（设计 §3.3 / §10.3）；
4. 每个写动作 probe 独立过门禁，不捆绑放行（计划 §1 总原则）。
