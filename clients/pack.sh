#!/usr/bin/env bash
# agent-tool-runtime 发货包一键打包脚本（boss CLI 捆绑在内）
#
# 用法（仓库任意目录）：
#   bash clients/pack.sh <版本号> [--skip-smoke]
# 例：
#   bash clients/pack.sh 0.2.15
#
# 固化 2026-09-17 打包事故的教训（每步都在防一个真实踩过的坑）：
# 1. 两处 package.json 版本必须同步升——runtime 用 --install-links 捆绑 boss CLI，
#    是否刷新拷贝「只看版本号」：版本不变会把 node_modules 里删 OCR 之前的旧副本
#    原样打进包（0.2.13 重打包仍 140MB 即此原因），故版本由本脚本统一改
# 2. 打包前必须删 runtime/node_modules/boss-resume-assistant（上一条的同位防御）
# 3. 0.2.14 起包内不得有 ocr-python/rapidocr（简历识别已云端化）——产物硬校验，
#    且包体 >15MB 直接判失败（OCR 环境混入就是 ~140MB，体积是最后的防线）
# 4. scripts/__pycache__ 不得进包；构建前清 dist 防删除源码后的陈旧产物
# 5. 简历管线必需的 4 个 ps1 脚本必须在包内（缺一个客户机简历读取就挂）
# 6. 冒烟：干净目录安装 tgz 后 aid-runtime --help 必须能执行（README §二.3 要求）
set -euo pipefail

VER="${1:-}"
SKIP_SMOKE="${2:-}"

