> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# qwen3.8-flash

> Qwen3.8-Flash 是通义千问最新推出的多模态大模型，兼具强大的理解与生成能力和出色的响应速度。模型原生支持百万级上下文窗口，能够一次性处理超长文档、代码仓库和复杂对话。在编程辅助、智能体协作、图文理解等场景中表现尤为出色——无论是自动修复代码、操作桌面应用，还是分析图表与长视频，都能给出准确、高质量的结果。同时兼容 OpenAI 与 Anthropic 主流接口协议，可无缝接入 Claude Code、Codex 等开发者工具，轻松构建高并发应用与智能工作流。凭借优异的性能与极具竞争力的推理成本，Qwen3.8-Flash 是开发者和企业在 AI 应用中兼顾效果与效率的理想选择。

模型调用 ID（`model` 参数取值）：`qwen3.8-flash`

## 模型能力 <span id="h-bd06671ad2" />

<Tabs>
  <Tab title="华北2（北京）">
    <table><thead><tr><th>能力项</th><th>支持情况</th><th>能力项</th><th>支持情况</th></tr></thead><tbody><tr><td><p>输入模态</p></td><td><p><strong>Image</strong> <strong>Text</strong> <strong>Video</strong></p></td><td><p>输出模态</p></td><td><p><strong>Text</strong></p></td></tr><tr><td><p>模型体验</p></td><td><p>支持</p></td><td><p>Function Calling</p></td><td><p>支持</p></td></tr><tr><td><p>结构化输出</p></td><td><p>支持</p></td><td><p>联网搜索</p></td><td><p>支持</p></td></tr><tr><td><p>前缀续写</p></td><td><p>支持</p></td><td><p>上下文缓存</p></td><td><p>支持</p></td></tr><tr><td><p>批量推理</p></td><td><p>不支持</p></td><td><p>模型调优</p></td><td><p>不支持</p></td></tr></tbody></table>
  </Tab>

  <Tab title="新加坡">
    部署范围：国际

    <table><thead><tr><th>能力项</th><th>支持情况</th><th>能力项</th><th>支持情况</th></tr></thead><tbody><tr><td><p>输入模态</p></td><td><p><strong>Image</strong> <strong>Text</strong> <strong>Video</strong></p></td><td><p>输出模态</p></td><td><p><strong>Text</strong></p></td></tr><tr><td><p>模型体验</p></td><td><p>支持</p></td><td><p>Function Calling</p></td><td><p>支持</p></td></tr><tr><td><p>结构化输出</p></td><td><p>支持</p></td><td><p>联网搜索</p></td><td><p>支持</p></td></tr><tr><td><p>前缀续写</p></td><td><p>支持</p></td><td><p>上下文缓存</p></td><td><p>支持</p></td></tr><tr><td><p>批量推理</p></td><td><p>不支持</p></td><td><p>模型调优</p></td><td><p>不支持</p></td></tr></tbody></table>
  </Tab>

  <Tab title="德国（法兰克福）">
    部署范围：全球

    <table><thead><tr><th>能力项</th><th>支持情况</th><th>能力项</th><th>支持情况</th></tr></thead><tbody><tr><td><p>输入模态</p></td><td><p><strong>Image</strong> <strong>Text</strong> <strong>Video</strong></p></td><td><p>输出模态</p></td><td><p><strong>Text</strong></p></td></tr><tr><td><p>模型体验</p></td><td><p>支持</p></td><td><p>Function Calling</p></td><td><p>支持</p></td></tr><tr><td><p>结构化输出</p></td><td><p>支持</p></td><td><p>联网搜索</p></td><td><p>不支持</p></td></tr><tr><td><p>前缀续写</p></td><td><p>支持</p></td><td><p>上下文缓存</p></td><td><p>支持</p></td></tr><tr><td><p>批量推理</p></td><td><p>不支持</p></td><td><p>模型调优</p></td><td><p>不支持</p></td></tr></tbody></table>
  </Tab>

  <Tab title="日本（东京）">
    部署范围：全球

    <table><thead><tr><th>能力项</th><th>支持情况</th><th>能力项</th><th>支持情况</th></tr></thead><tbody><tr><td><p>输入模态</p></td><td><p><strong>Image</strong> <strong>Text</strong> <strong>Video</strong></p></td><td><p>输出模态</p></td><td><p><strong>Text</strong></p></td></tr><tr><td><p>模型体验</p></td><td><p>支持</p></td><td><p>Function Calling</p></td><td><p>支持</p></td></tr><tr><td><p>结构化输出</p></td><td><p>支持</p></td><td><p>联网搜索</p></td><td><p>不支持</p></td></tr><tr><td><p>前缀续写</p></td><td><p>支持</p></td><td><p>上下文缓存</p></td><td><p>支持</p></td></tr><tr><td><p>批量推理</p></td><td><p>不支持</p></td><td><p>模型调优</p></td><td><p>不支持</p></td></tr></tbody></table>
  </Tab>

  <Tab title="美国（弗吉尼亚）">
    部署范围：全球

    <table><thead><tr><th>能力项</th><th>支持情况</th><th>能力项</th><th>支持情况</th></tr></thead><tbody><tr><td><p>输入模态</p></td><td><p><strong>Image</strong> <strong>Text</strong> <strong>Video</strong></p></td><td><p>输出模态</p></td><td><p><strong>Text</strong></p></td></tr><tr><td><p>模型体验</p></td><td><p>支持</p></td><td><p>Function Calling</p></td><td><p>支持</p></td></tr><tr><td><p>结构化输出</p></td><td><p>支持</p></td><td><p>联网搜索</p></td><td><p>不支持</p></td></tr><tr><td><p>前缀续写</p></td><td><p>支持</p></td><td><p>上下文缓存</p></td><td><p>支持</p></td></tr><tr><td><p>批量推理</p></td><td><p>不支持</p></td><td><p>模型调优</p></td><td><p>不支持</p></td></tr></tbody></table>
  </Tab>

  <Tab title="中国香港">
    部署范围：全球

    <table><thead><tr><th>能力项</th><th>支持情况</th><th>能力项</th><th>支持情况</th></tr></thead><tbody><tr><td><p>输入模态</p></td><td><p><strong>Image</strong> <strong>Text</strong> <strong>Video</strong></p></td><td><p>输出模态</p></td><td><p><strong>Text</strong></p></td></tr><tr><td><p>模型体验</p></td><td><p>支持</p></td><td><p>Function Calling</p></td><td><p>支持</p></td></tr><tr><td><p>结构化输出</p></td><td><p>支持</p></td><td><p>联网搜索</p></td><td><p>不支持</p></td></tr><tr><td><p>前缀续写</p></td><td><p>支持</p></td><td><p>上下文缓存</p></td><td><p>支持</p></td></tr><tr><td><p>批量推理</p></td><td><p>不支持</p></td><td><p>模型调优</p></td><td><p>不支持</p></td></tr></tbody></table>
  </Tab>
