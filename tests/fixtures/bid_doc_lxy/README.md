# bid-doc-generator 兰香园测试用例 · 驱动脚本与复现产物

> 2026-11-10 基于 `ext_package/lxy-bid-testcase`（兰香园 D04、D06 瓷砖投标项目，gitignored，23MB 真实格式源文件）完成的三层全链路测试沉淀。
> **源文件不进仓库**，本目录只入库可复跑的驱动脚本（812K）与一轮完整复现产物，作为技能回归基准。

## 目录

| 文件 | 说明 |
|---|---|
| `run_steps.py` | 第 3 层 A 部分驱动：LLM 重生成 requirements / layout / matrix / price_input 四个中间配置（按 SKILL.md 六步法 Step 2-5 提取模型） |
| `run_content.py` | 第 3 层 B 部分驱动：matrix 承诺修订步 + 十册 content 分三批生成 |
| `coverage_check.py` / `coverage_check2.py` | requirements 逐条覆盖率对照（包内 50 条标准答案 vs 我方重提取） |
| `repro/` | 一轮完整复现产物：重生成的 5 个中间 JSON + 十册 DOCX（`docx/`）+ PACKAGE.md |

## 复跑方法

前置：源包在 `ext_package/lxy-bid-testcase/`（含 01-甲方招标文件 / 02-我方源文件 / 03-脚本解析产物 / 04-中间配置 标准答案）。

```bash
# 容器内执行（依赖 python-docx/openpyxl 与 LLM 网关配置）
docker exec aid-agent-api python /app/tests/fixtures/bid_doc_lxy/run_steps.py --step requirements|layout|matrix|price_input
docker exec aid-agent-api python /app/tests/fixtures/bid_doc_lxy/run_content.py --step revise_matrix|content_a|content_b|content_c
# 产物落 /tmp/lxy_bid_test/llm/，随后用 src/skills/bid-doc-generator-1.0.0/scripts/ 校验
```

LLM 用 `LLMGateway(provider_name='deepseek', model_codes={'deepseek': 'deepseek-flash'})`；一次完整复跑约 7 次调用、40 万 token 量级。

## 验收基准（对照包内 04-中间配置 标准答案）

- price_quote：含税总价 12,924,264.42 元、低于限价 3.83%（脚本确定性输出）
- check_layout：甲方强制项全 PASS
- matrix：63 条量级、零负偏离（承诺修订后）
- make_package：32 项一致性自检全 PASS
- requirements：50 条标准答案覆盖率 ≥ 80%（首轮实测 84%，细则类条款见下）

## 已知测试发现（2026-11-10，已回灌 SKILL.md v1.0.1）

1. requirements 首轮提取漏细则类条款（保证金到账细节、代理服务费承诺书否决条款等 8-9 条）-> Step 2 增加二轮「细则扫描」
2. layout 历史标书**视觉**范式（页眉/表头底纹/表格字号）纯文本解析抓不到 -> Step 3.5 要求从 docx 样式 XML 读取
3. content 跨文件口径不统一（「2 年/2年」、正文缺项目名；注意 make_package 自检只读正文不读页眉）-> Step 6 增加口径统一要求
