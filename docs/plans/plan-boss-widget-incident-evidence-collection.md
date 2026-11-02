# BOSS 隐藏窗口事故：客户机只读取证交付

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 取证脚本 | 投影当次绑定与视觉恢复元数据 | ✅ 完成（2026-10-31） | 显式字段白名单、事故时段筛选和缺失/错误计数；不接入 Runtime |
| 验证与交付 | 隔离夹具验证、独立检查、打包 | ✅ 完成（2026-10-31） | Windows PowerShell 5.1；独测23项通过/1项环境跳过；独立CR无阻断；客户回收22条事故观察且无解析错误 |

关联：[事故复盘](../incidents/boss-agent2-widget-stuck-hidden-20261030.md)。

**后续产品化必做项（2026-10-31用户明确要求）**：临时附件不能替代Runtime内置诊断。统一设计与待开发阶段分别登记在[架构第14节](../system/runtime-plugin-host-architecture-design.md#14-客户端内置诊断留存与一键导出2026-10-31待开发)、[Runtime计划第14节](plan-runtime-plugin-host.md#14-内置诊断留存与一键导出开发计划2026-10-31)。本页“完成”仅指临时附件，客户端能力仍待开发。

## 范围与成功条件

客户在原故障电脑、原 Windows 用户下手动运行附件。只读取当前用户临时目录的 `aid-boss-targets/*.json` 与 `aid-boss-visual/<UUID>/record.json`，输出一份新的 JSON；不调用浏览器、网络、Runtime、设备配置或输入 API，不修改源文件。视觉记录限定 2026-10-30 北京时间 10:40–11:20；绑定记录仅输出可关联的 ID 与文件时间，不把当前状态当历史快照。

输出字段白名单为目标/绑定/request ID、文件与采集时间、有限页面路径、窗口 PID/HWND/几何、观察模式、输入阶段及有限诊断枚举。禁止原样序列化源对象、错误消息、context/response、URL query、职位/姓名、消息、图片、本机路径或凭据。源目录不存在、JSON 损坏和不在事故时段分别计数；结果标注历史可能缺失，不声称能还原所有事件。拒绝跟随源目录/文件的重解析点。

## 验证与交付

隔离夹具放入明显的隐私/凭据哨兵，验证正确关联字段能导出、其他内容不泄漏，源文件摘要不变；覆盖缺目录、损坏 JSON、时段外记录与重解析点。使用 Windows PowerShell 5.1 验证实际启动入口。主控开发自测后由独立角色验证及审查；只发布诊断附件，不提交、安装或部署产品。

交付为 PS1、双击启动 CMD 和操作说明的 ZIP；启动器的执行策略仅作用于该次 PowerShell 进程，不修改系统策略。客户解压后运行，返回生成的单个 JSON。脚本可用不代表客户缓存仍存在或故障已解决。

## 验证记录

- 主控：PowerShell 语法与5.1实际启动、缺目录报告、合法元数据关联、隐私哨兵排除及源文件摘要不变通过。
- 独立测试：`node .tmp/boss-evidence-independent-test-20261031/run.mjs`，Windows PowerShell `5.1.26100.9549` 下23项通过、0失败；文件 symlink 创建无权限，1项环境跳过，根/子目录 junction 拒绝通过。独测未打开真实启动器交互窗口，其同目录引号路径经检查；后续客户已返回真实执行结果。证据保留 `.tmp/boss-evidence-independent-test-20261031/result.json`。
- 独立CR：无未解决P0/P1/P2。双方核对PS1 SHA256 `ed0de3a6d910b8da26a482cb7309e002cbd0cb527d369d87e210aea6615e1697` 一致；未访问真实客户缓存或操作浏览器/Runtime。
- 最终ZIP：`.tmp/BOSS-incident-evidence-kit-20261031.zip`，只含上述3个附件，SHA256 `ebf60befc484fb20226c77ea2c5c2a1d12569275f752dc72c0cb32bf97b2383d`。本轮仅交付独立诊断附件，无产品安装、部署或Git提交。
- 客户回收：`boss-widget-evidence-20261031-152140-7cec3b.json`，SHA256 `3961a1a305695c33c539aba3863c161e433f39eef348e11a687d37ef3dd797bc`；2份绑定、23份视觉记录中22份在事故时间窗，解析错误与issues均为0。核实22个request唯一、target/binding一致、10份整窗身份一致、两次关闭后验均满足；联结原runtime日志后确认第二次整窗原生点击路径。结果及局限见[复盘第16节](../incidents/boss-agent2-widget-stuck-hidden-20261030.md#16-客户回收元数据同一目标整窗原生点击可用2026-10-31)。分析脚本保留 `.tmp/boss-widget-incident-review-20261031/review-customer-metadata.mjs`；未操作浏览器或更改产品。
