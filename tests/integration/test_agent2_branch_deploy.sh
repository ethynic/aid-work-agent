#!/bin/bash
# Real local Git integration; only a rewritten temporary script is run, never the live deploy script.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
REAL_GIT=$(command -v git)
TEST_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_ROOT"' EXIT
export GIT_CONFIG_GLOBAL="$TEST_ROOT/gitconfig" GIT_CONFIG_NOSYSTEM=1
export GIT_TERMINAL_PROMPT=0
mkdir -p "$TEST_ROOT/bin" "$TEST_ROOT/home"
export HOME="$TEST_ROOT/home"
export REAL_GIT
export TEST_WORKDIR="$TEST_ROOT/work"
cat > "$TEST_ROOT/bin/git" <<'SH'
#!/bin/bash
printf 'git %s\n' "$*" >> "$ACTION_LOG"
case "$1" in
    config) exit 0 ;;
    fetch) [ "${FAIL_FETCH:-0}" != 1 ] || exit 71 ;;
    checkout) [ "${FAIL_CHECKOUT:-0}" != 1 ] || exit 72 ;;
    status) [ "${FAIL_STATUS:-0}" != 1 ] || exit 74 ;;
esac
exec "$REAL_GIT" "$@"
SH
cat > "$TEST_ROOT/bin/id" <<'SH'
#!/bin/bash
printf 'id\n' >> "$ACTION_LOG"
echo "${TEST_UID:-1000}"
SH
cat > "$TEST_ROOT/bin/sudo" <<'SH'
#!/bin/bash
printf 'sudo %s\n' "$*" >> "$ACTION_LOG"
# Credential test is the only permitted sudo action in these scenarios.
case "$1" in
    test) exec "$@" ;;
    chown|chmod) exit 0 ;;
    rm)
        # Only the temporary checkout's dist paths may be deleted.
        [[ "${@: -1}" == "$TEST_WORKDIR/frontend/dist."* ]] || exit 90
        exec "$@"
        ;;
    *) exit 90 ;;
esac
SH
cat > "$TEST_ROOT/bin/stat" <<'SH'
#!/bin/bash
echo ubuntu
SH
cat > "$TEST_ROOT/bin/find" <<'SH'
#!/bin/bash
printf 'find %s\n' "$*" >> "$ACTION_LOG"
SH
cat > "$TEST_ROOT/bin/docker" <<'SH'
#!/bin/bash
printf 'docker %s\n' "$*" >> "$ACTION_LOG"
# Stop at the first deploy boundary. No Docker daemon or services are accessed.
if [ "${SUCCESS:-0}" = 1 ]; then
    if [[ "$*" == *"npx vite build"* ]]; then
        mkdir -p "$TEST_WORKDIR/frontend/dist.new"
    fi
    exit 0
fi
exit 73
SH
chmod +x "$TEST_ROOT/bin/"*
export PATH="$TEST_ROOT/bin:$PATH"
export ACTION_LOG="$TEST_ROOT/actions"
# Seed a bare remote and deployment checkout without any external network.
"$REAL_GIT" init -q --bare "$TEST_ROOT/remote.git"
"$REAL_GIT" init -q -b master "$TEST_ROOT/seed"
"$REAL_GIT" -C "$TEST_ROOT/seed" config user.email local-test@example.invalid
"$REAL_GIT" -C "$TEST_ROOT/seed" config user.name 'Local integration test'
mkdir -p "$TEST_ROOT/seed/deploy"
# sed only rewrites environment-specific paths in an isolated copy.
sed -e "s|/var/www/agent2|$TEST_ROOT/work|g" \
    -e "s|/home/ubuntu/.git-credentials|$TEST_ROOT/creds|g" \
    -e "s|/root/.git-credentials.bak|$TEST_ROOT/creds.bak|g" \
    "$ROOT/deploy/agent2_update.sh" > "$TEST_ROOT/seed/deploy/agent2_update.sh"
