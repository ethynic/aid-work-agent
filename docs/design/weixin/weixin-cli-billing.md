# weixin-cli Kimi 视觉调用与服务端计费关联设计

> 状态：🔧 已实现（kimi-k3 单价 2026-08-27 由用户确认：输入 20 元/M、输出 100 元/M，已落 `deploy/db_update.sql`，待部署执行）
>
> 日期：2026-08-27（设计）；2026-08-27（实施）
>
> 上位设计：[weixin-cli 设计](weixin-cli-design.md)
>
> 参考实现：[协会客户端设计 §2.2 / §3.3](../../tools/association-client-design.md)

## 1. 背景与问题

weixin-cli 的 PowerShell 驱动（`clients/weixin-cli/drivers/ps1/_common.ps1`）直接在本机调用
Kimi（Moonshot）视觉 API（`kimi-k3`，POST `https://api.moonshot.cn/v1/chat/completions`，
base64 截图）做 UI 元素定位。现状两个问题：

1. **密钥治理**：Kimi key 曾作为缺省值内嵌在产品代码中（已删除，见 §6），且已泄漏进 git
   历史（commit `970b3961`、`experiments/probes/` 下脚本），必须轮换。只要 CLI 继续直连
   Moonshot，key 就必须分发到每台用户机器，泄漏面无法收敛。
2. **计费断裂**：本机直调的用量完全不经过服务端计费体系，租户积分（`tenants.credit_balance`）
   不扣减，与协会客户端「LLM 走服务端代理集中计费」的既有模式不一致。

## 2. 现有计费/扣点算法现状（调研结论）

### 2.1 扣点算法与扣减点

| 链路 | 扣减位置 | 算法 |
|------|---------|------|
| 服务端 Web 会话 | `src/services/session_record.py`（会话记录落库时按轮计算，写 `chat_records.usage_breakdown`） | `src/services/billing.py::calculate_credit_cost_with_breakdown`：按 token × 单价（元/百万 token），区分 input/output/cached（命中缓存按 `cached_input_price_per_m`，显式缓存创建按输入价 125%），ceil 2 位小数 |
| 客户端 CLI（协会客户端） | `src/api/client_routes.py:199` `POST /api/client/v1/llm/chat` 调用后同步 `ClientUsageLogDB.record_llm_usage`（`src/db/client_binding_db.py:280`） | 标准积分 = 同一个 `calculate_credit_cost`；实扣 = `ceil(raw × credit_multiplier × 100) / 100`，`credit_multiplier` 默认 **10.0**（`src/config/settings.py` `settings.client.credit_multiplier`，历史沿革 5→10→25→10，commit `ff8ea7c7`），同事务 `UPDATE tenants.credit_balance` 原子扣减；余额 ≤0 时 402 阻断 |
| 非 LLM 调用 | `record_non_llm_usage`（OCR/日志等） | credit_cost=0，只记账 |

- 价目表：PostgreSQL `token_cost_prices`（全平台统一价，无 tenant_id），列含
  `input_price_per_m / output_price_per_m / cached_input_price_per_m / tiered_pricing(JSONB 分档)
  / price_per_second / embedding_price_per_m / asr_price_per_call / is_multimodal`；
  种子在 `deploy/init-postgres.sql`。**`is_multimodal`（2026-08-28 新增）**：TRUE 表示模型原生
  支持图片输入（目前为 kimi-k3、GLM-5.3-Flash、qwen-vl-max、qwen-vl-plus、qwen3-vl-flash），供后续图片路由——多模态模型收到用户上传
  图片可直接进 content 数组原生理解，纯文本模型维持先 OCR 识别文字。
  单价缺失时 `calculate_credit_cost` 返回 0（记 warning，不阻断）。
- failover 计价：计费模型优先取响应自带 `model` 字段（qwen `parse_response` 带），deepseek
  回退主 provider 名（commit `1566caf5` 修复 failover 切备用 provider 记错单价的问题）。
- `src/llm/llm_call_logger.py` 只是调用日志，不做扣费。

### 2.2 Kimi/Moonshot provider 现状

- `src/llm/gateway.py` 只注册 `qwen / zhipu / deepseek` 三个 provider，**无 Moonshot provider**。
- `QwenProvider` 是 OpenAI 兼容模式（`https://dashscope.aliyuncs.com/compatible-mode/v1`），
  多模态消息（content 为数组）在 `BaseLLMProvider._format_messages` 原样透传
  （`src/llm/providers/base.py:152`），视觉请求格式上可承载；百炼平台也承载 kimi 等第三方模型
  （代码注释提及），但 `kimi-k3` 是 Moonshot 官方 API 模型，是否在百炼可得、价格多少**未验证**。
- 现有客户端代理端点 `POST /api/client/v1/llm/chat` 的 `LlmChatRequest` **无 model 字段**，
  固定走 `llm_gateway` 默认模型，目前不支持指定视觉模型。

### 2.3 客户端接入/鉴权现状

