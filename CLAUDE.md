# CLAUDE.md

本文档为 Claude Code (claude.ai/code) 在本项目中工作时提供指导。

## 项目概览

AID Work Agent 是企业员工智能代理系统，通过对话式 AI 处理企业员工的日常任务。系统使用国内大模型提供商（Qwen/DashScope 和 ZhipuAI），支持多渠道接入（企业微信、钉钉、飞书）。系统支持多租户，可承载数百并发用户。

**设计原则**：稳定性和可预测性优先于创造力。专业、简洁的回复。不确定时诚实承认。不幽默、不娱乐、不学术猜测。敏感信息必须加密，绝不以明文形式返回给用户。

## 命令

### 后端
```bash
pip install -r requirements.txt          # 安装依赖
python -m src.main                       # 启动 FastAPI 服务器（端口 8000）
CLI_MODE=true python -m src.main         # CLI 聊天模式（不启动 Web 服务器）
python gradio_app.py                     # Gradio 调试 UI（端口 7860）
gunicorn -c deploy/gunictern.conf.py src.main:app  # 生产环境服务器
```

### Docker
```bash
docker compose -f docker-compose.local.yml up -d        # 开发环境
docker compose -f docker-compose.prod.yml up -d --build  # 生产环境
```

## 开发规范

详细规范见 [dev_rules/](dev_rules/) 目录：

| 文档 | 内容 |
|------|------|
| [dev_rules/backend_dev.md](dev_rules/backend_dev.md) | 后端开发规范，日志、错误处理、异步/Gunicorn、API 命名规范 |
| [dev_rules/frontend_dev.md](dev_rules/frontend_dev.md) | 前端开发规范 |
| [dev_rules/testing.md](dev_rules/testing.md) | 测试目录结构、分层规则、Fixtures、运行命令 |
| [dev_rules/architecture.md](dev_rules/architecture.md) | 系统架构、核心组件、扩展点 |
| [dev_rules/database_dev.md](dev_rules/database_dev.md) | 数据库表开发规范，包括表分类、租户隔离要求、变更记录 |

## Git 提交规范
**不要自动提交代码，仅当用户明确说“提交代码”才提交**
1. 提交前执行 `git fetch` 拉取远程最新代码
2. 检查是否有冲突：`git status` 或 `git diff origin/master`
3. 如有冲突先解决冲突再提交
4. 提交后立即 `git push` 推送到远程

## 语言说明

本项目使用多种语言：README、注释和提示词主要为中文（简体）。代码标识符为英文。UI 文本为中文。