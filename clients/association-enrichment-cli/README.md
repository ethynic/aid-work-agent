# 协会资料批量补全 CLI

可输入文字、CSV 或 XLSX，按首次出现顺序去重后逐协会执行官网优先、网络检索降级和微信联系人取证，最终写入 Excel。

```powershell
python clients/association-enrichment-cli/association_enrichment_cli.py `
  --association "中国日用玻璃协会,中国缝制机械协会" `
  --output .\association-result.xlsx
```

CSV/XLSX 必须包含 `协会名称`、`association_name`、`association` 或 `单位名称` 列。浏览器固定以可见模式启动。`--dry-run` 仅验证输入和去重，不调用外部服务。

CLI 标准输出只包含数量、状态与输出路径，不输出手机号。微信原始证据仍保存在当前 Windows 用户 DPAPI 加密 artifact 中；完整手机号只写入用户指定的本地 Excel。
