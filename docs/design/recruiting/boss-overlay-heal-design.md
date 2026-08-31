# BOSS 弹层自愈设计（overlay heal，2026-08-31）

> 状态：✅ 已实现（三端：boss-cli 0.2.6 原语 + 云端编排 + 计费）
> 问题：BOSS 页面上工具操作偶发触发不可预见的弹层（广告/功能引导/活动弹窗），弹层盖在
> 最上层导致后续操作必然失败。何时触发、弹层元素是什么均不可预知——只知道「下一个动作报错了」。

## 1. 方案总览

```
本地工具失败（UI_CHANGED / BUSY）
  → 云端下发 boss_overlay_inspect（只读：导出主文档文本节点清单 text/坐标/class）
  → 云端选关闭控件：
      ① 启发式：白名单文本 + 弹层类名特征（dialog/mask/modal/pop/layer/guide/banner/toast）
        同时命中 → 直选（零 LLM 费用）
      ② 未命中 → chat_lite 轻量模型结构化选择（usage 记 record_background_llm_usage，
        source=boss_overlay_heal）
  → 云端下发 boss_overlay_dismiss（按文本 Win32 点击 + 快照校验弹层消失）
  → 重试原操作一次（失败/成功如实返回）
```

## 2. 安全铁律（三层防误点）

1. **白名单**（工程持有，两侧同步：CLI `DISMISS_TEXT_WHITELIST` = 云端 `DISMISS_WHITELIST`）：
   关闭/关闭弹窗/关闭广告/知道了/我知道了/我知道啦/以后再说/下次再说/稍后再说/暂不/暂不需要/
   暂不使用/不再提醒/不再提示/残忍拒绝/取消/跳过/忽略/×/✕/✖/X/No thanks/Close
2. **LLM 约束**：只能输出白名单内且确实存在于候选清单的文本——非白名单（如「立即领取」）
   或编造文本一律拒绝（防幻觉）；icon 引用 `icon:<cls>` 必须命中关闭语义正则（close/guanbi）
   且在候选清单内
3. **CLI 端再校验**：dismiss 收到非白名单文本直接 INVALID_ARGUMENT 拒绝执行；
   点击后快照校验弹层消失（文本/控件计数减少），未消失报 UI_CHANGED

### 2.1 真机实证修正（2026-08-31，本轮真机测试发现）

- **无文字图标关闭 × 是主流形态**：真机广告弹窗（BOSS直聘 Windows 客户端推广）的关闭按钮
  无文字，class 是唯一信号（`boss-popup__close`/`icon-close`/`ad-banner-close` 等 5 个 close-like
  控件）。纯文本候选完全抓不到 → inspect 增导 `icon_candidates`（class 命中 close/guanbi 正则），
  dismiss 支持 `icon:<cls>` 引用（class 本身含关闭语义，安全等价文本白名单）
- **点击通道：CDP 合成点击有效、Win32 真实点击无效**（对照实验：同一坐标 Win32 点完弹窗不动，
  CDP clickBrowse 一击关闭）。弹层关闭属页面 UI 操作，与「写动作第一优先 Win32」不冲突——
  dismiss 采用 CDP 主通道 + 未生效 Win32 兜底一次的双通道策略
- **广告轮播位**：限时优惠广告条关闭后同 slot 立即轮换新广告（复用同一 close class），
  「计数减少」校验会保守判未关闭——自愈编排放弃并如实上报；重复调用可关掉轮换后的新广告

## 3. 触发与边界

| 项 | 决策 |
|---|---|
| 触发码 | 仅 `UI_CHANGED` / `BUSY`（弹层遮挡的典型症状） |
| 绝不触发 | `EXECUTION_UNKNOWN`（写后结果不明，重试可能重复副作用）/ WRONG_PAGE / PAYWALL / CHROME_UNAVAILABLE / NOT_LOGGED_IN / CANCELLED |
| 自愈轮数 | 每次原调用最多 1 轮（inspect→dismiss→retry）；overlay 原语自身 `heal_eligible=False` 防递归 |
| 失败语义 | 自愈任何环节失败都返回原错误（`data.heal` 附过程说明），绝不掩盖原始错误 |

## 4. 计费

- 自愈原语 inspect/dismiss 零单价；**仅在自愈真正救回操作（重试成功）时收专项费**
  `boss_overlay_heal`，价格 `settings.boss_tool_billing.overlay_heal_price`（默认 2 积分，
  用了 LLM 故高于单次工具调用），记 client_usage_logs（stage=boss_tool）
- 重试成功的原工具按正常单价计费一次（首次失败未扣费，天然不双扣）
- 自愈的 LLM token 经 `record_background_llm_usage` 计入 chat_records（与工具费分开计量）

## 5. 落点索引

| 层 | 文件 |
|---|---|
| CLI 提取 | `clients/boss-resume-assistant/src/main/boss/OverlayInspector.ts` |
| CLI 关闭 | `clients/boss-resume-assistant/src/main/boss/OverlayDismissExecutor.ts` |
| CLI 原语 operation | `src/main/operations/bossOverlayInspect.ts` / `bossOverlayDismiss.ts`（MCP tool 18 个） |
| 云端选择 | `src/services/overlay_heal_service.py`（启发式 + chat_lite + 记账） |
| 云端编排 | `src/local_tools/proxy_tool.py`（`_dispatch_and_wait` + `_heal_overlay`） |
| 配置 | `settings.boss_tool_billing.overlay_heal_enabled / overlay_heal_price`（config.yaml 可覆盖） |
| 注册 | runtime TRUSTED_MANIFEST 18 / manifest.py·catalog.py·LOCAL_PROXY_TOOL_CLASSES 20（不进 SUBAGENT 白名单，agent 不可见） |
| 测试 | CLI `tests/overlay-heal.test.ts`；云端 `tests/unit/local_tools/test_overlay_heal.py` |

## 6. 已知边界（后续可演进）

- 只读主文档 doc[0]（read-chat 真机实证弹层在主文档）；iframe 内弹层不在候选（出现时再扩展）
- 广告轮播位（同 slot 关旧广告换新广告）校验保守判未关闭，重复调用可关掉轮换后的新广告（真机已验证）
- 未做视觉模型兜底（class + DOM 文本已足够；截图+视觉留待 DOM 路线失效时评估）
