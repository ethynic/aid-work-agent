# 端侧会话任务 C5 发布与回滚

日期：2026-09-15。适用场景仅 `weixin.conversation.v1`。当前 **禁止灰度**，见 [验收报告](../research/weixin-cli/edge-session-acceptance.md)。本文件是后续操作规程，不代表已执行发布。

## 放行前置条件

1. C0 真机授权记录完整：设备、微信版本、账号、单聊/群聊绑定、对端配合方式、时段、内容范围、发送上限。单聊/群聊分别验收，未验证类型不开。
2. 受信身份验证与真实截图接线有证据；当前 `sessionObserve.ts` 默认无 captureFn，返回 unavailable，不能用 fixture 身份覆盖生产绑定。
3. 干净 Windows 用户环境实际安装产物，验证 Node/Provider/驱动/OCR 模型及解释器可定位。本轮 `ocrResident.ts` 已优先使用包根 `ocr-python/python.exe`，没有随包解释器时保留开发venv回退。实际安装闭环尚未验收；禁止靠把开发机 PATH 当随包依赖来验收。
4. [计划 §8 A1–A11](../plans/desktop-automation/plan-edge-session-task.md) 全部有对应范围证据，无未关闭 P0/P1；端到端同条件至少30样本，编排性能/正确性/预算分别通过。
5. 空库初始化与幂等重放、增量迁移、隔离 API/background 两进程启动通过。不得在生产环境运行测试 fixture 或启用测试 verified 绑定。

## 灰度配置与操作

安装准备命令：`npm --prefix clients/weixin-cli run pack:portable -- <embedded-OCR绝对目录> <已存在的输出绝对目录>`。源目录必须为预装RapidOCR/onnxruntime/Pillow及模型的embedded Python，普通venv、符号链接依赖及跳出包根的 `_pth` 均拒绝。脚本在临时副本中清除PYTHONPATH/PYTHONHOME并以 `-I` 构造OCR引擎后组装npm归档，不修改BOSS环境。当前没有准备好的embedded源，实际产物仍未生成。

这是需 `npm install <tgz>` 安装依赖的Provider归档，不含Node或node_modules，也没有新增bin入口；在安装目录通过 `node node_modules/aid-weixin/dist/src/cli/index.js mcp --stdio` 启动。目标需满足现有Node≥22。发布前还须在干净用户下安装归档、检查模型确实进入包、运行常驻OCR合成图测试及重启恢复；打包前自检不能替代该步骤。

当前 `configs/config.yaml` 两项均保持 false。将来门禁通过后，先为 **同一个明确租户** 设置 `session_tasks.tenant_allowlist` 与 `weixin_conversation.tenant_allowlist`，再开启对应 enabled。空 allowlist 在现有实现中表示不限租户，不能用于灰度。

仅一台授权设备、一个授权会话、一项有限预算任务；通过现有工作台确认单发布。单任务完整观察、决策、许可、发送、回执闭环后，依据60秒观察覆盖实测再扩容，最多5项；不自动扩租户/设备。v2Send 必须与实际 Provider 能力一致，配置 true 不会补出不存在的驱动或身份证据。

enabled/tenant_allowlist 在发布、claim、新许可等路径按文件版本热读；其余 worker 节奏、租约配置为进程快照，修改后重启相应 API/后台/Runtime 进程并复核。不要因为关闭新场景而关闭固定内容营销、BOSS 或通用结果接收服务。

## 范围限定的回滚

1. 单租户回滚：从两个 allowlist 的明确列表中移除目标租户，保留其他租户。**若删除后为空，必须同时关闭该 gate**，否则空列表会放行所有租户。关闭整个会话场景时将 `weixin_conversation.enabled=false`；需要停全部通用会话执行时再关闭 `session_tasks.enabled`。不改 `weixin_marketing.enabled`。
2. 用该租户属主身份读取会话任务列表，按 `task_id` 和当前 `version` 调用 `POST /api/session-tasks/{task_id}/pause`。永久取消用 stop。发生版本冲突重新读取，禁止忽略 CAS 或直接批量改表。
3. 保持 API、现有账务与结果/outbox 接收链可用，核对已领取动作。界面语义为“停止新发送，已有动作结果待确认”；已提交动作不能承诺撤回。
4. 对每项任务核对唯一 execution_links、invocation/delivery、attempt、已结算与未决预留。unknown 不释放占用、不重派；旧 fence 回执仍记事实，不能使旧授权复活。
5. 保留 session journal、outbox、受控正文、审计及数据库列；不删除账本、不清空队列、不回退给 standard lane 或主 Agent 循环。先暂停并对账，待无未知效果才做版本回退。代码版本回退不能代替兼容性验收。
6. 恢复须显式选择 `fresh_baseline`，提供当前版本/输入水位；历史不补发，开场白不重建。身份、覆盖、unknown 等 blocked 需专门证据处置，不能直接按恢复按钮洗白。

## 监控与交接

按 tenant+scenario+task 记录阶段、last_observed_at、input_version、控制代、租约、预算已花费/预留、unknown/gap数量、待同步数量。消息正文与凭据不进入普通日志。纯等待不通知；完成、失败、接管、预算耗尽使用现有去重站内通知。

发现错目标、重复发送、越权、预算突破，立即停止该范围新许可并保留证据。灰度登记必须包含版本、授权范围、A1–A11结果、全部失败/skip、安装环境、耗时分布及回滚演练结果。当前不具备自动发布条件。
