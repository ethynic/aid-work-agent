# 生产环境主日志 WARNING 审计（2026-09-16 ~ 09-18）

> 数据来源：生产新机（129.211.65.243）容器 `aid-agent-api`，`/app/log/agent/aid-work-agent_202609{16,17,18}.log`。
> 统计口径：`grep WARNING` 共 **119 条**（09-16：54 条 / 09-17：14 条 / 09-18：51 条），按日志位置聚合为 **8 类**。
> ERROR 已另行检查，本文档仅覆盖 WARNING。逐项处理时在「处理状态」列更新。
> **处理结论（2026-09-18）：全部完成**——解决 1 类（#1）、完成开发 4 类（#3/#4/#6/#7，待部署）、其余经评估不重要收尾不处理（#2/#5/#8）。

---

## 分类汇总

| # | 日志位置 | 条数 | 日期分布 | 严重度 | 处理状态 |
|---|---------|------|---------|--------|---------|
| 1 | `src.memory.mid_term:_get_model_limit:463` | 93 | 48/10/35 | 低 | ✅ 已随 09-18 15:56 重启解决（部署时差，详见下文） |
| 2 | `src.wechat_mp.callback:callback_verify:259` | 8 | 09-18 集中 | 中 | ⏸ 暂不处理（2026-09-18 收尾评估不重要，复发再核对 token） |
| 3 | `src.core.agent:_reorder_messages_for_llm:1648` | 6 | 3/3/0 | 低 | ✅ 已完成开发（丢弃预览加长到 500 字符，含完整 ASR 文本，供人工评判） |
| 4 | `src.core.text_sanitizer:sanitize_text:26` | 5 | 1/0/4 | 中 | ✅ 已完成开发（降级 INFO + 补 source 来源标签，待部署） |
| 5 | `src.wechat_mp.summarize:summarize:158/161` | 3 | 0/0/3 | 低 | ⏸ 暂不处理（2026-09-18 收尾，已有回退属偶发） |
| 6 | `src.core.redis_client:_connect:376` | 2 | 0/1/1 | 低 | ✅ 已完成开发（启动期重试 + compose 健康依赖 + 恢复清残留，待部署） |
| 7 | `src.saas.api.channel_routes:_transcribe_voice_with_asr:454` | 1 | 09-16 | 低 | ✅ 已处理（根因 ASR 免费试用过期，日志降级 INFO，待部署） |
| 8 | `src.tools.data_analysis.analysis_agent:run:158` | 1 | 09-16 | 低 | ⏸ 暂不处理（2026-09-18 收尾，单次且有重试兜底） |

---

## 逐类明细与处理建议

### 1. ContextCompression unknown model_code [deepseek-flash]（93 条，占 78%）

```
ContextCompression unknown model_code [deepseek-flash] (provider=deepseek), fallback to default limit=512000
```

- **现象**：上下文压缩模块不识别 `deepseek-flash` 这个 model_code，每次压缩都走 fallback 默认 limit=512000，并打一条 WARNING。三天 93 条，全天分布（非集中爆发），说明有租户/会话稳定使用 deepseek-flash。
- **影响**：功能不受损（fallback 值可能就是合理值），但日志被刷屏，且若 512000 与该模型真实上下文窗口不符，会导致压缩过早或过晚。
- **处理结论（2026-09-18 复核）**：**无需改代码**。本地 09-16 14:24 提交 810b3169（deepseek-v4-flash 官方改名 deepseek-flash 全量同步）已包含该映射，但生产 09-18 15:54 才部署、15:56 重启——93 条全是部署时差内的过渡期噪音。重启后实测：容器内映射含 `deepseek-flash`、`settings.llm.deepseek.model='deepseek-flash'`、最后一条 WARNING 为 15:24:11（重启前），无复发。

### 2. wechat_mp callback GET 验签失败（8 条，09-18 集中）

