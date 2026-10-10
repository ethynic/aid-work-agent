# Office 组件测试夹具

这四个小型文件随仓库保存，供 `AttachmentPreviewPanel.office.test.ts` 经 MSW 提供真实格式内容；普通 `npm test` 不依赖本机 `test_uploads`、Docker 或 Python。

| 文件 | 必要覆盖 |
|------|---------|
| multisheet.xlsx | 三个工作表、合并单元格、切换内容 |
| bigrows.xlsx | 1251 行、预览截断到 1000 行 |
| sample.pptx | 四页解析、翻页及首页/末页禁用 |
| sample.docx | DOCX 输入与组件缩放；渲染器由测试 mock |

更新夹具时，从仓库根目录执行：

```sh
python frontend/e2e/scripts/gen-office-fixtures.py --output-dir frontend/web/__tests__/fixtures/office
```

默认仅生成这四个小型文件（合计约 144 KiB）。需要手工验收超大文件时单独使用 `--include-oversize`，输出到 `test_uploads/office_preview`；不将大文件加入单元测试夹具。