if ! [[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "用法: bash clients/pack.sh <版本号> [--skip-smoke]   例: bash clients/pack.sh 0.2.15"
    exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
BOSS="$ROOT/boss-resume-assistant"
RUNTIME="$ROOT/agent-tool-runtime"
RELEASE="$ROOT/release"
TGZ="$RUNTIME/agent-tool-runtime-$VER.tgz"

command -v node >/dev/null && command -v npm >/dev/null || { echo "需要 node/npm"; exit 1; }
[ -d "$BOSS" ] && [ -d "$RUNTIME" ] || { echo "目录不对：请在 clients/ 仓库里运行"; exit 1; }

echo "[1/7] 两处 package.json 版本 → $VER（同步 lockfile）"
# 注意：node -e 里不能用 MSYS 风格绝对路径（/c/... 会被当成 C:\c\...），cd 进目录用相对路径
(cd "$BOSS" && node -e "
const fs = require('fs');
const j = JSON.parse(fs.readFileSync('package.json', 'utf8'));
j.version = '$VER';
fs.writeFileSync('package.json', JSON.stringify(j, null, 2) + '\n');
console.log('  boss package.json 已更新');
")
(cd "$RUNTIME" && node -e "
const fs = require('fs');
const j = JSON.parse(fs.readFileSync('package.json', 'utf8'));
j.version = '$VER';
fs.writeFileSync('package.json', JSON.stringify(j, null, 2) + '\n');
console.log('  runtime package.json 已更新');
")
(cd "$BOSS" && npm install --package-lock-only >/dev/null 2>&1)
(cd "$RUNTIME" && npm install --package-lock-only >/dev/null 2>&1)

echo "[2/7] 清理：stale 捆绑拷贝 / __pycache__ / 旧 dist"
rm -rf "$RUNTIME/node_modules/boss-resume-assistant"
rm -rf "$BOSS/scripts/__pycache__"
rm -rf "$BOSS/dist" "$RUNTIME/dist"
rm -f "$TGZ"

echo "[3/7] npm pack（prepack 自动：构建 boss CLI → 构建 runtime → install-links 捆绑）"
(cd "$RUNTIME" && npm pack >/dev/null 2>&1)
[ -f "$TGZ" ] || { echo "打包失败：未产出 $TGZ"; exit 1; }

echo "[4/7] 产物校验"
SIZE=$(wc -c < "$TGZ")
if [ "$SIZE" -gt $((15 * 1024 * 1024)) ]; then
    echo "失败：包体 $((SIZE / 1024 / 1024))MB 超过 15MB——大概率 OCR 环境混入（正常 ~3.4MB）"
    exit 1
fi
# 文件清单一次取全再本地校验：pipefail 下「tar | grep -q」会因 grep 提前退出给 tar
# 送 SIGPIPE（141），把存在的文件误判成缺失
LIST=$(tar -tzf "$TGZ")
OCR_HITS=$(grep -ci "ocr-python\|rapidocr" <<<"$LIST" || true)
[ "$OCR_HITS" -eq 0 ] || { echo "失败：包内发现 OCR 环境残留 $OCR_HITS 处"; exit 1; }
BUNDLED_VER=$(tar -xzOf "$TGZ" package/node_modules/boss-resume-assistant/package.json 2>/dev/null \
    | grep -o '"version": *"[^"]*"' | head -1 | grep -o '[0-9][^"]*')
[ "$BUNDLED_VER" = "$VER" ] || { echo "失败：捆绑 boss CLI 版本 $BUNDLED_VER ≠ $VER（stale 拷贝）"; exit 1; }
for f in cv-stitch.ps1 cv-wheel.ps1 cv-segdiff.ps1 win-click.ps1; do
    grep -q "boss-resume-assistant/scripts/$f" <<<"$LIST" \
        || { echo "失败：缺少简历管线必需脚本 $f"; exit 1; }
done
grep -q "boss-resume-assistant/dist/src/cli/index.js" <<<"$LIST" \
    || { echo "失败：缺少 boss CLI 入口"; exit 1; }
grep -q "dist/src/cli.js" <<<"$LIST" || { echo "失败：缺少 runtime 入口"; exit 1; }
echo "  体积 $((SIZE / 1024 / 1024))MB、无 OCR 残留、捆绑 boss@$BUNDLED_VER、必需脚本齐全"

echo "[5/7] 冒烟（干净目录安装 + 双 CLI 可执行）"
if [ "$SKIP_SMOKE" = "--skip-smoke" ]; then
    echo "  跳过（--skip-smoke；发布前请自行补做，README §二.3）"
else
    SMOKE=$(mktemp -d)
    (cd "$SMOKE" && npm init -y >/dev/null 2>&1 && npm install "$TGZ" >/dev/null 2>&1)
    node "$SMOKE/node_modules/agent-tool-runtime/dist/src/cli.js" --help >/dev/null
    node "$SMOKE/node_modules/agent-tool-runtime/node_modules/boss-resume-assistant/dist/src/cli/index.js" --help >/dev/null
    rm -rf "$SMOKE"
    echo "  aid-runtime / boss CLI 均可执行"
fi

echo "[6/7] 产物移入 clients/release/"
mv "$TGZ" "$RELEASE/"

echo "[7/7] VERSION.txt"
STAMP=$(date +%Y-%m-%d)
python - "$VER" "$STAMP" "$RELEASE/VERSION.txt" <<'PYEOF'
import io, sys, os
ver, stamp, path = sys.argv[1], sys.argv[2], sys.argv[3]
entry = (
    f"agent-tool-runtime {ver}（内含 boss-cli {ver}）\n"
    f"构建日期：{stamp}\n\n"
    f"{ver} 相对上一版本的变化\n（发布前请把本行替换为实际变更说明）\n\n"
)
s = io.open(path, encoding="utf-8").read() if os.path.exists(path) else ""
io.open(path, "w", encoding="utf-8").write(entry + s)
print(f"  已插入 {ver} 占位条目（发布前补充实际变更说明）")
PYEOF

echo ""
echo "=== 打包完成: clients/release/agent-tool-runtime-$VER.tgz ($((SIZE / 1024 / 1024))MB) ==="
echo "发布前还差两步（人工）："
echo "  1. 在 clients/release/VERSION.txt 顶部条目补充本版变更说明（有占位行提醒）"
echo "  2. git 提交：两个 package.json + 两个 lockfile + release/VERSION.txt"
echo "服务端部署提醒：客户端 0.2.14 起简历识别走云端，服务端需先部署并配置 ZHIPU_API_KEYS"