```
wechat_mp callback: GET 验签失败 config_id=chan_0a3f8eb8a109
wechat_mp callback: GET 验签失败 config_id=chan_46bb27a75b8e
```

- **现象**：09-18 16:15 与 16:49 两个时间窗各 4 连发，涉及 2 个渠道配置。GET 验签是微信后台「服务器配置」保存/启用时发出的 echostr 校验请求。
- **可能原因**：① 管理员在微信公众平台点过「提交验证」但服务器 token 配置不匹配；② token 更新后未同步；③ 外部扫描伪造请求。
- **处理建议**：核对这 2 个 config_id 的 token 与公众平台后台是否一致；确认是否有人当天在操作公众号后台。4 连发成对出现更像人工触发的验证操作，若 token 一致则需查验签计算逻辑。

### 3. _reorder_messages_for_llm 丢弃连续 user 消息（6 条）

```
后端日志：_reorder_messages_for_llm 检测到连续 user，已丢弃较早的 N 条（保留最新）
```

- **现象**：防御性合并逻辑触发。样本包括用户连发多条文本（`'你好'`+`'我想了解一下科融信平台'`、`'家装用'`）、以及 **`[ASR识别结果]` 消息被丢弃**。
- **关注点**：被丢弃的 user 消息内容不再进入 LLM 上下文。若 ASR 识别结果被丢弃，等于用户语音内容丢失（可能语音后紧跟一条文字消息时触发）。低频（3 天 6 条），但涉及用户输入丢失，值得定位触发链路。
- **处理结论（2026-09-18 开发完成，待部署）**：按用户要求暂不改丢弃策略，先补诊断信息——被丢弃 user 的内容预览从 120 字符加长到 500 字符，语音消息（内容即 `[ASR识别结果] 文本`）的完整识别文本会记入日志，供人工评判丢弃是否合理（无需回听语音文件）。后续若证实误丢，再评估「拼接而非丢弃」策略。

### 4. text_sanitizer 剔除异常字符（5 条）

```
后端日志：文本含异常字符，已剔除（原文长度=403/428/502/507/8149）
```

- **现象**：孤立代理字符（surrogate）清洗生效，但**日志未记录文本来源**，无法定位是哪个入口漏了源头清洗。09-18 15:57:30 同一秒 3 条（长度 403/428/502 疑似同一来源批量）。
- **关联**：孤立代理字符治理（2026-09-10 事故）出口清洗已上线并正常兜底，但新入口仍在产生脏字符，源头未根治。
- **处理结论（2026-09-18 开发完成，待部署）**：`sanitize_text` 日志降级为 INFO（该日志是兜底清洗成功的正常记录，非异常告警），并新增可选 `source` 参数——日志带来源标签（`llm_gateway`/`tool_result`/`embedding_input`/`kb_parse`/`data_analysis_api`/`data_analysis_schema`/`wechat_mp_content`/`wechat_mp_ingest` 共 8 个入口已传入）+ 命中字符类别统计（surrogate/control/noncharacter/BOM）+ 首个命中码点（如 `首个=U+D83C`）。部署后再观察即可定位漏清洗源头；8149 长文那条优先排查（可能是知识库/网页抓取）。

### 5. wechat_mp 文章总结 LLM 超时（3 条，09-18 17:36~17:37）

```
wechat_mp 文章总结第1次尝试失败: TimeoutError
wechat_mp 文章总结第2次尝试失败: TimeoutError
wechat_mp 文章总结两次尝试均失败（回退 merged 原文入库）: TimeoutError
```

- **现象**：WP13 图片解析/总结链路单篇两次 LLM 调用均超时，回退 merged 原文入库（设计内降级，功能未坏）。
- **处理建议**：已有重试 + 回退，属偶发。若同模型持续超时可关注 deepseek/qwen 侧当时延迟；暂观察。

### 6. Redis DNS 解析临时失败（2 条）

