# 容器化开发与自测环境

需要启动项目或自测时，优先使用本机 Linux 容器。各平台复用仓库的 [Dockerfile](../../../Dockerfile)、[本地 Compose](../../../docker-compose.local.yml) 和测试入口，容器挂载本次开发的工作区。

## 1. 选择 Docker 环境

| 开发机 | 选择顺序 | 在哪里执行命令 |
| --- | --- | --- |
| Windows | WSL2 发行版内的 Docker Engine 优先，其次是 Docker Desktop 的 Linux 容器 | WSL2 路径在发行版终端执行，Docker Desktop 路径在 Windows 终端执行 |
| macOS | Docker Desktop | macOS 终端 |
| Linux | 本机 Docker Engine | Linux 终端 |
| WSL2 内的智能体 | 当前发行版中的 Docker Engine | 当前终端 |

在 Windows 运行 `wsl.exe --list --verbose` 确认可用的 WSL2 发行版。在选中的发行版中进入实际工作区，例如 `cd /mnt/c/repos/aid-work-agent`。若发行版或其 Docker Engine 不可用，检查 Windows Docker Desktop。其余平台直接在本机终端检查 Docker。

```bash
docker context show
docker info --format '{{.OSType}}'
docker compose version
```

确认实际连接的是本机引擎，且 `OSType` 为 `linux`。本机的 WSL2 路径示例使用 `Ubuntu-22.04`。在 Windows 仓库根目录可执行 `wsl.exe -d Ubuntu-22.04 --exec bash -l`，进入后以 `pwd` 确认路径。

## 2. 复用或启动本地服务

先检查现有 API 容器。挂载到 `/app` 的目录应是当前工作区，在 WSL2 下按 Linux 路径核对，例如 `/mnt/c/repos/aid-work-agent`。

```bash
docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app"}}{{.Source}}{{end}}{{end}}' aid-agent-api
docker compose -f docker-compose.local.yml ps
```

已经运行且挂载正确的容器直接复用。多个工作区共享 Docker 引擎时，`aid-agent-api` 是固定容器名，8000 是默认端口。另一个工作区在用时，普通单测可使用下文的一次性容器，服务联调时先协调切换。

WSL2 可直接使用 Windows 盘上的工作区。如果密集文件访问或测试子进程在 `/mnt/c` 下超时，可以把当前开发工作区放到 WSL2 文件系统中，再从该路径启动和自测。原工作区有未提交改动时，先确认新路径包含同样的改动。

### 首次准备依赖

完整启动需要本机专用 PostgreSQL、Redis 和三个持久化卷。现有 WSL2 环境已具备 `aid-wecom-local_local` 网络、`postgres` / `redis` 服务和对应卷，可直接使用。在新机器上，按以下步骤准备：

1. 在仓库根目录构建后端镜像。首次构建在填入实际 `.env` 前进行。已有凭据时，用包含本次源码且排除了 `.env` 的构建上下文更新镜像。

   ```bash
   docker build --build-arg APT_MIRROR=https://mirrors.aliyun.com -t aid-agent-api:latest .
   ```

2. 创建缺少的依赖资源。下列命令需要在终端环境中已设置仅用于本机的 `POSTGRES_PASSWORD`。Bash 中用 `repo="$PWD"` 设置当前路径，PowerShell 中用 `$repo = (Get-Location).Path`。

   ```bash
   docker network create aid-wecom-local_local
   docker volume create aid-wecom-local_storage
   docker volume create aid-wecom-local_uploads
   docker volume create aid-wecom-local_logs
   docker run -d --name aid-dev-postgres --restart unless-stopped --network aid-wecom-local_local --network-alias postgres -e POSTGRES_USER=aid_local -e POSTGRES_PASSWORD -e POSTGRES_DB=aid_work_agent_local -v aid-dev-postgres-data:/var/lib/postgresql/data --mount "type=bind,source=${repo}/deploy/init-postgres.sql,target=/docker-entrypoint-initdb.d/10-aid.sql,readonly" pgvector/pgvector:pg16
   docker run -d --name aid-dev-redis --restart unless-stopped --network aid-wecom-local_local --network-alias redis -v aid-dev-redis-data:/data redis:7-alpine redis-server --appendonly yes
   docker exec aid-dev-postgres pg_isready -h 127.0.0.1 -U aid_local -d aid_work_agent_local
   docker exec aid-dev-redis redis-cli ping
   ```

3. 从 `.env.example` 创建 `.env` 并填入本地配置。如果沿用已有 `.env`，在启动前核对 PostgreSQL 和 Redis 指向本机开发资源。使用上述网络时，`DATABASE_URL` 的主机为 `postgres`，端口为 `5432`，数据库为 `aid_work_agent_local`，用户为 `aid_local`，密码与容器一致。追踪库 `LOGS_DATABASE_URL` 按需指向专用本地库。设置 `REDIS_ENABLED=true`、`REDIS_HOST=redis`、`HEAVY_TOOLS_MODE=local`。联调 Runner 时，设置 `AGENT_RUNNER_API_URL=http://runner-api:8091` 和本地 `AGENT_RUNNER_WEB_SERVICE_TOKEN`，可设 `AGENT_RUNNER_WORKER_SLOTS=1`。模型或第三方凭据按实际联调范围配置。

### 启动与检查

默认使用 bridge 网络，容器通过服务名访问本机 PostgreSQL 和 Redis，API 端口发布到开发机的 8000。按本次任务选择服务。

```bash
docker compose -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.local.yml up -d --no-build aid-agent-api
# 需要联调 Runner 时
docker compose -f docker-compose.local.yml up -d --no-build aid-agent-api runner-api runner-worker
docker compose -f docker-compose.local.yml ps
```

