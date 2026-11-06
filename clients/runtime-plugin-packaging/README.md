# 第一方 Runtime 离线包格式与打包入口

**Runtime + BOSS 从源码到最终 ZIP 的唯一操作流程：[完整打包手册](../../docs/ops/runtime-boss-release-guide.md)。** 本页保留容器格式、其他 Provider 资产和签名原理，不维护另一套出包命令。

此目录仅在构建机运行。客户机接收 `.aidplugin.zip`，验证签名与清单后解包，不运行 npm、pip、Git 或安装 hook。支持 BOSS、微信、企业微信；工具集合由本次插件 `version --json` 和 `runtime-manifest.json` 产生，不把历史工具数量当作当前清单。Host 可执行范围由 Runtime 的受信策略决定。

## 包格式

外层仅 `envelope.json`、`payload.zip`。内层包含 `runtime-manifest.json`、`dist/src`、生产 `node_modules`、批准的驱动及适用的内部原生依赖/OCR 资产。签名及 manifest 摘要使用 RFC8785；包摘要和大小绑定内层 ZIP 字节。`distribution.required_files` 覆盖内层普通文件，仅排除 manifest 自身，避免自引用。

## 当前构建及信任规则

- [构建机与依赖安装](../../docs/ops/runtime-boss-release-guide.md#2-构建机条件及执行约定)：当前 BOSS/Host 依赖 `clients/shared/win32-native-input`，构建前安装该共享包依赖；BOSS-only 不准备 OCR。
- [签署和公钥登记](../../docs/ops/runtime-boss-release-guide.md#5-签署-boss-插件登记公钥及完整历史快照)：单 BOSS 使用 `--provider boss`、唯一 `--test-key-id`、固定 Node 和全新输出目录。不要照抄历史批次的签名 ID。
- 验收公钥唯一登记表为 `trust/acceptance-roots.json`，原始 r15 资产在 `trust/history/runtime-r15.json`，后续完整快照在 `trust/history/*.roots.json`。旧 key_id、公钥、Provider 范围和 test_only 不变；登记工具只追加公钥，不读取旧私钥。
- 单 BOSS 新根先收窄到 `ai.aidwork.boss-recruiting` 再登记。prepare/package/verify 消费完整登记集合，不使用本轮只有一个根的文件替代历史集合。
- 每次交付保留完整公开根快照；同步这些公开资产即可换构建机，不要求用户解绑重配。新私钥只驻签名进程，不能为同一已交付版本重签不同内容。
- 正式签署使用外部 `--signer` 和不可变 `--release-id`，不能混用 `--test`；Windows production 另需有效代码签名，详见[发行边界](../../docs/ops/runtime-boss-release-guide.md#10-正式签名发行与故障排查)。

如需构建微信或企业微信，`package.mjs` 还支持 `--provider weixin`、`--provider wecom`，不传 provider 才会遍历三个 Provider；对应源码先分别构建，各自便携 Python 资产按下节准备。不要为只交付 BOSS 的任务额外构建全部三包。

## 微信 OCR 与企微本地库 Python 资产

微信 Provider 保持 `prepare-ocr.py`、`ocr-assets.lock.json` 和 `ocr-python` 原路径，用于现有 OCR 能力。企微 Provider 使用独立的 `prepare-localdb.py`、`localdb-assets.lock.json` 和 `localdb-python`，只包含锁定来源和 SHA256 的 CPython 3.12.10、pycryptodome、tzdata 与 app-local CRT；不带 RapidOCR、Pillow、ORT、OpenCV 或模型。

两种资产均在构建机下载并核对来源摘要，MSVC 包还核对 Microsoft Authenticode，仅解包 CAB。企微资产构建、签包及离线验包分别验证包内 AES-CBC、SQLite 和 Asia/Shanghai IANA 日期能力。发行包内 Python 以 `-I -B` 运行，签名 payload 不含 `__pycache__`。`python_bootstrap.py` 保留便携解释器在 Runtime 深路径的兼容处理；开发 checkout 可用仓库 venv，已安装插件依赖包内解释器。

复制 drivers 时排除开发机的 `__pycache__`、`.pyc` 和 `.pyo`。企微打包只复制当前源码对应的 `dist/src` 模块，避免 TypeScript 删除模块后旧编译文件混入签名包。

## 隔离验收包与信任根

`--test` 输出根文件 `ISOLATED-TEST-TRUST-ROOT.json`，只用于 H3 `acceptance` profile：

```json
{
  "schemaVersion": 1,
  "profile": "acceptance",
  "roots": [{ "key_id": "aid-isolated-schema-example", "public_key": "<SPKI PEM>", "providers": ["ai.aidwork.boss-recruiting", "ai.aidwork.weixin", "ai.aidwork.wecom"], "test_only": true }]
}
```

该根单独交付，包内不含可自行建立信任的公钥。生产配置必须拒绝测试根。正式生产信任根、签名边界、CPython 3.12.10 的发行安全评审和 Microsoft CRT 再分发许可资格仍需正式发行流程核验；本轮开发包不代表这些门已通过。

## 验证与最终交付

签名、搬迁、实际原生依赖加载、Runtime 验包以及交付 ZIP 的逐项命令统一见[打包手册第 7～9 节](../../docs/ops/runtime-boss-release-guide.md#7-插件验包和相关验证)。现有 `verify-offline.py` 会验证清单、版本并做 doctor 只读检查；未登录导致的未就绪和原生模块缺失不能混为一谈。静态验包不表示真实 NSIS 安装、客户升级或 BOSS 业务已通过。

源码定向测试入口仍为 `npm test`（本目录），不生成客户交付 ZIP。编译缓存和打包中间文件不交付；最终 EXE、插件、说明、报告及组合 ZIP 统一汇总至 `clients/release/`，不在仓库根目录新建 release。
