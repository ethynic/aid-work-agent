# 无源码交付私有云——代码保护方案（调研，待实施）

> 状态：📋 调研完成，等有实际客户需求再实施（2026-10-22）。
> 背景：私有云交付原则上不含源代码，但当前部署方式将全部代码放上了客户服务器。
> 前置上下文：[济南诺度私有云部署](../../deploy/济南诺度私有云部署.md)。

---

## 1. 目标与边界

**目标**：私有云交付物中不出现可读的 Python 源码、技能/提示词 Markdown、前端源码；客户持有服务器 root 的前提下，把逆向成本抬高到"需要专业团队投入数周"，而非绝对保密。

**边界（先接受的前提）**：

- 客户拥有宿主机 root，任何技术手段都只提高门槛，不构成绝对保密——法律合同（保密条款）是最后兜底
- Docker 容器**没有**实用的加密机制：镜像即分层 tar（overlayfs），`docker save` 即可拿到全部明文文件；社区无成熟的"加密镜像运行时"
- 交付载体的选择（Docker / Podman / 裸进程 / VM）与代码保密是**正交问题**，换载体不解决保密

## 2. 为什么不建议 git pull 拉取代码（结论：废弃此方式）

git pull 交付与"无源码交付"直接冲突，且比"明文 .py 文件"泄露面更大：

| 泄露面 | 说明 |
|--------|------|
| 全量源码 | 工作区即完整源码，无需任何逆向 |
| 全部 git 历史 | 含已删除的旧代码、历史版本、可能在提交记录中残留的凭据/IP；`git log` 即泄露迭代思路 |
| 拉取自主权 | 客户可随时 pull 到最新版（含未交付范围的功能）、可切任意历史 commit |
| .git 目录即仓库副本 | 即使删除工作区源码只留 .git，`git checkout` 一下即可还原 |

**替代交付形态**：CI/构建机产出"发行物"（加密/编译后的镜像），客户侧只执行镜像导入与启动：

```
构建机（我方，持有源码）
  -> 多阶段 Docker build（builder 阶段加密/编译 -> runtime 阶段只拷贝发行物）
  -> docker save 导出镜像 tar
  -> 离线交付（U 盘/网盘/SCP）或我方私有 registry 受控分发
  -> 客户侧 docker load + compose up -d
```

版本升级 = 交付新版本镜像包替换（compose up -d 重建），客户全程不接触源码仓库。镜像 tag 按版本号管理并附变更说明。

## 3. 保护分层方案

### 3.1 分层总表

| 层 | 内容 | 处理方式 | 保护强度 |
|----|------|---------|---------|
| 核心业务逻辑 | `src/core/`、`src/llm/`、`src/services/` | Cython/Nuitka 编译为 C 机器码（.so / 二进制） | 最高（无字节码可反编译） |
| 其余 Python | `src/tools/`、`src/api/`、`src/channels/`、入口脚本等 | PyArmor 混淆加密 | 中（字节码级，可抬高门槛） |
| 前端 | `frontend/` | 构建仅保留 dist，删除源码 | 已定，无争议 |
| 提示词/技能 | `subagents/*/SUBAGENT.md`、`src/skills/*/SKILL.md` | 打包加密为运行时解密资源（见 3.4） | 中 |
| 配置 | `configs/config.yaml`、`.env` | 配置项最小化交付，敏感项注入环境变量 | 低（本身就是交付物） |
| 日志 | 运行日志 | 交付前审查日志级别与内容，避免 prompt/业务片段外泄 | 辅助 |

**注意**：提示词往往比代码更值钱（SUBAGENT.md / SKILL.md 全是明文 Markdown），必须与代码同等级保护，不能只顾 .py。

### 3.2 核心模块编译为 C（Cython / Nuitka）

- **推荐 Cython 按模块编译**：把目录内模块编译为 .so 扩展，保护强度接近原生二进制，构建复杂度可控。全程序 Nuitka（编成单个可执行文件）对大型动态项目风险高，不建议首选
- **本项目已知风险点（实施时逐项验证）**：
  - pydantic 动态模型、SQLAlchemy declarative（依赖源码级装饰器/元类，需实测编译后行为）
  - 工具自动发现 `src/tools/registry.py` 的 `discover_tool_classes()`（动态 import + 类扫描）
  - 技能/子智能体加载（`src/core/skill_loader.py` 读 Markdown 文件、动态执行技能脚本）
  - gunicorn 多 worker 启动链路、`__init__.py` 懒加载 `__getattr__` 模式
- **实施策略**：先拿 `src/core/agent_engine` 单模块试点，验证通过后再扩大；编译不过的模块回退 PyArmor
- 编译产物为 .so，需要与目标机 CPU 架构/glibc 版本匹配——在**与客户服务器同基础镜像**的容器内构建

