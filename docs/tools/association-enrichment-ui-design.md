# 协会资料调查工作台 UI 设计

> 状态：🔧 MVP 开发中

## 目标与边界

在现有 `AssociationBatchEnricher` 与 `ProjectAssociationProviders` 之上提供独立本地
Web UI，不通过 shell 调用 CLI。服务默认仅监听 `127.0.0.1`，浏览器采集保持可见，
微信 RPA 全局串行。

工作流分为两个明确阶段：

1. 用户粘贴文字或上传 CSV/XLSX，项目 LLM 仅从原输入证据中提取协会名称，严格 JSON
   返回并做语义去重；用户可以删除条目，但不能新增未被解析出的名称。
2. 用户确认清单后逐协会执行官网发现、可见浏览器采集、字段提取、微信取证及 Excel
   输出。界面显示总进度、当前协会和当前步骤。

## 本地架构

- `clients/association-enrichment-ui/app.py`：FastAPI 本地入口和静态单页。
- `src/services/association_enrichment_ui.py`：清单解析、任务状态机、全局互斥和审计存储。
- 运行元数据写入 `%LOCALAPPDATA%/AidWorkAgent/association-enrichment-ui/runs`，进程重启
  后可读取已完成任务。
- Excel 写入对应任务目录，通过本地下载接口返回。

任务状态为 `pending/running/completed/partial/failed`。第一版使用轮询，避免 SSE
断线恢复复杂度。全局执行锁保证同一时刻只有一个任务驱动浏览器和微信；忙时启动返回
明确冲突。

## 输入安全

- 仅允许 `.csv`、`.xlsx`，默认最大 10 MiB；文件名不作为路径使用。
- CSV 兼容 UTF-8 BOM 与 GB18030；XLSX 使用 `data_only=True`，不执行公式或宏。
- 上传内容存入系统临时目录，解析结束立即删除。
- LLM 必须返回 `{"associations":[{"name","evidence"}]}`。名称及 evidence 都必须可在
  输入单元格或文本中按空白归一化后逐字找到，否则整次解析失败，禁止模型臆造。
- 语义去重由 LLM 给出规范名称并保留首次出现项，服务端仍执行确定性去重和证据复核。

## 审计与隐私

普通元数据事件只包含：
`timestamp/association/stage/kind/summary/detail_ref`。手机号统一脱敏，网页正文、微信
列表/详情、OCR 文本不进入 stdout 或元数据 JSON。

每个详细事件正文使用当前 Windows 用户 DPAPI 独立加密落盘。详情接口只允许环回请求，
按 `detail_ref` 解密单条返回。官网 provider 通过可选 audit callback 记录 URL、标题和
正文；微信 provider 解密既有 DPAPI artifact 后，将列表、详情和 OCR 结果重新写入该
任务的加密审计记录。

## LLM Token 精确计量

只累计项目 LLM provider 响应的真实 `usage`，不按文字长度估算。`input_tokens` 对应
`prompt_tokens`，其中已经包含 `cached_input_tokens/cached_tokens`；每次 `total_tokens`
统一按 `input_tokens + output_tokens` 重算，不信任 provider 可能不一致的总数，也不得再次加 cached。每次调用记录调用
次数、输入、缓存输入、输出和总 token，但审计事件不记录 prompt 或 response 正文。

主进程通过 run-scope `contextvar` recorder 隔离并串行聚合清单解析、官网识别、官网
资料/领导抽取和 fallback。微信列表/详情 judge 在独立 Python 子进程中运行，其 usage
作为安全协议字段进入 DPAPI artifact，再由 provider 汇入父 run；浏览器、PowerShell
和 OCR 自身不计 token。清单解析发生在 run 创建前，usage 暂存在一次性 plan 中；用户
确认后作为独立 `batch_overhead` 保留，不虚假归属给任一协会，同时计入批次总量。
批次平均值是“每个已确认协会分摊的整体批次成本”，即包含 `batch_overhead` 的批次总量除以最终去重后的协会数。

UI 轮询响应实时返回每协会及批次总量、平均输入/缓存/输出/总 token；Excel 原有
19 字段主表不变，新增独立“Token用量”工作表。

## 界面

采用“调查工作台”工业编辑风：左侧输入和确认清单，中间串行流水线与总体进度，右侧
证据事件流。深墨色纸张背景、警戒橙作为运行强调色、等宽数据字体；不使用通用后台
卡片和紫色渐变。窄屏自动折叠为单列，交互元素有键盘焦点和可读标签。