协会客户端已有完整可复用模式：

- 一次性激活码（`client_activation_codes`，bcrypt hash）→ `POST /api/client/v1/activate`
  换长期 `access_token`（`client_bindings`，Bearer 鉴权）；
- LLM 调用走 `ProxyLLMGateway`（`clients/association-client-cli/runtime/proxy_gateway.py`）→
  `POST /api/client/v1/llm/chat`，402=余额不足停止任务，响应带 `billing.{raw_credit_cost,
  credit_cost, balance_after}`；
- `GET /api/client/v1/credits` 查余额，`POST /api/client/v1/logs` 遥测上报。

weixin-cli 目前与服务端**无任何通信**，无激活、无 token、无上报。

## 3. 候选方案

### 方案 A：走服务端 LLM 网关代理（推荐）

weixin-cli 不再持有 Kimi key。激活后拿 `access_token`，`Invoke-KimiVision` 改为 POST
服务端代理端点，由服务端调 Moonshot 并按现有算法扣点。

- 一致性：✅ 复用 `record_llm_usage` ×10 系数、同事务扣余额、402 阻断、`token_cost_prices`
  价目表，与协会客户端完全同一条计费路径；
- 安全性：✅ Kimi key 只存在于服务端 `.env`，CLI 本机只有租户级 access_token（可吊销）；
  base64 截图经 HTTPS 到自有服务端再出网，链路可控；
- 成本：服务端需新增 Moonshot provider + 价目行 + 端点 model 白名单（见 §4），CLI 侧
  `Invoke-KimiVision` 改 HTTP 目标（改动集中在一个函数）。

### 方案 B：本机直调 + 用量事件上报（不推荐）

CLI 继续直连 Moonshot，调用后把 usage 上报服务端扣点。

- 一致性：❌ 扣点依赖客户端如实上报，可伪造/丢失；与服务端「调用即扣」语义不同；
- 安全性：❌ Kimi key 仍需下发到每台用户机器，泄漏面不收敛（本设计的初衷就是消除这一点）；
- 成本：上报端点虽小，但要补对账/防丢失机制，长期成本反而高。

### 方案 C：切换模型到百炼 kimi/qwen-vl（备选）

不新增 Moonshot provider，改用百炼承载的视觉模型（如 qwen3-vl-flash，价目表已有）。
风险：`kimi-k3` 定位准确率是 probe 真机验证过的（p1/p2 报告 4/4 成功），换模型需重跑
P3 稳定性矩阵，属于能力面变更，不应和计费改造捆绑。仅当 Moonshot 商务/合规不可行时考虑。

## 4. 方案 A 详细设计

### 4.1 服务端改动

1. **Moonshot provider**（`src/llm/providers/moonshot.py`）：OpenAI 兼容，可参照
   `QwenProvider` 实现（或子类化，覆盖 `DEFAULT_BASE_URL=https://api.moonshot.cn/v1`，
   不写 `enable_thinking`/缓存参数）；`gateway.py` 的 `PROVIDERS` 注册 `moonshot`，
   `_build_key_pool`/`_build_provider` 增加分支；`settings.llm.moonshot`
   （keys/model/base_url）+ `.env.example` 补 `MOONSHOT_API_KEYS`。
   注意 `kimi-k3` 为推理模型，`temperature` 必须为 1 或省略——provider 内对 kimi 系模型
   忽略调用方 temperature。
2. **客户端代理端点支持视觉模型**：`LlmChatRequest` 增加 `model: Optional[str]`，
   服务端做**白名单校验**（仅允许 `token_cost_prices` 中已定价且标记为客户端可用的视觉
   模型，首期即 `kimi-k3`）；命中时用 `LLMGateway(provider_name="moonshot",
   model_codes={"moonshot": req.model})` 单独实例调用，计费沿用响应自带 model 查价目表。
   多模态 content 数组原样透传即可（`base.py:152` 已支持）。
3. **价目表**：`deploy/db_update.sql` 增加 `kimi-k3` 的
   `INSERT ... ON CONFLICT` 行（单价以 Moonshot 官方当期价格为准，开发时填入）。
4. **429 语义**：Moonshot 429 目前由 CLI 重试（25s×4）；走代理后由服务端 KeyPool
   并发控制 + failover 处理，CLI 侧保留对 5xx 的有限重试即可。

### 4.2 weixin-cli 改动

1. 新增激活/配置：`AID_WEIXIN_SERVER_URL` + 激活流程复用 `/api/client/v1/activate`
   （参考 `clients/association-client-cli`），`access_token` 存本机（DPAPI 加密，复用
   probe 已验证的 DPAPI 能力）；删除 `AID_WEIXIN_KIMI_API_KEY`。
2. `Invoke-KimiVision` 改 POST `{server}/api/client/v1/llm/chat`（model=kimi-k3，
   purpose=weixin-vision-<artifact>），402 → `Throw-DriverError 'NO_CREDIT'`，
   401 → 引导重新激活。
