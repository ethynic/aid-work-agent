# wecom_personal_rpa 服务端拉取会话存档 — 问题交接文档

> **创建时间**：2026-07-09
> **当前状态**：❌ 解密链路未走通，需要新 AI 接手修复
> **接手指引**：读完本节"当前未解决问题"即可直接干活，前面章节是背景。

---

## 0. 当前未解决问题（接手必读）

### 0.1 现象

agent2 部署最新代码（commit `f1b765c`）后，日志只有 poller 触发，**没有 fetcher 的"拉取完成"日志、没有"解密失败"日志、没有任何异常**。poller 每分钟触发 1 次租户拉取，但 fetcher 像静默消失了一样。

`channel_messages` 表查不到任何 `tenant_9eb3e45cab83` 的 wecom_personal_rpa 消息（0 条）。

### 0.2 最可能根因（强烈怀疑）

**SDK `DecryptData` 在子进程里挂起，导致 `asyncio.to_thread(wecom_finance_sdk.decrypt_data_raw, ...)` 永远不返回**。

证据链：
1. fetcher 拿到 Redis 锁 → 进 `_fetch_once_internal`
2. 拉到 seq=3 那条历史密文（`encrypt_chat_msg` 长度 393 = 4n+1，物理上无法 base64 解码）
3. 调 `wecom_finance_sdk.decrypt_data_raw(random_key_bytes, encrypt_chat_msg)` → 走子进程池 → 调 C SDK `DecryptData`
4. **C SDK 遇到这个 4n+1 密文不会返回错误，而是挂起或死循环**
5. `asyncio.to_thread` 永远拿不到返回值 → fetcher 卡住
6. `_fetch_timeout_seconds = 30` 的外层 `asyncio.wait_for` **可能也拦不住**（asyncio 取消不会真的杀掉同步阻塞的线程）
7. Redis 锁（TTL 60s）一直占着 → 下一轮 poller 拿不到锁 → 跳过 → 日志只看到 poller

### 0.3 已部分修复（commit 未提交）

在 `fetcher.py` 加了**单条消息超时**（`_SINGLE_ITEM_TIMEOUT_SECONDS = 10`），用 `asyncio.wait_for(self._decrypt_one(...), timeout=10)` 包装解密调用。超时走原来的"跳过该条 + 推进 seq"分支。

**但这个修复有限**：
- `asyncio.wait_for` 取消 await 后，**底层 `to_thread` 里的 SDK 调用仍在跑**（Python 线程不能被强制 kill）
- 子进程池（`multiprocessing.Pool size=1`）被这个挂起的调用占住，**下次解密还是没池可用**
- 多条坏消息累计后，整个租户的会话存档功能彻底卡死

### 0.4 接手 AI 需要解决的问题

按优先级：

**P0：让 SDK 卡死不要拖死整个 fetcher**
- 方案 A（推荐）：给 `wecom_finance_sdk.decrypt_data_raw`（子进程池代理层）加**子进程级超时**，用 `pool.apply_async` + `task.get(timeout=N)`。超时后 `_reset_pool()` 强杀子进程池重建。
- 方案 B：每条消息用一个**临时 spawn 子进程**（`multiprocessing.Process`），用 `process.join(timeout=N)`，超时 `process.terminate()`。比池方案性能差但隔离更干净。
- 方案 C：在 `_sdk_inner.decrypt_data_raw` 里用 `signal.alarm` 给 SDK 调用设 SIGALRM 超时（Linux only，且 ctypes 调用可能拦不住信号）。

**P1：诊断"为什么 fetcher 日志完全消失"**
- 当前假设是 SDK 挂起。但也可能是别的：Redis 锁泄漏、asyncio 事件循环阻塞、gunicorn worker 卡死。
- 验证方法：在 fetcher 关键节点加 `logger.info` 打点，确认卡在哪一步。
  - `_fetch_once_internal` 入口
  - `get_chat_data` 返回后（拉到几条）
  - 进入循环前
  - 每条 `for item in batch.items` 入口
  - `_decrypt_one` 调用前
  - `_decrypt_one` 返回后
  - `_process_inbound_message` 调用后

**P2：seq=3 这条 4n+1 密文怎么处理**
- 真机数据：`encrypt_chat_msg` 长度 393，无 `=` padding，物理上无法 base64 解码。
- 怀疑：之前用 Python 手工 AES 时遇到的 4n+1 错误，**改成 SDK DecryptData 后还是同样的密文**。SDK 自己应该处理这个，但实测 SDK 挂起（？）或返回错误（？）。
- 短期：单条失败超时跳过 + 推进 seq（已实现）
- 长期：搞清楚 SDK 对这种密文到底返回什么，或者企微后台是不是真的下发了坏数据。

### 0.5 接手 AI 第一步建议

