/**
 * cli/validate.ts 参数校验层测试（fail-loud，纯函数离线测试）。
 *
 * 背景（2026-08 真机事故）：未知 flag 曾被静默忽略（`greet --help` 按默认参数执行了
 * 真实写动作）、`greet 冯修业` 的姓名被静默丢弃（按非定向全量模式跑掉）。
 * 本文件锁死四类规则：未知 flag 拒绝 / 多余位置参数拒绝 / greet 显式意图二选一 / help 短路。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { parseArgs } from '../src/cli/args.js'
import { parseGreetNames, validateCommandArgs } from '../src/cli/validate.js'

/** 便捷封装：argv → 校验结果（command 取 positional[0]，与 main() 调用方式一致） */
function v(argv: string[]) {
  const args = parseArgs(argv)
  return validateCommandArgs(argv[0], args)
}

// ---------- 未知 flag 拒绝 ----------

test('未知 flag 拒绝：greet --verbose → 错误消息含「不认识的参数 --verbose」与用法提示', () => {
  const r = v(['greet', '--names', '冯修业', '--verbose'])
  assert.equal(r.ok, false)
  if (!r.ok) {
    assert.match(r.message, /不认识的参数 --verbose/)
    assert.match(r.message, /greet --help/)
  }
})

test('未知 flag 拒绝：send-to --dry（拼写错误）→ 拒绝而非静默忽略', () => {
  const r = v(['send-to', '张三', '--message', 'hi', '--dry'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /不认识的参数 --dry/)
})

test('未知 flag 拒绝：无专属 flag 的命令（doctor --all）→ 拒绝', () => {
  const r = v(['doctor', '--all'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /不认识的参数 --all/)
})

test('未知 flag 拒绝：事故复现 greet --help 之外再挂未知 flag（--limit 5 --force）→ 拒绝', () => {
  const r = v(['greet', '--all', '--limit', '5', '--force'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /不认识的参数 --force/)
})

// ---------- 多余位置参数拒绝 ----------

test('位置参数拒绝：greet 冯修业（姓名写在位置参数上）→ 错误消息特别提示 --names/--all', () => {
  const r = v(['greet', '冯修业'])
  assert.equal(r.ok, false)
  if (!r.ok) {
    assert.match(r.message, /冯修业/)
    assert.match(r.message, /greet --names/)
    assert.match(r.message, /--all/)
  }
})

test('位置参数拒绝：send-to 张三 李四（两个姓名）→ 拒绝', () => {
  const r = v(['send-to', '张三', '李四', '--message', 'hi'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /李四/)
})

test('位置参数拒绝：goto 多带一个位置参数 → 拒绝', () => {
  const r = v(['goto', 'recommend', 'extra'])
  assert.equal(r.ok, false)
})

test('位置参数拒绝：0 位置参数命令（reject now）→ 拒绝', () => {
  const r = v(['reject', 'now'])
  assert.equal(r.ok, false)
})

test('位置参数放行：goto/send-to/select-job 恰好 1 个位置参数 → ok', () => {
  assert.equal(v(['goto', 'recommend']).ok, true)
  assert.equal(v(['send-to', '张三', '--message', 'hi', '--dry-run']).ok, true)
  assert.equal(v(['select-job', '前端开发']).ok, true)
})

// ---------- greet 显式意图（--names / --all 二选一） ----------

test('greet 裸命令（不带 --names 也不带 --all）→ 拒绝，消息解释不再默认全量', () => {
  const r = v(['greet'])
  assert.equal(r.ok, false)
  if (!r.ok) {
    assert.match(r.message, /不再默认全量/)
    assert.match(r.message, /--names/)
    assert.match(r.message, /--all/)
  }
})

test('greet --names 不带值 → 拒绝（需要姓名列表）', () => {
  const r = v(['greet', '--names'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /--names 需要姓名列表/)
})

test('greet --names 与 --all 同时给 → 拒绝（互斥）', () => {
  const r = v(['greet', '--all', '--names', '冯修业'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /互斥/)
})

test('greet --names a,,b（空项）→ 拒绝（不静默过滤空项）', () => {
  const r = v(['greet', '--names', 'a,,b'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /空项/)
})

test('greet --names "  "（全空）→ 拒绝', () => {
  const r = v(['greet', '--names', '  '])
  assert.equal(r.ok, false)
})

test('greet --names 4 人 → 拒绝（CLI 提前拦，上限 3 与 operation/MCP 一致）', () => {
  const r = v(['greet', '--names', 'a,b,c,d'])
  assert.equal(r.ok, false)
  if (!r.ok) assert.match(r.message, /最多 3/)
})

test('greet 正常定向：--names 半角逗号 + --limit → ok', () => {
  assert.equal(v(['greet', '--names', '冯修业,李四', '--limit', '5']).ok, true)
})

test('greet 正常定向：--names 全角逗号分隔 → ok（与 filter --education 同款解析）', () => {
  assert.equal(v(['greet', '--names', '冯修业，李四']).ok, true)
})

test('greet 正常全量：--all / --all --limit 20 → ok', () => {
  assert.equal(v(['greet', '--all']).ok, true)
  assert.equal(v(['greet', '--all', '--limit', '20']).ok, true)
})

// ---------- help 短路（必须先于一切拒绝生效） ----------

test('help 短路：greet --help / greet -h → ok（即使裸 greet 缺意图也放行，main 打 USAGE 退出 0）', () => {
  assert.equal(v(['greet', '--help']).ok, true)
  assert.equal(v(['greet', '-h']).ok, true)
})

test('help 短路：greet 冯修业 --help → ok（help 先于位置参数拒绝）', () => {
  assert.equal(v(['greet', '冯修业', '--help']).ok, true)
})

test('help 短路：未知子命令带 --help → ok（main 短路打 USAGE 退出 0）', () => {
  assert.equal(v(['nope', '--help']).ok, true)
})

// ---------- 全局 flag 与未知子命令 ----------

test('全局 flag 放行：cdp-port 任何命令可带', () => {
  assert.equal(v(['greet', '--all', '--cdp-port', '9223']).ok, true)
  assert.equal(v(['doctor', '--cdp-port', '9223']).ok, true)
})

test('未知子命令不在本层报错：交回 main() default 分支（保持既有「未知子命令」文案）', () => {
  assert.equal(v(['nope', '--whatever']).ok, true)
  assert.equal(v([]).ok, true)
})

// ---------- 原型链 key 畸形输入（不抛 TypeError、不当成已登记命令） ----------

test('原型链 key 子命令（__proto__/constructor/toString 等）不当成已登记命令：不抛 TypeError、按未知子命令交回 main default', () => {
  // 修复前 table[key] 命中 Object.prototype 继承成员（非 undefined），flag 循环里
  // cmdFlags.includes(...) 抛 TypeError → exit 1 栈打印而非干净的「未知子命令」exit 2
  for (const cmd of ['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf']) {
    const r = v([cmd, '--limit', '5'])
    assert.equal(r.ok, true, `${cmd} 应与其它未知子命令一致交回 main() default 分支`)
  }
})

test('原型链风格 flag（--__proto__ / --constructor）按未知 flag 拒绝（flags 存 Map，无原型污染）', () => {
  const r1 = v(['greet', '--all', '--__proto__', 'x'])
  assert.equal(r1.ok, false)
  if (!r1.ok) assert.match(r1.message, /--__proto__/)
  const r2 = v(['greet', '--all', '--constructor'])
  assert.equal(r2.ok, false)
  if (!r2.ok) assert.match(r2.message, /--constructor/)
})

// ---------- parseGreetNames（index.ts greet case 取值共用同一函数） ----------

test('parseGreetNames：split → trim，半角/全角逗号都支持', () => {
  assert.deepEqual(parseGreetNames('冯修业, 李四'), { ok: true, names: ['冯修业', '李四'] })
  assert.deepEqual(parseGreetNames('冯修业，李四，王五'), { ok: true, names: ['冯修业', '李四', '王五'] })
})

test('parseGreetNames：空串 / 空项 / 超 3 人 → ok:false', () => {
  assert.equal(parseGreetNames('').ok, false)
  assert.equal(parseGreetNames('a,,b').ok, false)
  assert.equal(parseGreetNames(',a,').ok, false)
  assert.equal(parseGreetNames('a,b,c,d').ok, false)
})
