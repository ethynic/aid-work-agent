# 工作区瘦身：sparse-checkout 排除非运行目录

> 原为 [服务器部署现状.md](./服务器部署现状.md) §9.1，2026-11-08 拆分为独立文档。

## 背景

两台服务器共 4 个仓库目录（243 新机 `agent` / `agent1` 于 2026-09-17 补配，旧机 `agent2` / `agent3` 2026-09-02 配置）均启用了 git sparse-checkout，`git pull` / `git reset --hard` 时以下目录不再落到工作区：

```
.agents/ .claude/ .codebuddy/ .pytest_cache/ .zcode/ clients/ contracts/ docs/ ext/ openspec/ plans/ test_uploads/ tests/
```

> 2026-11-08 新增排除 `clients/`（11MB/951 文件，桌面客户端、RPA、CLI 源码）与 `contracts/`（204KB，接口 schema）：服务端容器运行时零依赖——`src/` 中仅注释引用；`association_enrichment_providers.py` 的 clients 路径是 PyInstaller 打包桌面客户端的 dev 模式解析（跑在开发机）；DTO 定义在 Python 代码中，无 schema 文件加载。
>
> **应用进度**：2026-11-08 已在 254 旧机 `agent2` / `agent3` 应用（两仓库 clients/contracts 无未提交修改，应用后容器全部健康）；243 新机 `agent` / `agent1` **待执行**。

> ⚠️ `deploy/` **必须保留**在检出列表中：容器 ENTRYPOINT 是 `gunicorn -c deploy/gunicorn.conf.py`（经 `.:/app` 挂载读取）、镜像构建 COPY `deploy/fix_tmp.sh`、更新脚本与备份 cron 都依赖它。

> ⚠️ **`plans/` 是运行时可写目录，排除后必须预创建**（2026-09-03 agent3 启动事故教训）：
>
> `plans/` 不仅是文档目录，还是 `PlanManager` 的运行时持久化目录。sparse-checkout 排除后若目录内无未跟踪运行时文件残留，**整个目录会消失**。容器以 uid 1000 运行、项目目录属主不一致时 `mkdir` 报 `PermissionError` → gunicorn worker 无法 boot，容器 crash 循环。
>
> **修复与预防**（属主对齐运行身份 uid 1000 gid 999）：
>
> ```bash
> sudo mkdir -p /var/www/agent3/plans
> sudo chown 1000:999 /var/www/agent3/plans
> ```

## 配置方式（新 clone 的服务器需重新执行）

```bash
cd /var/www/agent   # agent2 / agent3 同理
git config core.sparseCheckout true
printf '/*\n!/.agents/\n!/.claude/\n!/.codebuddy/\n!/.pytest_cache/\n!/.zcode/\n!/clients/\n!/contracts/\n!/docs/\n!/ext/\n!/openspec/\n!/plans/\n!/test_uploads/\n!/tests/\n' > .git/info/sparse-checkout
git read-tree -mu HEAD
```

回滚：`git config --unset core.sparseCheckout && rm .git/info/sparse-checkout && git read-tree -mu HEAD`。

> ⚠️ **远程执行 sparse-checkout 命令严禁接管道截断输出**（2026-09-17 agent1 事故）：
>
> `git sparse-checkout set ... 2>&1 | head -20` 中 head 提前关闭管道，SIGPIPE 把 git 进程杀在 apply 中途——`core.sparseCheckout=true` 已写入但规则文件未落盘，后续任何 sparse 应用按「空规则」执行，把工作区已跟踪文件成片删除（agent1 曾波及 src/configs/deploy/skills）。另外注意：
>
> - 迁移当天 root 跑容器留下的 **root 属主文件会阻断 unlink**，sparse 应用后工作区残留半删目录，需 `sudo rm -rf` 残留目录后重新应用（两目录在 docs/system/wechat-mp、tests/unit/* 等处均遇到过）
> - `git read-tree -mu HEAD` 只更新 index 有变化的文件，**不能恢复**已删文件；恢复用 `git checkout -f HEAD`（遵守 sparse 规则、保留 untracked、会覆盖本地未提交修改）