```bash
# 1. 在 agent2 上确认当前状态
ssh agent2
docker logs --since 30m aid-agent-api2 2>&1 | grep -E "fetcher|archive|DecryptData" | tail -50
# 如果还是没有 fetcher 日志，说明确实卡死

# 2. 看 Redis 锁状态（如果锁还在 = 上次 fetcher 没正常退出）
docker exec aid-agent-api2 python -c "
from src.core.redis_client import redis_client
keys = redis_client.client.keys('wecom_rpa:archive:lock:*')
print('锁:', keys)
for k in keys:
    print(f'  {k}: TTL={redis_client.client.ttl(k)}')
"

# 3. 看 last_seq / last_error
docker exec aid-agent-api2 python -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT config_id, last_seq, last_fetch_at, last_error_at, LEFT(last_error_msg, 300) FROM tenant_channel_configs WHERE config_id = 'chan_616337ad19f6'\")
for r in cur.fetchall(): print(r)
"
```

根据这三条命令的输出，**再决定怎么改**，不要盲改。

---

## 1. 功能背景：这个功能要干嘛

### 1.1 业务定位

企业微信「会话内容存档」功能：拉取员工/客户在企微里的聊天记录（文本/图片/语音/视频），用于合规审计、客户跟进、AI 智能回复。

### 1.2 系统架构（服务端拉取模式）

```
┌────────────────┐  ①回调通知   ┌─────────────────┐
│ 企业微信服务器  │ ──────────→ │ 我们的服务端     │
│                │             │ (aid-agent-api2)│
│                │             │                 │
│                │  ②拉取密文   │  callback_handler│
│                │ ←────────── │  ↓               │
│                │             │  fetcher         │
│                │  ③返回密文   │  ↓               │
│                │ ──────────→ │  C SDK 解密      │
│                │             │  ↓               │
│                │             │  _process_inbound│
│                │             │  ↓               │
│                │             │  agent 主循环    │
└────────────────┘             └─────────────────┘
```

**关键文件**（都在 `src/channels/wecom_personal_rpa/archive/`）：

| 文件 | 职责 |
|------|------|
| `poller.py` | 60s 兜底轮询，扫描所有 verified 配置，触发 fetcher |
| `callback_handler.py` | 接收企微回调事件，触发 fetcher |
| `fetcher.py` | **核心**：拉密文 → 解密 → 入库 channel_messages |
| `verifier.py` | 配置凭证验证（verify 接口） |
| `chat_crypto.py` | RSA-PKCS1v15 解密 encrypt_random_key |
| `wecom_finance_sdk.py` | SDK 主进程代理层（子进程池） |
| `_sdk_inner.py` | SDK 子进程实现层（ctypes 加载 .so） |
| `http_client.py` | 调 SDK GetChatData（也走子进程） |

### 1.3 解密流程（企微官方规范）

```
encrypt_random_key (base64)
  ↓ RSA-PKCS1v15 解密（用数据库存的私钥）
random_key (32 bytes)
  ↓
  ↓ 连同 encrypt_chat_msg 一起传给 SDK DecryptData
  ↓
SDK DecryptData(random_key, encrypt_chat_msg)
  ↓ SDK 内部：base64 decode + AES-256-CBC + PKCS7
明文 JSON 字符串
```

**绝对不要用 Python 自己实现 AES 解密**（曾经试过，遇到 SDK 返回的 4n+1 长度密文彻底卡死）。SDK 内部对边界情况有容错。

### 1.4 数据库

- **租户配置表**：`tenant_channel_configs`
  - 字段：`config_id`、`tenant_id`、`channel_type`、`config`（JSON 含 5 个加密凭证）、`last_seq`、`last_fetch_at`、`last_error_at`、`last_error_msg`、`verified`
- **消息入库表**：`channel_messages`
  - 渠道消息统一表，wecom_personal_rpa 的消息按 `session_id LIKE '%wecom_personal_rpa%'` 过滤

### 1.5 关键凭证（测试租户）

```
DATABASE_URL: postgresql://aid_user:Aid_2026@124.222.3.254:5433/aid_work_agent2
chan_id: chan_616337ad19f6
tenant_id: tenant_9eb3e45cab83
corp_id: ww2ed7298c926e081c
```

### 1.6 服务器

- agent2.aidingyi.cn → 124.222.3.254
- 容器：`aid-agent-api2`，宿主机 8001 → 容器 8000
- gunicorn workers=3，每个 worker 跑一份 poller（Redis 锁防并发解密）
- 部署方式：`git pull && docker restart aid-agent-api2`

---

## 2. 已完成的修复（按时间顺序）

### 2.1 SDK 子进程隔离（commit `c5ee158`）

**问题**：gunicorn worker 加载 .so 后 C 堆被破坏，worker 静默退出。

**修复**：把 ctypes 加载整体移到 spawn 子进程里（`_sdk_inner.py`），主进程通过 `multiprocessing.Pool(spawn, size=1)` 调用（`wecom_finance_sdk.py`）。

### 2.2 RSA padding PKCS1v15（commit `5791d36`）

**问题**：所有真机密文 RSA 解密失败。

