# AID Work Agent 部署指南

## 目录

- [快速开始](#快速开始)
- [部署方式](#部署方式)
  - [方式一：Docker Compose 部署](#方式一docker-compose-部署)
  - [方式二：Docker 手动部署](#方式二docker-手动部署)
  - [方式三：直接部署（无 Docker）](#方式三直接部署无-docker)
- [配置说明](#配置说明)
- [环境变量](#环境变量)
- [验证部署](#验证部署)
- [常见问题](#常见问题)

---

## 快速开始

### 前置要求

- Docker 20.10+
- Docker Compose 2.0+ (可选，但推荐)
- 至少 2GB 内存

### 快速启动（使用 Docker Compose）

```bash
# 1. 克隆项目
git clone <repository_url>
cd aid-work-agent

# 2. 创建环境变量文件
cp deploy/.env.example deploy/.env

# 3. 编辑环境变量，填入你的 API Key
vim deploy/.env

# 4. 启动服务
docker compose up -d

# 5. 查看服务状态
docker compose ps

# 6. 查看日志
docker compose logs -f
```

---

## 部署方式

### 方式一：Docker Compose 部署

#### 1. 仅启动 API 服务

```bash
docker compose up -d aid-agent-api
```

API 服务将在 http://localhost:8000 启动。

#### 2. 同时启动 API 和 Gradio UI

```bash
docker compose --profile ui up -d
```

- API 服务：http://localhost:8000
- Gradio UI：http://localhost:7860

#### 3. 使用自定义端口

编辑 `deploy/.env` 文件：

```bash
API_PORT=9000
GRADIO_PORT=7861
```

#### 4. 停止服务

```bash
docker compose down

# 如果需要删除数据卷
docker compose down -v
```

---

### 方式二：Docker 手动部署

#### 1. 构建镜像

```bash
docker build -t aid-agent:latest .
```

#### 2. 运行容器

**启动 FastAPI 服务：**

```bash
docker run -d \
  --name aid-agent-api \
  -p 8000:8000 \
  -e SERVER_MODE=fastapi \
  -e LLM_PROVIDER=qwen \
  -e QWEN_API_KEY=your_api_key \
  -v $(pwd)/logs:/app/logs \
  aid-agent:latest
```

**启动 Gradio UI：**

```bash
docker run -d \
  --name aid-agent-ui \
  -p 7860:7860 \
  -e SERVER_MODE=gradio \
  -e LLM_PROVIDER=qwen \
  -e QWEN_API_KEY=your_api_key \
  -v $(pwd)/logs:/app/logs \
  aid-agent:latest
```

---

### 方式三：直接部署（无 Docker）

#### 1. 安装依赖

```bash
# 使用虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
.\venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

#### 2. 配置环境变量

```bash
# Linux/Mac
export LLM_PROVIDER=qwen
export QWEN_API_KEY=your_api_key

# Windows PowerShell
$env:LLM_PROVIDER="qwen"
$env:QWEN_API_KEY="your_api_key"
```

#### 3. 启动服务

**FastAPI 服务：**

```bash
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

**Gradio UI：**

```bash
python gradio_app.py --host 0.0.0.0 --port 7860
```

---

## 配置说明

### 配置文件

主配置文件位于 `configs/config.yaml`，支持通过环境变量覆盖。

### 配置优先级

环境变量 > config.yaml 默认值

---

## 环境变量

### 必需变量

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `LLM_PROVIDER` | LLM 提供商 | `qwen` 或 `zhipu` |
| `QWEN_API_KEY` | 阿里云 Qwen API Key | `sk-xxxxxxxx` |
| `ZHIPU_API_KEY` | 智谱 GLM API Key | `xxxxxxxx` |

### 可选变量

#### 应用配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DEBUG` | `false` | 调试模式 |
| `API_PORT` | `8000` | API 服务端口 |
| `GRADIO_PORT` | `7860` | Gradio UI 端口 |

#### 企业微信配置

| 变量名 | 说明 |
|--------|------|
| `WECOM_ENABLED` | 启用企业微信 (`true`/`false`) |
| `WECOM_CORP_ID` | 企业 ID |
| `WECOM_AGENT_ID` | 应用 AgentID |
| `WECOM_SECRET` | 应用 Secret |
| `WECOM_TOKEN` | Token |
| `WECOM_ENCODING_AES_KEY` | EncodingAESKey |

#### 钉钉配置

| 变量名 | 说明 |
|--------|------|
| `DINGTALK_ENABLED` | 启用钉钉 (`true`/`false`) |
| `DINGTALK_APP_KEY` | App Key |
| `DINGTALK_APP_SECRET` | App Secret |
| `DINGTALK_TOKEN` | Token |
| `DINGTALK_ENCODING_AES_KEY` | EncodingAESKey |

#### 飞书配置

| 变量名 | 说明 |
|--------|------|
| `FEISHU_ENABLED` | 启用飞书 (`true`/`false`) |
| `FEISHU_APP_ID` | App ID |
| `FEISHU_APP_SECRET` | App Secret |
| `FEISHU_VERIFICATION_TOKEN` | Verification Token |
| `FEISHU_ENCRYPT_KEY` | Encrypt Key |

#### 邮件工具配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SMTP_SERVER` | - | SMTP 服务器 |
| `SMTP_PORT` | `465` | SMTP 端口 |
| `SMTP_USER` | - | SMTP 用户名 |
| `SMTP_PASSWORD` | - | SMTP 密码 |
| `IMAP_SERVER` | - | IMAP 服务器 |
| `IMAP_PORT` | `993` | IMAP 端口 |

#### 其他工具配置

| 变量名 | 说明 |
|--------|------|
| `TAVILY_API_KEY` | Tavily 搜索 API Key |
| `BAIDU_OCR_API_KEY` | 百度 OCR API Key |
| `BAIDU_OCR_SECRET_KEY` | 百度 OCR Secret Key |

---

## 验证部署

### 健康检查

```bash
# 检查 API 健康状态
curl http://localhost:8000/health

# 预期响应
{
  "status": "healthy",
  "version": "1.0.0",
  "provider": "qwen"
}
```

### 测试聊天接口

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

---

## 常见问题

### Q1: 容器启动失败，提示 "port is already allocated"

端口被占用，修改端口：

```bash
# 方法1：使用 .env 文件修改端口
echo "API_PORT=9000" >> deploy/.env

# 方法2：直接指定端口运行
docker run -p 9000:8000 aid-agent:latest
```

### Q2: 容器内存不足

增加 Docker 内存限制，或添加 SWAP：

```bash
docker run -d --memory=4g ...
```

### Q3: 找不到配置文件

确保在项目根目录运行容器，配置文件挂载：

```bash
docker run -v $(pwd)/configs:/app/configs ...
```

### Q4: Playwright 浏览器启动失败

容器已预装 Playwright 浏览器，如有问题，检查系统依赖：

```bash
# 重新安装浏览器
docker exec <container_id> playwright install chromium --with-deps
```

### Q5: 如何查看实时日志

```bash
# Docker Compose
docker compose logs -f

# Docker 手动
docker logs -f <container_id>
```

### Q6: 如何进入容器调试

```bash
docker exec -it <container_id> /bin/bash
```

---

## 生产环境建议

1. **使用反向代理**：如 Nginx、Traefik
2. **启用 HTTPS**：配置 SSL 证书
3. **限制端口访问**：使用防火墙规则
4. **定期备份配置**：备份 `config.yaml` 和环境变量
5. **监控部署**：使用 Prometheus + Grafana
6. **日志管理**：配置日志轮转，避免磁盘满

---

## 目录结构

```
aid-work-agent/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 配置
├── .dockerignore           # Docker 构建排除文件
├── requirements.txt        # Python 依赖
├── configs/
│   └── config.yaml         # 主配置文件
├── src/                    # 源代码
├── gradio_app.py           # Gradio UI 入口
├── deploy/
│   ├── README.md           # 本文档
│   ├── deploy.sh           # 部署脚本
│   └── .env.example        # 环境变量示例
└── logs/                   # 日志目录（运行时创建）
```
