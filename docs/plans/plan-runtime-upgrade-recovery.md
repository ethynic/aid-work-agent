# Runtime 覆盖升级与异常退出自动恢复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 升级协调退出与原运行意图恢复 | ✅ 完成（2026-10-12） | 安装器协调 stop/drain；保护运行意图，取消安装恢复自启选择 |
| Phase 2 | Windows 进程监督与跨重启恢复 | ✅ 完成（2026-10-12） | Job监督、DPAPI启动账本与重启见证验证通过；保留身份与业务事实 |
| Phase 3 | 独立验证、CR及未签名验收ZIP | ✅ 完成（2026-10-12） | 最终包内EXE与独立Main8项通过，CR无P0/P1/P2；r3 ZIP摘要/CRC通过 |

## 共同检查点与版本登记（2026-10-12）

共同契约仍为 v2.0 / H1 候选0.3；Device API、binding、任务授权不变。当前主控唯一写入 core management/Provider launch 和 Runtime 组合层、Electron升级入口、NSIS及发行材料；原生进程监督由有界开发角色负责，独立测试与CR只读产品代码。

- H1严格state/schema保持不变；恢复标志仅经直属受信Node→Main私有IPC `runtime_recovery` v1传递，instance_id绑定当前握手，不转发renderer。只有已证明旧资源停止才发送，不代表业务结案。
- 内部 HostAdapter 增加可选 executionMarker/recoverPreviousExecution，canChangePlugins兼容增加可选method；无恢复实现的旧适配器保持 fail closed。
- 本机 `host-running.json` v2 保存 instance_id 与 DPAPI保护的执行监督记录引用；同用户/home唯一Host写入。旧 `{instance_id}` 格式保持识别，但必须先登记 DPAPI保护的重启见证，再验证 Windows kernel BootIdentifier 已变化才能归档。
- 本机 process-supervision v1 记录按当前用户DPAPI保护，含 generation/home/Windows session/jobName；Provider启动前先落盘，原生辅助器将Node挂入KILL_ON_JOB_CLOSE Job后才执行。同boot恢复必须在原Windows session查询Job，切换会话则要求重启。不管理外部已有Chrome。
- 归档异常marker创建身份核对 fence，不能冒充业务ACK；journal/outbox/session/云端unknown保留。恢复连接不重放旧工具调用。只允许经验签/更高版本门的import，保留旧release全部文件，新增调用用新版本；已有未结案v2/session仍阻断，换身份和破坏性插件操作仍阻断，不清fence。
- `--aidwork-exit-for-update` 仅安装的Runtime处理；协调正常停止，安装等待有界，未成功时不覆盖。旧r1/r2没有该入口，要求一次正常Windows重启迁移，之后升级自动协调。

## 验证与限制

验证同boot/跨boot、篡改或误绑定记录、真实进程树及Host退出、在途任务升级、未配对与显式停止、自动恢复不重放、安装包内容和隔离启动。网络不通且原v1结果无法交付时，升级等待超时并停止安装，保留旧进程；本轮不改云端结果协议或扩大为v1持久结果改造。未知业务事实需要业务核对，不能通过卸载删除。

## 最终实现与独立验证（2026-10-12）

- 从 r3 开始，安装/卸载经固定维护入口停止领取新任务、等待原业务和结果收尾，再等待直属Host实际退出；升级等待600秒，超时暂停安装，不强杀。X继续隐藏到托盘，升级无需用户先手动退出。
- Main先冻结新管理请求再读取状态、保存原运行意图。意图使用既有CurrentUser DPAPI保护，FIFO串行读写和归档；维护期间禁止恢复启动，迟到的renderer回复不能覆盖意图。维护失败后的新明确start/stop可覆盖旧意图；显式停止不会被升级或登录恢复误启动。
- 旧r1/r2不认识维护入口。安装器为相同Windows用户/home登记保护的kernel BootIdentifier见证、验证摘要的安装器副本及一次RunOnce续装；用户正常重启后才证明旧进程已停止。取消/失败按绑定恢复原自启项，不强制重启、不要求例行提交日志。
- 新版受管Node先挂入Windows Job再运行，Host退出或异常死亡回收所属后代；外部已有Chrome不纳入。默认同时回收本次启动的Chrome，保留登录profile。启动前保护账本落盘，恢复限定home、generation、boot和session；证据不足保持阻断。
- 配对、插件、业务journal/outbox/session以及身份核对fence保留。进程资源恢复不冒充业务ACK、不重放unknown。保留r1/r2公钥；BOSS升级到0.3.1补齐签名物理点击C#依赖，已有旧release文件保留。
- 独立core/store24项、Runtime15项、Desktop22项通过；恢复、原生监督、实际compiled Main异步入口与NSIS隔离fixture验证通过。最后新增Main回归包含维护期间迟到start/stop回复，8项通过。最终CR无剩余P0/P1/P2；详细命令和实际结果随ZIP保存在独立验证记录。
- 实际包内EXE维护退出、DPAPI跨进程读取、重开消费、无实例维护及smoke验证；包内模块与独立测试dist核对，确认进程退出及home锁释放。真实用户自启项未修改。

实际Windows重启/RunOnce登录续装、客户完整安装卸载、真实云端配对和BOSS业务仍需验收。隔离NSIS fixture和模拟跨boot验证不能替代以上验收；未执行真实长任务业务升级或不同Windows session恢复。包构建交付时尚未提交/推送，服务器未部署。

## 最终交付

- ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r3.zip`，131009495字节、11文件；CRC及全部文件SHA256复读通过。
- ZIP SHA256：`231ac282ea8153acd1a9bdc15136011bdac6fb4052e2bb4284e214000fcc9b12`。
- 安装器126280275字节，SHA256 `2163a20b15f7ec412acc62ca1db7683195b851c4691449ee1d69eb9d2ab036d6`，Windows NotSigned。
- BOSS 0.3.1插件SHA256 `4b06d9c6d2c2cc19dba0eadd2debdf342cf324bd872484eb5a256ed67e1d6892`，本批隔离发布者签名，22工具；安装器保留旧发布者根。
- 最新包内Main SHA256 `624f726318473c95521d3eabeaddab3f223fd1b8ff974234d8e951805883bf1f` 与独立compiled回归一致；同EXE实际维护退出/DPAPI读取/重开消费/无实例/smoke全部通过，进程及home锁释放，真实Run未变。

## 提交核对（2026-10-12）

用户明确授权提交；fetch后快进同步远程master至`195d1efe`，Runtime/BOSS相关源码没有远程重叠改动。保留其他任务7个文件及ideas中的数据分析条目；本提交只包含Runtime/BOSS相关代码、配置说明与登记文档。同步后Desktop typecheck及启动/监督/升级22项再次通过；包内程序和ZIP摘要保持不变，未部署服务器。