**修复**：从 OAEP-SHA1 改成 PKCS1v15（企微官方要求）。早期代码注释撒谎说"OAEP-SHA1：企微官方规范"，实际相反。

### 2.3 fetcher 单条失败不死循环（commit `1454a59`）

**问题**：fetcher 单条解密失败时 `break` 不推进 seq，每分钟重拉同一条又失败，死循环。

**修复**：单条失败也推进 seq + continue。

### 2.4 Base64 padding 容错（commit `d645668`）

**问题**：SDK 偶尔返回的密文缺末尾 `=` padding，Python `base64.b64decode` 严格模式拒绝。

**修复**：新增 `_b64decode_lenient`，自动补 padding + 4n+1 物理不可长度检测。

### 2.5 SDK DecryptData 替代 Python AES（commit `f1b765c`）

**问题**：seq=3 真机密文长度 393（4n+1），物理上无法 base64 解码。Python 手工 AES 彻底无解。

**修复**：弃用 Python AES 实现（`chat_crypto.decrypt_chat_msg` / `decrypt_message` 删除），改用 SDK 官方 `DecryptData` 接口。`encrypt_chat_msg` 的 base64 + AES 全部交给 SDK 内部处理。

**关键改动**：
- `chat_crypto.py`：只保留 `decrypt_random_key`（RSA 解密）
- `fetcher.py` / `verifier.py`：解密路径改为 `RSA 解 random_key → SDK DecryptData`
- `_sdk_inner.decrypt_data_raw`：encrypt_key 接受 `Union[str, bytes]`（真实 random_key 是 UTF-8 字符串，测试模拟的随机字节也可以）

### 2.6 单条消息超时（未提交）

**问题**：见本节 0.1-0.2，SDK 卡死导致 fetcher 静默消失。

**修复**：`fetcher._fetch_once_internal` 给单条解密加 `asyncio.wait_for(timeout=10)`。

**局限**：`asyncio.wait_for` 取消 await 不会真的杀底层 to_thread 线程，子进程池仍被占用。需要更彻底的子进程级超时（见 0.4 P0）。

---

## 3. 关键参考资料

### 3.1 官方文档

- 获取会话内容：https://developer.work.weixin.qq.com/document/path/91774
- 加解密方案：https://developer.work.weixin.qq.com/document/path/96211

### 3.2 业界主流实现（GitHub）

- `oiuv/WeWorkFinanceSdk`（Python 封装 + 行业事实标准）
  - `decrypt.py`：RSA 解密 random_key（用 `.decode('utf-8')`，证明 random_key 是 UTF-8 字符串）
  - `WxChat.py`：调 sdktools 命令行的 SDK DecryptData

### 3.3 项目内设计文档

- `docs/system/wecom-personal-rpa-server-archive-listener-design.md`（主设计）
- `docs/system/wecom-personal-rpa-protocol.md`（协议）
- `docs/system/wecom-personal-rpa-sdk-deploy.md`（SDK 部署）

### 3.4 诊断脚本

- `scripts/check_archive_keys.py`：拉密文 + 验证 RSA padding + 打印密文长度
  - 用法：`docker exec aid-agent-api2 python /app/scripts/check_archive_keys.py chan_616337ad19f6`

---

## 4. 已知非问题（不要浪费时间）

| 现象 | 解释 | 处理 |
|------|------|------|
| 每秒多次"触发 1 个租户拉取"日志 | gunicorn 3 worker 各跑一份 poller | Redis 锁已防并发，日志噪声而已 |
| `test_wecom_callback.py` 4 个失败 | async fixture 缺 `@pytest_asyncio.fixture` 装饰器 | 与本功能无关，预先存在 |
| `multiprocessing.pool.Pool.__del__` warning | 子进程池销毁时的 race | 退出时 noise，无影响 |

---

## 5. 接手 AI 的开发流程约束

按项目规范（`.claude/rules/dev_workflow.md`）：

1. **三智能体流程**（非平凡改动必走）：开发 → 测试 → CodeReview，串行执行
2. **不自动提交**：用户说"提交代码"才提交，提交前 `git fetch` 检查冲突
3. **严格限定暂存范围**：`git add <specific-files>`，不要 `git add -A`（工作区有无关改动）
4. **测试命令**：`./scripts/dev_test.sh <pytest 参数>` 自动探测容器/宿主机环境
5. **import 安全检查**：改完后跑 `python -c "from src.channels.wecom_personal_rpa.archive import fetcher, ..."`

---

## 6. 关联记忆

- 主记忆条目：`C:\Users\PC\.claude\projects\c--repos-aid-work-agent\memory\project_wecom-personal-rpa-server-archive-verification.md`
- 教训记忆（建议接手 AI 也读）：之前两次错误修复都是因为没查官方/业界实现就盲改算法。**遇到企微 SDK 相关问题先查 GitHub 主流实现 + 官方文档**，不要自己琢磨算法边界。
