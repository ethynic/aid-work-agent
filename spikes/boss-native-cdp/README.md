# BOSS 原生 CDP Phase 0 工具

本目录只保留已经真机验证过的最小原生 CDP 路径：

- 用户手动登录并确认推荐页就绪；
- DOMSnapshot 精确定位当前可见候选文本；
- 原生 Input 打开详情；
- 原生滚轮分段截图；
- 原生 Escape 关闭详情；
- 脱敏 JSONL 协议审计。

不包含 Playwright、Puppeteer、Selenium、`Runtime.*` 或页面脚本注入。WASM 摘要探针已经删除，后续正文只通过截图和本地 OCR 处理。

## 离线验证

```powershell
npm install
npm test
```

## 真机只读基线

人工启动带独立 profile 和调试端口的可见 Chrome，登录 BOSS、关闭弹窗并进入推荐列表后运行：

```powershell
npm run audit -- --confirm-page-ready --confirm-plaintext-artifacts `
  --endpoint http://127.0.0.1:9222 `
  --output <受保护的临时目录>
```

现场脚本必须显式确认明文产物。DOMSnapshot 和截图包含候选人 PII，只能写入受保护的临时目录，禁止提交到仓库，验证完成后及时删除。

当前工具不自动选择候选人、不自动批量循环，也不执行“打招呼”“不合适”等写动作。单次详情验证由现场操作者从最新 DOMSnapshot 选取当前可见且唯一的候选文本后执行，页面变化时重新采集快照，不复用旧姓名或坐标。
