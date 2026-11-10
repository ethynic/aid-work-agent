#!/usr/bin/env bash
# 逐文件限时回归：绕开已知挂死文件（mcp-conformance / mcp-server 派生 MCP 子进程不退出）。
# 为什么不用 npm test：run-tests.mjs 一次 node --test 收集全部测试文件，上述两文件挂死会
# 卡死整个进程且无任何输出——开发会话多次因此整段阻塞（2026-11-10 等）。见 AGENTS.md §5。
#
# 用法：
#   bash scripts/test-bounded.sh               # 全量（默认排除挂死文件，8 并行）
#   bash scripts/test-bounded.sh resume timing # 按文件名子串过滤（不区分大小写）
# 退出码：有失败文件时为 1。基线预存失败清单见 AGENTS.md §5，不计入新改动的失败。
set -u
cd "$(dirname "$0")/.."
npx tsc -p tsconfig.main.json || exit 1
cd dist/tests
pattern="${*:-.}"
tmp=$(mktemp)
ls *.test.js | grep -Ev '^(mcp-conformance|mcp-server)\.test\.js$' \
  | grep -iE "$pattern" \
  | xargs -P 8 -I{} sh -c "timeout 90 node --test {} >/dev/null 2>&1 || echo '{}' >> '$tmp'"
echo "=== test-bounded 完成；FAIL 文件如下（基线预存失败见 AGENTS.md §5）==="
cat "$tmp" 2>/dev/null
n=$(wc -l < "$tmp" 2>/dev/null || echo 0)
rm -f "$tmp"
exit $(( n > 0 ? 1 : 0 ))
