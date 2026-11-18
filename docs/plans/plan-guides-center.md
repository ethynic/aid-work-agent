# 教程文档集中管理 + Portal 文档中心 — 开发计划

> 设计文档：[docs/design/guides-center/guides-center-design.md](../design/guides-center/guides-center-design.md)
> 分支：`feat/guides-center`

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 文档迁移（29 篇 → docs/guides/ 四分类）+ manifest/README + 全仓引用更新 | ✅ 完成（2026-11-18） | git mv 保留历史；改写脚本双通道（指向迁移文档/迁移文档外链）；断链审计通过；test_manual_sql_safety 运行时路径同步 |
| Phase 2 | 后端 API（catalog/doc + 平台管理员鉴权 + 路径白名单）+ 集成测试 + .dockerignore 白名单 | ✅ 完成（2026-11-18） | tests/unit/test_guides_api.py 17 用例通过（宿主机 python；当前仓库无可用容器） |
| Phase 3 | 前端 GuidesCenter 页面 + 路由/菜单 + 组件测试 + 构建 | ✅ 完成（2026-11-18） | 组件测试 5 用例、routes.test.ts 快照同步（补齐此前漏更的 3 条路由）；`npm run build`（vue-tsc+边界+verify-web-build）通过 |
| Phase 4 | 文档登记 + 独立验证智能体（测试+审查）+ 提交推送 | ✅ 完成（2026-11-18） | 独立验证：测试回归全过（后端 19/前端 6/routes 快照/build/断链审计无新增）、迁移完整性确认；审查修复——提交误带入的 local-dev-environments 用户在途重写已回退为 HEAD 内容+路径更新，空字节 500→404、`.md#锚点` 链接拦截、失败后 TOC 残留、加载竞态、3 处旧路径残留（verify-plugin-runtime.mjs 等）已修；wecom-proxy 前端配置教程归入 channels/。遗留：portal 登录真实页面冒烟待联调环境（设计 §6） |
