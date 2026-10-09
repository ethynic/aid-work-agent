# Runtime 管理 feature

H3 在公共壳中渲染 `RuntimeManagementPage`，注入 `port: RuntimeManagementPort`。模块不访问 `window` bridge、路径、设备凭证或业务 invoke，也不负责产品托盘和退出策略。页面卸载仅取消观察及本地等待，不停止 Host。

`request` / `observe` 使用 H1 候选0.3；`onDisconnect` 结束当前连接代次。`choosePackage({instance_id,request_key})` 由 Main 可信选择器实现，返回 `null`（取消）或 `{selection_ref,label}`；label 仅显示名称，不能带任意设备路径。同一 request_key 绑定该选择引用及 import 用途。能力 `first_party_plugins` 存在时才提供安装/启停/卸载。

消费端在生产模块校验回复形状；共享 instance/revision 水位，state/list 分别 dirty。连接代次变更清快照并结束旧 Promise；同实例短暂断连保留原未确认意图及已接单operation，重连后由用户用原key重查未知请求，已接单仅查询operation。确认为新实例时清除未确认意图并提示核对，禁止自动重放；显式dispose清敏感内存。配对码仅在提交过程和不确定结果重试的必要内存意图内，页面不保存至浏览器存储或日志。

feature 内 Vitest 配置可独立运行：`npx vitest run --config desktop/features/runtime/vitest.config.ts`。H3 可将这些测试纳入公共 Desktop include；Runtime 不修改全局配置。页面视觉验收须 H3 实际接线后执行。

完整签名包检查可能包含大型OCR依赖；管理查询默认有界等待120秒，H3的请求与首次describe预算须一致。连接变更/页面卸载立即结束等待，超时只说明结果未确认，不取消已接单Host动作或自动重放。

独立 feature 构建：`npx vite build --config desktop/features/runtime/vite.config.ts --outDir <临时输出目录>`。Vue 由壳提供，公共 Desktop 的既有 semantic CSS 和 `--d-*` tokens 由壳加载。全局入口尚未接线时，原 `build:desktop` 通过不代表 feature 已进入产品包，须同时完成此独立编译和 H3 最终接线构建。