printf 'master\n' > "$TEST_ROOT/seed/payload"
"$REAL_GIT" -C "$TEST_ROOT/seed" add .
"$REAL_GIT" -C "$TEST_ROOT/seed" commit -qm master
MASTER_SHA=$("$REAL_GIT" -C "$TEST_ROOT/seed" rev-parse HEAD)
"$REAL_GIT" -C "$TEST_ROOT/seed" checkout -qb feature/monitor
printf 'feature\n' > "$TEST_ROOT/seed/payload"
# Different on-disk script size/content catches Bash reading a replaced script mid-execution.
printf '# replaced script\nexit 99\n' > "$TEST_ROOT/seed/deploy/agent2_update.sh"
printf 'tracked remotely\n' > "$TEST_ROOT/seed/collision"
"$REAL_GIT" -C "$TEST_ROOT/seed" add .
"$REAL_GIT" -C "$TEST_ROOT/seed" commit -qm feature
FEATURE_SHA=$("$REAL_GIT" -C "$TEST_ROOT/seed" rev-parse HEAD)
"$REAL_GIT" -C "$TEST_ROOT/seed" remote add origin "$TEST_ROOT/remote.git"
"$REAL_GIT" -C "$TEST_ROOT/seed" push -q origin master feature/monitor
"$REAL_GIT" clone -q "$TEST_ROOT/remote.git" "$TEST_ROOT/work"
printf 'https://local-test@codeup.aliyun.com\n' > "$TEST_ROOT/creds"
cp "$TEST_ROOT/creds" "$TEST_ROOT/creds.bak"
# Standalone launcher for failure scenarios and default deployment from detached HEAD.
cp "$TEST_ROOT/work/deploy/agent2_update.sh" "$TEST_ROOT/launcher.sh"
passed=0
run_case() {
    local expected=$1
    shift
    : > "$ACTION_LOG"
    set +e
    bash "$LAUNCHER" "$@" > "$TEST_ROOT/output" 2>&1
    result=$?
    set -e
    if [ "$result" -ne "$expected" ]; then
        cat "$TEST_ROOT/output"
        echo "Expected $expected, got $result: $*" >&2
        exit 1
    fi
    if [ "$expected" = 73 ] || [ "${SUCCESS:-0}" = 1 ]; then
        grep -q '^docker ' "$ACTION_LOG"
    else
        ! grep -Eq '^(docker|find) ' "$ACTION_LOG"
    fi
    passed=$((passed + 1))
}
assert_head() {
    [ "$("$REAL_GIT" -C "$TEST_ROOT/work" rev-parse HEAD)" = "$1" ]
    # Every switch must leave the original local master pointer intact.
    [ "$("$REAL_GIT" -C "$TEST_ROOT/work" rev-parse refs/heads/master)" = "$MASTER_SHA" ]
    grep -Fq "发布提交: $1" "$TEST_ROOT/output"
}
LAUNCHER="$TEST_ROOT/launcher.sh"
run_case 0 --help
! grep -Eq '^(id|sudo|git config|git fetch) ' "$ACTION_LOG"
TEST_UID=0 run_case 0 --help
for invalid in '' '..' 'refs/heads/master' 'feature bad' 'x;touch bad' '@{-1}'; do
    run_case 1 --branch "$invalid"
    ! grep -Eq '^(id|sudo|git config|git fetch) ' "$ACTION_LOG"
