# Probe 报告模板

> 阶段：M0 产物，对应 [开发计划](../../../plans/weixin/plan-weixin-cli.md) §2 与设计 §7.2
>
> 使用方式：每个 probe 在 `docs/research/weixin-cli/<probe-id>.md` 按本模板填写；实验代码与 `probe.json` 放 `clients/weixin-cli/experiments/probes/<probe-id>/`。
>
> 禁止内容：聊天正文、联系人明文、手机号、cookie、token、未脱敏截图。

---

# Probe <probe-id>：<一句话主题>

> 日期：YYYY-MM-DD
>
> 风险等级：P0 观察 / P1 导航 / P2 沙箱写 / P3 稳定性（定义见 [probe-risk-and-whitelist.md](./probe-risk-and-whitelist.md)）
>
> 关联：`clients/weixin-cli/experiments/probes/<probe-id>/probe.json`

## 1. 假设

<要验证的 UI 可达性/稳定性假设，一句话；说明机器可验证的成功终态是什么>

## 2. 环境

| 项 | 值 |
|---|---|
| 微信版本 | |
| Windows 版本 / build | |
| DPI | 100% / 125% / 150% |
| 显示器 | 单屏 / 双屏（负坐标） |
| 主题 | 深色 / 浅色 |
| 窗口状态 | 最大化 / 普通 |
| 账号 | 测试账号标识（hash 或代号，不写明文） |

## 3. 允许动作与目标白名单

<引用 probe.json 中的 allowed_actions 与 target_whitelist；P2 必须逐条列出白名单目标代号>

## 4. 步骤与证据摘要

| # | 步骤 | 证据（截图 hash / UIA 摘要 / 剪贴板 format / 稳定错误码） | 耗时 |
|---|---|---|---|
| 1 | | | |

<证据只记 hash、结构摘要和错误码；不贴正文、不贴明文联系人>

## 5. 结果

- 尝试次数 / 成功次数：
- 成功终态是否机器可验证：是 / 否（说明验证方式）
- 观察到的稳定错误码：
- 残留检查（窗口 / 剪贴板 / 按键状态）：

## 6. 结论

<假设成立 / 不成立 / 部分成立；关键发现>

## 7. 后续决策

- [ ] 进入产品化（对应 tool：<名称>，门禁核对见设计 §7.3）
- [ ] 需要补充 probe（说明缺口）
- [ ] 停留在实验区（说明阻塞原因）
