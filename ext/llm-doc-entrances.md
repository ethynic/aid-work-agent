# LLM 提供商文档入口速查（curl 抓取用）

> 用途：本环境 WebSearch/WebFetch 被企业网络拦截，调研一律走 Bash + curl 兜底。本文登记各 LLM 提供商的官方文档入口，供下次直接 curl 抓取。可达性探测日期：2026-08-16（HTTP 200）。
> 相关快照：`ext/qwen-llm-price.md`（百炼价格）、`ext/qwen-llm-context-cache.md`（百炼缓存）、`ext/mimo-mi-api-docs.md`（小米 MiMo API 摘要）。

## 文档入口

| 提供商 | 入口 URL | 抓取结果 | 备注 |
|--------|----------|---------|------|
| 百炼（DashScope） | `https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc` | 200，约 29KB | 控制台文档页，内容可能在 JS 中加载（SPA），抓 HTML 后需提取内嵌文档数据或按右侧目录逐页抓具体帮助页（help.aliyun.com） |
| DeepSeek | `https://api-docs.deepseek.com/zh-cn/` | 200，约 45KB | VitePress 类静态站点，正文 SSR 可直接抓；`/zh-cn/api/create-chat-completion` 等子页同域抓取 |
| 小米 MiMo | `https://mimo.mi.com/docs/zh-CN/api/guidance/` | 200，约 360KB | SSR 渲染可直接抓，页面较大需先落盘再解析；已抓摘要见 `ext/mimo-mi-api-docs.md` |

## 抓取模板

```bash
# 探测可达性
curl -s -m 12 -L -o /dev/null -w "%{http_code}" -A "Mozilla/5.0" "<url>"

# 抓取落盘（大页面先落盘再解析）
curl -s -m 20 -L -A "Mozilla/5.0" "<url>" > /tmp/xxx.html
python3 -c "import re,sys,html; t=open('/tmp/xxx.html',encoding='utf-8').read(); t=re.sub(r'<script.*?</script>|<style.*?</style>','',t,flags=re.S); t=re.sub(r'<[^>]+>',' ',t); print(html.unescape(re.sub(r'\s+',' ',t)))"
```

## 相关文档

- [deepseek-v4-flash 平替模型调研](../docs/research/deepseek-v4-flash-replacement-research.md)
- [小米 MiMo-v2.5 平替可行性调研](../docs/research/mimo-v2.5-replacement-research.md)
