# 第一方 Runtime 离线包格式与打包入口

**Runtime、BOSS 和 WeCom 从源码到最终 ZIP 的操作流程见[完整打包手册](../../docs/ops/runtime-boss-release-guide.md)。** 手册包含验收签名配置默认路径及 WeCom 便携 Python 准备步骤。

此目录仅在构建机运行。客户机接收 `.aidplugin.zip`，验证签名与清单后解包，不运行 npm、pip、Git 或安装 hook。支持 BOSS、微信、企业微信；工具集合由本次插件 `version --json` 和 `runtime-manifest.json` 产生，不把历史工具数量当作当前清单。Host 可执行范围由 Runtime 的受信策略决定。

## 包格式

外层仅 `envelope.json`、`payload.zip`。内层包含 `runtime-manifest.json`、`dist/src`、生产 `node_modules`、批准的驱动及适用的内部原生依赖/OCR 资产。签名及 manifest 摘要使用 RFC8785；包摘要和大小绑定内层 ZIP 字节。`distribution.required_files` 覆盖内层普通文件，仅排除 manifest 自身，避免自引用。

## 低层打包器及公钥文件

`package.mjs` 接收已构建的 Provider 源码。`--provider boss`、`--provider wecom`、`--provider weixin` 分别选择一个 Provider，省略会遍历三个。验收公钥登记表在 `trust/acceptance-roots.json`，已交付的公开根快照在 `trust/history/`。登记工具只追加公钥，Provider 范围按实际用途设定。

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

`verify-offline.py --directory <插件目录> --node <固定Node路径> --trust-roots <公开根.json>` 在构建机核对签名、清单及搬迁后的只读 version/doctor。隔离包可省略 `--trust-roots`，使用目录内的 `ISOLATED-TEST-TRUST-ROOT.json`。本目录的源码定向测试入口为 `npm test`。
