# Verbose 中间消息灰度与回滚手册（Runbook）

> 状态：✅ 已交付（Phase 4）
> 制定日期：2026-08-31
> 适用范围：Agent 用户可见中间消息（verbose）功能 MVP 全量代码（Phase 0–4 已完成开发）
> 依据：`docs/plans/plan-agent-intermediate-feedback.md` §8（灰度与回滚）、`docs/system/agent-intermediate-feedback-design.md` §11（配置与灰度）、§14（验收标准）
> 读者：运维 / 值班工程师 / 负责真机验收的产品与测试同学

---

## 0. 功能与开关速览

| 项 | 值 |
|---|---|
| 代码默认 | `enabled=true`（2026-09-01 产品决策：全局默认启用；紧急回滚仅靠 `force_disabled=true`） |
| 配置位置 | `configs/config.yaml` → `agent.verbose_feedback`（settings 模型 `src/config/settings.py` 同构） |
| 生效优先级 | ① 全局 `force_disabled=true` → ② 请求级显式配置 → ③ 渠道 `config.verbose_feedback` → ④ 微信客服旧 `waiting_indicator` 兼容映射 → ⑤ 全局 `agent.verbose_feedback` → ⑥ 代码默认值 |
| kill switch | `force_disabled=true`：最高优先级，覆盖请求级、渠道级和旧 waiting indicator，是紧急回滚的唯一手段 |
| 每轮上限 | 最多 1 条 verbose；response 开始后不再发送 |
| 系统兜底 | **无**（2026-09-01 产品决策：system watchdog 已删除；verbose 仅由策略产生，无策略的慢 turn 不提示） |

**配置样例（开启）：**

```yaml
agent:
  verbose_feedback:
    force_disabled: false
    enabled: true
    max_text_chars: 60
    delivery_timeout_seconds: 5
    fallback_message: "正在处理你的请求，复杂任务可能需要一点时间，请耐心等待。"
```

> ⚠️ 热加载：若当前部署（Gunicorn sync worker / 多进程）不支持配置热加载，**任何配置变更后必须重启服务**才能生效。普通本轮配置在 owner 开始时冻结，运行期不可改写。

---

## 1. 第一阶段：部署后回归验证清单

> 2026-09-01 起全局默认 `enabled=true`，本阶段不再是"关闭态部署"；如需关闭态验证，临时在 config.yaml 显式置 `enabled=false`。目的：确认上线后新旧链路行为符合预期。逐项打勾：

| # | 验证项 | 操作 | 通过标准 |
|---|---|---|---|
| 1.1 | 服务可启动 | 重启服务 | 进程正常监听，无 import / 配置加载报错；日志无 `[VERBOSE]` ERROR |
| 1.2 | 配置加载 | `python -c "from src.config.settings import settings; print(settings.agent.verbose_feedback)"` | 输出 `enabled=True`（或与 config.yaml 一致） |
| 1.3 | Web 回归 | 任意会话发一条短问答 + 一条命中长任务策略的长问答 + 一条无策略的慢问答 | 短问答与无策略慢问答均无 verbose 帧；策略长问答出现 1 条 `type: "verbose"` 帧，final 正常，assistant metadata 含 `verboseMessages` |
| 1.4 | 渠道回归 | 企微 / 微信客服 / 钉钉 / 飞书各发一条短消息 | final 回复正常送达；短消息无多余中间消息；微信客服配额消耗与改造前一致 |
| 1.5 | 微信客服旧配置 | 对已配置 `waiting_indicator` 的租户发长任务 | 映射到新机制，单条提示，无重复发送 |
| 1.6 | 企微个人 RPA | 发一条长任务 | 先 verbose 后 final 两条独立投递；幂等键分离 |
| 1.7 | 构建产物 | 前端 `npm run build` + typecheck | 通过 |

此阶段任何一项失败：立即按 §4 回滚（`force_disabled=true`）。

---

## 2. 第二阶段：观察指标

### 2.1 开启步骤

全局默认已开启，直接部署即生效；如需按渠道/租户收敛，参考 §3 的渠道级配置。

### 2.2 观察指标（开启后至少观察 1 个工作日）

| 指标 | 采集方式 | 健康阈值 |
|---|---|---|
| time-to-first（提示发出时点） | 日志 `[VERBOSE] emitted ... time_to_first=Xs` | 分布合理（policy 均应在长工具启动前；无 system 兜底，watchdog 已删除） |
| verbose 送达 / 失败 | dispatcher outcome 日志（sent / timeout / false / exception / suppressed） | 失败率 < 5%，失败必须不影响 final |
| final 成功率 | 与开启前基线对比 | 不下降（verbose 失败绝不阻塞 final） |
| 每轮消息数 | 渠道会话审计 | 每轮最多 1 条 verbose，无重复 |
| dispatcher 超时 | `delivery_timeout_seconds`（默认 5s）命中次数 | 极少；命中时 final 正常 |
| 微信客服回复配额 | `WeComKfReplyBudget` 日志 | verbose 后 final 至少成功一条；限流只剩 1 个额度时 verbose 被抑制 |

异常处置：任何指标恶化 → 先按 §4 紧急回滚，再排查。

---

## 3. 第三阶段：Web 先灰度 → 分渠道开启

顺序固定，**每一步观察合格后再进行下一步**：