访问 `http://localhost:8000/health`，只记录响应的 `status` 字段。应看到 `healthy`。Runner 联调另外确认 Runner API 和 worker 的状态。API 使用 uvicorn 热重载监视 `src/`。修改依赖时更新镜像，修改 `.env` 时重新创建本地容器。WSL2 访问默认端口时先检查 WSL 和 Windows 两侧的 `localhost:8000`。仅在已有 WSL mirrored 网络且需要让 API 使用宿主网络时考虑 [host 网络覆盖](../../../docker-compose.local.hostnet.yml)。该覆盖会使 API 脱离 Compose 网络，依赖 `postgres`、`redis`、`runner-api` 等容器 DNS 的本机配置继续使用默认 bridge。

## 3. 在容器内自测

**后端。** 在可以调用 Docker 的 Bash 中，从当前工作区用 `bash scripts/dev_test.sh <相关测试> -p no:cacheprovider -q` 复用运行中的 `aid-agent-api`。复用入口使用宿主 Python 核对测试安全文件，实际 pytest 在容器内运行。Windows 的 WSL2 路径可从仓库根执行 `wsl.exe --exec bash -lc 'bash scripts/dev_test.sh tests/unit/test_models.py -p no:cacheprovider -q'`，非默认发行版在 `--exec` 前加 `-d <发行版>`。

普通单测不需要启动 API、PostgreSQL 或 Redis。无匹配的运行容器、宿主没有 Bash 或 Python 时，在启动 API 之前构建或复用同一仓库镜像，再从选中的 Docker 终端运行一次性容器。替换示例测试路径，`repo` 的设置同上。

```bash
docker run --rm --mount "type=bind,source=${repo},target=/app" -w /app --entrypoint bash aid-agent-api:latest scripts/dev_test.sh tests/unit/test_models.py -p no:cacheprovider -q
```

Python 临时检查也使用选定的应用镜像或当前容器，例如 `docker exec aid-agent-api python --version`。浏览器用例按 [测试脚本](../../../scripts/dev_test.sh) 使用 Redis helper。普通 pytest 未显式配置隔离库时使用无数据库模式。测试或检查指向其他工作区的结果不得用于当前改动。

**PostgreSQL。** 本地 API 和 Runner 默认连本地业务库。联调已有业务流程时，可以查询本地业务数据，也可以写入本轮专用记录。先核对实际目标是本机容器和预期数据库。有事务的测试可回滚，需要提交的联调记录按确切 ID 及时清理。[测试规范](../../../.claude/rules/testing.md#数据库安全入口)

```bash
docker exec -it <本地业务PG容器> psql -U <本地业务用户> -d <本地业务库>
```

自动化 SQL 回归、迁移和批量数据验证使用专用 PostgreSQL。从当前仓库根目录执行以下命令，示例以 Runner 存储集成测试为例。

```bash
docker compose -f docker-compose.test-db.yml run --rm sql tests/integration/agent_runner_service/test_storage.py -p no:cacheprovider -q
```

首次自动启动专用 PG，之后复用同一个容器。每轮 pytest 连接自己的 `aid_test_*` 库并在结束时清理。测试入口直接使用当前工作区和已有的 `aid-agent-api:latest` 镜像，连接配置已在 Compose 中提供。纯 SQL 路径禁用 Redis，避免等待本地业务 Redis。可用 `docker compose -f docker-compose.test-db.yml ps` 查看状态，用 `docker compose -f docker-compose.test-db.yml up -d --wait postgres` 提前启动。手工验证 SQL 时可在专用 PG 内用 `docker compose -f docker-compose.test-db.yml exec postgres psql -U postgres -d postgres`，按需创建并清理本轮临时库。

**Web 前端。** 复用应用镜像内的 Node.js 和 npm，使用锁文件安装依赖并执行 Web 构建。匿名卷隔离 Linux `node_modules`，同时保留宿主机已有依赖目录。

```bash
docker run --rm --mount "type=bind,source=${repo},target=/app" --mount type=volume,target=/app/frontend/node_modules -w /app/frontend --entrypoint bash aid-agent-api:latest -c 'npm ci && npm run build'
```

针对性测试使用同样的镜像执行 `npm ci && npm test -- <相关测试>`。页面联调时，Vite 的 `/api` 代理访问 `127.0.0.1:8000`。WSL2 和 Linux 可让 Vite 容器使用 host 网络，后端继续使用默认 bridge 并发布 8000 端口。Docker Desktop 4.34 及以上可在本机已启用 host networking 时使用同一命令。[Vite 配置](../../../frontend/vite.config.ts) · [Docker host 网络说明](https://docs.docker.com/engine/network/drivers/host/#docker-desktop)

```bash
docker run --rm --network host --mount "type=bind,source=${repo},target=/app" --mount type=volume,target=/app/frontend/node_modules -w /app/frontend --entrypoint bash aid-agent-api:latest -c 'npm ci && npm run dev -- --host 127.0.0.1 --port 3000 --strictPort'
```

从开发机打开 `http://localhost:3000`。桌面端原生运行、打包及签名按各客户端的目标系统执行。

## 4. 本地容器不可用时使用 agent3

本机无法运行容器，或所需集成在本地无法验证时，使用已有配套服务的 **agent3 在线开发测试环境**。记录本机阻塞原因，并按验证类型选择入口。

- 单测和构建可在已有访问权限且可用的任务专属工作区运行，再核对提交和未提交改动是否与当前工作区一致。使用隔离测试容器和测试数据，保留在线服务的工作区和运行状态。
- 服务联调先确认在线版本是否包含本次改动。若需向在线服务更新代码、运行部署脚本、重建或重启，按 [真机部署授权](../../../AGENTS.md#真机部署授权) 执行。

记录实际运行的代码版本和验证结果。当前版本仍未验证的项目注明缺口。
