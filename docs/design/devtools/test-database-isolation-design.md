# 测试数据库隔离与 Runtime 断言修复

日期：2026-10-26；来源为[WeCom计划W9](../../plans/plan-wecom-cli-group-chat-experiment.md)；进度见[开发计划](../../plans/plan-test-database-isolation-runtime-assertions.md)。本次仅测试设施与断言，不改产品数据库连接策略、Runtime工具权限或公共wire。

实施状态：本地开发、独立TEST与CR已完成；CREATE成功资格在游标退出前登记，收尾故障修复已验证。真实Docker未运行，仅验证GitBash/stub入口；pgvector不在本轮验收范围。未提交、部署或恢复监听。

## 问题与目标

根tests/conftest.py会加载业务.env、回退默认共享库，在session前后连接并删除测试命名租户；真实SkillLoader也可能建表。scripts/dev_test_isolated_db.py同样会从.env获取管理目标，随机库名不能证明管理连接安全。此前未证明隔离的批次是否连接/写入仍未知，本次不连接业务库追查。

默认普通pytest须可运行纯单测，任何未证明安全的数据库初始化/清理在网络连接前拒绝。真实SQL仅在明确指定的本机aid_test_*库运行；隔离runner只使用明确指定的本机管理库postgres创建/删除本次随机库。不得增加不安全绕过参数。

## 最小方案

共享的纯测试安全模块供conftest与隔离runner使用，不导入产品src模块。移除两个入口的.env加载及硬编码业务DSN。普通pytest没有DATABASE_URL时进入无数据库模式，预设惰性安全配置、禁用追踪库并在psycopg2实际连接边界拒绝，避免SkillLoader或导入路径绕过session hook；显式非法URL/非本机/非测试库应在src导入前报脱敏配置错误。真实连接模式固定本轮精确数据库/本机端点，检查DB与logs目标，拒绝可覆写host/dbname的query/service/hostaddr等旁路及影响连接的libpq环境默认值；只保护正常测试驱动入口，不宣称隔离任意恶意Python代码。

租户清理只有明确安全库模式才能执行，连接后先核实际current_database与期望完全一致，再查询/删除；collect-only和无数据库模式不清理、不主动初始化产品连接池。隔离runner在任何连接前验证本机管理目标postgres；子进程业务与追踪连接都指向本次随机aid_test库，最终仅删除自己成功创建的库。现有Linux/Docker与Windows入口均需能给出清楚的安全错误，不能默认使用容器业务连接。

Runtime仅修tests/providers.test.ts：当前25项包括只读boss_recovery_probe；当前摘要独立固定，同时从历史投影排除后来新增的probe以维持既有历史锚点；Provider集合明确含runtime-recovery共5项。不得删生产工具/放宽白名单来使旧断言通过。

## 验收

子进程反证必须覆盖：无DSN/恶意业务.env/显式共享或远端DSN均不触达DB；正常无DB单测与collect-only可执行；实际池/SkillLoader连接不能逃出guard；异常不泄露凭据；真实私有PG随机库能创建/初始化/清理，trace同库，cleanup实际库名错误时零DELETE。测试真实的连接边界与SQL，不用仅断言环境字符串冒充隔离。覆盖Windows及shell宿主/容器参数转发，无法实际启动容器则明确模拟边界。Runtime修后广跑原失败的整个providers文件并检查写属性、跨Provider隔离及历史锚点，不能直接把当前摘要抄为历史摘要。
