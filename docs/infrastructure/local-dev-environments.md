# 本地开发调试环境说明（venv / WSL 容器 / Mac 容器）

> 本文档明确项目支持的三种本地开发、调试、测试方式，以及各自的网络要求。
> 三种方式的目标一致：**后端服务统一落在 `localhost:8000`，前端 Vite 代理（`http://127.0.0.1:8000`）与 `.env` 配置（数据库统一连 `DATABASE_URL` 指向的线上测试库）无需按环境改动**，启动容器后本地调试体验与 venv 完全一致。

---

## 1. 三种方式总览

| # | 方式 | 适用环境 | 容器文件 | 网络要求 |
|---|------|---------|---------|---------|
| 方式一 | 本地 venv 虚拟环境 | Windows（主）、macOS 均可 | 不用容器 | 直接走本机网络，天然 localhost |
| 方式二 | WSL2 内运行容器 | Windows + WSL2（容器网络与本地一致，**不走 NAT**） | `docker-compose.local.yml` | WSL 开启 mirrored 网络模式；容器建议走 host 网络覆盖文件 |
| 方式三 | Mac 版 Docker Desktop 容器 | macOS | `docker-compose.local.yml` | 端口发布到 localhost（默认）；或 4.34+ 开启 host networking |

**统一原则**：

1. 容器网络必须与开发机本地网络一致（桥接/host 模式），**禁止依赖 NAT 后的独立网段 IP**（如 `172.x.x.x`）访问服务。
2. 任何一种方式下，开发者都只使用 `http://localhost:8000` 访问后端，与 venv 方式无差别。
3. **同一时间只运行一种方式**：venv 进程与容器都默认绑定 8000 端口，混跑会端口冲突。切换方式前先停掉另一种（venv 停进程，容器 `docker compose -f docker-compose.local.yml down`）。

---

## 2. 公共前置（三种方式通用）

1. **环境变量**：复制 `.env.example` 为 `.env` 并填写必要配置（LLM API Key、`DATABASE_URL` 等）。
2. **数据库**：数据库连接统一使用 `.env` 中 `DATABASE_URL` 配置的**线上测试数据库**，本地**不部署任何数据库服务**（不新增本地 Postgres/Redis 等）。三种方式连接的是同一个测试库，`.env` 无需按环境改动；唯一要求是开发机到测试库的网络可达。
3. **前端**：三种方式下前端都不进容器，统一在 `frontend/` 目录 `npm install && npm run dev`。Vite 已配置 `/api` 代理到 `http://127.0.0.1:8000`（见 `frontend/vite.config.ts`），后端落在 localhost:8000 后前端无需任何改动。

---

## 3. 方式一：本地 venv 环境

Windows 上的默认方式，直接使用宿主机 Python 环境。

```bash
# 1. 创建并激活虚拟环境（首次）
python -m venv venv
venv\Scripts\activate            # Windows；macOS/Linux 为 source venv/bin/activate

# 2. 安装依赖（首次）
pip install -r requirements.txt

# 3. 配置 .env 后启动后端（默认监听 8000）
python -m src.main

# CLI 聊天模式（可选）
CLI_MODE=true python -m src.main
```

- 优点：无虚拟化层，调试器断点、日志、文件 IO 都是原生路径，启动最快。
- 特点：依赖安装在 Windows 侧 venv 中；部分依赖（Playwright/Chromium、Pandoc 等）需按工具文档单独安装。
- 验证：`curl http://localhost:8000/health` 返回正常即启动成功。

---

## 4. 方式二：WSL2 内运行容器（容器网络与本地一致，不走 NAT）

适用场景：希望后端跑在与生产一致的 Linux 容器里（Python 3.11-slim + 完整工具链），同时保留本地 localhost 调试体验。容器编排文件为根目录 [docker-compose.local.yml](../../../docker-compose.local.yml)：挂载整个项目目录、uvicorn `--reload` 热更新、已设 `WATCHFILES_FORCE_POLLING=true` 兼容 Windows 文件系统的变更监听。

### 4.1 前置：WSL2 开启 mirrored 网络模式（关键，不走 NAT）

WSL2 默认使用 NAT 虚拟网络（WSL 内是独立网段），必须改为 **mirrored 镜像网络**，让 WSL 与 Windows 共享同一套网络接口，容器端口才会直接落在 Windows 的 localhost 上：

1. 编辑（新建）`%UserProfile%\.wslconfig`：

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

2. 重启 WSL 生效：

   ```powershell
   wsl --shutdown
   ```

3. 验证：在 WSL 内 `ip addr` 看到的应是 Windows 宿主机的 IP（而非 `172.x.x.x` 独立网段）；Windows 侧 `curl http://localhost:8000` 与 WSL 侧互相可达（mirrored 模式下 loopback 双向共享）。

   > mirrored 模式要求 Windows 11 22H2（build 22621）及以上。

4. 在 WSL 发行版内安装 Docker Engine（docker-ce，非 Docker Desktop；已装可跳过）：

   ```bash
   curl -fsSL https://get.docker.com | sh
   ```

### 4.2 启动（推荐：host 网络覆盖，容器无 NAT、无端口映射）

项目提供 [docker-compose.local.hostnet.yml](../../../docker-compose.local.hostnet.yml) 覆盖文件，让容器直接使用 WSL 的网络栈（等价于桥接到本地网络）：容器内 `localhost` 即 WSL loopback（mirrored 模式下即 Windows loopback），回环语义与 venv 完全一致，8000 端口直接绑定在宿主网络栈上。

```bash
cd /mnt/c/repos/aid-work-agent    # 或 clone 到 WSL 文件系统内的路径
docker compose -f docker-compose.local.yml -f docker-compose.local.hostnet.yml up -d --build
docker compose -f docker-compose.local.yml -f docker-compose.local.hostnet.yml logs -f aid-agent-api
```

