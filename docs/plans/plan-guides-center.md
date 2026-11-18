# 教程文档集中管理 + Portal 文档中心 — 开发计划

> 设计文档：[docs/design/guides-center/guides-center-design.md](../design/guides-center/guides-center-design.md)
> 分支：`feat/guides-center`

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 文档迁移（29 篇 → docs/guides/ 四分类）+ manifest/README + 全仓引用更新 | ✅ 完成（2026-11-18） | git mv 保留历史；改写脚本双通道（指向迁移文档/迁移文档外链）；断链审计通过；test_manual_sql_safety 运行时路径同步 |
| Phase 2 | 后端 API（catalog/doc + 平台管理员鉴权 + 路径白名单）+ 集成测试 + .dockerignore 白名单 | ✅ 完成（2026-11-18） | tests/unit/test_guides_api.py 17 用例通过（宿主机 python；当前仓库无可用容器） |
| Phase 3 | 前端 GuidesCenter 页面 + 路由/菜单 + 组件测试 + 构建 | ✅ 完成（2026-11-18） | 组件测试 5 用例、routes.test.ts 快照同步（补齐此前漏更的 3 条路由）；`npm run build`（vue-tsc+边界+verify-web-build）通过 |
| Phase 4 | 文档登记 + 独立验证智能体（测试+审查）+ 提交推送 | 🔧 进行中 | 见设计文档 §6 验证缺口说明 |
