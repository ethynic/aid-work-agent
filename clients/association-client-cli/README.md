# 协会信息收集客户端 CLI

承载协会信息收集完整业务逻辑的命令行工具，通过 PyInstaller 打包成单个 exe，
由 Electron 客户端 spawn 调用。

## 命令

```bash
# 激活（首次使用）
association-cli.exe activate --code AC-XXXXXXXXXXXX [--server-url https://agent.xxx.cn]

# 查询积分余额
association-cli.exe credits

# 协会信息收集
association-cli.exe collect --associations "中国黄金协会,中国机械工业协会" --output result.xlsx
# 或从文件输入
association-cli.exe collect --input input.csv --output result.xlsx
```

## 架构

```
main.py                         CLI 入口（activate/credits/collect）
runtime/
  proxy_gateway.py              ProxyLLMGateway（走服务端 /api/client/v1/llm/chat 计费）
  config.py                     激活凭证存储 / machine_id 采集
  progress_reporter.py          NDJSON 进度输出（stdout，供 Electron 解析）
  powershell_runner.py          微信 RPA PowerShell 子进程封装
  playwright_check.py           Playwright Chromium 检测
scripts/
  wechat-souyisou*.ps1          微信 RPA 脚本（从 wechat-souyisou-rpa 复制）
  llm_judge.py                  LLM judge（改造为走代理）
  ocr_adapter.py                OCR 适配器（改造为走代理）
build.spec                      PyInstaller 打包配置
install-playwright.cmd          Playwright 预装引导脚本
```

## 开发运行

```bash
cd clients/association-client-cli
# 项目根目录必须在 PYTHONPATH（main.py 自动设置）
python main.py activate --code AC-TESTCODE0001 --server-url http://localhost:8000
```

## 打包

```bash
pip install pyinstaller
pyinstaller build.spec
# 产出 dist/association-cli.exe
```

## 依赖

- Python 3.11+
- Playwright Chromium（客户预装，运行 install-playwright.cmd）
- 微信 PC 版（已登录，用于 RPA 取证手机号）
- 服务端运行中（提供 /api/client/v1/* 端点）
