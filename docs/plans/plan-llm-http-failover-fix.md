# LLM HTTP 故障转移修复计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 修订原设计、登记异常契约和回归范围 | ✅ 完成（2026-10-15） | 已定位 HTTP 503 状态码在 Provider 包装时丢失 |
| Phase 2 | Provider 状态码透传、流式错误读取和回归测试 | ✅ 完成（2026-10-15） | 93 个新用例覆盖三类 Provider 及 Qwen 子类；定向自测 176 passed |
| Phase 3 | 独立测试、CodeReview、最终检查 | ✅ 完成（2026-10-15） | 独立测试 209 passed；CR 无遗留 P0/P1/P2；import/diff 检查通过，未部署 |

## 设计与范围

沿用并修订 [LLM Failover 设计 §2.7](../infrastructure/llm-failover-design.md#27-错误分类)。2026-10-15 生产日志显示：DeepSeek 返回 HTTP 503，正文为 `service_unavailable_error`；Provider 将其转为仅含正文的 `RuntimeError`，错误分类无法识别，直接终止且不计入熔断。

本次属于核心跨模块异常契约修复，按高风险流程执行：主控开发和自测，再由独立测试与独立 CodeReview 验证。

- 引入兼容 `RuntimeError` 的 `ProviderHTTPError`，保存 `status_code`；三类 Provider 的普通/流式 HTTP 错误均使用该异常。Qwen 子类继承同一修复。
- 结构化 HTTP 状态优先：5xx、429 可切换，其他 HTTP 状态不按响应文本猜测；保留旧式无结构 `RuntimeError` 的现有兼容规则。
- 流式非成功响应在连接上下文关闭前读取错误正文，防止 `ResponseNotRead` 遮蔽原始 HTTP 错误；正文读取断线、超时或协议错误仍保留已知 HTTP 状态。
- 流式输出首个 chunk 后失败直接向上传播，禁止再切换并拼接另一模型的回复；与原设计 §2.6 保持一致。
- 不调整模型配置、备用链、KeyPool、费用或 token 参数；不改变公开调用签名，不操作任何服务器。

## 验收标准

1. 经真实 Provider 请求实现、使用 HTTP MockTransport 返回不含状态码的 503 JSON，普通调用和流式建连均切换备用 Provider，并增加主 Provider 失败计数。
2. 429 / 其他 5xx 按状态切换；400 等普通 HTTP 错误即使正文含 `503`、`timeout` 也不误切换。
3. DeepSeek、Qwen、GLM 均保留状态码及既有 RuntimeError 捕获兼容性；流式未预读正文可正常诊断。
4. 首个 chunk 后失败不调用备用 Provider，已输出内容不重复或拼接。
5. 运行相关 LLM 单测和新模块 import 检查；独立测试与 CR 无未处理阻断项。

## 验证记录

- 环境：WSL 的本地 `aid-agent-api` 容器，`/mnt/c/repos/aid-work-agent` 挂载到 `/app`；Python `/opt/venv/bin/python`、httpx 0.28.1、pytest 9.1.1。HTTP 使用 MockTransport，不调用真实模型。
- 修复前：新增回归用例 53 failed、4 passed，复现状态码丢失、流式未读正文以及部分回复后错误切换。
- 最终开发自测：`./scripts/dev_test.sh tests/unit/llm/test_provider_http_failover.py tests/unit/test_llm_failover.py tests/unit/llm/test_deepseek_provider.py tests/unit/test_qwen_provider_thinking_cache.py tests/unit/llm/test_moonshot_provider.py -p no:cacheprovider -q --tb=short` → **176 passed，0 failed，0 skipped**（退出码 0）。
- 独立 CodeReview：首轮发现 1 个 P2（已收到错误响应头后，读取正文失败仍可能丢失状态）；新增 36 个回归用例在修复前全部失败。最终统一正文读取边界后，审查者重跑原复现得到 `ProviderHTTPError / status_code=503 / retryable=True`，确认 P2 关闭，无新增 P0/P1/P2。
- 最终 import 检查：本地容器导入 `ProviderHTTPError`、`read_error_body`、FailoverGateway、LLMGateway 及四类 Provider → `LLM imports OK`（退出码 0）；相关 `git diff --check` 通过。
- 最终独立测试：`./scripts/dev_test.sh tests/unit/llm/test_provider_http_failover.py tests/unit/test_llm_failover.py tests/unit/test_key_pool_cross_loop.py tests/unit/test_llm_gateway_max_tokens.py tests/unit/llm/test_llm_gateway_lite_model.py -p no:cacheprovider -q --tb=short` → **169 passed，0 failed，0 skipped**。另在容器 `/tmp` 执行独立临时 pytest → **40 passed，0 failed，0 skipped**：覆盖四类 Provider 的 503/429/400 正文读取中断、响应关闭、KeyPool 释放、连续失败熔断及后续跳过主槽，另验证成功流不被预读；临时文件已清理。本角色最终共 209 passed，不与开发自测重复相加。
- 未执行真实模型 API 或服务器验证；2026-10-15 用户已授权提交本修复及相关文档，未部署。
