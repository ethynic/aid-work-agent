# 企业微信本地库解密读取（local-db 通道）技术调研

> 调研日期：2026-10-14　|　状态：结论已被本机全链路验证（单一版本）
> 关联：`clients/wecom-cli`（read-session 现走 UI 滚动 + OCR/模型通道）、`docs/design/wecom/wecom-cli-design.md`
> **集成设计：[wecom-cli 本地库数据通道设计](../design/wecom/wecom-cli-localdb-design.md)（2026-10-14 评审定稿：本地库为读历史/待回复检测/发送校验三能力的主数据源，UI/OCR 转兜底——本文 §1 "read-session 第三通道"建议已被该设计取代）**
> 目标：评估"直接解密企微本地 SQLite 库读取会话记录"作为 wecom-cli 的数据通道，并给出按版本分派的步骤化架构设计

## 1. 结论摘要

- **可行且已验证**：Windows 企业微信 5.0.11.6029 本机全链路打通——定位数据目录 → 静态指纹确认算法 → 只读内存扫描提取主密钥 → 副本逐页解密 → WAL 修复回放 → 会话/消息/联系人全部读出，`integrity_check=ok`，消息内容与客户端 UI 逐条吻合。
- **算法**：wxSQLite3 非遗留 aes128cbc 页加密（每页独立密钥/IV，详见 §3）。密钥 16 字节，登录时服务器下发，仅存在于运行中进程内存，不落盘。
- **与 wecom-cli 现有 read-session 的关系**：现有通道需抢前台窗口、清未读角标、启发式分侧；本地库通道零 UI 副作用、可读全量历史、不依赖窗口状态，但要求**登录态**且数据以本地落库为准（准实时）。两者互补，建议作为 read-session 的第三通道（`channel=local-db`）演进。
- **版本强绑定风险**：算法指纹、密钥内存形态、页布局三者任一随版本变化即失效，必须按"步骤化 + 版本矩阵分派"设计，失效显式化（见 §5）。

## 2. 已验证的技术事实（2026-10-14，本机）