done
run_case 1 --branch
run_case 1 --branch --help
run_case 1 --branch master --branch master
run_case 1 --unknown
run_case 73
assert_head "$MASTER_SHA"
# Execute the actual path checkout will replace, rather than a stationary launcher.
LAUNCHER="$TEST_ROOT/work/deploy/agent2_update.sh"
run_case 73 --branch feature/monitor
assert_head "$FEATURE_SHA"
grep -Fq '发布分支: origin/feature/monitor' "$TEST_ROOT/output"
LAUNCHER="$TEST_ROOT/launcher.sh"
run_case 73
assert_head "$MASTER_SHA"
printf 'local edit\n' >> "$TEST_ROOT/work/payload"
run_case 1 --branch feature/monitor
grep -q 'local edit' "$TEST_ROOT/work/payload"
! grep -q '^git fetch ' "$ACTION_LOG"
"$REAL_GIT" -C "$TEST_ROOT/work" restore payload
printf 'preserve me\n' > "$TEST_ROOT/work/untracked"
run_case 1 --branch feature/monitor
[ -f "$TEST_ROOT/work/untracked" ]
rm "$TEST_ROOT/work/untracked"
# An ignored file must also survive a colliding target checkout.
printf 'collision\n' > "$TEST_ROOT/work/.git/info/exclude"
printf 'ignored local content\n' > "$TEST_ROOT/work/collision"
run_case 1 --branch feature/monitor
grep -q 'ignored local content' "$TEST_ROOT/work/collision"
rm "$TEST_ROOT/work/collision"
FAIL_FETCH=1 run_case 1 --branch feature/monitor
FAIL_CHECKOUT=1 run_case 1 --branch feature/monitor
FAIL_STATUS=1 run_case 1 --branch feature/monitor
run_case 1 --branch missing/branch
# Full successful return must not resume reading arbitrary trailing replacement bytes.
"$REAL_GIT" -C "$TEST_ROOT/seed" checkout -qb feature/long-script
{
    printf '# replacement file\n'
    for i in {1..1000}; do printf '# long replacement padding\n'; done
    printf 'touch "$TEST_WORKDIR/UNEXPECTED_TRAILING_EXECUTION"\n'
} > "$TEST_ROOT/seed/deploy/agent2_update.sh"
"$REAL_GIT" -C "$TEST_ROOT/seed" add .
"$REAL_GIT" -C "$TEST_ROOT/seed" commit -qm long-script
"$REAL_GIT" -C "$TEST_ROOT/seed" push -q origin feature/long-script
mkdir -p "$TEST_ROOT/work/frontend/dist" \
    "$TEST_ROOT/work/src/tools/ppt/renderer-node/dist" \
    "$TEST_ROOT/work/src/tools/ppt/renderer-node/src" \
    "$TEST_ROOT/work/src/tools/ppt/renderer-node/node_modules/pptxgenjs"
printf 'lock\n' > "$TEST_ROOT/work/src/tools/ppt/renderer-node/package-lock.json"
printf 'renderer\n' > "$TEST_ROOT/work/src/tools/ppt/renderer-node/dist/render.js"
# Keep test build artifacts ignored, matching a deployed checkout.
printf 'frontend/\nsrc/\nlog/\n' >> "$TEST_ROOT/work/.git/info/exclude"
cp "$TEST_ROOT/launcher.sh" "$TEST_ROOT/work/deploy/agent2_update.sh"
# The launcher copy is identical to the current master version.
LAUNCHER="$TEST_ROOT/work/deploy/agent2_update.sh"
SUCCESS=1 run_case 0 --branch feature/long-script
[ ! -e "$TEST_ROOT/work/UNEXPECTED_TRAILING_EXECUTION" ]
grep -q '更新完成' "$TEST_ROOT/output"
LAUNCHER="$TEST_ROOT/launcher.sh"
run_case 73
assert_head "$MASTER_SHA"
# A stale tracking ref must never allow publishing a branch deleted upstream.
"$REAL_GIT" -C "$TEST_ROOT/seed" push -q origin --delete feature/monitor
"$REAL_GIT" -C "$TEST_ROOT/work" show-ref --verify --quiet refs/remotes/origin/feature/monitor
run_case 1 --branch feature/monitor
[ "$("$REAL_GIT" -C "$TEST_ROOT/work" rev-parse HEAD)" = "$MASTER_SHA" ]
echo "PASS: $passed isolated branch deployment scenarios (real local Git, no deploy/network)."