```
[Redis] 连接失败，降级到内存存储: Error -3 connecting to aid-redis:6379. Temporary failure in name resolution.
```

- **现象**：09-17 20:56:41、09-18 15:56:47 各一次，容器内 `aid-redis` DNS 瞬时解析失败，自动降级内存。Gunicorn 6 worker 内存隔离下，降级窗口内的跨请求状态可能不一致（该 worker 独享内存缓存）。
- **处理结论（2026-09-18 开发完成，待部署）**：定性为部署窗口期故障——两条均对应 redis 容器重建/重启后 api 容器先于其就绪拉起，worker 首连时 Docker DNS 未注册，`_connect` 单次失败即降级进进程内存，降级窗口内跨 worker 状态写入漂移（用户取消标记、recap 锁等）。降级非终身（`_ensure_connection` 每次操作自愈重连），风险在窗口期内。三层防御：
  1. `docker-compose.prod.yml`：api + background 加 `depends_on: aid-redis: condition: service_healthy`，等 Redis 健康再启动（单容器 `docker restart` 绕过 depends_on 的场景由下一条兜底）；
  2. `redis_client._connect` 新增 `retries` 参数：进程启动首连退避重试 3 次（2s/4s/8s）；运行期重连保持单次快速失败，避免 Redis 长期宕机时每操作多次阻塞；
  3. 从降级恢复连接成功时清空内存降级存储（降级期写入不回补 Redis，残留数据会在下次降级窗口被误读）并打 warning 提示排查状态漂移。
  已知代价：api 启动最多延迟 ~30s（redis healthcheck 首探 30s）；启动首连重试最坏阻塞 ~34s（gunicorn timeout=120 覆盖）。

### 7. wecom_kf 语音转文字 ASR HTTP 400（1 条，09-16 15:11）

```
[wecom_kf] 语音转文字失败: 阿里云 ASR 服务异常: HTTP 400
```

- **现象**：单次。当时推测音频格式问题，后经 ERROR 日志确认真实根因：`status=40000010 Gateway:FREE_TRIAL_EXPIRED`（阿里云 ASR 免费试用过期，非音频问题），账户问题已另行处理。
- **处理结论（2026-09-18）**：渠道侧 WARNING 降级 INFO——工具内部（`speech_to_text_tool._call_aliyun_asr`）每次非 200 已打带 status+body 的 ERROR，channel_routes 的 WARNING 属重复记录且信息更少，降级后仅保留「失败 -> 回退 [语音消息]」的渠道侧痕迹。

### 8. AnalysisAgent 空总结重试（1 条，09-16 15:09）

```
[AnalysisAgent] 空总结（iteration=17），重试一次
```

- **现象**：数据分析智能体第 17 轮迭代产出空总结，自动重试。已有重试机制兜底。
- **处理建议**：单次观察即可。

---

## 处理结果（2026-09-18 收尾）

| 项 | 结果 |
|----|------|
| #1 deepseek-flash 模型映射 | ✅ 已解决：09-18 15:56 部署重启后归零（部署时差过渡期噪音） |
| #3 连续 user 丢弃 | ✅ 完成开发：丢弃预览加长 500 字符（含完整 ASR 文本），待部署 |
| #4 sanitizer 无来源日志 | ✅ 完成开发：降级 INFO + source 来源标签（8 入口），待部署 |
| #6 Redis DNS 降级 | ✅ 完成开发：三层防御（compose 健康依赖 + 启动重试 + 恢复清残留），待部署 |
| #7 ASR HTTP 400 | ✅ 已处理：根因免费试用过期，渠道侧 WARNING 降级 INFO，待部署 |
| #2 wechat_mp 验签失败 | ⏸ 暂不处理（收尾评估不重要，4 连发成对更像人工触发验证，复发再核对 token） |
| #5 文章总结超时 / #8 空总结重试 | ⏸ 暂不处理（均有兜底机制的单次/偶发事件） |
