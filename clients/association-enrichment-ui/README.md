# 协会资料调查工作台

在仓库根目录执行：

```powershell
.\clients\association-enrichment-ui\start.ps1
```

默认打开 `http://127.0.0.1:8765`。如果不希望自动打开浏览器：

```powershell
.\clients\association-enrichment-ui\start.ps1 --no-open
```

输入文字或选择 CSV/XLSX 后，先点击“模型解析清单”，检查并删除不需要的条目，再确认
执行。官网浏览器始终可见；执行期间不要操作微信。任务完成后从中间栏下载 Excel。

详细网页、微信列表、详情和 OCR 文本使用当前 Windows 用户 DPAPI 加密，保存在
`%LOCALAPPDATA%\AidWorkAgent\association-enrichment-ui\runs`。普通运行元数据只含
脱敏摘要。该目录只能由执行任务的同一个 Windows 用户解密。