1. **Web**（部署配置单独开启）：SSE 原地更新 assistant 占位；重点观察 `verboseMessages` metadata 合并正确、按 `eventId` 去重、`delivery="streamed"` 冻结。
2. **钉钉 / 飞书 / 企微应用**：走渠道 dispatcher；重点观察 slow-adapter / false / 异常时 final 不受影响、每轮单条。
3. **微信客服**：**重点观察回复配额**——5 次 reply budget 中 verbose 只允许消耗低优先级额度，final 必须保底；观察限流抑制日志与人工转接场景。
4. **企微个人 RPA**：最后开启；确认 verbose 与 final 使用不同 delivery request id（两个可独立去重的投递），outbox 与直推路径均为 verbose → final 顺序。

渠道级开启方式：该渠道 `config.verbose_feedback`（优先级高于全局）；不支持 UI 的渠道临时用全局配置。

---

## 4. 紧急回滚（唯一手段）

```yaml
agent:
  verbose_feedback:
    force_disabled: true   # 其余字段保持不变
```

- **立即生效范围**：覆盖请求级配置、渠道配置、微信客服旧 `waiting_indicator`；全部新旧 verbose 发送立即停止。
- **在途抑制**：该开关在 **owner 创建时** 和 **dispatcher 真正发送前** 各读取一次，因此也能抑制尚未发出的在途提示。
- **必须重启**：若部署环境不支持热加载，修改后**必须重启服务**。
- **不恢复旧 watchdog**：旧微信客服 waiting indicator 的旧实现已移除（`process_with_waiting_indicator` 不可再导入），回滚后由 `force_disabled` 统一关闭，**禁止也无法**恢复成第二套 watchdog。
- 回滚后验证：重复 §1 的 1.3–1.6 项。

---

## 5. 真机验收矩阵（交付用户逐项打勾）

约定：长任务首选「生成旅游报价单」（travel-quote）或「Excel 模板填充」（fill_template）；短任务用普通问答。每项记录：通过 / 失败 + 截图。

### 5.1 Web

| # | 前置配置 | 操作步骤 | 预期结果 |
|---|---|---|---|
| W1 | enabled=true | 发起 travel-quote 长任务 | 最终文件前看到一条业务化提示（无工具名/命令/路径），随后文件正常到达 |
| W2 | enabled=true | 发起 8 秒内完成的短问答 | 无任何中间提示 |
| W3 | enabled=true | 断开网络/关闭页面后重连查看历史 | 历史消息含该条 verbose（metadata.verboseMessages），无重复 |
| W4 | force_disabled=true | 重复 W1 | 无中间提示，其余行为不变 |

### 5.2 企微（应用渠道）

| # | 前置配置 | 操作步骤 | 预期结果 |
|---|---|---|---|
| Q1 | 渠道开启 | 长任务 | 先收 1 条中间提示，后收 final；无其他中间消息 |
| Q2 | 渠道开启 | 长任务期间追加一条新消息 | 行为与改造前一致：按 cancel/merge 重算，最终回复不丢，verbose 累计仍 ≤1 条 |
| Q3 | 渠道开启 | 短问答 | 无中间提示 |

### 5.3 微信客服

| # | 前置配置 | 操作步骤 | 预期结果 |
|---|---|---|---|
| K1 | 渠道开启 | 长任务 | 先 1 条提示、后 final；每次咨询最多多消耗 1 个回复配额，final 必达 |
| K2 | 渠道开启 | 长内容 final（资产多） | 限流只剩 1 个额度时 verbose 被抑制、final 正常发送 |
| K3 | 已配置旧 waiting_indicator 的租户 | 长任务 | 旧配置继续生效（映射新机制），仅 1 条提示不重复 |
| K4 | force_disabled=true | 重复 K1 | 全部静默（旧配置也被覆盖） |

### 5.4 钉钉 / 飞书

| # | 前置配置 | 操作步骤 | 预期结果 |
|---|---|---|---|
| D1/F1 | 渠道开启 | 长任务 | verbose → final 顺序正确，各恰好 1 条 |
| D2/F2 | 渠道开启 | final 中附带文件 | 文件交付正常，verbose 不占文件位 |

### 5.5 企微个人 RPA

| # | 前置配置 | 操作步骤 | 预期结果 |
|---|---|---|---|
| R1 | 渠道开启 | 长任务 | 收到 2 条独立消息：先 verbose、后 final；无一条被幂等吞掉 |
| R2 | 渠道开启 | 投递重试 | verbose/final 各自幂等，重试不产生重复 |

### 5.6 通用 P0（任意渠道）

| # | 场景 | 预期结果 |
|---|---|---|
| P0-1 | 提示后追加消息 | final 不消失；行为与改造前一致 |
| P0-2 | 转人工 / 取消 | 无残留提示；无遗留后台任务 |
| P0-3 | 敏感文案注入（若配置了违规 fallback/模板） | 展示 fallback 降级文案（source 仍为 policy）而非原文 |

---

## 6. 验收完成后的动作

1. 将 `docs/ideas.md` #64 移至 `docs/ideas_finished.md`，标记 ✅ 已完成开发。
2. 在 `docs/plans/plan-agent-intermediate-feedback.md` §11 补记真机矩阵结果与灰度数据。
