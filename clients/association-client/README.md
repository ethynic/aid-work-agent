# 协会信息收集助手 - 桌面客户端

Electron 桌面应用，提供协会信息收集的 GUI 入口（输入、进度、日志、结果、积分展示）。
业务逻辑由本地命令行工具（`association-client-cli`）承载，客户端 spawn 调用。

## 架构

```
electron/
  main.ts        主进程（窗口创建 + IPC + CLI Runner 集成）
  preload.cts    窗口预加载脚本（窄 IPC 桥）
  cliRunner.ts   spawn CLI exe + 流式解析 NDJSON
  config.ts      safeStorage 加密存储激活凭证
  security.ts    CSP + 安全 webPreferences
src/
  index.html     renderer HTML
  app.js         renderer 逻辑（通过 window.associationClient IPC 调主进程）
  styles.css     样式
```

## 开发运行

```bash
cd clients/association-client
npm install
npm start    # 编译 + 启动 Electron
```

开发模式下，CLI 命令通过 `python ../association-client-cli/main.py` 运行。

## 打包

```bash
# 1. 先构建 CLI exe
cd ../association-client-cli
pip install pyinstaller
pyinstaller build.spec

# 2. 构建 Electron 安装包
cd ../association-client
npm install
npm run package:win
# 产出 release/AssociationClient-1.0.0-win-x64.exe
```

## 客户使用流程

1. 安装 Playwright Chromium（运行 `install-playwright.cmd`）
2. 登录微信 PC 版
3. 启动客户端 → 输入激活码激活
4. 输入协会名称 → 点击"开始收集"
5. 查看进度/日志，完成后下载 Excel