### 3.3 PyArmor（其余 Python 代码）

- 免费版提供基础混淆（源码 -> 加密字节码 + 运行时扩展解密执行）；RFT 模式、BCC 模式、JIT 等高级模式需付费授权——**实施前先验证免费版对本项目代码量/特性的能力边界**（旧版试用对单脚本大小有限制）
- 对 FastAPI / gunicorn / pydantic 等动态框架兼容性好，一条 `pyarmor gen` 产出发行目录，改造量小
- 如需**硬件绑定**（license 锁 MAC/CPU/硬盘序列号，防镜像被拷走他机运行），需确认该能力在免费版还是付费版——对私有云场景价值大，必要时购买授权
- 注意：PyArmor 运行时解密发生在内存，理论上有内存抓取类攻击面，故核心逻辑仍走编译路线

### 3.4 技能/提示词 Markdown 的处理

Markdown 数据文件 PyArmor 管不到，需单独处理（二选一）：

1. **打包内嵌**：把 SKILL.md / SUBAGENT.md 内容打进加密 Python 包（随 PyArmor 加密），加载器改为从包内读取，磁盘上不落明文
2. **对称加密落盘**：交付前对技能文件加密（AES），运行时由已保护的加载模块解密到内存使用；密钥编译进二进制或由镜像环境变量注入

方案 2 改造面小（只需改 skill_loader / subagent loader 的读取入口），建议先做方案 2。

### 3.5 Dockerfile 多阶段构建骨架

```dockerfile
# ---------- builder 阶段（不交付）----------
FROM python:3.x-slim AS builder
COPY . /src
# 安装 cython/pyarmor，执行加密与编译脚本
RUN python build/protect_build.py \
    --compile src/core src/llm src/services \
    --pyarmor-rest src \
    --encrypt-skills src/skills subagents

# ---------- runtime 阶段（交付物）----------
FROM python:3.x-slim
COPY --from=builder /dist /app     # 只含 .so、加密脚本、加密技能资源
# 无 .py 明文、无 .git、无 frontend 源码
```

构建产物结构验收清单：交付镜像内 `find /app -name "*.py"` 应仅剩 PyArmor 加密产物（无明文源码）、无 `.git`、无技能明文 Markdown、无前端源码。

## 4. 运维与交付影响（提前想清楚）

| 影响点 | 说明 |
|--------|------|
| 排障变难 | 加密/编译后异常堆栈可读性下降（PyArmor 有 obfuscated traceback），需在构建时保留符号映射供我方远程排查 |
| 热修复 | 客户侧不能改代码；小修也需走"我方构建 -> 新镜像包"流程，交付 SLA 要提前约定 |
| 客户自改/二开 | 被技术性阻断，商务上要明确（这也是保护的目的之一） |
| 环境兼容 | .so 产物绑定架构与 glibc，构建镜像基础层需与客户服务器一致；换服务器需重新交付 |
| 构建流水线成本 | 每次发版都要跑加密+编译+验证，需固化为脚本（如 `scripts/build_protected_release.sh`）并跑冒烟测试 |
| 许可证管理 | 若启用硬件绑定 license，需建 license 签发流程与客户机器信息采集表 |

## 5. 不解决什么 / 残余风险

- 客户 root 可 dump 运行中进程内存（PyArmor 层可被内存取证；编译层基本免疫）
- 行为可观测：通过日志、网络抓包、API 行为分析可推断部分逻辑与提示词——日志审查（§3.1）是必要配套
- `.env`、数据库 schema、LLM 供应商账号（若由客户提供则无此问题）本身在客户手里

## 6. 实施清单（有需求时按序执行）

- [ ] 0. 验证 PyArmor 免费版能力边界（代码量、特性支持、license/硬件绑定是否付费项）
- [ ] 1. 搭建受保护构建流水线：`build/protect_build.py`（Cython 编译 + PyArmor + 技能加密）+ 多阶段 Dockerfile
- [ ] 2. `src/core/agent_engine` 单模块 Cython 试点：编译 -> 单测 -> 集成测试 -> gunicorn 启动验证
- [ ] 3. 扩大编译范围到 `src/llm/`、`src/services/`，编译失败的模块回退 PyArmor
- [ ] 4. 技能/提示词加密改造（skill_loader / subagent loader 读取入口，方案 2 优先）
- [ ] 5. 镜像产物验收清单脚本化（无 .py 明文 / 无 .git / 无明文 Markdown / 冒烟测试）
- [ ] 6. 交付流程文档化：镜像 tar 交付 + 版本升级 SOP + license 签发（如启用）
- [ ] 7. 商务配套：合同保密条款 + 交付范围说明（不含源码与二开能力）
