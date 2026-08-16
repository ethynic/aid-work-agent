# 简历库 CLI 对接交接说明（resume-detail 命令联调指引）

> 写给 2026-08-17（周一）公司电脑上的新会话。云端/服务端代码已提交（本提交），
> 公司电脑上 **boss-resume-assistant CLI 的简历详情命令（读取简历详情 + 截图 + OCR）已实现但未提交**，
> 本文说明两边如何对接。读完后按 §5 步骤执行即可。

## 1. 两边现状

### 云端（已提交，本仓库 master）
- **服务层** `src/services/recruiting_resume_service.py`：简历库全部业务逻辑
  （建表、base64 图片落盘、CRUD、契约适配），智能体工具与前端 API 共用
- **前端 API** `src/api/recruiting_operator.py`（`/api/recruiting-operator/resumes`，薄壳）
- **前端页面** `/recruiting-operator/resumes` 简历库（列表筛选/详情画廊/手动录入），
  SUBAGENT.md 已声明 business_pages，侧边栏「招聘操作智能体 → 简历库」可进
- **本地工具** `BossResumeDetailTool`（`src/local_tools/proxy_tool.py`，name=`boss_resume_detail`）：
  像其他 boss 工具一样走 设备闸门 → invocation → 本机 Runtime → MCP stdio 执行 CLI；
  **CLI 成功结果在云端工具层直接落库**，返回给 LLM 的只有紧凑摘要
  `{resume_id, candidate_name, job_name, image_count, ocr_char_count}`，
  图片字节/OCR 全文在所有返回路径都不进 LLM 上下文（有序列化断言测试盯着）
- **受信清单** `src/local_tools/catalog.py` 已含 `boss_resume_detail`
- **SUBAGENT.md** 已加工具/授权规则（读取+内部入库，非外部写动作）/链路/简历库提示

### 本机 CLI（公司电脑，未提交）
- `clients/boss-resume-assistant/` 里已实现简历详情命令：读取简历详情、截图、OCR 出文本
- **首要任务：先把 CLI 代码提交推送**（它是这部分的源头，云端这边已等它）
- 注意：仓库里另有未跟踪的 `clients/boss-resume-assistant/{chi_sim,eng}.traineddata`
  （Tesseract OCR 训练数据，2.4MB/5.2MB）——若 CLI 的 OCR 依赖它们，提交时确认
  .gitignore 策略与安装方式（npm pack 打包 or 文档说明下载），避免别的机器装不上

## 2. 数据契约（对接核心）

**契约对齐点**：`src/services/recruiting_resume_service.py` 的
`create_resume_record_from_tool_result(tenant_id, user_id, payload, source)`，
函数 docstring 有醒目标记「──【契约对齐点 2026-08-16】──」。

CLI 命令的结果经 Runtime 回传后落在 invocation `result_json.data`，即下面的 `payload`。
当前服务端按此形状宽容解析（**支持别名**，大概率不用改服务端；不符则改这一处即可）：

```jsonc
{
  "candidate_name": "张三",          // 必填；别名 name。缺失 → RESUME_PAYLOAD_INVALID，不落库
  "job_name": "后端开发工程师",       // 可选；别名 job / position（当前招聘职位框的职位名）
  "basic_info": {                    // 可选；别名 candidate_info。任意键值 dict
    "学历": "本科", "工作年限": "5年", "期望薪资": "15-20K", "城市": "深圳"
  },
  "ocr_text": "OCR 全文……",          // 可选；别名 ocr / text
  "images": [                        // 可选；别名 screenshots。有序多图
    { "name": "resume_p1.png", "mime_type": "image/png", "base64": "iVBORw0KGgo..." }
  ]
}
```

硬约束（服务端已实现校验，CLI 侧注意满足）：
- `mime_type` 白名单 **png / jpeg / gif**（webp/bmp/svg 会被 400 拒绝）；缺省按 image/png
- 单张解码后 **≤10MB**；base64 可带可不带 `data:image/png;base64,` 前缀
- **失败结果的 `data` 不要携带 payload**（如部分截图 base64）：服务端已兜底剥离
  （失败分支 data 置 None），但契约上约定失败只回 code/message 更干净
- payload 字段类型须为字符串/dict/list（int 等错型会落 RESUME_STORE_FAILED 而非 PAYLOAD_INVALID，服务端 P2 待加固）

## 3. CLI 侧需要对齐的三件事

1. **MCP tool 名必须是 `boss_resume_detail`**（与 catalog.py 受信清单/云端工具名一致），
   operation 注册进 `src/main/operations/index.ts` 的 OPERATIONS 表（CLI 与 MCP 共用，业务只实现一次）
2. **命令输出结构**按 §2 payload 组织（或改服务端契约对齐点适配 CLI 实际字段名——二选一，改哪边都行，建议小改）
3. **工具入参**：云端 `BossResumeDetailTool.InputModel` 目前无参数（读当前会话候选人）。
   若 CLI 命令需要参数（如指定会话/滚动截图页数），同步补：
   - `src/local_tools/proxy_tool.py` 的 InputModel（Pydantic）
   - `subagents/recruiting-operator/SUBAGENT.md`（描述里说明参数）

## 4. 本地验证（不用真机，先跑这些）

```bash
# 服务端单测（假 payload 打桩基类 execute，验证编排/落库/摘要/不进上下文）
venv/Scripts/python.exe -m pytest tests/integration/test_recruiting_resume_tool.py -v
# API 层 + 回归
venv/Scripts/python.exe -m pytest tests/integration/test_recruiting_operator_apis.py tests/unit/local_tools -v

# CLI 侧（clients/boss-resume-assistant/）
npm run typecheck && npm test
```

## 5. 真机验收步骤

1. 关掉所有 Chrome → `chrome.exe --remote-debugging-port=9222` 启动日常 Chrome → 登录 BOSS
2. `node dist/src/cli/index.js doctor` 四项全绿
3. Runtime 在线（本地工具页面设备 selected + active）
4. Web 对话 recruiting-operator：「读取当前候选人的简历并保存」
   → 智能体应执行 `boss_goto(chat)` → `boss_resume_detail`，回复入库摘要（姓名/职位/截图张数）
5. 打开「招聘操作智能体 → 简历库」页面：新记录出现，详情里图片可看、OCR 文本完整、职位/日期正确
6. 若契约不符：看后端日志 `boss_resume_detail 结果入库失败 payload 不符契约`，
   按 §2/§3 对齐后重试（RESUME_PAYLOAD_INVALID 不会留半截数据，直接重跑安全）

## 6. 已知边界（联调时顺手看）

- P2：失败 payload 约定（§2 已写）、payload 字段类型校验、DB 失败时已落盘图片的孤儿清理、
  `_cleanup_image_files` 清理范围含引用路 file_id（触发面极窄）
- `boss_resume_detail` timeout 600s（截图+OCR 较慢）；effect=unknown 时摘要 message 会保留
  「实际效果未知，禁止重试」提示
