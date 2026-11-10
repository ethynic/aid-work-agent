#!/usr/bin/env bash
# 逐文件限时回归：绕开已知挂死文件（mcp-conformance / mcp-server 派生 MCP 子进程不退出）。
# 为什么不用 npm test：run-tests.mjs 一次 node --test 收集全部测试文件，上述两文件挂死会
# 卡死整个进程且无任何输出——开发会话多次因此整段阻塞（2026-11-10 等）。见 AGENTS.md §5。
#
# 用法：
#   bash scripts/test-bounded.sh               # 全量（默认排除挂死文件，8 并行）
#   bash scripts/test-bounded.sh resume timing # 多关键词按「或」过滤：文件名含任一关键词即选中
# 退出码：0 全部通过；1 有失败文件；2 零个测试被选中（过滤词无匹配——未执行≠通过，按失败处理）。
# 基线预存失败清单见 AGENTS.md §5，不计入新改动的失败。
set -u
cd "$(dirname "$0")/.."
npx tsc -p tsconfig.main.json || exit 1
cd dist/tests

files=$(ls *.test.js | grep -Ev '^(mcp-conformance|mcp-server)\.test\.js$')
if [ "$#" -gt 0 ]; then
  # 每个关键词独立匹配后合并去重（OR），不做整段连续文本匹配（2026-11-10 评审修正）
  matched=""
  for kw in "$@"; do
    m=$(printf '%s\n' "$files" | grep -iE -- "$kw" || true)
    matched=$(printf '%s\n' "$matched" "$m")
  done
  files=$(printf '%s\n' "$matched" | sed '/^$/d' | sort -u)
fi
count=$(printf '%s\n' "$files" | grep -c . || true)
if [ "${count:-0}" -eq 0 ]; then
  echo "test-bounded：0 个测试文件被选中（过滤词: $*）——未执行任何测试，按失败处理" >&2
  exit 2
fi
echo "test-bounded：选中 $count 个测试文件（过滤词: ${*:-无，全量}；默认已排除 mcp-conformance/mcp-server）"
tmp=$(mktemp)
printf '%s\n' "$files" | xargs -P 8 -I{} sh -c "timeout 90 node --test {} >/dev/null 2>&1 || echo '{}' >> '$tmp'"
echo "=== test-bounded 完成；FAIL 文件如下（基线预存失败见 AGENTS.md §5）==="
cat "$tmp" 2>/dev/null
n=$(wc -l < "$tmp" 2>/dev/null || echo 0)
rm -f "$tmp"
exit $(( n > 0 ? 1 : 0 ))