3. 未配置服务端时的降级：允许显式设置 `AID_WEIXIN_KIMI_API_KEY` 走本机直调
   （仅开发/调试用途，文档标注不计费、不入账），生产分发版关闭该开关。

### 4.3 验收

- 服务端：`tests/unit/api/test_client_routes.py` 增加 model 白名单/moonshot 计费断言；
  扣费金额 = 响应 usage × `kimi-k3` 单价 × 10，ceil 2 位。
- CLI：`npm test` 全绿；真机冒烟（搜索→发送）走代理成功且 `client_usage_logs` 有
  `stage=weixin-vision-*` 记录、租户余额正确减少。

## 5. 实施状态（2026-08-27 实施完成）

已实现：

- 服务端：
  - `src/llm/providers/moonshot.py`：MoonshotProvider（复用 QwenProvider 的 OpenAI 兼容
    链路；kimi 系模型请求体省略 temperature；content 为空回退 reasoning_content——
    兜底开关在 `qwen.py::_parse_response`，仅 `REASONING_CONTENT_FALLBACK = True`
    的子类（MoonshotProvider）生效，QwenProvider 存量行为不变）；
  - `src/llm/gateway.py`：注册 `moonshot`（PROVIDERS / `_build_key_pool` / `_build_provider`
    / `get_model_name`），`LLMGateway` 新增 `use_failover=False` 选项——模型绑定型调用
    禁止跨 provider 降级（降级到文本模型既无法完成视觉任务又会按错误模型计价）；
  - `src/llm/failover.py`：`_get_provider_cfg` 补 moonshot 映射；
  - `src/config/settings.py` + `configs/config.yaml`：`llm.moonshot`（MOONSHOT_API_KEYS /
    MOONSHOT_MODEL_CODE / MOONSHOT_BASE_URL，`.env.example` 已有登记段）；
  - `src/api/client_routes.py`：`LlmChatRequest.model` + `CLIENT_MODEL_PROVIDER_MAP`
    白名单（首期 `kimi-k3 → moonshot`）+ 定价 fail closed（token_cost_prices 无非零
    单价行 → 400 MODEL_NOT_PRICED）+ 按 model 缓存的专用网关；
  - `deploy/db_update.sql`：kimi-k3 价目行已写入但**整段注释**——官方人民币定价未公布，
    待确认后取消注释填入真实单价（未定价期间端点对 kimi-k3 一律 MODEL_NOT_PRICED 拒绝，
    不产生免单）。
- CLI（clients/weixin-cli）：
  - `src/security/dpapi.ts`：DPAPI（CurrentUser）存取，复用仓库既有 ProtectedData idiom；
  - `src/platform/serverProxy.ts`：`AID_WEIXIN_SERVER_URL` + `AID_WEIXIN_ACTIVATION_CODE`
    环境变量驱动，首次调用惰性激活，token DPAPI 加密落 `%APPDATA%\aid-weixin\binding.json`；
  - `src/platform/powershell.ts`：驱动子进程统一注入代理 env（token 只走 env 不进命令行），
    驱动错误码白名单补 `CONFIG_MISSING` / `INSUFFICIENT_CREDIT`；
  - `src/operations/types.ts`：ErrorCode 新增 `CONFIG_MISSING` / `INSUFFICIENT_CREDIT`
    （402 归并 BLOCKED 被否：BLOCKED 语义是风控，余额不足的用户动作是充值，需独立码）；
  - `drivers/ps1/_common.ps1`：`Invoke-KimiVision` 代理优先——402 → INSUFFICIENT_CREDIT、
    401 → CONFIG_MISSING（提示重新激活）；显式 `AID_WEIXIN_KIMI_API_KEY` 走本机直调降级
    （仅开发调试，不计费）。
- 测试：服务端 `tests/unit/llm/test_moonshot_provider.py`（5 项）+
  `tests/unit/api/test_client_routes.py` 新增白名单/计费路由（5 项），共 38 项全绿；
  CLI `tests/server-proxy.test.ts`（6 项）+ powershell-driver 白名单断言，npm test 97 项全绿、
  typecheck 0 错误。

未做 / 待办：

- kimi-k3 官方人民币单价确认后取消 `deploy/db_update.sql` 注释段并执行（当前代理端点
  对 kimi-k3 fail closed，功能在单价落库前不可用——这是有意的防免单设计）；
- 服务端 + 真实 Moonshot key 端到端联调未做（本机无运行中的服务端，单测覆盖代替）；
- 真机回归（搜索→发送走代理）待单价落库后验证。

## 6. 安全事项（必须人工跟进）

- `sk-Eqn3...VsTc`（git 历史 commit `970b3961` 及 `experiments/probes/` 脚本中）视为已泄漏，
  **需到 Moonshot 控制台轮换/吊销**。代码侧已清理产品代码内的缺省值，但 git 历史不可改，
  轮换是唯一收敛手段。
