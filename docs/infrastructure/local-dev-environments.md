# 容器化开发与自测环境

本文是本仓库本地开发与自测的唯一执行手册。环境选择、服务启动与切换、测试入口和排查命令集中在这里维护。修改本地 Compose、测试入口或依赖时，同步更新本手册。项目入口和专项测试文档链接到本手册。

需要启动项目或自测时，优先使用本机 Linux 容器。各平台复用仓库的 [Dockerfile](../../Dockerfile)、[本地 Compose](../../docker-compose.local.yml) 和测试入口，容器挂载本次开发的工作区。

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

已经运行且挂载正确的容器直接复用。多个工作区共享 Docker 引擎时，`aid-agent-api` 是默认容器名，8000 是默认端口。另一个工作区在用时，按[指定工作区验证](#4-指定工作区验证)选择自测或切换服务。

WSL2 可直接使用 Windows 盘上的工作区。如果密集文件访问或测试子进程在 `/mnt/c` 下超时，可以把当前开发工作区放到 WSL2 文件系统中，再从该路径启动和自测。原工作区有未提交改动时，先确认新路径包含同样的改动。

### 首次初始化

在仓库根检查应用镜像。缺少镜像，或本次改动了 `Dockerfile`、`requirements.txt` 等镜像依赖时构建一次。日常源码修改由容器挂载直接读取。

```bash
docker image inspect aid-agent-api:latest --format '{{.Id}}'
docker build --build-arg APT_MIRROR=https://mirrors.aliyun.com -t aid-agent-api:latest .
```

应用镜像包含工具所需的 `rg`、`pandoc`、`soffice`、`ffmpeg` 和 Chromium。工具报缺少二进制时，先确认容器使用的镜像，在容器内用 `command -v <命令>` 或 `--version` 核对。相关工具用途见[工具开发规范](../tools/tool-development-spec.md)。

只运行自测时，镜像就绪即可跳到[自测命令](#3-在容器内自测)。需要启动应用时，若 `.env` 不存在或为空，复制 `.env.example` 为 `.env`。PowerShell 和 Bash 均可使用 `cp .env.example .env`。

默认万相视频供应商在应用启动时读取 `WANX_API_KEY`，也可复用 `QWEN_API_KEYS`。只做健康检查、数据库和页面联调时，可在 `.env` 设置 `QWEN_API_KEYS=local-dev-placeholder` 供启动使用。验证真实模型功能时换为相应的开发凭据。联调 Runner 时在 `.env` 设置 `HEAVY_TOOLS_MODE=local`、本地 `AGENT_RUNNER_WEB_SERVICE_TOKEN`，按需设置 `AGENT_RUNNER_WORKER_SLOTS=1`。

**新机器**用 [本地数据覆盖文件](../../docker-compose.local-data.yml) 自动创建专用网络、持久卷、业务 PostgreSQL 和 Redis。应用配置由覆盖文件直接指向本地依赖。Compose 会等待 PostgreSQL 和 Redis 就绪。

```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-data.yml up -d --no-build --wait aid-agent-api
# 需要联调 Runner 时
docker compose -f docker-compose.local.yml -f docker-compose.local-data.yml up -d --no-build --wait aid-agent-api runner-api runner-worker
```

**已有本地数据库和 Redis**，且现有容器使用 `aid-wecom-local_local` 网络时，沿用原 `docker compose -f docker-compose.local.yml up -d --no-build aid-agent-api` 命令和 `.env`。使用已有工作区前核对两个连接的实际目标。日常重用现有服务和卷，不执行 `down -v`。

访问 `http://localhost:8000/health`，只记录响应的 `status` 字段，应看到 `healthy`。Runner 联调另外确认 Runner API 和 worker 的状态。API 使用 uvicorn 热重载监视 `src/`。修改 `.env` 后重建受影响的本地容器。WSL2 访问默认端口时先检查 WSL 和 Windows 两侧的 `localhost:8000`。仅在已有 WSL mirrored 网络且需要让 API 使用宿主网络时考虑 [host 网络覆盖](../../docker-compose.local.hostnet.yml)。依赖 `postgres`、`redis`、`runner-api` 等容器 DNS 的本机配置使用默认 bridge。

### 本地联调排查

排查时沿用启动环境的项目名与 Compose 文件。使用本地数据覆盖文件时，从仓库根执行：

```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-data.yml ps
docker compose -f docker-compose.local.yml -f docker-compose.local-data.yml exec redis redis-cli ping
docker compose -f docker-compose.local.yml -f docker-compose.local-data.yml exec postgres pg_isready -h 127.0.0.1 -U aid_local -d aid_work_agent_local
```

Redis 应返回 `PONG`，PostgreSQL 应开始接受连接。沿用旧数据网络时，在那个环境现有的 Redis 和 PostgreSQL 容器中执行相应检查。需要定位初始化或连接失败时，用同一 Compose 项目的 `logs --tail 100` 指定相关服务，记录结果前脱敏。

在当前工作区的 API 容器中调试命令行聊天时，进容器执行 `CLI_MODE=true python -m src.main`。

## 3. 在容器内自测

**后端。** 从当前仓库根运行，替换示例测试路径。命令复用已有镜像并直接挂载当前源码。普通单测无需启动 API、PostgreSQL 或 Redis，也无需宿主机安装 Python。

```bash
docker compose -f docker-compose.dev-tools.yml run --rm python tests/unit/test_models.py -p no:cacheprovider -q
```

已有 API 容器挂载当前工作区时，也可以沿用 `bash scripts/dev_test.sh`。浏览器用例按该 [测试脚本](../../scripts/dev_test.sh) 使用 Redis helper。测试或检查指向其他工作区的结果不得用于当前改动。

可将示例路径换成测试文件、子目录或具体测试节点，用 `-k` 选择用例。默认排除 `e2e` 和 `real_browser`。显式传入 `-m` 会替换默认筛选，需要保留的排除条件应写进表达式，例如 `-m 'tools and not e2e and not real_browser'`。

需要实际浏览器与 Redis helper 时，确认默认名为 `aid-agent-api` 的本地 API 容器正在运行并挂载当前工作区。在能调用该 Docker 引擎的 Bash 中运行 `bash scripts/dev_test.sh -m 'not e2e and real_browser' -p no:cacheprovider -q`。脚本使用该终端的 `python` 核对测试入口，实际测试在容器内运行。

**PostgreSQL。** 本地 API 和 Runner 默认连本地业务库。联调已有业务流程时，可以查询本地业务数据，也可以写入本轮专用记录。先核对实际目标是本机容器和预期数据库。有事务的测试可回滚，需要提交的联调记录按确切 ID 及时清理。[测试规范](../../.claude/rules/testing.md#数据库安全入口)

```bash
docker exec -it <本地业务PG容器> psql -U <本地业务用户> -d <本地业务库>
```

自动化 SQL 回归、迁移和批量数据验证使用专用 PostgreSQL。从当前仓库根目录执行以下命令，示例以 Runner 存储集成测试为例。

```bash
docker compose -f docker-compose.test-db.yml run --rm sql tests/integration/agent_runner_service/test_storage.py -p no:cacheprovider -q
```

首次自动启动专用 PG，之后复用同一个容器。每轮 pytest 连接自己的 `aid_test_*` 库并在结束时清理。测试入口直接使用当前工作区和已有的 `aid-agent-api:latest` 镜像，连接配置已在 Compose 中提供。纯 SQL 路径禁用 Redis，避免等待本地业务 Redis。可用 `docker compose -f docker-compose.test-db.yml ps` 查看状态，用 `docker compose -f docker-compose.test-db.yml up -d --wait postgres` 提前启动。手工验证 SQL 时可在专用 PG 内用 `docker compose -f docker-compose.test-db.yml exec postgres psql -U postgres -d postgres`，按需创建并清理本轮临时库。

已有自定义专用测试 PG 时，可通过环境变量 `DATABASE_URL` 显式提供管理连接，再运行 `bash scripts/dev_test.sh --isolated-db <相关测试>`。管理库名为 `postgres`，连接地址须在测试进程的网络命名空间内通过 loopback 访问。日常 SQL 验证直接使用上面的 Compose 入口。

**Web 前端。** 使用应用镜像内的 Node.js 和 npm。首次命令按 lockfile 安装依赖并写入命名卷，此后测试、构建、Vite 复用该卷。`package.json`、lockfile 或工具链变更时自动刷新。更新同一工作区的依赖前先退出 Vite。并行验证依赖不同的分支时，用 `-p <项目名>` 为其中一个工作区指定自己的缓存卷。

```bash
docker compose -f docker-compose.dev-tools.yml run --rm web test -- web/__tests__/router/desktopBuildVerifier.test.ts
docker compose -f docker-compose.dev-tools.yml run --rm web run build
```

需要覆盖率或监听时，使用同一 `web` 入口运行 `run test:coverage` 或 `run test:watch`。默认 Web 构建产物位于当前工作区 `frontend/dist/`。

页面联调时，Vite 的 `/api` 代理访问 `127.0.0.1:8000`。WSL2 和 Linux 使用 `web-dev` 的 host 网络，后端用默认 bridge 并发布 8000 端口。Docker Desktop 4.34 及以上在本机启用 host networking 后也可使用此入口。[Vite 配置](../../frontend/vite.config.ts) · [Docker host 网络说明](https://docs.docker.com/engine/network/drivers/host/#docker-desktop)

```bash
docker compose -f docker-compose.dev-tools.yml run --rm web-dev
```

从开发机打开 `http://localhost:3000`。桌面端原生运行、打包及签名按各客户端的目标系统执行。

手动联调 Portal 登录可访问 `/portal/login`，页面验证见 [Portal 测试指南](../../frontend/web/__tests__/saas-portal-test-guide.md#手动测试用例)。

## 4. 指定工作区验证

先定位目标工作区，核对 `git rev-parse HEAD` 和 `git status --short`。需要测试另一个分支的提交时，可以在当前仓库根用 `git worktree add --detach ../aid-work-review my-feature` 创建独立 checkout。需要验证未提交改动时，直接使用包含改动的那个工作区。

### 只跑自测

在目标工作区根运行[自测命令](#3-在容器内自测)，Compose 将该工作区挂载到 `/app`。应用镜像、专用测试 PG 和默认前端依赖卷可继续复用。多个工作区同时用不同的前端锁文件时，给各自的 `docker-compose.dev-tools.yml` 命令加不同的 `-p` 项目名。

如果目标分支还没有 Compose 入口，从已具备本手册的环境目录运行命令，并按下文设置 `AID_LOCAL_SOURCE_DIR` 指向目标。真实 SQL 测试使用目标工作区的 `scripts/dev_test_isolated_db.py` 和初始化 SQL。

### 将正在使用的本地 API 和 Runner 切到目标工作区

`docker compose ls` 可查看当前项目名和 Compose 文件。先记录当前 `/app` 的来源。留在负责启动该本地环境的目录，沿用同一个 `-p` 项目名、同一组 `-f` 文件与这套环境的 `.env`。使用已含 `AID_LOCAL_SOURCE_DIR` 的本地 Compose 配置。切换后，API 和 Runner 会一起读取目标工作区的源码与配置，并继续使用该项目的 PostgreSQL、Redis 和卷。

下例的 `aid-local` 是原项目名，替换为实际名字。WSL2 在 Linux 终端设置目标的 `/mnt/c/...` 或 WSL 原生绝对路径。macOS 和 Linux 使用该机的绝对路径。

```bash
export AID_LOCAL_SOURCE_DIR=/mnt/c/repos/aid-work-review
docker compose -p aid-local -f docker-compose.local.yml -f docker-compose.local-data.yml stop runner-monitor runner-worker runner-api
docker compose -p aid-local -f docker-compose.local.yml -f docker-compose.local-data.yml up -d --no-build --wait aid-agent-api runner-api runner-worker
```

在 Windows Docker Desktop 的 PowerShell 中改用 `$env:AID_LOCAL_SOURCE_DIR = 'C:/repos/aid-work-review'`，其余 Compose 命令相同。原环境使用旧的外部数据网络时，保留原来仅使用 `-f docker-compose.local.yml` 的命令。按原环境选择应用服务，只运行 API 时执行 `up ... aid-agent-api`。原先启用了 `runner-monitor` 时，在 `up` 服务列表一并包含它。等正在使用本地环境的任务结束后切换服务。涉及可能改变本地数据结构的迁移时，使用[专用测试 PG](#3-在容器内自测)或独立 Compose 项目。

如果 API 与 Runner 分属不同 Compose 项目，分别使用各自原来的项目名、文件与 `.env`。先停对应项目的 Runner 服务，再切换 API。API 健康后，在 Runner 项目用 `up -d --no-build --no-deps --wait runner-api` 启动 Runner API，再用同样参数启动原有的 worker 和 monitor。这样能沿用已运行的 API，并保持同一业务库的处理进程使用同一工作区。

切换后检查 `/health` 并核对每个相关容器的 `/app` 来源，预期均为目标路径。可从 `docker compose -p aid-local -f docker-compose.local.yml -f docker-compose.local-data.yml ps` 取容器名。

```bash
docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app"}}{{.Source}}{{end}}{{end}}' <容器名>
```

验证结束，把变量恢复为记录的原路径。原先使用默认路径时，用 `unset AID_LOCAL_SOURCE_DIR`（Bash）或 `Remove-Item Env:AID_LOCAL_SOURCE_DIR`（PowerShell）。按相同项目、文件组和应用服务执行上述 `stop`、`up`，最后复核挂载。复用应用镜像即可读到普通源码变更。镜像依赖有变化时，从目标工作区按[首次初始化](#首次初始化)构建，切回时使用与原工作区依赖对应的镜像。Docker 可能在目标工作区生成空 `.env` 挂载占位文件。以后单独从该目录启动应用时，按首次初始化处理空文件。

## 5. 本地容器不可用时使用 agent3

本机无法运行容器，或所需集成在本地无法验证时，使用已有配套服务的 **agent3 在线开发测试环境**。记录本机阻塞原因，并按验证类型选择入口。

- 单测和构建可在已有访问权限且可用的任务专属工作区运行，再核对提交和未提交改动是否与当前工作区一致。使用隔离测试容器和测试数据，保留在线服务的工作区和运行状态。
- 服务联调先确认在线版本是否包含本次改动。若需向在线服务更新代码、运行部署脚本、重建或重启，按 [真机部署授权](../../AGENTS.md#真机部署授权) 执行。

记录实际运行的代码版本和验证结果。当前版本仍未验证的项目注明缺口。
