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

如需构建微信或企业微信，`package.mjs` 还支持 `--provider weixin`、`--provider wecom`，不传 provider 才会遍历三个 Provider；对应源码先分别构建，OCR 资产按下节准备。不要为只交付 BOSS 的任务额外构建全部三包。

## 微信 / 企业微信的 OCR 资产（BOSS-only 跳过）

`ocr-assets.lock.json` 固定 CPython 3.12.10 embed-amd64、19 份官方 PyPI wheels、Microsoft VC Runtime 14.44.35211 和构建机解包工具的版本/原始 SHA256/来源。Python 摘要来自官方 Sigstore bundle；wheel 摘要来自官方 PyPI release metadata。所有 wheel 包内许可证/模型保留，Python LICENSE 和 Microsoft CRT license 一并交付。MSVC exe 另外核对 Microsoft Authenticode；仅解包 CAB，绝不运行其安装器。构建用的 7-Zip 不进入客户机包。

企微本地库解密延迟使用 `Crypto.Cipher.AES`，发行依赖包含锁定的 pycryptodome 3.23.0 Windows x64 ABI3 wheel及许可证。仅SQLite查询/OCR成功不能证明能解密；资产准备、企微签包及离线验包均验证包内AES-CBC与SQLite，交付还用包内driver解密独立生成的合成加密快照。复用旧OCR目录缺Crypto时应在签包前准确失败，不能借用系统Python掩盖缺依赖。

`prepare-ocr.py` 下载并校验所有字节，产生 `assets/ocr-python-final`。内部 Python 自带通用 `msvcp140.dll`/`msvcp140_1.dll` 等 CRT 文件，不依赖本机 System32 中已有的 VC Redistributable。RapidOCR/ORT/OpenCV/Pillow 初始化在复制后的目录内验证。Windows 系统 API/DLL、PowerShell 和目标业务软件仍是平台条件。

发行包里的所有 Python 调用使用 `-I -B`，忽略开发 Python 环境且不向签名 payload 写入 `__pycache__`。微信 history/Win32 底座已迁入 drivers，运行不再读取 experiments。仅没有 runtime-manifest 的开发 checkout 保留旧仓库 venv 回退；已安装包缺内部 OCR 则明确失败。

复制drivers时排除开发机重生成的`__pycache__`、`.pyc`和`.pyo`，保留其余源文件原始字节；无需在每轮测试后手动清空开发缓存。

企微0.2.1的TS本地库与两个PS OCR调用共用包内python_bootstrap.py，处理实际安装布局的Windows进程/原生DLL长路径。仅嵌入Python启用，DLL句柄保留至退出，只读选取系统现成短路径，realpath兼容表示限runtime目录；业务文件/argv/stdin及退出码保持，不导入Crypto、不复制缓存或调整系统策略。签包后还须在实际已装深目录以默认TS桥解密合成快照、实际PS OCR识别合成图片；短目录/import成功不替代该检查。无现成短名的卷设置未宣称经过验证，代码不增加可用性门禁。

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
