# AID Work Agent

企业员工智能代理系统 - V1.0 MVP版本

## 简介

AID Work Agent 是一个专用于处理企业员工日常工作的智能代理系统，能够理解用户需求、规划实现路径、调用系统工具和技能来完成任务。

## 核心功能

### V1.0 MVP版本

- 基础对话能力（基于国产大模型）
- 企业微信单渠道接入
- 核心工具集：邮件收发、OCR识别、文档摘要、信息检索
- 基础权限控制
- Skill文件解析和加载
- 短期记忆（会话上下文管理）
- 母体智能体基础规划能力

## 项目结构

```
aid-work-agent/
├── src/
│   ├── main.py                    # 应用入口
│   ├── config/                    # 配置管理
│   ├── core/                      # 核心引擎
│   │   ├── agent.py               # 母体智能体
│   │   ├── dialog_manager.py      # 对话管理器
│   │   ├── intent_engine.py       # 意图理解引擎
│   │   ├── planner.py             # 规划调度引擎
│   │   └── executor.py            # 工具执行引擎
│   ├── llm/                       # LLM网关层
│   ├── channels/                  # 渠道适配器
│   ├── tools/                     # 工具集
│   ├── memory/                    # 记忆系统
│   └── models/                    # 数据模型
├── configs/
│   └── config.yaml                # 配置文件
├── skills/                        # Skill文件目录
├── plans/                         # 设计文档
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填写必要的配置
```

### 3. 启动服务

```bash
# 启动Web服务
python -m src.main

# 或使用命令行聊天模式
CLI_MODE=true python -m src.main
```

### 4. 访问API

- 健康检查: `GET /health`
- Web聊天: `POST /api/chat`
- 工具列表: `GET /api/tools`
- 企业微信回调: `POST /wecom/callback`

## 配置说明

### LLM配置

支持以下国产大模型：

| 模型 | 提供商 | 配置项 |
|------|--------|--------|
| 通义千问 | 阿里云 | `LLM_PROVIDER=qwen` + `API_KEYS` |
| 智谱GLM | 智谱AI | `LLM_PROVIDER=zhipu` + `API_KEYS` |

### 企业微信 / 钉钉 / 飞书渠道配置

渠道凭证（CorpID、AgentID、Secret、AppKey、AppSecret 等）**不再通过环境变量配置**。
请在管理后台「渠道配置」页面为对应租户录入，凭证会加密存储在 `tenant_channel_configs` 表中。

## API使用示例

### Web聊天接口

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好",
    "user_id": "test_user"
  }'
```

响应：

```json
{
  "success": true,
  "response": "您好！我是AID助手，很高兴为您服务。",
  "session_id": "xxx"
}
```

## 支持的意图

| 意图 | 说明 | 示例 |
|------|------|------|
| email_send | 发送邮件 | "帮我给张三发邮件" |
| email_read | 读取邮件 | "查看我的收件箱" |
| doc_summarize | 文档摘要 | "总结这份文档" |
| doc_translate | 文档翻译 | "翻译成英语" |
| ocr_image | 图片识别 | "识别这张图片的文字" |
| web_search | 网络搜索 | "搜索关于AI的资讯" |
| help | 获取帮助 | "你能做什么" |

## 扩展开发

### 添加新工具

1. 创建工具类，继承 `BaseTool`
2. 在 `src/main.py` 中注册工具

### 添加新渠道适配器

1. 创建适配器类，继承 `ChannelAdapter`
2. 实现消息解析和发送方法

## 版本规划

- **V1.0 MVP** (当前): 基础对话、企业微信、核心工具集
- **V1.5**: 钉钉/飞书渠道、知识库检索、多轮复杂任务
- **V2.0**: 多租户、管理后台、长期记忆、多智能体协作

## 许可证

MIT License
