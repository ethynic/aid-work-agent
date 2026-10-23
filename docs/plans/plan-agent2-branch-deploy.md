# agent2 指定分支发布设计与开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| 设计 | 参数、版本解析与失败边界 | ✅ 完成（2026-10-22） | 本文确定最小改动；只准备能力，不执行部署 |
| 开发 | agent2_update.sh 分支参数与隔离自测 | ✅ 完成（2026-10-22） | 25场景通过；包含脚本被检出覆盖后的完整成功返回 |
| 验证 | 独立测试、独立CR与主控检查 | ✅ 完成（2026-10-22） | 外部独测19项及集成25场景通过，CR无阻断；未部署 |

## 目标与范围

用户要求wecom工作树开发的Runtime/Runner持续任务先从特性分支发布agent2测试，测试通过后再整合master。为此扩展 `deploy/agent2_update.sh`，支持 `--branch <远程分支名>`；不传维持发布origin/master，增加 `--help`。参数必须在任何Git配置、凭据修复、代码写入或Docker动作之前校验；help不要求root/ubuntu身份，不触发任何发布动作。

仅支持origin的分支名，不接收任意shell、远程URL、commit/tag或模糊ref。正确引用带斜杠分支，拒绝缺值、重复/未知参数、非法ref。fetch明确获取目标分支，解析并固定远程commit；不存在/删除、fetch失败、提交不可解析均在构建/重启前退出，不能回退master或使用旧远程缓存。日志标明请求分支及实际SHA，不输出凭据。

复用现有发布代码获取边界，避免改前后端构建、容器/overlay、数据库、凭据修复或资源配置。代码检出须避免将原本地分支指针重置为另一个分支；不得自动stash/清理工作区。若检出存在覆盖风险，应明确失败并由用户处理。新脚本被拉取后仍按本次已解析的目标版本继续；不借本改动增加任意旧版本的兼容承诺。

## 验证与发布

本项为发布链路高风险改动，开发自测后保留独立测试和CR。隔离场景覆盖默认master、feature/斜杠分支、已切换分支后再次默认、远程分支不存在但缓存存在、非法/重复/缺值参数、帮助、fetch/检出失败、dirty保护和SHA输出；失败时不得执行Docker或后续清理。运行bash语法检查及真实临时Git仓库测试，替换部署命令，禁止运行真实服务器脚本。

用户尚未下达部署指令。完成后只提供用法：`bash deploy/agent2_update.sh --branch feature/ask-user-question-card`。真实执行需该分支先提交并推送，且服务器拿到支持参数的新脚本；这些不是本轮的提交/推送或部署授权。agent2测试通过后仍需按用户指示合并master，不自动合并。

### 首次使用与后续发布

服务器旧脚本不识别`--branch`，不能直接用旧脚本发布特性分支，也不要手工覆盖受Git跟踪的脚本（会触发dirty保护）。在目标分支已提交并推送、用户明确授权部署后，以ubuntu用户从远程分支导出独立临时脚本运行。以下仅为操作说明，本轮未执行：

```bash
(
  set -e
  cd /var/www/agent2
  deploy_branch='feature/ask-user-question-card'
  git fetch --no-tags origin "+refs/heads/$deploy_branch:refs/remotes/origin/$deploy_branch"
  script_sha=$(git rev-parse --verify "refs/remotes/origin/$deploy_branch^{commit}")
  launcher=$(mktemp /tmp/agent2-update.XXXXXX.sh)
  trap 'rm -f -- "$launcher"' EXIT
  git show "$script_sha:deploy/agent2_update.sh" > "$launcher"
  bash "$launcher" --branch "$deploy_branch"
)
```

临时脚本仍会再次fetch并固定实际部署SHA，以它输出的“发布提交”为验收版本；发布过程中不要继续推进分支。若首次fetch遇到凭据或权限错误，先按现有运维流程处理，不能通过运行旧发布脚本修复（会同时发布master）。新脚本已经落到服务器检出目录后，后续可直接运行：

```bash
bash deploy/agent2_update.sh --branch feature/ask-user-question-card
# 不传参数仍发布origin/master
bash deploy/agent2_update.sh
```

脚本使用detached HEAD，不移动原本地分支指针；检查版本应以`git rev-parse HEAD`和发布日志为准。遇到未提交/未跟踪文件、目标路径上的ignored文件碰撞或远程分支获取失败时退出，需人工处理后重试，不会自动stash、清理或回退。该能力只选择代码版本，不新增数据库或部署失败自动回滚保证。

若切回的master或其他历史分支尚未包含新脚本，服务器检出目录也会恢复成该版本的旧脚本；下次指定分支发布仍须使用上述临时脚本方式，不能假定新参数永久存在于所有分支。

### 本地验证记录（2026-10-22）

- 开发者自测和独立测试者分别运行`tests/integration/test_agent2_branch_deploy.sh`，25场景通过；真实临时bare origin/clone配合Docker、sudo等替身，不接触真实部署目录。
- 独立测试者另写外部隔离测试，19项通过，覆盖默认/斜杠分支、远程删除残留缓存、参数拒绝、fetch/checkout失败、dirty保护、SHA固定及本地分支指针保持。
- 独立CR通过；发现的脚本自覆盖尾部读取风险已以预解析函数与同一命令组退出修复，并以较长替换文件、完整成功返回及禁止执行新尾部的回归验证。
- 最终脚本SHA256：`F9E62482C084FF8313FFE8A25E719E750B0D4BA168694C9321EA018C8A63C22F`；主控核对最终diff与同版本验证证据，并完成Bash语法、help及差异格式检查。
- 未进行真实Linux/agent2验收、提交、推送或部署，不能据此声称服务已上线。Runner功能仍为设计/计划阶段，见[持续任务计划](plan-agent-runner-durable-sleep.md)。