</Tabs>

## 上下文限制 <span id="h-3a176132ee" />

<table><thead><tr><th>参数</th><th>值</th><th>参数</th><th>值</th></tr></thead><tbody><tr><td><p>最大输入长度</p></td><td><p>991808</p></td><td><p>最大输出长度</p></td><td><p>131072</p></td></tr><tr><td><p>最大输入长度（思考模式下）</p></td><td><p>983616</p></td><td><p>最大输出长度（思考模式下）</p></td><td><p>131072</p></td></tr><tr><td><p>上下文长度</p></td><td><p>1000000</p></td><td><p>最大思维链长度</p></td><td><p>262144</p></td></tr></tbody></table>

## 模型价格 <span id="h-84300568d3" />

本文仅展示模型调用原价，不包含限时优惠等活动信息，请前往[百炼控制台](https://bailian.console.aliyun.com/cn-beijing/model/market)查看活动优惠。

<Tabs>
  <Tab title="华北2（北京）">
    <table><thead><tr><th>计费项</th><th>价格（元）</th><th>单位</th></tr></thead><tbody><tr><td><p>输入</p></td><td><p>0.8</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输出</p></td><td><p>2.7</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输入（缓存命中）</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存创建</p></td><td><p>1.25</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存命中</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr></tbody></table>
  </Tab>

  <Tab title="新加坡">
    部署范围：国际

    <table><thead><tr><th>计费项</th><th>价格（元）</th><th>单位</th></tr></thead><tbody><tr><td><p>输入</p></td><td><p>1.094</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输出</p></td><td><p>3.427</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输入（缓存命中）</p></td><td><p>0.117</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存创建</p></td><td><p>1.458</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存命中</p></td><td><p>0.117</p></td><td><p>每百万tokens</p></td></tr></tbody></table>
  </Tab>

  <Tab title="德国（法兰克福）">
    部署范围：全球

    <table><thead><tr><th>计费项</th><th>价格（元）</th><th>单位</th></tr></thead><tbody><tr><td><p>输入</p></td><td><p>0.8</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输出</p></td><td><p>2.7</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输入（缓存命中）</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存创建</p></td><td><p>1.25</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存命中</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr></tbody></table>
  </Tab>

  <Tab title="日本（东京）">
    部署范围：全球

    <table><thead><tr><th>计费项</th><th>价格（元）</th><th>单位</th></tr></thead><tbody><tr><td><p>输入</p></td><td><p>0.8</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输出</p></td><td><p>2.7</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输入（缓存命中）</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存创建</p></td><td><p>1.25</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存命中</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr></tbody></table>
  </Tab>

  <Tab title="美国（弗吉尼亚）">
    部署范围：全球

    <table><thead><tr><th>计费项</th><th>价格（元）</th><th>单位</th></tr></thead><tbody><tr><td><p>输入</p></td><td><p>0.8</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输出</p></td><td><p>2.7</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>输入（缓存命中）</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存创建</p></td><td><p>1.25</p></td><td><p>每百万tokens</p></td></tr><tr><td><p>显式缓存命中</p></td><td><p>0.1</p></td><td><p>每百万tokens</p></td></tr></tbody></table>
  </Tab>
</Tabs>

## 限流 <span id="h-9a524f93f0" />

<Tabs>
  <Tab title="华北2（北京）">
    本地域采用动态限流机制，TPM 限流值按百炼平台月消费额度分档，RPM 限额较高、正常使用不会触发限流。各档位数值请参见[动态限流](/zh/model-studio/quota-management)。
  </Tab>

  <Tab title="新加坡">
    部署范围：国际

    本地域采用动态限流机制，TPM 限流值按百炼平台月消费额度分档，RPM 限额较高、正常使用不会触发限流。各档位数值请参见[动态限流](/zh/model-studio/quota-management)。
  </Tab>

  <Tab title="德国（法兰克福）">
    部署范围：全球

    <table><thead><tr><th>参数</th><th>值</th></tr></thead><tbody><tr><td><p>RPM（每分钟请求数）</p></td><td><p>30,000</p></td></tr><tr><td><p>TPM（每分钟tokens）</p></td><td><p>5,000,000</p></td></tr></tbody></table>
  </Tab>

  <Tab title="日本（东京）">
    部署范围：全球

    <table><thead><tr><th>参数</th><th>值</th></tr></thead><tbody><tr><td><p>RPM（每分钟请求数）</p></td><td><p>30,000</p></td></tr><tr><td><p>TPM（每分钟tokens）</p></td><td><p>5,000,000</p></td></tr></tbody></table>
  </Tab>

  <Tab title="美国（弗吉尼亚）">
    部署范围：全球

    <table><thead><tr><th>参数</th><th>值</th></tr></thead><tbody><tr><td><p>RPM（每分钟请求数）</p></td><td><p>30,000</p></td></tr><tr><td><p>TPM（每分钟tokens）</p></td><td><p>5,000,000</p></td></tr></tbody></table>
  </Tab>

  <Tab title="中国香港">
    部署范围：全球

    <table><thead><tr><th>参数</th><th>值</th></tr></thead><tbody><tr><td><p>RPM（每分钟请求数）</p></td><td><p>30,000</p></td></tr><tr><td><p>TPM（每分钟tokens）</p></td><td><p>5,000,000</p></td></tr></tbody></table>
  </Tab>
</Tabs>
