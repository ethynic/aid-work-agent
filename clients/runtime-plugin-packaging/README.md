# 第一方 Runtime 离线包构建

此目录仅在构建机运行。客户机接收 `.aidplugin.zip`，验证签名与完整清单后解包；不运行 npm、pip、Git 或安装 hook。支持 BOSS、微信、企业微信，保留各 Provider 实际 MCP 工具集合（当前分别 21、8、10 项）。Host 可执行的受信工具子集由 Runtime 原有策略决定。

外层仅 `envelope.json`、`payload.zip`。内层包含完整 `runtime-manifest.json`、`dist/src`、生产 `node_modules`、驱动、包元数据和适用的内部 OCR Python/模型/原生依赖。签名和 manifest 摘要采用 RFC8785；包摘要/大小绑定内层原始 ZIP 字节。`distribution.required_files` 覆盖内层所有普通文件，只排除 manifest 自身，避免自引用。

## 构建

在仓库根运行，Node 路径必须是 H3 批准的 22.23.3 Windows x64 / ABI127：

```powershell
npm --prefix clients/runtime-plugin-packaging ci --ignore-scripts
npm --prefix clients/boss-resume-assistant run build:main
npm --prefix clients/weixin-cli run build:main
npm --prefix clients/wecom-cli run build:main
venv/Scripts/python.exe clients/runtime-plugin-packaging/prepare-ocr.py
node clients/runtime-plugin-packaging/package.mjs --test --node C:/repos/aid-work-agent/clients/runtime-plugin-packaging/assets/node/node-v22.23.3-win-x64/node.exe --npm-cli C:/nvm4w/nodejs/node_modules/npm/bin/npm-cli.js --output C:/repos/aid-work-agent/clients/runtime-plugin-packaging/release/final-acceptance
```

`--node`、`--npm-cli` 是可信构建输入，按构建机实际位置提供。固定 Node ZIP 官方 SHA256：`2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71`。构建工具验证其版本、平台、架构和 ABI；不会将 Node PATH 或 Electron executable 当客户机执行入口。

输出目录必须全新，工具拒绝覆盖已生成包。一次运行签署全部三包，使用同一个仅驻内存的开发 signer；不保存私钥。单 Provider 调试可传 `--provider boss|weixin|wecom`，但新的测试运行仍应使用新目录。正式发布改为 `--signer <外部配置.json> --release-id <不可变发布标识>`，配置含 `key_id`、`private_key_path`，不能与 `--test` 混用；正式私钥不入库、不打印。

## OCR 资产

`ocr-assets.lock.json` 固定 CPython 3.12.10 embed-amd64、18 份官方 PyPI wheels、Microsoft VC Runtime 14.44.35211 和构建机解包工具的版本/原始 SHA256/来源。Python 摘要来自官方 Sigstore bundle；wheel 摘要来自官方 PyPI release metadata。所有 wheel 包内许可证/模型保留，Python LICENSE 和 Microsoft CRT license 一并交付。MSVC exe 另外核对 Microsoft Authenticode；仅解包 CAB，绝不运行其安装器。构建用的 7-Zip 不进入客户机包。

`prepare-ocr.py` 下载并校验所有字节，产生 `assets/ocr-python-final`。内部 Python 自带通用 `msvcp140.dll`/`msvcp140_1.dll` 等 CRT 文件，不依赖本机 System32 中已有的 VC Redistributable。RapidOCR/ORT/OpenCV/Pillow 初始化在复制后的目录内验证。Windows 系统 API/DLL、PowerShell 和目标业务软件仍是平台条件。

发行包里的所有 Python 调用使用 `-I -B`，忽略开发 Python 环境且不向签名 payload 写入 `__pycache__`。微信 history/Win32 底座已迁入 drivers，运行不再读取 experiments。仅没有 runtime-manifest 的开发 checkout 保留旧仓库 venv 回退；已安装包缺内部 OCR 则明确失败。

## 隔离验收包与信任根

`--test` 输出根文件 `ISOLATED-TEST-TRUST-ROOT.json`，只用于 H3 `acceptance` profile：

```json
{
  "schemaVersion": 1,
  "profile": "acceptance",
  "roots": [{ "key_id": "aid-isolated-dev-only", "public_key": "<SPKI PEM>", "providers": ["ai.aidwork.boss-recruiting", "ai.aidwork.weixin", "ai.aidwork.wecom"], "test_only": true }]
}
```

该根单独交付，包内不含可自行建立信任的公钥。生产配置必须拒绝测试根。正式生产信任根、签名边界、CPython 3.12.10 的发行安全评审和 Microsoft CRT 再分发许可资格仍需正式发行流程核验；本轮开发包不代表这些门已通过。

## 验证

```powershell
npm --prefix clients/runtime-plugin-packaging test
venv/Scripts/python.exe clients/runtime-plugin-packaging/verify-offline.py --directory C:/repos/aid-work-agent/clients/runtime-plugin-packaging/release/final-acceptance --node C:/repos/aid-work-agent/clients/runtime-plugin-packaging/assets/node/node-v22.23.3-win-x64/node.exe
```

smoke 在系统临时目录复制 payload，移除仓库/npm/Python/Git PATH 和 Provider 凭证，只留 Windows 系统 PATH 与隔离 artifact 目录；检查分离信任根签名、manifest/每文件完整性、真实固定 Node 的 version/doctor JSON、包内 Python 对合成图片的实际 OCR，并核对执行后 payload 全部字节与文件集合不变。doctor 的 `runtime_readiness` 是结构化基础运行条件；进程存在不冒充已验证登录，业务动作仍执行既有门禁。smoke 不发送真实消息、不点击业务 GUI，不代替最终干净 Windows 测试机和安装包验收。

源码定向测试通过；全套 CLI 回归仍有 HEAD 可复现的既有失败，主开发计划登记准确证据，不将本包自测表述为所有业务回归全绿。产物与依赖缓存在本目录 .gitignore 范围，不提交。
