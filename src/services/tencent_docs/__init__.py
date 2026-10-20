"""腾讯文档 OpenAPI 适配（Phase 1，docs/plans/plan-tencent-docs-knowledge-import.md）

模块划分：
- client: OpenAPI HTTP 客户端（三元组鉴权、V2/V3 两种响应错误形态、频控错误映射）
- converter: doc v3 属性树 -> Markdown 纯函数转换器
- exporter: async-export 导出兜底（DOCX/PDF）+ 每 openID 每日配额闸门

Phase 1 仅实现凭据直连（个人 Token / 后续 OAuth token 均以
TencentDocsCredentials 注入，调用方无感）；OAuth 授权流（任务 #2）
与多租户 token 存储后续补齐。
"""
