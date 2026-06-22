# Pandoc 安装与部署指南

> `word_process` 工具的 `md_to_word` 操作（Markdown → Word）依赖 [Pandoc](https://pandoc.org/) 命令行工具。本文档说明各环境的安装与排查方式。
>
> 关联设计文档：[md_to_word 重构计划：改用 Pandoc 引擎](./md_to_word_pandoc_plan.md)

---

## 1. 为什么需要 Pandoc

`word_process` 工具内部通过 `src/tools/word/md_to_word.py` 的 `_pandoc_convert()` 调用 `pandoc` 命令完成转换：

```
md_text → normalize_markdown() → 临时 .md → pandoc subprocess → 临时 .docx → python-docx → CJK 字体后处理
```

Pandoc 由 `md_to_word.py:_find_pandoc()` 定位，查找顺序：

1. `shutil.which("pandoc")` — 系统 PATH
2. Windows 常见路径：`%LOCALAPPDATA%\Pandoc\pandoc.exe`、`C:\Program Files\Pandoc\pandoc.exe`、`C:\Program Files (x86)\Pandoc\pandoc.exe`
3. 都找不到时直接抛 `FileNotFoundError`（Fail loud），错误信息含安装指引链接。该异常被 `word_process_tool.py` 的 `except FileNotFoundError` 捕获，转为 `{"success": False, "error": "未检测到 Pandoc..."}` 返回给 Agent

**典型报错**（Pandoc 未安装时）：

```
未检测到 Pandoc。word_process 工具的 Markdown 转 Word 功能依赖 Pandoc 命令行工具，
请参照《Pandoc 安装与部署指南》(...) 安装后重试。
```

> 旧版本会报 `[WinError 2] 系统找不到指定的文件。`，该错误对用户和 Agent 都不直观、会触发 Agent 反复重试或改调其它工具，现已改为 Fail loud 直接提示缺失依赖。

---

## 2. 各环境状态

| 环境 | 是否已内置 | 说明 |
|------|-----------|------|
| Docker / Linux 部署 | ✅ 是 | `Dockerfile:106` 已 `apt-get install pandoc`，无需额外操作 |
| Windows 本地开发 | ❌ 需手动安装 | 见 §3 |
| macOS 本地开发 | ❌ 需手动安装 | 见 §4 |
| Linux 本地开发（非 Docker） | ❌ 需手动安装 | 见 §5 |

---

## 3. Windows 安装

### 方式一：winget（可正常访问 GitHub 时）

```bash
winget install --id JohnMacFarlane.Pandoc -e --accept-source-agreements --accept-package-agreements
```

安装后**重启终端**使 PATH 生效。

### 方式二：手动下载 portable（国内推荐）

winget 直连 GitHub releases 容易超时报错 `0x80072f78 (InternetOpenUrl failed)`。可改用 GitHub 加速镜像下载 portable 版，放到 `_find_pandoc()` 默认检查的路径（**无需管理员权限、无需改 PATH**）：

```bash
# 1. 下载（任选一个可达的镜像，这里用 gh-proxy）
curl -fL -o "$LOCALAPPDATA/Temp/pandoc.zip" \
  "https://gh-proxy.com/https://github.com/jgm/pandoc/releases/download/3.10/pandoc-3.10-windows-x86_64.zip"

# 2. 解压 pandoc.exe 到 %LOCALAPPDATA%\Pandoc\
mkdir -p "$LOCALAPPDATA/Pandoc"
python -c "import zipfile,os,shutil,glob; \
  src=os.environ['LOCALAPPDATA']+'/Temp/pandoc.zip'; \
  dst=os.environ['LOCALAPPDATA']+'/Pandoc'; \
  tmp=os.environ['LOCALAPPDATA']+'/Temp/pandoc_extract'; \
  shutil.rmtree(tmp,ignore_errors=True); \
  zipfile.ZipFile(src).extractall(tmp); \
  exe=glob.glob(tmp+'/**/pandoc.exe',recursive=True)[0]; \
  shutil.copy(exe,os.path.join(dst,'pandoc.exe')); \
  print('installed')"

# 3. 验证
"$LOCALAPPDATA/Pandoc/pandoc.exe" --version
```

可用的 GitHub 加速镜像（任选其一，依次回退）：

- `https://gh-proxy.com/`
- `https://ghfast.top/`
- `https://mirror.ghproxy.com/`

> 放到 `%LOCALAPPDATA%\Pandoc\pandoc.exe` 后，`_find_pandoc()` 会自动命中，**不需要把目录加进系统 PATH**。

---

## 4. macOS 安装

```bash
brew install pandoc
pandoc --version
```

---

## 5. Linux 安装（非 Docker 本地开发）

```bash
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y pandoc

# RHEL / CentOS / Fedora
sudo dnf install -y pandoc

# Arch
sudo pacman -S pandoc

pandoc --version
```

> 生产部署走 Docker，`Dockerfile` 已内置，不要在服务器上单独安装。

---

## 6. 验证安装

最快验证方式 —— 确认 `_find_pandoc()` 能解析到可执行文件路径（而非回退到字符串 `"pandoc"`）：

```bash
cd c:/repos/aid-work-agent
python -c "from src.tools.word.md_to_word import _find_pandoc; print(_find_pandoc())"
```

预期输出一个真实路径（如 `C:\Users\xxx\AppData\Local\Pandoc\pandoc.exe` 或 `/usr/bin/pandoc`），而不是单纯的 `pandoc`。

端到端验证（跑一次真实转换）：

```bash
python -c "
from src.tools.word.md_to_word import convert
doc = convert('# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n', title='测试')
print('段落数:', len(doc.paragraphs), '表格数:', len(doc.tables))
"
```

能正常打印段落数和表格数即表示整条 `normalize → pandoc → CJK 后处理` 链路通畅。

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `未检测到 Pandoc...`（旧版为 `[WinError 2] 系统找不到指定的文件`） | Pandoc 未安装 / 不在 PATH 与默认检查路径 | 按本文档安装 |
| `winget` 报 `0x80072f78 InternetOpenUrl failed` | 国内直连 GitHub releases 超时 | 改用 §3 方式二的镜像下载 |
| `_find_pandoc()` 返回 `pandoc`（字符串） | 三个查找位置都没命中 | 确认可执行文件路径与 §1 的默认路径一致，或将其加入 PATH |
| `Pandoc conversion failed (exit N)` | Markdown 内容本身导致 pandoc 报错 | 查看 `result.stderr`（已在异常信息中截断保留前 500 字符） |

---

## 8. 维护说明

- 升级 Pandoc：重新执行安装步骤覆盖即可，`_find_pandoc()` 自动识别新版本。
- Docker 部署的 Pandoc 版本由 `Dockerfile` 的 Debian trixie 源决定（当前为 3.x），如需锁定版本，修改 `Dockerfile:106` 的 `pandoc` 为 `pandoc=<版本>`。