| 事实 | 证据 |
|---|---|
| 数据根：`{文档}\WXWork\{user_id}\`（文档可能被 OneDrive 重定向） | CEF 日志 `browser_process_impl ... cache_path` 行 |
| 敏感库全部加密：`Data\message.db`、`session.db`、`user.db`、`company.db`、`kv.db` 等 | 文件头采样 |
| 库头 16-24 字节为**明文** SQLite 页头 `10 00 02 02 00 40 20 20`；前 8 字节跨库恒定 `b3df88da44b8102f` | 文件头直接观测 |
| WAL 容器头明文标准格式，帧校验和=密文 → 加密在 pager 层 | 校验和复算比对 |
| 算法 = wxSQLite3 非遗留 aes128cbc；每页 `pagekey=MD5(mkey‖pgno_LE‖"sAlT")`、`iv=MD5(LCG(pgno+1))` | 二进制常量（`sAlT`、LCG 52774/40692/3791/2147483399 聚集于 0x399000 区）+ 开源 sqlite3mc `cipher_wxaes128.c` 对照 |
| 主密钥 16 字节在主进程私有可写内存（EVP 上下文），登录时经 `GetPcLocalEncryptKeyRsp` 下发，`DbKeyVerKey` 管版本 | RTTI 符号 `DbKeyManager`/`QueryUserDBKeys` + 内存扫描实测（约 266s 扫 211MB，步长 1） |
| 验证先知：`AES_dec(pagekey(k,1), file[8:16]‖file[24:32]) ⊕ IV(1) == file[16:24]`（页 1 密文重组含坑：`[16:24]` 是明文页头，密文后半截在 `[24:32]`） | 合成加密页自测通过后实扫命中 |
| WAL 解密后必须按明文重算每帧校验和才能回放 | SQLite wal 校验和算法复算 |
| `content` 列为 protobuf 包装的 UTF-8 文本（未做应用层加密） | message_table 采样 |

完整证据链见 `clients/wecom-cli/experiments/local-db-decrypt/EVIDENCE.md`；已验证代码（key 扫描/解密/WAL 重建/消息解析）在同目录，脚注见其 README（experiments/ 被 wecom-cli .gitignore 忽略，属本地开发材料）。

## 3. wecom-cli 现有架构摘要（代码落点必须对齐）

- **分层**：CLI 命令（`src/cli/commands/`）与 MCP tool（`src/mcp/toolDefs.ts`）共用 **operation**（`src/operations/`，`registry.ts` 注册）。operation 永不 reject：返回 `OperationResult{success,code,message,effect,data,retryable,run_id}`，稳定错误码，Host 不解析自然语言。
- **驱动层**：operation → `drivers/ps1/*.ps1`（`DRIVER_JSON` 单行 JSON 进程协议）→ `drivers/py/*.py`（如 chat_ocr.py，stdout 最后一行 `CHATOCR_JSON: {...}`，环境缺失也退出码 0 + error 对象）。
- **Python 环境**：仓库根 `venv\Scripts\python.exe`（打包态为 `ocr-python\python.exe`），缺失 → `CONFIG_MISSING`。
- **探测模式**：`src/platform/environment.ts` 严格只读、best-effort、可注入替身（platform/sessionName/execFileFn），`doctor` 与 `wecom_probe` 共用。
- **现有 read-session**：UI 滚动抓取 + 模型通道/OCR 兜底（M10b 双通道先例：`data.channel="model"|"ocr"`）——本地库通道作为第三通道有直接先例可循。

## 4. 步骤化设计与版本分派（核心）

流水线 8 步，每步一个稳定接口 + 一个或多个**按版本分派的实现**。环境探测（步骤 1-3）决定每步走哪段代码；版本无关步骤全局复用，版本相关步骤的实现随矩阵条目分派，缺失即显式报"需重新逆向"。

| 步骤 | 接口 | 版本敏感度 | 实现落点（wecom-cli 内） | 分派依据 |
|---|---|---|---|---|
| S1 版本探测 | `detectInstall(): {version, installDir, upgradeSignal}` | 无关（产出驱动后续分派） | `src/platform/` 新增只读探测（exe 元数据 + 安装目录版本子目录 + upgrade 信号），模式对齐 environment.ts | — |
| S2 数据目录定位 | `locateDataDir(): {root, userId, dbPaths}` | 低（路径模式参数化） | 同上平台层；回退链：CEF 日志 → `Documents\WXWork` → OneDrive 重定向 | 矩阵 `data_dir_pattern` |
| S3 指纹预检 | `fingerprint(db): {magic, plaintextHeader, walMode}` | 检查器无关，指纹值来自矩阵 | TS 读库头比对矩阵 `db_magic`；可选二进制常量指纹（`sAlT`+LCG） | 矩阵逐字段比对 |
| S4 密钥提取 | `scanKey(page1, strategy): hex` | **高**（cipher/密钥形态/内存特征） | `drivers/py/wecom_keyscan.py`（pycryptodome；PID 由 TS/PS 传入以省 psutil 依赖），按矩阵 `scan_strategy` 分派 | `scan_strategy: dual-md5-sha256-oracle`（现有实现） |
| S5 快照复制 | 复制 db+wal 到工作区 | 无关 | 通用复制 + integrity 兜底重试 | — |
| S6 逐页解密+WAL 修复 | `decrypt(snapshot, mkey, algorithm)` | **高**（页布局/派生函数） | `drivers/py/wecom_decrypt.py`，按矩阵 `algorithm` 分派 codec（当前 `wxaes128cbc-nonlegacy`；future: legacy/sqlcipher4/…） | `algorithm` |
| S7 解析导出 | `parse(decrypted): {sessions, messages, users}` | 中（表名/protobuf 结构可能变） | `drivers/py/wecom_dbparse.py`，schema 常量表按版本分派；消息方向/类型映射可先做采样标注 | `schema_profile` |
| S8 矩阵维护 | version → 方案参数 → status | 无关（基础设施） | 矩阵 JSON（schema 见 §5）+ validate 子命令；status ∈ validated/stale/unknown/broken | — |

**失效语义**：S3/S4/S6 任一失败 → 矩阵置 `broken` + 结构化失败点（指纹变 / key 扫不到 / 解密校验败），operation 返回新稳定错误码（建议 `VERSION_UNSUPPORTED`；key 扫不到且进程在运行 → 独立码，区别于 `NOT_LOGGED_IN`），**禁止静默降级为乱码结果**。

## 5. 版本有效性矩阵（数据结构）

```json
{
  "schema_version": 1,
  "entries": {
    "5.0.11.6029": {
      "status": "validated",
      "verified_date": "2026-10-14",
      "algorithm": "wxaes128cbc-nonlegacy",
      "page_size": 4096,
      "db_magic": "b3df88da44b8102f",
      "key_form": "raw16-in-private-rw",
      "scan_strategy": "dual-md5-sha256-oracle",
      "data_dir_pattern": "{documents}/WXWork/{user_id}",
      "schema_profile": "wecom-5.0.x"
    }
  }
}
```

复检分级（便宜→贵）：① 静态指纹（读文件头，无进程接触）；② 二进制常量指纹（算法是否被魔改）；③ 动态冒烟（登录态下扫 key → 解 page 1 → integrity_check），全过置 `validated`。版本变更信号：exe 版本号、安装目录新版本子目录、`WXWorkUpgrader`/`upgrade` 目录活动。

## 6. 集成形态建议（决策点）

- **方案 A（推荐）**：read-session 增加第三通道 `channel=local-db`（M10b 双通道先例）；operation 层做通道选择：默认 OCR/model，`--channel local-db` 显式指定；本地库通道零 UI 副作用、无需前台窗口，适合批量历史读取。
- **方案 B**：独立只读 operation（如 `wecom_local_history_read` / `wecom_local_validate`）。边界更清晰，但与 read-session 语义重叠。
- 共同部分：pycryptodome 需进 venv 依赖声明；驱动输出协议沿用"stdout 最后一行单行 JSON"风格；密钥等敏感物**不落日志、不入返回值、不入库文档**（返回 only 消息内容与会话元数据）。
- 合规边界（须写入设计/README）：仅支持本机当前登录账号自己的数据；只读观察进程（无注入/无写内存/无提权）；本地库原文永不修改。

## 7. 开放问题

1. 通道选择默认策略（显式参数 vs 自动探测可用性）。
2. S7 的 schema_profile 粒度（大版本号 or 特征指纹判定）。
3. 增量读取策略（`since_days` 映射到 WAL+主库合并查询的边界语义）。
4. 打包态（`ocr-python` 独立 python）是否同样捆绑 pycryptodome。
