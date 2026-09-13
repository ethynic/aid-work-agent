"""端侧会话任务通用协议模块（C1）。

模块划分：constants（枚举/错误码）/ models（发布契约）/ init_tables（DDL）/
texts（受控加密文本）/ confirmations（发布确认链）/ config（配置与热读门控）/
service（生命周期与设备协议）/ api（用户根+设备根路由）。
场景特定逻辑在 src/weixin_conversation/；通用层不 import 场景实现。
"""

import psycopg2.extras

# 服务层参数大量使用 uuid.UUID（task/assignment/spec 等）；注册 UUID 适配器使
# psycopg2 可直接绑定（仅新增 uuid.UUID→uuid 的适配，既有字符串传参不受影响）。
psycopg2.extras.register_uuid()
