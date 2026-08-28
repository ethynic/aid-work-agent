# aid-weixin（weixin-cli）

第一方微信操作 CLI / MCP Provider。设计文档见 [docs/design/weixin/weixin-cli-design.md](../../docs/design/weixin/weixin-cli-design.md)。

## 配置

### 服务端代理模式（默认，计费）

UI 元素定位依赖 Kimi 视觉模型（`kimi-k3`）。默认走服务端 LLM 网关代理集中计费
（设计见 [weixin-cli-billing.md](../../docs/design/weixin/weixin-cli-billing.md)），
本机**不持有 Kimi key**，只需：

```powershell
$env:AID_WEIXIN_SERVER_URL = "https://<服务端地址>"
$env:AID_WEIXIN_ACTIVATION_CODE = "AC-XXXX..."   # 一次性激活码（租户管理员在 portal 生成）
```

首次需要 Kimi 调用时自动激活（`POST /api/client/v1/activate`）换取 access_token，
token 经 DPAPI（CurrentUser）加密存于 `%APPDATA%\aid-weixin\binding.json`，之后复用。
余额不足时操作以 `INSUFFICIENT_CREDIT` 失败；凭据失效/缺失以 `CONFIG_MISSING` 失败
（删除 binding.json 后重设激活码即可重新激活）。

### 本机直调降级（仅开发调试，不计费）

```powershell
$env:AID_WEIXIN_KIMI_API_KEY = "sk-..."   # 显式设置时优先于代理模式
```

> 安全提示：仓库 git 历史（commit `970b3961` 及 `experiments/probes/` 下脚本）曾内嵌过真实 key，该 key 视为已泄漏，必须轮换后再使用。

## 常用命令

```bash
npm run build      # 编译 TS → dist/
npm test           # 构建 + 契约/单元测试
npm run cli        # 构建后启动 human CLI
```
