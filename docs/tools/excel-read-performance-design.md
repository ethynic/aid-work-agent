# Excel 读取性能与事件循环隔离修复

关联：[开发计划](../plans/plan-excel-read-performance.md)、[故障分析](../incidents/wecom-kf-excel-read-runner-interrupted-20261015.md)。

## 现状与原因

2026-10-15 正式服微信客服读取约 14KB 的景点资源 Excel，工作表范围为 A1:XFC28（28 行、16,383 列），read 阶段约 222 秒，随后 Runner 中断。旧景点资料未迁入 metadata，导致统一检索未返回项目表，模型额外选择回读原文件；数据补迁沿用[原设计](../system/knowledge-base/unified-kb-resource-table-metadata-design.md)与既有脚本。

代码根因是 ExcelReader 在列宽计算的每列循环中重新读取 sheet.max_row。openpyxl 的该属性会遍历已实例化的单元格；此前提取数据又把工作表矩形中的空格实例化，反复扫描使计算量放大。ReadTool.execute 同时在 asyncio 事件循环中直接执行同步 Excel 解析，长读期间 Runner 心跳无法调度。线上 30 秒租约、10 秒心跳远短于读取耗时；租约失效属于高置信判断，原日志未保存心跳异常类型。

已有索引引用的 file-tools-claude-code-parity-design.md / tool-overall-optimization-design.md 在当前 master 中已不存在；本修复单列设计，不重建已删除文档。

## 修复边界

1. 在 ExcelReader 的数据提取和文本格式化入口各缓存一次行列边界，循环使用固定值。保留现有矩形数据、空白格、工作表信息和格式，不以固定列数截断，不改变公式 data_only=True 语义。
2. ReadTool 的 Excel 分支使用 asyncio.to_thread 调用原同步包装方法；路径授权仍在原入口校验，解析器继续保持同步。线程可继续完成已开始的只读解析，取消不会强行终止线程，不宣称新增线程取消能力。
3. workbook 在解析、指定工作表不存在或异常退出时均关闭，避免提前返回遗漏资源释放。
4. 不调整 Runner 租约、计费、worker 取消策略或其他文档路由；若后续仍有任务取消缺陷另行分析。

## 验收

- 使用真实 openpyxl 构造含 XFC28 样式格的工作簿，完整读取值、保留 28×16,383 范围，耗时降至秒级。
- 反向保护：循环不会按列数重复查询动态维度属性；不同工作表、0/False/空值、超过显示宽度的文本和缺失工作表行为正确。
- Excel 解析阻塞时，事件循环中的心跳探针仍可推进；取消调用不会向模型返回虚假的成功。
- 定向 read 工具与迁移纯逻辑回归、独立测试及独立 CR 通过。正式服代码部署须另获明确指示。