> host 网络模式下 compose 的 `ports` 端口映射会被忽略（启动时有 warning，属预期），服务固定监听在宿主 8000 端口。

### 4.3 备选：默认 bridge + 端口发布

不加载覆盖文件时，`docker-compose.local.yml` 使用 bridge 网络 + `8000:8000` 端口发布。在 WSL mirrored 模式下发布的端口同样直接落在 Windows localhost，开发体验一致（差异仅在容器内的回环语义，见 4.4 注意事项）：

```bash
docker compose -f docker-compose.local.yml up -d --build
```

### 4.4 注意事项

- **仓库位置与热重载延迟**：仓库放 Windows 盘（`/mnt/c/...`）时 WSL 跨文件系统 IO 较慢。compose 已将 uvicorn 热重载监听收窄到 `--reload-dir src`（避免轮询扫描 frontend/node_modules 等数万文件），实测 /mnt/c 下代码修改到自动重载约 10-15 秒。需要秒级热重载的密集后端迭代，建议将仓库 clone 到 WSL 原生文件系统（如 `~/repos/aid-work-agent`）；两份工作区不要同时运行服务（8000 端口冲突）。
- **数据库**：三种方式统一连接 `.env` 中 `DATABASE_URL` 指向的线上测试数据库，容器直接出网访问该库，bridge 与 host 网络模式无差异，`.env` 不需要按环境改动。本地不新增任何数据库服务。
- **前端**：可在 Windows 侧照常 `npm run dev`（代理 127.0.0.1:8000 直接可达），也可在 WSL 内运行，二者等价。
- 验证清单：Windows 侧 `curl http://localhost:8000/health`；WSL 侧同样 `curl http://localhost:8000/health`；后端日志确认已连上 `.env` 指向的线上测试库；前端页面正常调用后端接口。

---

## 5. 方式三：macOS + Docker Desktop 容器

Mac 上没有 venv 方式的历史约束，直接用 Mac 版 Docker Desktop 跑同一份 `docker-compose.local.yml`：

```bash
cd /path/to/aid-work-agent
docker compose -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.local.yml logs -f aid-agent-api
```

### 网络说明（桥接到本地 localhost，不走 NAT 网段）

Docker Desktop for Mac 运行在一台轻量虚拟机中，但**发布（publish）的端口由 Docker Desktop 自动转发到 Mac 的 loopback**——`ports: "8000:8000"` 发布后，Mac 侧直接 `http://localhost:8000` 访问，开发者视角与桥接/venv 一致，不接触虚拟机 NAT 网段 IP。

- **数据库**：与方式二相同，统一连接 `.env` 中 `DATABASE_URL` 指向的线上测试数据库，容器直接出网访问，无需 `host.docker.internal`，`.env` 零改动。

- **可选：真正的 host 网络**（Docker Desktop 4.34+）：Settings → Resources → Network → 勾选 **Enable host networking** 后，可使用 `docker-compose.local.hostnet.yml` 覆盖文件（同方式二），容器内 `localhost` 即 Mac loopback，行为与方式二完全一致。

- **前端**：Mac 侧 `frontend/` 下 `npm run dev`，代理 127.0.0.1:8000 直接可达。
- 验证清单：Mac 终端 `curl http://localhost:8000/health`；后端日志确认已连上 `.env` 指向的线上测试库；前端页面正常调用后端接口。

---

## 6. 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| 容器启动后 Windows 访问不到 localhost:8000（WSL） | 未开启 mirrored 网络模式，或改完 `.wslconfig` 未执行 `wsl --shutdown`。确认 WSL 内 IP 与 Windows 一致 |
| WSL 内 `ip addr` 显示 172.x 网段 | 仍是 NAT 模式。Windows 版本需 11 22H2+；确认 `.wslconfig` 中 `networkingMode=mirrored` 拼写与节名 `[wsl2]` 正确 |
| 8000 端口被占用 | venv 进程与容器混跑。切换方式前先停另一种（`docker compose -f docker-compose.local.yml down` 或结束 python 进程） |
| 容器内连不上测试数据库 | 数据库统一是 `.env` 中 `DATABASE_URL` 指向的线上测试库。确认开发机到测试库网络可达（VPN/白名单）、`DATABASE_URL` 配置正确；venv 与容器连接的是同一个库，排查时先看后端启动日志中的数据库初始化报错 |
| 改代码后容器没反应 | 热重载只监听 `--reload-dir src`（见 `docker-compose.local.yml`），改 src/ 以外的文件（configs、skills 等）需手动重启容器：`docker compose -f docker-compose.local.yml -f docker-compose.local.hostnet.yml restart`；/mnt/c 下检测延迟约 10-15 秒属正常（轮询机制） |
| 容器内时区/文件权限异常 | 参见 `docker-compose.local.yml` 内注释中的已知问题兜底（tmpfs /tmp mode=1777 等） |

---

## 7. 相关文件

| 文件 | 用途 |
|------|------|
| [docker-compose.local.yml](../../../docker-compose.local.yml) | 本地开发容器编排（方式二/三共用）：挂载源码、uvicorn --reload |
| [docker-compose.local.hostnet.yml](../../../docker-compose.local.hostnet.yml) | host 网络覆盖文件：容器直接绑定宿主网络栈（不走 bridge/NAT、无端口映射） |
| [Dockerfile](../../../Dockerfile) | 后端镜像构建（多阶段，python:3.11-slim） |
| `frontend/vite.config.ts` | 前端开发服务器与 `/api` 代理（固定指向 127.0.0.1:8000） |
